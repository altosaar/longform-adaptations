"""Wikipedia: List of films based on magazine articles.

Each bullet reads: Film – "Headline", Publication, Date – Author. The rendered HTML (via
the parse API) gives the film's page link and the article's external link directly.
"""

from __future__ import annotations

import re

import fetch
from seeds import clean, link_fields, parse_citation, parse_html, split_wayback

NAME = "wikipedia_list"
PAGE = "List_of_films_based_on_magazine_articles"
API = "https://en.wikipedia.org/w/api.php"
_ARCHIVED = re.compile(r"\s*Archived \d{4}-\d{2}-\d{2} at the Wayback Machine")


def collect() -> list[dict]:
    r = fetch.get(API, NAME, params={
        "action": "parse", "page": PAGE, "prop": "text", "format": "json", "formatversion": "2"})
    html = r.json()["parse"]["text"]
    return parse(html)


def parse(html: str) -> list[dict]:
    tree = parse_html(html)
    for junk in tree.css("sup, .mw-editsection, style"):
        junk.decompose()
    out = []
    for li in tree.css(".mw-parser-output > ul > li"):
        film_link = li.css_first('a[href^="/wiki/"]')
        text = clean(_ARCHIVED.sub("", li.text()))
        parts = re.split(r"\s*–\s*", text)
        if film_link is None or len(parts) < 3:  # "See also" bullets and the like
            continue
        film_text = clean(parts[0])
        href = film_link.attributes.get("href") or ""
        row: dict = {
            "film_title": film_text.strip("'\""),
            "wikipedia_url": "https://en.wikipedia.org" + href.split("#")[0] if href.startswith("/wiki/") else "",
            "source_author": clean(parts[-1]),
            "seed_context": text,
        }
        row |= parse_citation(" – ".join(parts[1:-1]))
        links = [a.attributes.get("href") or "" for a in li.css("a.external")]
        if links:
            row |= link_fields(links[0])
        snapshot = next((u for u in links[1:] if split_wayback(u)[1]), None)
        if snapshot and not row.get("archive_url"):  # a {{Webarchive}} copy after the live link
            row |= {"archive_url": snapshot, "archive_status": "wayback"}
        out.append(row)
    return out
