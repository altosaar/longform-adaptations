"""Texas Monthly, "How Texas Monthly Went Hollywood" (June 2023): the magazine's own account
of its adaptations. Prose, not a list, so one ExtractProseAdaptations call reads the
adaptations out of it (text with the page's links kept). Titles the page calls in development
are kept too (Land Man has since premiered as Landman); enrich's Wikidata link confirms which
were made, and the page's own status wording stays in seed_context."""

from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace

import fetch
from seeds import clean, link_fields, parse_html

NAME = "texas_monthly_hollywood"
URL = "https://www.texasmonthly.com/the-stand-up-desk/how-texas-monthly-went-hollywood/"
_MEDIA = {"series": "scripted_series", "docuseries": "docuseries", "film": "film", "tv movie": "tv_movie"}


def page_text(html: str) -> str:
    """Article text with links inlined as [text](url), so the model can copy them exactly."""
    tree = parse_html(html)
    for junk in tree.css("script, style, nav, header, footer, aside, form"):
        junk.decompose()
    body = tree.css_first("article") or tree.body
    for a in body.css("a[href]"):
        a.replace_with(f"[{clean(a.text())}]({a.attributes.get('href')})")
    return re.sub(r"\s*\n\s*", "\n", body.text(separator=" ")).strip()


def headline(url: str) -> str:
    """The linked article's own headline (og:title, else <h1>): a lookup, not a guess."""
    r = fetch.get(url, NAME)
    if not r.ok:
        return ""
    tree = parse_html(r.text)
    og = tree.css_first('meta[property="og:title"]')
    h1 = tree.css_first("h1")
    title = (og.attributes.get("content") if og else "") or (h1.text() if h1 else "")
    return clean(re.sub(r"\s*[|–-]\s*Texas Monthly\s*$", "", title or ""))


def extract(text: str, passes: int = 3) -> list[dict]:
    """Union of a few extraction passes (one low-effort pass misses titles), cached next to
    the raw page so a re-collect returns the same rows until the page itself changes."""
    from baml_sdk import ExtractProseAdaptations

    cache = fetch.RAW / NAME / f"extraction-{hashlib.sha1(text.encode()).hexdigest()[:12]}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    found: dict[str, dict] = {}
    for _ in range(passes):
        for x in ExtractProseAdaptations(URL, text[:20000]):
            found.setdefault(re.sub(r"[^a-z0-9]", "", x.screen_title.lower()), x.model_dump())
    cache.write_text(json.dumps(list(found.values()), indent=1))
    return list(found.values())


def collect() -> list[dict]:
    text = page_text(fetch.get(URL, NAME).text)
    rows = []
    for x in map(SimpleNamespace, extract(text)):
        # only links the page itself carries, and never the page's own address
        own = x.source_url and x.source_url.rstrip("/") == URL.rstrip("/")
        url = x.source_url if x.source_url and x.source_url in text and not own else None
        row = {
            "film_title": clean(x.screen_title),
            "medium": _MEDIA.get((x.medium or "").lower()),
            "source_title": clean(x.source_title or ""),
            "source_author": clean(x.source_author or ""),
            "publication": clean(x.publication or ""),
            "source_date": str(x.source_year) if x.source_year else "",
            "seed_context": f"Texas Monthly ({URL}), June 2023: {x.relation}; status then: {x.status}. "
                            + (f"Article: {x.source_title}. " if x.source_title else ""),
        }
        if url and not row["source_title"] and "/podcasts/" not in url:
            row["source_title"] = headline(url)
        rows.append(row | (link_fields(url) if url else {}))
    return rows
