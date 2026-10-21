"""The Electric Typewriter: great articles that became films. Grouped by genre (<h3>); each
entry is an <h4> link reading 'Title by Author (Film)', followed by a one-line blurb."""

from __future__ import annotations

import re

import fetch
from seeds import clean, link_fields, parse_html

NAME = "tetw"
URL = "https://tetw.org/Great_Articles_that_Became_Films"
_ENTRY = re.compile(r"^(?P<title>.+?)\s+by\s+(?P<author>.+?)(?:\s*\((?P<film>[^()]+)\))?$")


def collect() -> list[dict]:
    return parse(fetch.get(URL, NAME).text)


def parse(html: str) -> list[dict]:
    tree = parse_html(html)
    out = []
    genre = ""
    for node in tree.css("h3, h4"):
        if node.tag == "h3":
            genre = clean(node.text())
            continue
        a = node.css_first("a")
        href = (a.attributes.get("href") or "") if a else ""
        if a is None or "tetw.org" in href:  # '75 more articles about sport'
            continue
        m = _ENTRY.match(clean(a.text()))
        if not m:
            continue
        blurb = node.next
        while blurb is not None and blurb.tag not in ("-text", "h4", "hr", "h3"):
            blurb = blurb.next
        context = clean(blurb.text()) if blurb is not None and blurb.tag == "-text" else ""
        out.append({
            "film_title": clean(m.group("film") or ""),
            "source_title": clean(m.group("title")),
            "source_author": clean(m.group("author")),
            "seed_context": f"[{genre}] {context}".strip(),
            **link_fields(href.replace("%0A", "")),
        })
    return out
