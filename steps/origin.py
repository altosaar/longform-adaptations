"""Origin: trace a credited book (or podcast) back to the periodical piece it grew out of
(Addendum 2 #1). Lookups first, the model last:

  1. same film, same author: a journalism row for this film by this author is the origin
  2. otherwise one BAML TraceOrigin call (Haiku + web search), once per row (origin_log)

When an origin is found the row is rewritten to describe it: source_title, publication,
date and type become the print origin's (the TV addendum's rule), the book's title moves
to source_aliases, and credited_source_type keeps saying "book". Reviewed rows are never
rewritten. Run after classify, before dedupe (which then merges the two rows of case 1).
"""

from __future__ import annotations

import asyncio
import logging
import re

import db
import publications
from steps import cli

log = logging.getLogger("origin")
TRACE_TYPES = ("book",)  # podcast-seed rows are traced by the fill step; manual rows never


def surnames(authors: str | None) -> set[str]:
    return {db.norm(a).split()[-1] for a in re.split(r",|&|\band\b|/", authors or "") if db.norm(a)}


def journalism_type(model_type: str | None, publication: str | None) -> str:
    if model_type in db.JOURNALISM:
        return model_type
    pub = publications.lookup(publication)
    return {"daily": "news_article", "wire": "wire_or_brief"}.get(pub["kind"] if pub else "", "magazine_feature")


def rewrite(con, r: dict, title: str, author: str | None, publication: str | None, date: str | None,
            origin_type: str) -> None:
    """Make the row describe its print origin; keep the credited work as an alias."""
    same_work = db.norm(r["source_title"]) == db.norm(title)
    aliases = [a for a in (r["source_aliases"] or "").split("|") if a]
    if r["source_title"] and db.norm(r["source_title"]) != db.norm(title) and r["source_title"] not in aliases:
        aliases.append(r["source_title"])
    chain = r["source_chain"] or ""
    if not re.match(r"\s*(article|series|newspaper|magazine)", chain, re.I):
        chain = "article → " + (chain or f"{r['source_type'] or 'book'} → film")
    con.execute("""UPDATE adaptations SET source_title = ?, source_author = COALESCE(?, source_author),
                   publication = COALESCE(?, publication), source_date = COALESCE(?, source_date),
                   source_type = ?, origin_type = ?, origin_title = NULL, source_aliases = ?, source_chain = ?
                   WHERE id = ?""",
                (title, author, publication, date, origin_type, origin_type, "|".join(aliases) or None,
                 chain, r["id"]))
    if not same_work:  # a different piece: the old link and snapshot belonged to the book
        con.execute("""UPDATE adaptations SET publication_normalized = NULL, source_url = NULL,
                       source_url_origin = NULL, archive_url = NULL, archive_status = NULL WHERE id = ?""", (r["id"],))


async def _trace(rows: list[dict], workers: int = 4) -> list[tuple[dict, object]]:
    from baml_sdk import TraceOrigin_async

    sem = asyncio.Semaphore(workers)

    async def one(r: dict):
        async with sem:
            kind = "podcast" if r["source_type"] == "podcast_or_other" else "book"
            try:
                return r, await TraceOrigin_async(r["source_title"], r["source_author"], kind, r["film_title"],
                                                  r["film_year"])
            except Exception as exc:
                log.warning("trace failed for %s: %s", r["film_title"], str(exc)[:200])
                return r, None

    return await asyncio.gather(*(one(r) for r in rows))


def main() -> None:
    a = cli(__doc__)
    con = db.connect()
    con.execute("CREATE TABLE IF NOT EXISTS origin_log (id TEXT PRIMARY KEY, evidence TEXT)")
    done = {x[0] for x in con.execute("SELECT id FROM origin_log")}
    rows = db.rows(con)
    hand = ("manual",)  # manual and manual_tv rows are a human's call
    todo = [r for r in rows if r["source_type"] in TRACE_TYPES and r["source_title"] and not r["reviewed"]
            and not any(s.startswith(hand) for s in (r["seed"] or "").split(","))
            and (a.force or r["id"] not in done)]
    todo = todo[: a.limit] if a.limit else todo

    # 0. the table already knows: a "book" row whose outlet is a periodical names an article
    #    (the classifier labelled it by what the film credits). Fix the type; no search.
    for r in list(todo):
        pub = publications.lookup(r["publication"], r["publication_normalized"])
        if pub and pub["kind"] in ("daily", "weekly", "magazine", "online", "wire", "trade"):
            t = journalism_type(r["origin_type"], r["publication"])
            con.execute("UPDATE adaptations SET source_type = ?, origin_type = ? WHERE id = ?", (t, t, r["id"]))
            con.execute("INSERT OR REPLACE INTO origin_log VALUES (?, ?)",
                        (r["id"], f"periodical: {pub['canonical_name']}"))
            todo.remove(r)
    con.commit()

    # 1. the table already knows: same film, a journalism row by the same author
    by_film: dict[str, list[dict]] = {}
    for r in rows:
        by_film.setdefault(r["wikidata_qid"] or db.norm(r["film_title"]), []).append(r)
    ask = []
    for r in todo:
        film = by_film.get(r["wikidata_qid"] or db.norm(r["film_title"]), [])
        article = next((x for x in film if x["id"] != r["id"] and x["source_type"] in db.JOURNALISM
                        and surnames(x["source_author"]) & surnames(r["source_author"])), None)
        if article:
            rewrite(con, r, article["source_title"], article["source_author"], article["publication"],
                    article["source_date"], article["source_type"])
            con.execute("INSERT OR REPLACE INTO origin_log VALUES (?, ?)",
                        (r["id"], f"same film, same author: {article['source_title']}"))
            log.info("%s: %s → %s (same author's article in another row)", r["film_title"], r["source_title"],
                     article["source_title"])
        else:
            ask.append(r)
    con.commit()

    # 2. ask the model, with web search
    log.info("tracing %d books/podcasts with web search", len(ask))
    found = 0
    for r, o in asyncio.run(_trace(ask)):
        if o is None:
            continue
        if o.from_periodical and o.title:
            otype = journalism_type(o.origin_type.value.lower() if o.origin_type else None, o.publication)
            rewrite(con, r, o.title, o.author, o.publication, o.date, otype)
            found += 1
            log.info("%s: %s grew out of %s (%s)", r["film_title"], r["source_title"], o.title, o.publication)
        elif r["origin_type"] in db.JOURNALISM:
            # the classifier guessed a journalism origin the search couldn't name: don't count it
            con.execute("UPDATE adaptations SET origin_type = ? WHERE id = ?", (r["source_type"], r["id"]))
            db.add_flag(con, r["id"], "origin-unconfirmed")
        con.execute("INSERT OR REPLACE INTO origin_log VALUES (?, ?)", (r["id"], o.evidence))
        con.commit()
    log.info("search found a print origin for %d of %d", found, len(ask))


if __name__ == "__main__":
    main()
