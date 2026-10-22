"""Export: adaptations.csv (the table as-is) and adaptations.html, one self-contained page
for human review with two tabs: Articles (the list, filterable and sortable) and Sources
(the seed lists with their URLs, stats and breakdowns). Rows are rendered into the HTML
itself (readable with JS off, or by an agent), and the same records are embedded as JSON
in <script id="data">.

`--queue` also prints the review queue: unreviewed rows missing an IMDb link or archive.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import re
from collections import Counter
from datetime import date
from statistics import mean, median
from urllib.parse import quote

import db
import seeds
from seeds import imdb_list, longform, longreads, tetw, texas_monthly_hollywood, thr
from steps import cli

log = logging.getLogger("export")
CSV_PATH = db.ROOT / "adaptations.csv"
HTML_PATH = db.ROOT / "adaptations.html"
ICON = (db.ROOT / "assets" / "icon.svg").read_text().strip()
NEGATIVE = db.NEGATIVE
JOURNALISM = db.JOURNALISM  # positives are counted by origin_type (Addendum 2 #1)
VERSION = "v0.1"
EVAL_PATH = db.ROOT / "eval.json"
IMDB_PAGES = db.ROOT / "data" / "imdb_pages.json"
SITE = "https://longformadaptations.jaan.io/"  # canonical URL; the justfile's `site` too
DESCRIPTION = ("Films and TV series adapted from newspaper and magazine journalism, each linked to its "
               "source article, an archived copy, IMDb and Rotten Tomatoes.")
LOCALISH = ("local", "alt_weekly", "regional")
QUEUE_SQL = ("reviewed = 0 AND (imdb_id IS NULL OR ((archive_status = 'none' OR archive_status IS NULL) "
             "AND COALESCE(source_type, '') NOT IN ('book', 'short_fiction', 'podcast_or_other')))")
# Each seed's list page(s): (name, url). The IMDb seed's pages are read from the saved copies.
SEED_PAGES = {
    "wikipedia_list": [("Wikipedia: List of films based on magazine articles",
                        "https://en.wikipedia.org/wiki/List_of_films_based_on_magazine_articles")],
    "wikipedia_category": [("Wikipedia: Category: Films based on newspaper and magazine articles",
                            "https://en.wikipedia.org/wiki/Category:Films_based_on_newspaper_and_magazine_articles")],
    "longform": [("Longform: The Longform Guide to Adaptations", longform.URL)],
    "longreads": [("Longreads: Movies based on articles", longreads.URL)],
    "tetw": [("The Electric Typewriter: Great Articles that Became Films", tetw.URL)],
    "thr": [("The Hollywood Reporter: Movies based on magazine articles", thr.URL)],
    "imdb_list": [("IMDb list ls540838468", "https://www.imdb.com/list/ls540838468/")],
    "texas_monthly_hollywood": [("Texas Monthly: How Texas Monthly went Hollywood", texas_monthly_hollywood.URL)],
    "manual": [("Hand-curated rows (seeds/manual.csv)", None)],
    "manual_tv": [("Hand-kept TV rows (seeds/manual_tv.csv)", None)],
    "wikipedia_podcast_adaptations": [("Wikipedia: List of podcast adaptations",
                                       "https://en.wikipedia.org/wiki/List_of_podcast_adaptations")],
}
SEED_NOTES = {
    "thr": "Read from its Wayback copy.",
    "imdb_list": "Saved from a browser into raw/imdb/ (IMDb blocks automated fetches).",
    "texas_monthly_hollywood": "Prose, not a list: adaptations extracted by Haiku, links kept only if on the page.",
    "manual": "Local-newspaper additions go here first.",
    "manual_tv": "TV seed: no public list tracks series adapted from journalism.",
    "wikipedia_podcast_adaptations": "TV seed. Each podcast traced to a print origin by Haiku + web search; "
                                     "most have none and are kept as podcast negatives.",
}


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def label(v: str | None) -> str:
    return (v or "").replace("_", " ")


def link(text, url) -> str:
    return f'<a href="{e(url)}">{e(text)}</a>' if url else e(text)


def money(v) -> str:
    if v is None:
        return ""
    return f"${v / 1e6:,.0f}M" if v >= 1e6 else f"${v:,}"


def unsourced(r: dict) -> bool:
    """No source named at all (Addendum 2 #7): kept, but out of the headline counts."""
    return not (r["source_title"] or r["source_author"] or r["publication"])


def listed_row(r: dict) -> bool:
    """The Articles tab: journalism and local / regional / alt-weekly outlets (no books, short
    fiction, podcasts or unsourced rows). All rows stay in the CSV and script#data."""
    if unsourced(r):
        return False
    return (r["origin_type"] in JOURNALISM
            or (r["publication_scope"] in LOCALISH and r["origin_type"] not in NEGATIVE))


def needs_review(r: dict) -> bool:
    """Unreviewed, and flagged, or missing a link we'd expect: an IMDb ID, or (for journalism) an archive."""
    unarchived = r["archive_status"] in (None, "none") and r["origin_type"] in JOURNALISM
    return not r["reviewed"] and (bool(r["review_flags"]) or not r["imdb_id"] or unarchived)


SYNOPSIS_SOURCES = {"imdb": "IMDb", "rotten_tomatoes": "Rotten Tomatoes",
                    "wikipedia_generated": "generated from Wikipedia", "article_generated": "generated from the article"}
BASIS = {"imdb_credits": "IMDb credits", "wikipedia_wording": "Wikipedia wording", "model": "model", "manual": "manual"}


def synopsis_html(r: dict) -> str:
    if not r["synopsis"]:
        return ""
    src = SYNOPSIS_SOURCES.get(r["synopsis_source"] or "", r["synopsis_source"] or "")
    return f'<span class="line">{e(r["synopsis"])} <i>({e(src)})</i></span>'


def title_links(r: dict) -> str:
    out = [link("Wikipedia", r["wikipedia_url"]) if r["wikipedia_url"] else "",
           link("IMDb", f'https://www.imdb.com/title/{r["imdb_id"]}/') if r["imdb_id"] else "",
           link("Rotten Tomatoes", r["rt_url"]) if r["rt_url"] else ""]
    return " · ".join(x for x in out if x)


def notes_html(r: dict, none: bool) -> str:
    """The AI Notes popover: everything the classifier and lookups decided about the row."""
    pid = f"n{r['id'][:12]}"
    flags = [f for f in (r["review_flags"] or "").split("|") if f]
    fid = label(r["adaptation_fidelity"])
    if fid and r["fidelity_basis"]:
        fid += f" ({BASIS.get(r['fidelity_basis'], r['fidelity_basis'])})"
    fields = [
        ("Origin", "unsourced" if none else label(r["origin_type"])),
        ("Credited", label(r["credited_source_type"])),
        ("Scope", label(r["publication_scope"])),
        ("Fidelity", fid),
        ("Chain", r["source_chain"]),
        ("Seeds", ", ".join(s for s in (r["seed"] or "").split(",") if s)),
        ("Review flags", ", ".join(f.replace("-", " ") for f in flags)),
        ("Classifier note", r["classify_notes"]),
    ]
    items = "".join(f"<dt>{k}</dt><dd>{e(v)}</dd>" for k, v in fields if v)
    count = f" · {len(flags)} flag{'s' if len(flags) > 1 else ''}" if flags else ""
    return (f'<button type="button" class="notes" popovertarget="{pid}">AI Notes{count}</button>'
            f'<div class="pop" id="{pid}" popover><button type="button" class="x" popovertarget="{pid}" '
            f'popovertargetaction="hide" aria-label="Close">✕</button><dl>{items}</dl></div>')


def bayesian(rows: list[dict], value: str, count: str) -> dict[str, float]:
    """IMDb's weighted rating: (v·R + m·C) / (v + m), with C the mean rating and m the median
    count across the table. A score from few votes or reviews is pulled toward the mean."""
    have = [r for r in rows if r[value] is not None and r[count]]
    if not have:
        return {}
    c, m = mean(r[value] for r in have), median(r[count] for r in have)
    return {r["id"]: (r[count] * r[value] + m * c) / (r[count] + m) for r in have}


def percentiles(values: dict[str, float]) -> dict[str, float]:
    """Mid-rank percentile (0–100) of each value among all of them."""
    xs = sorted(values.values())
    out = {}
    for k, v in values.items():
        below = sum(x < v for x in xs)
        out[k] = 100 * (below + 0.5 * (xs.count(v))) / len(xs)
    return out


def scores(rows: list[dict]) -> dict[str, float]:
    """The default order: percentiles of the vote-weighted IMDb rating and the review-weighted
    Tomatometer, averaged with RT counted twice. A title with only one of them takes that one."""
    imdb = percentiles(bayesian(rows, "imdb_rating", "imdb_votes"))
    rt = percentiles(bayesian(rows, "rt_score", "rt_reviews"))
    out = {}
    for r in rows:
        parts = [(w, p[r["id"]]) for w, p in ((1, imdb), (2, rt)) if r["id"] in p]
        if parts:
            out[r["id"]] = sum(w * x for w, x in parts) / sum(w for w, _ in parts)
    return out


def row_html(r: dict, same_film: bool = False, score: float | None = None) -> str:
    neg = r["origin_type"] in NEGATIVE
    none = unsourced(r)
    rt = (link(f'{r["rt_score"]}%', r["rt_url"]) if r["rt_score"] is not None
          else '<span class="line">too few reviews</span>' if r["rt_url"] else "")
    attrs = {
        "article": r["source_title"] or "", "film": r["film_title"] or "",
        "score": f"{score:.1f}" if score is not None else "",
        "rating": r["imdb_rating"] if r["imdb_rating"] is not None else "",
        "rt": r["rt_score"] if r["rt_score"] is not None else "",
        "votes": r["imdb_votes"] or "", "reviews": r["rt_reviews"] or "",
        "gross": r["box_office_usd"] or "",
    }
    data = " ".join(f'data-{k}="{e(v)}"' for k, v in attrs.items())
    aliases = [a for a in (r["source_aliases"] or "").split("|") if a]
    classes = " ".join(c for c in ("neg" if neg else "", "same" if same_film else "") if c)
    search = '<sup title="URL found by Haiku web search">*</sup>' if r["source_url_origin"] == "search" else ""
    archive = f' ({link("Archive", r["archive_url"])})' if r["archive_url"] else ""
    if r["source_language"] and r["source_language"] != "en" and r["source_url"]:
        translate = f"https://translate.google.com/translate?sl={r['source_language']}&tl=en&u={quote(r['source_url'], safe='')}"
        archive += f' ({link("Google Translate", translate)})'
    lines = [r["source_author"], r["publication_normalized"] or r["publication"], r["source_date"],
             f'also: {" · ".join(aliases)}' if aliases else ""]
    article = (f'<b>{link(r["source_title"] or "(title unknown)", r["source_url"])}</b>{search}{archive}'
               + "".join(f'<span class="line">{e(x)}</span>' for x in lines if x) + notes_html(r, none))
    medium = f' · {e(label(r["medium"]))}' if r["medium"] and r["medium"] != "film" else ""
    director = f' · {e(r["director"])}' if r["director"] else ""
    same = '<span class="line">↳ same film, another source</span>' if same_film else ""
    film = (f'{same}<b>{link(r["film_title"] or "(film unknown)", r["wikipedia_url"])}</b>'
            f'<span class="line">{e(r["film_year"])}{director}{medium}</span>{synopsis_html(r)}'
            f'<span class="line">{title_links(r)}</span>')
    nums = [
        ("Score (/100)", f"{score:.0f}" if score is not None else ""),
        ("IMDb rating (/10)", f'{r["imdb_rating"]:.1f}' if r["imdb_rating"] is not None else ""),
        ("IMDb votes", f'{r["imdb_votes"]:,}' if r["imdb_votes"] else ""),
        ("RT Critics Score", rt),
        ("RT reviews", f'{r["rt_reviews"]:,}' if r["rt_reviews"] else ""),
        ("Box office", money(r["box_office_usd"])
         + (f'<span class="line">{e(r["box_office_note"])}</span>' if r["box_office_usd"] and r["box_office_note"] else "")),
    ]
    cells = "".join(f'<td class="num" data-label="{k}">{v}</td>' for k, v in nums)
    cls = f' class="{classes}"' if classes else ""
    return (f'<tr {data}{cls}>'
            f'<td class="article">{article}</td><td class="film">{film}</td>{cells}</tr>')


def breakdown(title: str, counts: Counter, total: int) -> str:
    items = "".join(
        f'<tr><td>{e(label(k) or "unclassified")}</td><td class="num">{v}</td><td class="num">{v / total:.0%}</td></tr>'
        for k, v in counts.most_common())
    return f'<section><h3>{e(title)}</h3><table class="mini">{items}</table></section>'


def imdb_pages() -> list[dict]:
    """The saved IMDb pages (raw/imdb/, not committed) as name, url and title ids. Kept in
    data/imdb_pages.json too, so an export without raw/ (the deploy action) renders the same."""
    pages = sorted(imdb_list.SAVED_DIR.glob("*.html"))
    if not pages:
        return json.loads(IMDB_PAGES.read_text()) if IMDB_PAGES.exists() else []
    out = []
    for page in pages:
        text = page.read_text(errors="replace")
        url = re.search(r'<link rel="canonical" href="([^"]+)"', text)
        title = re.search(r"<title>([^<]+)</title>", text)
        name = re.sub(r"\s*-\s*IMDb$", "", html.unescape(title.group(1))) if title else page.stem
        out.append({"name": f"IMDb: {name}", "url": url.group(1) if url else None,
                    "ids": sorted({r["imdb_id"] for r in imdb_list.parse(text)})})
    IMDB_PAGES.write_text(json.dumps(out, indent=1) + "\n")
    return out


def seed_pages() -> list[tuple[str, str, str | None, set[str] | None]]:
    """(seed, name, url, imdb ids on that page or None): the IMDb seed is split per saved page."""
    out = []
    for s in seeds.ORDER + seeds.TV_ORDER:
        pages = imdb_pages() if s == "imdb_list" else []
        if not pages:
            out += [(s, name, url, None) for name, url in SEED_PAGES.get(s, [(s, None)])]
            continue
        out += [(s, p["name"], p["url"], set(p["ids"])) for p in pages]
    return out


def sources_table(rows: list[dict]) -> str:
    out = []
    for s, name, url, ids in seed_pages():
        mine = [r for r in rows if s in (r["seed"] or "").split(",") and (ids is None or r["imdb_id"] in ids)]
        journalism = sum(r["origin_type"] in JOURNALISM and not unsourced(r) for r in mine)
        where = f'<span class="line url">{link(url, url)}</span>' if url else ""
        note = f'<span class="line">{e(SEED_NOTES[s])}</span>' if s in SEED_NOTES and (ids is None or "/list/" in (url or "")) else ""
        out.append(f'<tr><td><b>{e(name)}</b>{where}{note}</td><td class="num">{len(mine)}</td>'
                   f'<td class="num">{journalism}</td></tr>')
    return "".join(out)


def eval_line() -> str:
    if not EVAL_PATH.exists():
        return "No eval yet (run <code>just eval</code>)."
    ev = json.loads(EVAL_PATH.read_text())
    parts = [f"{f.replace('_', ' ')} {s['accuracy']:.0%}" for f, s in ev["fields"].items() if s.get("accuracy") is not None]
    return (f"Eval {e(ev['date'])} against <a href=\"data/gold.csv\">data/gold.csv</a> ({ev['matched']} of "
            f"{ev['gold_rows']} gold rows matched, {ev['verified']} hand-verified): {e(' · '.join(parts))}. "
            f"Details in <a href=\"data/eval.json\">eval.json</a>.")


def render(rows: list[dict]) -> str:
    n = len(rows)
    types = Counter("unsourced" if unsourced(r) else r["origin_type"] for r in rows)
    credited = Counter(r["credited_source_type"] for r in rows)
    media = Counter(r["medium"] for r in rows)
    journalism = [r for r in rows if r["origin_type"] in JOURNALISM and not unsourced(r)]
    negatives = [r for r in rows if r["origin_type"] in NEGATIVE and not unsourced(r)]
    n_unsourced = sum(unsourced(r) for r in rows)
    localish = [r for r in journalism if r["publication_scope"] in LOCALISH]
    archived = sum(1 for r in rows if r["archive_url"])
    with_url = sum(1 for r in rows if r["source_url"])
    linked = sum(1 for r in rows if r["imdb_id"])
    queue = sum(needs_review(r) for r in rows)
    rt_scored = sum(1 for r in rows if r["rt_score"] is not None)
    with_syn = sum(1 for r in rows if r["synopsis"])
    generated = sum(1 for r in rows if (r["synopsis_source"] or "").endswith("_generated"))

    def pct(a, b):
        return f"{a / b:.0%}" if b else "—"

    stats = [
        (len(journalism), "journalism positives", "by origin: the first publication in the chain is journalism"),
        (len(negatives), "negatives", "book, short fiction or podcast origin (labeled, kept)"),
        (n_unsourced, "unsourced", f"no source named · {n - len(journalism) - len(negatives) - n_unsourced} other or unknown · {n} rows in all"),
        (len(localish), "local / regional / alt-weekly", f"{pct(len(localish), len(journalism))} of journalism"),
        (pct(linked, n), "IMDb linked", f"{linked} rows: Wikidata P345, else an exact OMDb title + year match"),
        (pct(archived, n), "archived", f"{archived} rows · {pct(archived, with_url)} of rows with a URL"),
        (pct(rt_scored, n), "Tomatometer", f"{rt_scored} critic scores · {sum(1 for r in rows if r['rt_url'])} RT pages"),
        (pct(with_syn, n), "synopsis", f"{with_syn - generated} from IMDb or RT · {generated} generated"),
        (queue, "review queue", "flagged, no IMDb link, or an article with no archive"),
    ]
    stat_html = "".join(f'<div><b>{e(v)} {e(k)}</b><span class="line">{e(s)}</span></div>' for v, k, s in stats)
    # Highest score first; unscored rows last, by film. A film with two sources (Argo: article
    # + memoir) shares a score, so its rows stay adjacent and the second is marked
    score = scores(rows)
    listed = sorted((r for r in rows if listed_row(r)), key=lambda r: -score.get(r["id"], -1))
    body = "\n".join(row_html(r, i > 0 and bool(r["wikidata_qid"]) and r["wikidata_qid"] == listed[i - 1]["wikidata_qid"],
                               score.get(r["id"])) for i, r in enumerate(listed))
    records = [{k: r[k] for k in db.NAMES if k != "seed_context"} for r in rows]
    payload = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    favicon = "data:image/svg+xml," + ICON.replace('"', "'").replace("#", "%23")
    jsonld = json.dumps({
        "@context": "https://schema.org", "@type": "Dataset", "name": "List of Longform Adaptations",
        "description": DESCRIPTION, "url": SITE, "dateModified": date.today().isoformat(),
        "keywords": ["film adaptations", "journalism", "magazine articles", "newspaper articles", "longform"],
        "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv",
                          "contentUrl": f"{SITE}data/adaptations.csv"}],
    }, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.format(
        site=SITE, description=e(DESCRIPTION), jsonld=jsonld,
        today=date.today().isoformat(), n=n, stats=stat_html, sources=sources_table(rows),
        icon=ICON, favicon=e(favicon),
        b_type=breakdown("Origin type", types, n), b_credited=breakdown("Credited source", credited, n),
        b_scope=breakdown("Publication scope (journalism)", Counter(r["publication_scope"] for r in journalism),
                          max(1, len(journalism))),
        b_medium=breakdown("Medium", media, n),
        version=VERSION, eval_line=eval_line(), shown=len(listed), rows=body, data=payload)


