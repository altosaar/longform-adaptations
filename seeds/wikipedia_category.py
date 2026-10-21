"""Wikipedia: Category:Films based on newspaper and magazine articles.

The category gives film titles only. For each page we keep the infobox `based_on` field and
the article lead as `seed_context`; the fill step has Haiku read the source out of them.
Pages are queried 50 (wikitext) / 20 (extracts) at a time rather than one call per film.
"""

from __future__ import annotations

import re

import fetch
from seeds import clean

NAME = "wikipedia_category"
CATEGORY = "Category:Films based on newspaper and magazine articles"
API = "https://en.wikipedia.org/w/api.php"
# Indian cinema (Addendum 2 #7): its pages are skipped unless the infobox names a source
_INDIAN = re.compile(r"\[\[Category:[^\]]*\b(?:Indian|Hindi|Tamil|Telugu|Malayalam|Kannada|Bengali|Marathi|"
                     r"Punjabi|Gujarati|Odia)\b[^\]]*\]\]|\|\s*country\s*=\s*\[*India\b", re.I)
EXCLUDED: list[tuple[dict, str]] = []  # (row, reason): rows collect must remove, see steps/collect.py
_TITLE_SUFFIX = re.compile(r"\s*\((?:\d{4} )?(?:[\w .'-]+ )?(?:film|movie|TV series|miniseries)\)$", re.I)


def _api(**params) -> dict:
    return fetch.get(API, NAME, params={"format": "json", "formatversion": "2", **params}).json()


def members() -> list[str]:
    titles, cont = [], {}
    while True:
        d = _api(action="query", list="categorymembers", cmtitle=CATEGORY, cmlimit="500", cmnamespace="0", **cont)
        titles += [m["title"] for m in d["query"]["categorymembers"]]
        if "continue" not in d:
            return [t for t in titles if not t.startswith("List of")]
        cont = {"cmcontinue": d["continue"]["cmcontinue"]}


def based_on(wikitext: str) -> str:
    """The infobox's `based_on` value as plain text ('"Racer X" by Kenneth Li')."""
    m = re.search(r"\|\s*based[_ ]on\s*=(.*?)(?=\n\s*\||\n\}\})", wikitext, re.S | re.I)
    if not m:
        return ""
    v = m.group(1)
    v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    v = re.sub(r"\{\{\s*based on\s*\|(.*?)\}\}", lambda x: _based_on_template(x.group(1)), v, flags=re.S | re.I)
    v = re.sub(r"\{\{\s*(?:ubl|unbulleted list|plainlist|flatlist)\s*\|", "", v, flags=re.I)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", v)
    v = re.sub(r"<br\s*/?>", "; ", v)
    v = re.sub(r"\{\{[^{}]*\}\}|''+|<[^>]+>", "", v)
    return clean(v.replace("}}", "").replace("|", "; "))


def _based_on_template(args: str) -> str:
    parts = [p.strip() for p in args.split("|") if p.strip() and "=" not in p]
    return f"{parts[0]} by {', '.join(parts[1:])}" if len(parts) > 1 else (parts[0] if parts else "")


def collect() -> list[dict]:
    titles = members()
    pages: dict[str, dict] = {t: {} for t in titles}
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        d = _api(action="query", prop="revisions|pageprops", rvprop="content", rvslots="main",
                 ppprop="wikibase_item", titles="|".join(batch), redirects="1")
        for p in d["query"]["pages"]:
            text = (p.get("revisions") or [{}])[0].get("slots", {}).get("main", {}).get("content", "")
            pages.setdefault(p["title"], {}).update(
                based_on=based_on(text), qid=p.get("pageprops", {}).get("wikibase_item", ""),
                indian=bool(_INDIAN.search(text)))
    for i in range(0, len(titles), 20):
        d = _api(action="query", prop="extracts", exintro="1", explaintext="1", exlimit="20",
                 titles="|".join(titles[i : i + 20]), redirects="1")
        for p in d["query"]["pages"]:
            pages.setdefault(p["title"], {})["lead"] = clean(p.get("extract", ""))[:1500]

    out = []
    EXCLUDED.clear()
    for title, p in pages.items():
        if not p:
            continue
        if p.get("indian") and not p.get("based_on"):
            EXCLUDED.append(({"film_title": _TITLE_SUFFIX.sub("", title)}, "indian-cinema-no-based-on"))
            continue
        context = f"Wikipedia infobox based_on: {p.get('based_on') or '(none)'}\nLead: {p.get('lead', '')}"
        out.append({
            "film_title": _TITLE_SUFFIX.sub("", title),
            "wikipedia_url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "wikidata_qid": p.get("qid", ""),
            "seed_context": context,
        })
    return out
