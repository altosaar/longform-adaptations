"""Discover: find the article URL for rows no seed linked. One BAML FindArticleUrl call per
row: Haiku first checks the links cited by the film's Wikipedia article, then falls back to
Anthropic's web search tool (one search per row). A hit counts only on the outlet's own
domain (data/publications.csv) or an archive. Fills source_url (source_url_origin=search)
and, when blank, the headline, outlet and date it found; a Wayback link also fills archive_url.

Asks only journalism rows (by origin_type) with a headline and an outlet. URLs found before
the domain rule existed are re-checked first; failures are cleared and asked again.
Each row is asked once (discover_log); --force asks again. Run after classify (it needs
source_type), then run archive to snapshot what it found.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import db
import fetch
import publications
from seeds import link_fields
from steps import cli
from steps.enrich import page_title

log = logging.getLogger("discover")
# Never the article itself: reference sites, stores, and the film's own pages
_NOT_ARTICLES = ("wikipedia.org", "wikidata.org", "imdb.com", "amazon.", "goodreads.com", "rottentomatoes.com",
                 "letterboxd.com", "themoviedb.org", "google.com/search", "youtube.com", "netflix.com")


def wanted(r: dict) -> bool:
    """Addendum 2 #8: journalism rows with an outlet and a headline but no URL."""
    return (db.blank(r["source_url"]) and r["origin_type"] in db.JOURNALISM
            and not db.blank(r["source_title"]) and not db.blank(r["publication"]))


def usable(url: str | None) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    host = urlsplit(url).hostname or ""
    return bool(host) and not any(bad in url for bad in _NOT_ARTICLES)


def on_outlet(url: str | None, r: dict) -> bool:
    """Only a hit on the outlet's own domain or an archive is accepted (Addendum 2 #8)."""
    pub = publications.lookup(r["publication"], r["publication_normalized"])
    return usable(url) and publications.own_or_archive_domain(url, pub)


def recheck(con) -> int:
    """Clear search-found URLs that fail the domain rule (found before it existed) and re-ask."""
    cleared = 0
    for r in db.rows(con, "source_url_origin = 'search'"):
        if not on_outlet(r["source_url"], r):
            con.execute("""UPDATE adaptations SET source_url = NULL, source_url_origin = NULL, archive_url = NULL,
                           archive_status = NULL WHERE id = ?""", (r["id"],))
            con.execute("DELETE FROM discover_log WHERE id = ?", (r["id"],))
            log.info("cleared %s: %s is not on %s's domain", r["film_title"], r["source_url"], r["publication"])
            cleared += 1
    con.commit()
    return cleared


def cited_links(wikipedia_url: str | None, limit: int = 80) -> list[str]:
    """External links on the film's Wikipedia page: its references often cite the article."""
    if not wikipedia_url:
        return []
    r = fetch.get("https://en.wikipedia.org/w/api.php", "wiki", params={
        "action": "parse", "page": page_title(wikipedia_url), "prop": "externallinks", "redirects": "1",
        "format": "json", "formatversion": "2"})
    links = r.json().get("parse", {}).get("externallinks", []) if r.ok else []
    keep = [u for u in links if usable(u) and "archive.org/details" not in u]
    return list(dict.fromkeys(keep))[:limit]


async def _discover_all(rows: list[dict], workers: int) -> list[tuple[dict, object]]:
    from baml_sdk import FindArticleUrl_async

    sem = asyncio.Semaphore(workers)

    async def one(r: dict):
        async with sem:
            try:
                return r, await FindArticleUrl_async(
                    r["film_title"] or "(unknown)", r["film_year"], r["source_title"], r["source_author"],
                    r["publication"], r["source_date"], cited_links(r["wikipedia_url"]) or ["(none)"])
            except Exception as exc:
                log.warning("discover failed for %s: %s", r["film_title"], str(exc)[:200])
                return r, None

    return await asyncio.gather(*(one(r) for r in rows))


def main() -> None:
    a = cli(__doc__, workers={"type": int, "default": 4})
    con = db.connect()
    con.execute("CREATE TABLE IF NOT EXISTS discover_log (id TEXT PRIMARY KEY, evidence TEXT)")
    recheck(con)
    done = {x[0] for x in con.execute("SELECT id FROM discover_log")}
    todo = [r for r in db.rows(con) if wanted(r) and (a.force or r["id"] not in done)]
    todo = todo[: a.limit] if a.limit else todo
    log.info("%d rows to search for (1 web search each)", len(todo))
    found = 0
    for r, link in asyncio.run(_discover_all(todo, a.workers)):
        if link is None:
            continue
        if link.url and not on_outlet(link.url, r):
            log.info("rejected %s for %s: not on the outlet's domain", link.url, r["film_title"])
        if on_outlet(link.url, r):
            values = link_fields(link.url) | {"source_url_origin": "search"}
            values |= {"source_title": link.headline, "publication": link.publication, "source_date": link.date}
            db.fill(con, r["id"], values)
            found += 1
        con.execute("INSERT OR REPLACE INTO discover_log VALUES (?, ?)", (r["id"], link.evidence))
        con.commit()
    log.info("found URLs for %d of %d rows", found, len(todo))


if __name__ == "__main__":
    main()
