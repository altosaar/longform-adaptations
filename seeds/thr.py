"""The Hollywood Reporter: movies based on magazine articles (a gallery, one slide per film).
The slides are embedded as JSON in the page; each has 'Film (Year)' as its title and a prose
caption naming the article. Author and outlet are left in `seed_context` for the fill step."""

from __future__ import annotations

import json
import re

import fetch
from db import norm
from seeds import clean, link_fields, parse_html

NAME = "thr"
_QUOTE = re.compile(r"[“\"]([^”\"]{3,})[”\"]")
URL = "https://www.hollywoodreporter.com/gallery/movies-are-based-magazine-articles-786327/"


def collect() -> list[dict]:
    r = fetch.get(URL, NAME)
    if not r.ok or "pmc-gallery" not in r.text:  # blocked live: try the newest Wayback copy
        r = fetch.get(f"https://web.archive.org/web/2024id_/{URL}", NAME)
    return parse(r.text)


def _slides(obj):
    if isinstance(obj, dict):
        if "caption" in obj and "title" in obj and "slug" in obj:
            yield obj
        for v in obj.values():
            yield from _slides(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _slides(v)


def parse(html: str) -> list[dict]:
    script = parse_html(html).css_first("script#pmc-gallery-js-extra")
    if script is None:
        return []
    text = script.text()
    data = json.loads(text[text.index("{") : text.rindex("}") + 1])
    out, seen = [], set()
    for s in _slides(data):
        if s["slug"] in seen:
            continue
        seen.add(s["slug"])
        m = re.match(r"^(.*?)\s*\((\d{4})(?:/\d{4})?\)\s*$", clean(s["title"]))
        film, year = (m.group(1), int(m.group(2))) if m else (clean(s["title"]), None)
        cap = parse_html(s.get("caption") or "")
        context = clean(cap.text())
        row = {"film_title": film, "film_year": year, "seed_context": context}
        # The headline is the quoted span; the link that carries it is the article URL.
        # (A caption's first link is sometimes the outlet's homepage, not the piece.)
        quoted = _QUOTE.search(context)
        if quoted:
            row["source_title"] = clean(quoted.group(1)).rstrip(",.")
            key = norm(row["source_title"])
            for a in cap.css("a[href]"):
                if norm(a.text()) and norm(a.text()) in key:
                    row |= link_fields(a.attributes.get("href", "").strip())
                    break
        out.append(row)
    return out