def main() -> None:
    a = cli(__doc__, queue={"action": "store_true", "help": "print the review queue"})
    con = db.connect()
    rows = db.rows(con, limit=a.limit)
    cols = [c for c in db.NAMES if c != "seed_context"]
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    HTML_PATH.write_text(render(rows))
    log.info("wrote %d rows to %s and %s", len(rows), CSV_PATH.name, HTML_PATH.name)
    if a.queue:
        for r in db.rows(con, QUEUE_SQL):
            print(f"{r['film_title']!s:40.40} | {r['source_title']!s:40.40} | imdb={r['imdb_id']} "
                  f"archive={r['archive_status']}")


# Two text sizes only (--m, --s); --m is 16px so iOS doesn't zoom into the inputs. Wide
# screens get the table; narrower ones (≤ 960px) get each row as a card, so nothing ever
# scrolls sideways.
TEMPLATE = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>List of Longform Adaptations</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{site}">
<script>
// Light unless the reader chose dark (not the OS setting): set before first paint
try {{ if (localStorage.getItem("theme") === "dark") document.documentElement.dataset.theme = "dark"; }} catch {{}}
</script>
<link rel="icon" href="{favicon}" type="image/svg+xml">
<meta property="og:title" content="List of Longform Adaptations">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:url" content="{site}">
<meta property="og:image" content="{site}og.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="List of Longform Adaptations: films and TV series adapted from newspaper and magazine journalism">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{jsonld}</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap" rel="stylesheet">
<style>
:root, :root[data-theme="light"] {{ color-scheme: light; --ink: #000; --paper: #fff; --icon-doc: #1b9955; --icon-cam: #df37e6; --icon-lens: #9a8600; --accent: #18864a; }}
/* Dark is jaan.io's palette (src/styles/tokens.css): --bg, --text, and the icon in --accent green, pink and yellow; light uses darker shades of those hues (3.6:1 on white) */
:root[data-theme="dark"] {{ color-scheme: dark; --ink: #e2e2e2; --paper: #000; --icon-doc: #24cb71; --icon-cam: #e870ed; --icon-lens: #fbda00; --accent: #24cb71; }}
:root {{ --m: 16px; --s: 13px; --rule: 2px solid var(--ink); --hair: 1px solid var(--ink); --nav-h: 52px; }}
* {{ box-sizing: border-box; }}
[hidden] {{ display: none !important; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{ margin: 0; background: var(--paper); color: var(--ink); font: 400 var(--m)/1.4 Inter, ui-sans-serif, system-ui, sans-serif; font-feature-settings: "tnum" 1, "cv11" 1; overflow-wrap: break-word; }}
h1, h2, h3, p, dl, dd {{ margin: 0; font-size: inherit; }}
h2, h3, b, dt, th {{ font-weight: 700; }}
a {{ color: inherit; text-decoration-thickness: 1px; text-underline-offset: 2px; }}
button, input, select {{ font: inherit; color: var(--ink); }}
.line {{ display: block; font-size: var(--s); }}
.url {{ overflow-wrap: anywhere; }}

header.top {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: var(--nav-h); padding: 0 16px; border-bottom: var(--rule); background: var(--paper); }}
.brand {{ display: flex; align-items: center; gap: 8px; font-weight: 700; text-decoration: none; }}
.brand svg {{ width: 22px; height: 28px; flex: none; }}
.tabs {{ display: flex; gap: 24px; }}
.tabs a, .theme-toggle {{ font-weight: 700; text-decoration: none; padding: 2px 4px; border: 0; background: var(--paper); cursor: pointer; }}
.tabs a[aria-current="page"] {{ background: var(--ink); color: var(--paper); }}
main {{ max-width: 1440px; margin: 0 auto; padding: 16px 16px 48px; }}

.controls {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding-bottom: 16px; }}
.controls input[type=search] {{ border: var(--hair); border-radius: 0; background: var(--paper); padding: 6px 8px; min-height: 40px; min-width: 0; max-width: 100%; }}
.controls input[type=search] {{ flex: 1 1 280px; }}
.count {{ margin-left: auto; font-weight: 700; }}

table {{ border-collapse: collapse; width: 100%; }}
.list th {{ position: sticky; top: 0; background: var(--ink); color: var(--paper); text-align: left; vertical-align: bottom; font-size: var(--s); padding: 8px; cursor: pointer; user-select: none; }}
.list th[aria-sort=ascending]::after {{ content: " ↑"; }}
.list th[aria-sort=descending]::after {{ content: " ↓"; }}
.list td {{ padding: 12px 8px; vertical-align: top; border-bottom: var(--hair); }}
.list .article, .list .film {{ width: 32%; }}
.num {{ text-align: right; }}
.list td.num {{ white-space: nowrap; }}
.list .line + .line, .film .line {{ margin-top: 2px; }}
tr.neg td {{ font-style: italic; }}
tr.same td {{ border-top: 0; }}
sup {{ font-size: var(--s); line-height: 0; }}

.notes {{ display: inline-block; margin-top: 8px; padding: 2px 8px; border: var(--hair); background: var(--paper); font-size: var(--s); font-style: normal; cursor: pointer; }}
.pop {{ margin: auto; width: min(440px, calc(100vw - 32px)); max-height: min(70vh, 560px); overflow: auto; padding: 12px 16px 16px; border: var(--rule); background: var(--paper); color: var(--ink); font-size: var(--s); font-style: normal; }}
.pop::backdrop {{ background: transparent; }}
.pop .x {{ float: right; margin: -4px -8px 0 8px; width: 36px; height: 36px; border: 0; background: var(--paper); font-size: var(--m); font-weight: 700; cursor: pointer; }}
.pop dt {{ margin-top: 8px; }}
.pop dt:first-child {{ margin-top: 0; }}

.hint {{ font-size: var(--s); margin-top: 16px; max-width: 80ch; }}
#tab-sources > * + * {{ margin-top: 32px; }}
#tab-sources h2 {{ padding-bottom: 8px; border-bottom: var(--rule); margin-bottom: 8px; }}
.dek {{ max-width: 70ch; }}
.dek p + p {{ margin-top: 8px; }}
.intro {{ margin-bottom: 16px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px 24px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 24px; }}
.grid h3 {{ margin-bottom: 4px; }}
.mini td {{ padding: 2px 0; vertical-align: top; font-size: var(--s); }}
.mini td + td {{ padding-left: 12px; }}
.seedlist td, .seedlist th {{ padding: 8px 0; vertical-align: top; text-align: left; border-bottom: var(--hair); }}
.seedlist th {{ font-size: var(--s); }}
.seedlist td + td, .seedlist th + th {{ padding-left: 16px; text-align: right; width: 1%; white-space: nowrap; }}
footer {{ font-size: var(--s); }}
footer p + p {{ margin-top: 8px; }}

.menu-btn, .menu {{ display: none; }}
@media (max-width: 960px) {{
  header.top {{ position: sticky; top: 0; z-index: 20; }}
  .list thead {{ display: none; }}
  .list, .list tbody {{ display: block; }}
  .list tr {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px 16px; padding: 16px 0; border-bottom: var(--hair); }}
  .list td {{ display: block; padding: 0; border: 0; text-align: left; }}
  .list .article, .list .film {{ width: auto; grid-column: 1 / -1; }}
  .list td.num {{ white-space: normal; }}
  .list td.num::before {{ content: attr(data-label); display: block; font-size: var(--s); font-style: normal; }}
  .list td.num:empty {{ display: none; }}
}}
@media (max-width: 640px) {{
  .tabs {{ display: none; }}
  .controls input[type=search] {{ flex: 1 1 100%; }}
  .count {{ margin-left: 0; }}
  .menu-btn {{ display: flex; align-items: center; justify-content: center; width: var(--nav-h); height: var(--nav-h); margin-right: -16px; border: 0; border-left: var(--rule); background: var(--paper); padding: 0; cursor: pointer; }}
  .menu {{ display: flex; flex-direction: column; position: fixed; inset: var(--nav-h) 0 0 0; z-index: 19; background: var(--paper); visibility: hidden; }}
  .menu.is-open {{ visibility: visible; }}
  .menu .theme-toggle {{ text-align: left; padding: 16px; border-bottom: var(--rule); }}
  .menu a {{ display: block; padding: 16px; border-bottom: var(--rule); font-weight: 700; text-decoration: none; }}
  .menu a[aria-current="page"] {{ background: var(--ink); color: var(--paper); }}
}}
/* Hover and keyboard focus, as in jaan.io's nav (src/styles/chrome.css .nav-link): the accent
   fills the link and the text takes the page colour (4.6:1 light, 9.9:1 dark). Last, so it
   wins over each link's resting background. The logo only underlines: its icon is green too. */
a, .theme-toggle, .notes, .pop .x {{ transition: background-color 225ms ease, color 225ms ease; }}
a:hover, a:focus-visible, .tabs a:hover, .tabs a:focus-visible, .menu a:hover, .menu a:focus-visible,
.theme-toggle:hover, .theme-toggle:focus-visible, .notes:hover, .notes:focus-visible, .pop .x:hover, .pop .x:focus-visible {{
  background: var(--accent); color: var(--paper); text-decoration: none; outline: none; }}
.brand:hover, .brand:focus-visible {{ background: none; color: inherit; text-decoration: underline; text-underline-offset: 4px; }}
/* Hamburger ↔ ✕ */
.menu-icon {{ position: relative; width: 20px; height: 20px; }}
.menu-icon i {{ position: absolute; left: 0; right: 0; height: 2px; background: currentColor; transition: transform .3s, top .3s; }}
.menu-icon i:first-child {{ top: 5px; }}
.menu-icon i:last-child {{ top: 13px; }}
.menu-btn[aria-expanded="true"] .menu-icon i {{ top: 9px; }}
.menu-btn[aria-expanded="true"] .menu-icon i:first-child {{ transform: rotate(45deg); }}
.menu-btn[aria-expanded="true"] .menu-icon i:last-child {{ transform: rotate(-45deg); }}
@media (prefers-reduced-motion: reduce) {{ .menu-icon i {{ transition: none; }} }}
</style>
</head>
<body>
<header class="top">
<a class="brand" href="#articles">{icon}<h1>Longform Adaptations</h1></a>
<nav class="tabs" aria-label="Tabs"><a href="#articles" data-tab="articles">Articles</a><a href="#sources" data-tab="sources">Sources</a><a href="https://github.com/altosaar/longform-adaptations/">GitHub</a><button class="theme-toggle" type="button" aria-pressed="false">Dark</button></nav>
<button class="menu-btn" type="button" aria-label="Open menu" aria-expanded="false" aria-controls="menu"><span class="menu-icon" aria-hidden="true"><i></i><i></i></span></button>
</header>
<nav class="menu" id="menu" aria-label="Menu"><a href="#articles" data-tab="articles">Articles</a><a href="#sources" data-tab="sources">Sources</a><a href="https://github.com/altosaar/longform-adaptations/">GitHub</a><button class="theme-toggle" type="button" aria-pressed="false">Dark</button></nav>
<main>
<section id="tab-articles" class="tab">
<p class="dek intro">This list collects films and TV series adapted from (often longform) journalism: newspaper stories, magazine features, columns and essays, plus a few short stories. Each entry links the original article and an archived copy, then the adaptation's pages on Wikipedia, IMDb and Rotten Tomatoes. An adaptation counts as journalism when its story first ran in a newspaper or magazine, even if the credits name a later book.</p>
<div class="controls">
<input type="search" placeholder="Filter: film, headline, author, outlet, note…" aria-label="Filter rows">
<span class="count" aria-live="polite">{shown} articles</span>
</div>
<table class="list">
<thead><tr>
<th data-key="article">Article</th><th data-key="film">Film Adaptation</th>
<th data-key="score" data-num aria-sort="descending" title="RT and IMDb percentiles, RT counted twice">Score (/100)</th><th data-key="rating" data-num>IMDb rating (/10)</th><th data-key="votes" data-num>IMDb votes</th><th data-key="rt" data-num>RT Critics Score</th><th data-key="reviews" data-num>RT reviews</th><th data-key="gross" data-num>Box office</th>
</tr></thead>
<tbody>
{rows}
</tbody>
</table>
<p class="hint">Sorted by score: each title's IMDb rating and Tomatometer are first weighted by their vote and review counts (IMDb's weighted-rating formula, which pulls a score from few votes toward the table's mean), then turned into percentiles across the table and averaged with Rotten Tomatoes counted twice; a title with only one of the two takes that one. Click a column heading to sort; click again to reverse. Listed: journalism and local / regional / alt-weekly outlets; books, short fiction, podcasts and unsourced rows are only in the <a href="data/adaptations.csv">CSV</a>. * = article URL found by web search on the outlet's own domain. Italic rows are negatives (book, short fiction or podcast origin). AI Notes holds the classifier's origin, credit, scope and fidelity calls, the source chain, review flags and its note.</p>
</section>

<section id="tab-sources" class="tab">
<div class="dek">
<p>Published lists seeded this collection, and the table below names each one and counts what it contributed. Wikidata, IMDb and Rotten Tomatoes supplied the years, ratings and links, and the figures further down summarize the result.</p>
<p>For adaptations of books, see <a href="https://booktomovies.com/">booktomovies.com</a>.</p>
</div>
<section>
<h2>Seed lists</h2>
<table class="seedlist">
<thead><tr><th>List</th><th>Listed</th><th>Journalism</th></tr></thead>
<tbody>{sources}</tbody>
</table>
<p class="hint">Listed: rows in this table that the list named, after dedupe (a row named by several lists counts for each). Journalism: those whose origin is journalism.</p>
</section>
<section>
<h2>{version} · generated {today} · {n} rows</h2>
<div class="stats">{stats}</div>
</section>
<section class="grid">
{b_type}
{b_credited}
{b_scope}
{b_medium}
</section>
<footer>
<p>IMDb ratings from the public dataset; Rotten Tomatoes critic scores (Tomatometer) from each title's RT page, OMDb as fallback; box office from Wikidata P2142 (worldwide unless noted), else OMDb (domestic).
Synopses come from IMDb (via OMDb), else Rotten Tomatoes, else are generated by Haiku from the Wikipedia lead, else from the source article; each is labeled with its source.
Scope comes from <a href="data/publications.csv">data/publications.csv</a>; medium and year from Wikidata; fidelity from credit wording (IMDb credits when saved, else Wikipedia), the model only downgrading. Rows are unreviewed.</p>
<p>{eval_line}</p>
<p>Data {version}: <a href="data/adaptations.csv">adaptations.csv</a> · <a href="data/publications.csv">publications.csv</a> · <a href="data/gold.csv">gold.csv</a> · <a href="data/eval.json">eval.json</a> · <a href="data/CHANGELOG.md">CHANGELOG</a> (also under <code>/data/{version}/</code>), and <code>script#data</code> on this page.</p>
<p>Icon: document filming by Aybige from <a href="https://thenounproject.com/browse/icons/term/document-filming/" target="_blank" title="document filming Icons">Noun Project</a> (CC BY 3.0)</p>
</footer>
</section>
</main>
<script type="application/json" id="data">{data}</script>
<script>
(() => {{
  const tbody = document.querySelector(".list tbody");
  const rows = [...tbody.rows];
  const q = document.querySelector(".controls input[type=search]");
  const count = document.querySelector(".count");
  rows.forEach(r => r._text = r.textContent.toLowerCase());
  q.addEventListener("input", () => {{
    const needle = q.value.trim().toLowerCase();
    let shown = 0;
    for (const r of rows) shown += !(r.hidden = needle && !r._text.includes(needle));
    count.textContent = needle ? `${{shown}} of ${{rows.length}} articles` : `${{rows.length}} articles`;
  }});

  // Sorting: by a column heading (wide screens); the original order is by score
  const original = rows.slice();
  const ths = [...document.querySelectorAll(".list th")];
  function sortBy(key, asc, num) {{
    ths.forEach(h => h.setAttribute("aria-sort", h.dataset.key === key ? (asc ? "ascending" : "descending") : "none"));
    const val = r => num ? parseFloat(r.dataset[key]) : r.dataset[key].toLowerCase();
    const sorted = !key ? original.slice() : rows.slice().sort((a, b) => {{
      const x = val(a), y = val(b);
      if (num) {{ if (isNaN(x)) return isNaN(y) ? 0 : 1; if (isNaN(y)) return -1; return asc ? x - y : y - x; }}
      return asc ? x.localeCompare(y) : y.localeCompare(x);
    }});
    sorted.forEach(r => tbody.appendChild(r));
  }}
  ths.forEach(th => th.addEventListener("click", () => {{
    const num = th.hasAttribute("data-num");
    const asc = th.getAttribute("aria-sort") === "ascending" ? false : th.getAttribute("aria-sort") === "descending" ? true : !num;
    sortBy(th.dataset.key, asc, num);
  }}));

  // AI Notes: a popover (works without JS, centered); with JS it sits by its button and
  // follows it as the page scrolls
  let open = null;
  function place(b) {{
    const pop = document.getElementById(b.getAttribute("popovertarget"));
    const r = b.getBoundingClientRect(), w = Math.min(440, innerWidth - 32);
    const left = Math.max(16, Math.min(r.left, innerWidth - w - 16));
    Object.assign(pop.style, {{ margin: "0", inset: "auto", left: left + "px" }});
    const below = innerHeight - r.bottom - 20, above = r.top - 20;
    if (below >= 240 || below >= above) Object.assign(pop.style, {{ top: r.bottom + 4 + "px", bottom: "auto", maxHeight: Math.min(560, below) + "px" }});
    else Object.assign(pop.style, {{ bottom: innerHeight - r.top + 4 + "px", top: "auto", maxHeight: Math.min(560, above) + "px" }});
  }}
  document.querySelectorAll(".notes").forEach(b => b.addEventListener("click", () => {{ open = b; place(b); }}));
  addEventListener("scroll", () => {{ if (open && document.querySelector(".pop:popover-open")) place(open); }}, {{ passive: true }});

  // Tabs: both sections are in the page (readable without JS); the hash picks the visible one
  const btn = document.querySelector(".menu-btn"), menu = document.getElementById("menu");
  function setMenu(open) {{
    menu.classList.toggle("is-open", open);
    btn.setAttribute("aria-expanded", String(open));
    btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  }}
  btn.addEventListener("click", () => setMenu(!menu.classList.contains("is-open")));
  addEventListener("keydown", ev => {{ if (ev.key === "Escape") setMenu(false); }});
  function showTab() {{
    const want = /^#(sources|stats)$/.test(location.hash) ? "sources" : "articles";
    document.querySelectorAll("section.tab").forEach(s => s.hidden = s.id !== "tab-" + want);
    document.querySelectorAll("[data-tab]").forEach(t => t.dataset.tab === want ? t.setAttribute("aria-current", "page") : t.removeAttribute("aria-current"));
    setMenu(false);
    scrollTo(0, 0);
  }}
  // Light/dark: the choice is remembered on this device
  const toggles = [...document.querySelectorAll(".theme-toggle")], root = document.documentElement;
  function labelTheme() {{
    const dark = root.dataset.theme === "dark";
    toggles.forEach(b => {{ b.textContent = dark ? "Light" : "Dark"; b.setAttribute("aria-pressed", String(dark));
      b.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode"); }});
  }}
  toggles.forEach(b => b.addEventListener("click", () => {{
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try {{ localStorage.setItem("theme", root.dataset.theme); }} catch {{}}
    labelTheme();
  }}));
  labelTheme();
  addEventListener("hashchange", showTab);
  showTab();
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
