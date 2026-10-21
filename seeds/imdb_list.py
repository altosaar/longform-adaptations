"""IMDb user list ls540838468 and the 'Favorite movie based on a magazine article' poll:
film + annotation '"Article", Publication, Date – Author'.

IMDb answers automated fetches (plain HTTP, TLS impersonation, and headless Chromium alike)
with a human-verification page, so this parser reads copies saved by hand from a browser
(File → Save Page As…) into raw/imdb/*.html. Without one the seed yields nothing.
"""

from __future__ import annotations

import html
import json
import logging
import re

from fetch import RAW
from seeds import clean, parse_citation, parse_html, year_of

NAME = "imdb_list"
SAVED_DIR = RAW / "imdb"
log = logging.getLogger(NAME)


def collect() -> list[dict]:
    pages = sorted(SAVED_DIR.glob("*.html"))
    if not pages:
        log.warning("imdb_list: no saved page in %s (IMDb blocks bots; save it from a browser)", SAVED_DIR)
        return []
    rows: dict[str, dict] = {}
    for page in pages:
        for r in parse(page.read_text(errors="replace")):
            rows.setdefault(r["imdb_id"], r)
    return list(rows.values())


def _items(obj):
    """Entries in IMDb's __NEXT_DATA__: a title node next to its annotation. Lists keep the
    title under `listItem` (annotation in node.description); polls under `item`."""
    if isinstance(obj, dict):
        title = obj.get("listItem") or obj.get("item") or obj.get("title")
        if isinstance(title, dict) and str(title.get("id", "")).startswith("tt"):
            yield obj, title
        for v in obj.values():
            yield from _items(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _items(v)


def _text(node) -> str:
    if isinstance(node, dict):
        for k in ("plainText", "plaidHtml", "originalText", "text", "value"):
            if k in node:
                return _text(node[k])
    return clean(html.unescape(re.sub(r"<[^>]+>", "", node))) if isinstance(node, str) else ""


def parse(page: str) -> list[dict]:
    tree = parse_html(page)
    out, seen = [], set()
    script = tree.css_first("script#__NEXT_DATA__")
    if script is not None:
        for item, title in _items(json.loads(script.text())):
            tt = title["id"]
            if tt in seen:
                continue
            seen.add(tt)
            node = item.get("node") or {}
            note = _text(node.get("description") or item.get("description") or {})
            out.append(_row(tt, _text(title.get("titleText", {})),
                            (title.get("releaseYear") or {}).get("year"), note))
        if out:
            return out
    # Fallback: the rendered list (older or simplified markup)
    for li in tree.css("li.ipc-metadata-list-summary-item, .lister-item"):
        a = li.css_first('a[href*="/title/tt"]')
        if a is None:
            continue
        tt = re.search(r"tt\d+", a.attributes.get("href") or "").group(0)
        if tt in seen:
            continue
        seen.add(tt)
        name = re.sub(r"^\d+\.\s*", "", clean(a.text()))
        note = li.css_first(".list-description, .ipc-html-content-inner-div, p.text-small")
        out.append(_row(tt, name, year_of(li.text()), clean(note.text()) if note else ""))
    return out


def _row(tt: str, title: str, year, note: str) -> dict:
    row = {"film_title": title, "film_year": year, "imdb_id": tt, "seed_context": note}
    parts = re.split(r"\s+[–—-]\s+", note)
    if len(parts) >= 2:
        row |= parse_citation(" – ".join(parts[:-1])) | {"source_author": clean(parts[-1])}
    return row
