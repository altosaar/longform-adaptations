"""Wikipedia: List of podcast adaptations (TV seed). One table: Podcast | Format | Title of
adapted work | Year. Format cells span several rows, so rowspans are expanded first.

Rows whose format is television or film are kept. The source is left blank on purpose:
the fill step asks Haiku (with web search) whether the podcast grew out of a published
article. If so, the article becomes the source (chain 'article → podcast → series');
if not, the podcast does (source_type podcast_or_other, the negative/ambiguous case).
"""

from __future__ import annotations

import re

import fetch
from seeds import clean, parse_html, year_of

NAME = "wikipedia_podcast_adaptations"
PAGE = "List_of_podcast_adaptations"
API = "https://en.wikipedia.org/w/api.php"
CONTEXT_PREFIX = "Source is a podcast: "
GENRE_UNKNOWN = "Podcast genre: unknown."
EXCLUDED: list[tuple[dict, str]] = []  # (row, reason): rows collect must remove, see steps/collect.py
WD_API = "https://www.wikidata.org/w/api.php"
# P136 genre labels that settle the question either way (Addendum 2 #7)
_FICTION = re.compile(r"fiction|audio drama|radio drama|comedy|improv|horror|thriller|science fiction|fantasy", re.I)
_NONFICTION = re.compile(r"true crime|documentary|journalism|news|history|interview|investigative|nonfiction|"
                         r"non-fiction|business|politic|narrative", re.I)


def genres(podcast_pages: list[str]) -> dict[str, list[str]]:
    """Wikipedia page title → the podcast's Wikidata genre labels (P136)."""
    out: dict[str, list[str]] = {}
    titles = [t for t in podcast_pages if t]
    qid_of: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        d = fetch.get(API, NAME, params={"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                                         "redirects": "1", "titles": "|".join(titles[i : i + 50]),
                                         "format": "json", "formatversion": "2"}).json()
        q = d.get("query", {})
        back = {x["to"]: x["from"] for x in q.get("redirects", []) + q.get("normalized", [])}
        for p in q.get("pages", []):
            if p.get("pageprops", {}).get("wikibase_item"):
                qid_of[back.get(p["title"], p["title"])] = p["pageprops"]["wikibase_item"]
    ents: dict[str, dict] = {}
    ids = sorted(set(qid_of.values()))
    for i in range(0, len(ids), 50):
        ents |= fetch.get(WD_API, NAME, params={"action": "wbgetentities", "ids": "|".join(ids[i : i + 50]),
                                                "props": "claims", "format": "json"}).json().get("entities", {})
    genre_ids = {c["mainsnak"]["datavalue"]["value"]["id"] for e in ents.values()
                 for c in e.get("claims", {}).get("P136", []) if c["mainsnak"].get("snaktype") == "value"}
    labels: dict[str, str] = {}
    gids = sorted(genre_ids)
    for i in range(0, len(gids), 50):
        d = fetch.get(WD_API, NAME, params={"action": "wbgetentities", "ids": "|".join(gids[i : i + 50]),
                                            "props": "labels", "languages": "en", "format": "json"}).json()
        labels |= {q: e.get("labels", {}).get("en", {}).get("value", "") for q, e in d.get("entities", {}).items()}
    for title, qid in qid_of.items():
        claims = ents.get(qid, {}).get("claims", {}).get("P136", [])
        out[title] = [labels.get(c["mainsnak"]["datavalue"]["value"]["id"], "") for c in claims
                      if c["mainsnak"].get("snaktype") == "value"]
    return out


def nonfiction(labels: list[str]) -> bool | None:
    """True/False when the genres settle it, None when they don't (the fill step asks Haiku)."""
    if any(_NONFICTION.search(g) for g in labels) and not any(_FICTION.search(g) for g in labels):
        return True
    if any(_FICTION.search(g) for g in labels):
        return False
    return None


def collect() -> list[dict]:
    r = fetch.get(API, NAME, params={
        "action": "parse", "page": PAGE, "prop": "text", "format": "json", "formatversion": "2"})
    return parse(r.json()["parse"]["text"])


def _grid(table) -> list[list]:
    """The table as a rectangular grid of <td>/<th> nodes, rowspans repeated downward."""
    grid: list[list] = []
    carry: dict[int, tuple[object, int]] = {}  # column → (cell, rows still to fill)
    for tr in table.css("tr"):
        cells = tr.css("td, th")
        row: list = []
        col = 0
        while cells or col in carry:
            if col in carry:
                cell, left = carry.pop(col)
                row.append(cell)
                if left > 1:
                    carry[col] = (cell, left - 1)
            else:
                cell = cells.pop(0)
                span = int(re.sub(r"\D", "", cell.attributes.get("rowspan") or "1") or 1)
                row.append(cell)
                if span > 1:
                    carry[col] = (cell, span - 1)
            col += 1
        grid.append(row)
    return grid


def parse(html: str) -> list[dict]:
    rows = _parse_table(html)
    by_page = genres([r.pop("_podcast_page") for r in rows if r.get("_podcast_page")] or [])
    EXCLUDED.clear()
    out = []
    for r in rows:
        page = r.pop("_page_title", "")
        labels = by_page.get(page, [])
        verdict = nonfiction(labels)
        if verdict is False:
            EXCLUDED.append((r, "fiction-podcast"))
            continue
        genre_note = f"Podcast genre: {', '.join(labels)}." if labels else ""
        r["seed_context"] += " " + (genre_note if verdict else GENRE_UNKNOWN)
        out.append(r)
    return out


def _parse_table(html: str) -> list[dict]:
    tree = parse_html(html)
    for junk in tree.css("sup, style"):
        junk.decompose()
    out = []
    for table in tree.css("table.wikitable"):
        grid = _grid(table)
        header = [clean(c.text()).lower() for c in grid[0]]
        col = {name: i for i, name in enumerate(header)}
        try:
            ip, ifmt = col["podcast"], col["format"]
            it = next(i for h, i in col.items() if h.startswith("title"))
            iy = next(i for h, i in col.items() if h.startswith("year"))
        except (KeyError, StopIteration):
            continue
        for row in grid[1:]:
            if len(row) <= max(ip, ifmt, it, iy):
                continue
            fmt = clean(row[ifmt].text()).lower()
            if not re.search(r"tele|tv|film|series", fmt):
                continue
            podcast = clean(row[ip].text())
            plink = row[ip].css_first('a[href^="/wiki/"]')
            ptitle = (plink.attributes.get("title") or "") if plink else ""
            title_cell = row[it]
            link = title_cell.css_first('a[href^="/wiki/"]')
            href = (link.attributes.get("href") or "") if link else ""
            film = clean(title_cell.text())
            page = "https://en.wikipedia.org" + href.split("#")[0] if href and "redlink" not in href else ""
            out.append({
                "film_title": film,
                "film_year": year_of(row[iy].text()),
                "medium": "film" if "film" in fmt and "tele" not in fmt else None,
                "wikipedia_url": page,
                "_podcast_page": ptitle,
                "_page_title": ptitle,
                "seed_context": f"{CONTEXT_PREFIX}{podcast}. Adapted as: {film} ({fmt}). "
                                + ("Format: a film." if fmt == "film" else "Format: a television series, not a film."),
            })
    return out
