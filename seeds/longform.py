"""Longform: The Longform Guide to Adaptations. One <article> per piece, with a
'Film: <i>Title</i>' line (missing on a few; the fill step reads those gaps)."""

from __future__ import annotations

import re

import fetch
from seeds import clean, iso_date, link_fields, parse_html

NAME = "longform"
URL = "https://longform.org/posts/the-longform-guide-to-adaptations"


def collect() -> list[dict]:
    return parse(fetch.get(URL, NAME).text)


def parse(html: str) -> list[dict]:
    out = []
    for art in parse_html(html).css("article.post__post"):
        link = art.css_first("a.post__link")
        title = art.css_first(".post__title")
        body = art.css_first(".post__body")
        if link is None or title is None:
            continue
        film = ""
        blurb = []
        for p in body.css("p") if body else []:
            t = clean(p.text())
            if t.lower().startswith("film:"):
                film = clean(t[5:])
            elif t:
                blurb.append(t)
        authors = [clean(a.text()) for a in art.css(".post__author")]
        pub = art.css_first(".post__publication")
        date = art.css_first(".post__date")
        row = {
            "film_title": film,
            "source_title": clean(title.text()),
            "source_author": " & ".join(a for a in authors if a),
            "publication": clean(pub.text()) if pub else "",
            "source_date": iso_date(date.text()) if date else "",
            "seed_context": " ".join(blurb),
        }
        href = link.attributes.get("href") or ""
        row |= link_fields(re.sub(r"\?currentPage=all$", "", href))
        out.append(row)
    return out
