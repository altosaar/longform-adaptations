"""Seed parsers. Each module exposes `NAME` and `collect() -> list[dict]` yielding raw rows
with whatever the list states; anything it can't parse is left blank for later steps.
"""

from __future__ import annotations

import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

ORDER = [
    "wikipedia_list",
    "wikipedia_category",
    "longform",
    "longreads",
    "tetw",
    "thr",
    "imdb_list",
    "texas_monthly_hollywood",
    "manual",
]
# TV seeds run on their own, less often: `just tv` (same table, same pipeline)
TV_ORDER = [
    "manual_tv",
    "wikipedia_podcast_adaptations",
]

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def clean(s: str | None) -> str:
    s = (s or "").replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", s).strip(" \t\n,;:–—-")


def iso_date(text: str | None) -> str:
    """'January 23, 1995' → 1995-01-23, 'Apr 2007' → 2007-04, '1972' → 1972, else ''."""
    t = clean(text)
    if not t:
        return ""
    m = re.search(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b", t)
    if m:
        return m.group(0)
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b[^,\d]*?,?\s*(\d{4})", t)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}"
    m = re.search(r"\b([A-Za-z]{3,9})\.?,?\s+(\d{4})\b", t)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1)[:3].lower()]:02d}"
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", t)
    return m.group(1) if m else ""


def year_of(text: str | None) -> int | None:
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", text or "")
    return int(m.group(1)) if m else None


_WAYBACK = re.compile(r"^https?://web(?:-wp)?\.archive\.org/web/(\d{8,14})[a-z_]*/(.+)$")


def split_wayback(url: str) -> tuple[str, str]:
    """A seed that links a Wayback snapshot gives us both URLs: (original, snapshot)."""
    m = _WAYBACK.match(url or "")
    if not m:
        return url, ""
    orig = m.group(2)
    if not orig.startswith("http"):
        orig = "http://" + orig
    return orig, url


def link_fields(url: str | None) -> dict:
    """source_url (+ archive_url when the seed already links a Wayback copy)."""
    if not url:
        return {}
    orig, snap = split_wayback(url)
    out = {"source_url": orig, "source_url_origin": "seed"}
    if snap:
        out |= {"archive_url": snap, "archive_status": "wayback"}
    return out


_QUOTED = re.compile(r"[\"“”„«]\s*(.+?)\s*[\"“”»]")


def parse_citation(text: str) -> dict:
    """'"Orchid Fever", The New Yorker, January 23, 1995' → title, publication, date.

    The quoted span is the headline; the next comma-separated piece is the outlet and
    whatever follows is the date. Unquoted citations ("Series of articles in X") yield
    only what is unambiguous.
    """
    t = clean(text)
    out: dict = {}
    m = _QUOTED.search(t)
    if m:
        out["source_title"] = clean(m.group(1)).strip("'\"“”")
        rest = t[m.end():]
    else:
        series = re.match(r"(?:a\s+)?series of (?:articles|reports|stories)\s+(?:in|for)\s+(.+)", t, re.I)
        if series:
            out["publication"] = clean(series.group(1).split(",")[0])
            return out
        head, _, rest = t.partition(",")
        out["source_title"] = clean(head)
    pieces = [clean(p) for p in rest.split(",") if clean(p).strip("'")]
    if pieces:
        out["publication"] = clean(pieces[0].strip("'"))
        out["source_date"] = iso_date(", ".join(pieces[1:]))
    return out


def parse_html(text: str) -> LexborHTMLParser:
    return LexborHTMLParser(text)


def now_year() -> int:
    return datetime.now().year
