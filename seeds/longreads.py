"""Longreads: 25 movies based on magazine stories. Each entry is an <h2> linking the film's
IMDb page, 'Film (Year)', followed by 'Based on <a>Article</a> by Author (Publication, Year)'."""

from __future__ import annotations

import re

import fetch
from seeds import clean, link_fields, parse_html, year_of

NAME = "longreads"
URL = "https://longreads.com/2020/02/28/movies-based-on-articles/"
_SOCIAL = ("twitter.com", "x.com", "longreads.com", "instagram.com")
_CREDIT = re.compile(r".*\bby\s+(?:the\s+)?(.+?)\s*\(([^()]*?),?\s*((?:19|20)\d{2})\)", re.I)


def collect() -> list[dict]:
    return parse(fetch.get(URL, NAME).text)


def parse(html: str) -> list[dict]:
    content = parse_html(html).css_first(".entry-content")
    out: list[dict] = []
    current: dict | None = None
    for node in content.css("h2, p") if content else []:
        if node.tag == "h2":
            a = node.css_first('a[href*="imdb.com/title/"]')
            if a is None:
                current = None
                continue
            m = re.search(r"tt\d+", a.attributes.get("href") or "")
            current = {
                "film_title": clean(a.text()),
                "film_year": year_of(node.text()),
                "imdb_id": m.group(0) if m else "",
            }
            continue
        text = clean(node.text())
        if current is None or not text.lower().startswith("based on"):
            continue
        links = [a for a in node.css("a") if not any(s in (a.attributes.get("href") or "") for s in _SOCIAL)]
        if "starting with" in text and len(links) > 1:  # Spotlight: 'a series of reports, starting with X'
            links = links[1:]
        row = dict(current, seed_context=text)
        if links:
            row["source_title"] = clean(links[0].text())
            row |= link_fields(links[0].attributes.get("href"))
        credit = _CREDIT.search(text)
        if credit:
            row |= {"source_author": clean(credit.group(1)), "publication": clean(credit.group(2)),
                    "source_date": credit.group(3)}
        if not row.get("source_title"):
            m = re.match(r"based on (?:the )?(.+?)\s+(?:series\s+)?by\b", text, re.I)
            row["source_title"] = clean(m.group(1)) if m else ""
        out.append(row)
        current = None
    return out
