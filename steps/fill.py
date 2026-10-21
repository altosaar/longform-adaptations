"""Fill: Haiku reads gaps out of a seed's own words (`seed_context`): the category seed's
infobox + lead, a gallery caption, a list blurb. Fills only blank collect columns. A film
credited to several written works gets one extra row per additional work.

Rows from the podcast-adaptations seed get one web-search question instead: did the podcast
grow out of a published article? If so the article is the row's source ('article → podcast
→ series'); if not, the podcast is (source_type podcast_or_other).

Each row is attempted once (tracked in the fill_log table) so re-runs are free; --force
retries everything.
"""

from __future__ import annotations

import asyncio
import logging
import re

import db
from seeds.wikipedia_podcast_adaptations import CONTEXT_PREFIX, GENRE_UNKNOWN
from seeds.wikipedia_podcast_adaptations import NAME as PODCAST_SEED
from steps import cli

log = logging.getLogger("fill")
_PLACEHOLDER = re.compile(r"not stated|not named|unknown|untitled|unspecified|\(title|\(headline", re.I)
FIELDS = ("film_title", "film_year", "source_title", "source_author", "publication", "source_date")


def is_podcast_row(r: dict) -> bool:
    return (r["seed_context"] or "").startswith(CONTEXT_PREFIX) and PODCAST_SEED in (r["seed"] or "")


def needs_fill(r: dict) -> bool:
    if is_podcast_row(r):
        return False
    return not db.blank(r["seed_context"]) and any(
        db.blank(r[f]) for f in ("film_title", "source_title", "source_author", "publication"))


def known_text(r: dict) -> str:
    lines = [f"{f}: {r[f]}" for f in FIELDS if not db.blank(r[f])]
    missing = [f for f in FIELDS if db.blank(r[f])]
    return "\n".join(lines + [f"missing: {', '.join(missing)}"])


async def _fill_all(rows: list[dict], workers: int = 8) -> list[tuple[dict, object]]:
    from baml_sdk import FillGaps_async

    sem = asyncio.Semaphore(workers)

    async def one(r: dict):
        async with sem:
            try:
                return r, await FillGaps_async(known_text(r), r["seed_context"])
            except Exception as exc:
                log.warning("fill failed for %s: %s", r["film_title"] or r["source_title"], str(exc)[:200])
                return r, None

    return await asyncio.gather(*(one(r) for r in rows))


async def _origins(rows: list[dict], workers: int = 4) -> list[tuple[dict, object]]:
    from baml_sdk import FindPodcastOrigin_async

    sem = asyncio.Semaphore(workers)

    async def one(r: dict):
        async with sem:
            try:
                return r, await FindPodcastOrigin_async(podcast_name(r), r["film_title"], r["film_year"])
            except Exception as exc:
                log.warning("podcast origin failed for %s: %s", r["film_title"], str(exc)[:200])
                return r, None

    return await asyncio.gather(*(one(r) for r in rows))


def podcast_name(r: dict) -> str:
    return r["seed_context"][len(CONTEXT_PREFIX):].split(". Adapted as:")[0]


def drop_fiction_podcasts(con) -> None:
    """Addendum 2 #7: fiction podcasts are noise, not negatives. Wikidata's genre settled most
    of them in the seed; Haiku answers yes/no for the rest."""
    from baml_sdk import IsNonfictionPodcast_async

    rows = [r for r in db.rows(con) if is_podcast_row(r) and GENRE_UNKNOWN in (r["seed_context"] or "")]

    async def ask(r: dict):
        try:
            return r, await IsNonfictionPodcast_async(podcast_name(r), r["film_title"])
        except Exception as exc:
            log.warning("genre question failed for %s: %s", r["film_title"], str(exc)[:200])
            return r, None

    async def run():
        return await asyncio.gather(*(ask(r) for r in rows))

    dropped = 0
    for r, yes in asyncio.run(run()):
        if yes is False:
            dropped += db.exclude(con, r["id"], PODCAST_SEED, "fiction-podcast (model)")
        elif yes:
            con.execute("UPDATE adaptations SET seed_context = replace(seed_context, ?, ?) WHERE id = ?",
                        (GENRE_UNKNOWN, "Podcast genre: nonfiction (model).", r["id"]))
    con.commit()
    log.info("podcast genre: asked %d, dropped %d fiction podcasts", len(rows), dropped)


