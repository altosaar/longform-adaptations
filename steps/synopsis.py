"""Synopsis: one sentence per title, taken from the first source that has one:

  1. imdb                 IMDb's plot line (served by OMDb, keyed by IMDb ID)
  2. rotten_tomatoes      the synopsis on the title's RT page (its first sentence)
  3. wikipedia_generated  Haiku (WriteSynopsis) from the Wikipedia lead
  4. article_generated    Haiku from the source article's archived excerpt
                          (raw/articles/<id>.txt, saved by `archive --excerpts`)

Pulled text is kept as written, trimmed to its first sentence; only tiers 3-4 are generated.
Fills blanks only (--force redoes every row). Run after enrich.
"""

from __future__ import annotations

import asyncio
import logging
import re

import db
import fetch
from steps import cli
from steps.archive import ARTICLES
from steps.enrich import omdb, page_title, rt_page

log = logging.getLogger("synopsis")
# A sentence ends at . ! ? before a space and a capital, but not after an initial or a
# common abbreviation ("J. R.", "U.S.", "Nov.", "Dr.").
_END = re.compile(r"[.!?](?=\s+[\"“A-Z])")
_ABBREV = {"mr", "ms", "mrs", "dr", "st", "jr", "sr", "vs", "no", "jan", "feb", "mar", "apr", "jun", "jul",
           "aug", "sep", "sept", "oct", "nov", "dec", "u.s", "u.k", "d.c", "l.a", "n.y", "mt", "ft", "gen", "lt"}


def first_sentence(text: str | None) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text or text.upper() == "N/A":
        return ""
    for m in _END.finditer(text):
        word = text[: m.start()].rsplit(" ", 1)[-1].lower().lstrip("(\"“")
        if len(word) == 1 or word in _ABBREV:
            continue
        return text[: m.end()]
    return text


def wikipedia_lead(url: str | None) -> str:
    if not url:
        return ""
    r = fetch.get("https://en.wikipedia.org/w/api.php", "wiki", params={
        "action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1", "redirects": "1",
        "titles": page_title(url), "format": "json", "formatversion": "2"})
    pages = r.json().get("query", {}).get("pages", []) if r.ok else []
    return (pages[0].get("extract") or "")[:3000] if pages else ""


def article_text(r: dict) -> str:
    path = ARTICLES / f"{r['id']}.txt"
    return path.read_text() if path.exists() else ""


def pulled(r: dict) -> tuple[str, str]:
    plot = first_sentence(omdb(r["imdb_id"]).get("Plot"))
    if plot:
        return plot, "imdb"
    if r["rt_url"]:
        rt = first_sentence(rt_page(r["rt_url"]).get("synopsis"))
        if rt:
            return rt, "rotten_tomatoes"
    return "", ""


async def _generate(jobs: list[tuple[dict, str, str]], workers: int = 8) -> list[tuple[dict, str, str]]:
    from baml_sdk import SynopsisBasis, WriteSynopsis_async

    sem = asyncio.Semaphore(workers)
    basis = {"wikipedia_generated": SynopsisBasis.Wikipedia, "article_generated": SynopsisBasis.Article}

    async def one(r: dict, source: str, text: str):
        async with sem:
            try:
                out = await WriteSynopsis_async(r["film_title"] or "(untitled)", r["film_year"],
                                                (r["medium"] or "film").replace("_", " "), basis[source], text)
                return r, first_sentence((out or "").strip().strip('"')), source
            except Exception as exc:
                log.warning("synopsis failed for %s: %s", r["film_title"], str(exc)[:200])
                return r, "", source

    return await asyncio.gather(*(one(*j) for j in jobs))


def main() -> None:
    a = cli(__doc__)
    con = db.connect()
    rows = db.rows(con, "1" if a.force else "synopsis IS NULL", limit=a.limit)
    counts: dict[str, int] = {}
    jobs: list[dict] = []
    for r in rows:
        text, source = pulled(r)
        if text:
            db.fill(con, r["id"], {"synopsis": text, "synopsis_source": source}, force=a.force)
            counts[source] = counts.get(source, 0) + 1
            continue
        jobs.append(r)
    con.commit()
    # Generated tiers: the Wikipedia lead, then (when the model finds no plot there, or there
    # is no page) the article excerpt
    for source, text_for in (("wikipedia_generated", lambda r: wikipedia_lead(r["wikipedia_url"])),
                             ("article_generated", article_text)):
        batch = [(r, source, t) for r in jobs if (t := text_for(r))]
        done = set()
        for r, text, src in asyncio.run(_generate(batch)):
            if text:
                db.fill(con, r["id"], {"synopsis": text, "synopsis_source": src}, force=a.force)
                counts[src] = counts.get(src, 0) + 1
                done.add(r["id"])
        jobs = [r for r in jobs if r["id"] not in done]
        con.commit()
    counts["none"] = len(jobs)
    log.info("synopses: %s", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