def fill_podcast_origins(con, done: set[str], force: bool, limit: int | None) -> None:
    todo = [r for r in db.rows(con) if is_podcast_row(r) and db.blank(r["source_title"])
            and (force or r["id"] not in done)]
    todo = todo[:limit] if limit else todo
    log.info("%d podcast rows to trace to a print origin", len(todo))
    found = 0
    for r, o in asyncio.run(_origins(todo)):
        if o is None:
            continue
        screen = "film" if r["medium"] == "film" else "series"
        if o.from_article and o.title:
            values = {"source_title": o.title, "source_author": o.author, "publication": o.publication,
                      "source_date": o.date, "source_chain": f"article → podcast → {screen}"}
            found += 1
        else:
            values = {"source_title": podcast_name(r), "source_type": "podcast_or_other",
                      "source_chain": f"podcast → {screen}"}
        db.fill(con, r["id"], values, force=force)
        con.execute("UPDATE adaptations SET seed_context = ? WHERE id = ?",
                    (f"{r['seed_context']} Print origin: {o.evidence}", r["id"]))
        con.execute("INSERT OR IGNORE INTO fill_log VALUES (?)", (r["id"],))
        con.commit()
    log.info("podcast rows: %d grew out of an article, %d did not", found, len(todo) - found)


def main() -> None:
    a = cli(__doc__)
    con = db.connect()
    con.execute("CREATE TABLE IF NOT EXISTS fill_log (id TEXT PRIMARY KEY)")
    done = {x[0] for x in con.execute("SELECT id FROM fill_log")}
    todo = [r for r in db.rows(con) if needs_fill(r) and (a.force or r["id"] not in done)]
    todo = todo[: a.limit] if a.limit else todo
    log.info("%d rows to fill", len(todo))
    added = 0
    for r, res in asyncio.run(_fill_all(todo)):
        if res is None:
            continue
        values = {"film_title": res.film_title, "film_year": res.film_year}
        # The schema requires a title, so the model sometimes writes a placeholder instead
        for src in res.sources:
            if src.title and _PLACEHOLDER.search(src.title):
                src.title = ""
        sources = list(res.sources)
        first = sources.pop(0) if sources else None
        if first is not None:
            values |= {"source_title": first.title, "source_author": first.author,
                       "publication": first.publication, "source_date": first.date}
        db.fill(con, r["id"], values, force=a.force)
        film = r["film_title"] or res.film_title
        have = {db.norm(x["source_title"]) for x in db.rows(con, "film_title = ?", [film])}
        for s in sources:  # a second credited work (often the book) becomes its own row
            if not s.title or db.norm(s.title) in have:
                continue
            new = {k: r[k] for k in ("seed", "film_year", "wikipedia_url", "wikidata_qid", "imdb_id", "seed_context")}
            new |= {"film_title": film, "film_year": r["film_year"] or res.film_year, "source_title": s.title,
                    "source_author": s.author, "publication": s.publication, "source_date": s.date,
                    "status": "produced"}
            nid = db.upsert(con, new)
            con.execute("INSERT OR IGNORE INTO fill_log VALUES (?)", (nid,))
            added += 1
        con.execute("INSERT OR IGNORE INTO fill_log VALUES (?)", (r["id"],))
        con.commit()
    log.info("filled %d rows, added %d rows for additional credited works", len(todo), added)
    drop_fiction_podcasts(con)
    fill_podcast_origins(con, done, a.force, a.limit)


if __name__ == "__main__":
    main()
