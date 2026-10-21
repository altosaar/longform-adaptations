"""Export: adaptations.csv (the table as-is) and adaptations.html, one self-contained page
for human review: stat strip, breakdowns, and the full list, filterable and sortable.
Rows are rendered into the HTML itself (readable with JS off, or by an agent), and the
same records are embedded as JSON in <script id="data">.

`--queue` also prints the review queue: unreviewed rows missing an IMDb link or archive.
"""

from __future__ import annotations

import csv
import html
import json
import logging
from collections import Counter
from datetime import date

import db
import seeds
from steps import cli

log = logging.getLogger("export")
CSV_PATH = db.ROOT / "adaptations.csv"
HTML_PATH = db.ROOT / "adaptations.html"
NEGATIVE = db.NEGATIVE
JOURNALISM = db.JOURNALISM  # positives are counted by origin_type (Addendum 2 #1)
VERSION = "v0.1"
EVAL_PATH = db.ROOT / "eval.json"
LOCALISH = ("local", "alt_weekly", "regional")
QUEUE_SQL = ("reviewed = 0 AND (imdb_id IS NULL OR ((archive_status = 'none' OR archive_status IS NULL) "
             "AND COALESCE(source_type, '') NOT IN ('book', 'short_fiction', 'podcast_or_other')))")
SEED_NOTES = {
    "imdb_list": "A user list and a poll, saved from a browser into raw/imdb/ (IMDb blocks automated fetches).",
    "manual": "Hand-curated rows (seeds/manual.csv); local-newspaper additions go here first.",
    "manual_tv": "Hand-kept TV rows (seeds/manual_tv.csv); no public list tracks series adapted from journalism.",
    "wikipedia_podcast_adaptations": "TV seed. Each podcast traced to a print origin by Haiku + web search; "
                                     "most have none and are kept as podcast negatives.",
}
MEDIA = ("film", "scripted_series", "docuseries", "tv_movie")


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def label(v: str | None) -> str:
    return (v or "").replace("_", " ")


def link(text, url) -> str:
    return f'<a href="{e(url)}">{e(text)}</a>' if url else e(text)


def count(v) -> str:
    """12,345 → 12K; 674,763 → 675K; 1,234,567 → 1.2M."""
    if v is None:
        return "?"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    return f"{v / 1e3:.0f}K" if v >= 1e4 else f"{v:,}"


def money(v) -> str:
    if v is None:
        return ""
    return f"${v / 1e6:,.0f}M" if v >= 1e6 else f"${v:,}"


def unsourced(r: dict) -> bool:
    """No source named at all (Addendum 2 #7): kept, but out of the headline counts."""
    return not (r["source_title"] or r["source_author"] or r["publication"])


def needs_review(r: dict) -> bool:
    """Unreviewed, and flagged, or missing a link we'd expect: an IMDb ID, or (for journalism) an archive."""
    unarchived = r["archive_status"] in (None, "none") and r["origin_type"] in JOURNALISM
    return not r["reviewed"] and (bool(r["review_flags"]) or not r["imdb_id"] or unarchived)


def tag(v: str | None, negative: bool = False) -> str:
    if not v:
        return '<span class="tag empty">—</span>'
    return f'<span class="tag{" neg" if negative else ""}">{e(label(v))}</span>'


SYNOPSIS_SOURCES = {"imdb": "IMDb", "rotten_tomatoes": "Rotten Tomatoes",
                    "wikipedia_generated": "generated from Wikipedia", "article_generated": "generated from the article"}


def synopsis_html(r: dict) -> str:
    if not r["synopsis"]:
        return ""
    src = SYNOPSIS_SOURCES.get(r["synopsis_source"] or "", r["synopsis_source"] or "")
    return f'<span class="syn">{e(r["synopsis"])} <span class="via">— {e(src)}</span></span>'


def title_links(r: dict) -> str:
    out = [link("Wikipedia", r["wikipedia_url"]) if r["wikipedia_url"] else "",
           link("IMDb", f'https://www.imdb.com/title/{r["imdb_id"]}/') if r["imdb_id"] else "",
           link("Rotten Tomatoes", r["rt_url"]) if r["rt_url"] else ""]
    return " · ".join(x for x in out if x)


def row_html(r: dict, same_film: bool = False) -> str:
    neg = r["origin_type"] in NEGATIVE
    none = unsourced(r)
    origin = '<span class="tag empty">unsourced</span>' if none else tag(r["origin_type"], neg)
    imdb = (f'<a href="https://www.imdb.com/title/{e(r["imdb_id"])}/">{e(r["imdb_id"])}</a>'
            if r["imdb_id"] else '<span class="miss">no link</span>')
    rating = f'<b>{r["imdb_rating"]:.1f}</b>' if r["imdb_rating"] is not None else ""
    rt = (link(f'{r["rt_score"]}%', r["rt_url"]) if r["rt_score"] is not None
          else link("page", r["rt_url"]) if r["rt_url"] else "")
    if r["rt_url"] and r["rt_score"] is None:
        rt += '<span class="by">too few reviews</span>'
    archive = {"wayback": "Wayback", "archive_is": "archive.is", "manual": "manual"}.get(r["archive_status"] or "")
    arch = link(archive, r["archive_url"]) if archive and r["archive_url"] else '<span class="miss">none</span>'
    seeds_list = (r["seed"] or "").split(",")
    attrs = {
        "type": "unsourced" if none else (r["origin_type"] or ""), "credited": r["credited_source_type"] or "",
        "scope": r["publication_scope"] or "", "medium": r["medium"] or "",
        "fidelity": r["adaptation_fidelity"] or "", "seeds": " ".join(seeds_list),
        "review": "1" if needs_review(r) else "0", "neg": "1" if neg else "0",
        "journalism": "1" if r["origin_type"] in JOURNALISM and not none else "0",
        "unsourced": "1" if none else "0",
        "year": r["film_year"] or "",
        "rating": r["imdb_rating"] if r["imdb_rating"] is not None else "",
        "rt": r["rt_score"] if r["rt_score"] is not None else "",
        "votes": r["imdb_votes"] or "", "reviews": r["rt_reviews"] or "",
        "gross": r["box_office_usd"] or "",
    }
    data = " ".join(f'data-{k}="{e(v)}"' for k, v in attrs.items())
    pub = r["publication_normalized"] or r["publication"]
    note = e(r["classify_notes"])
    flags = "".join(f'<span class="flag">{e(f.replace("-", " "))}</span>' for f in (r["review_flags"] or "").split("|") if f)
    aliases = [a for a in (r["source_aliases"] or "").split("|") if a]
    also = f'<span class="by">also: {e(" · ".join(aliases))}</span>' if aliases else ""
    basis = {"imdb_credits": "IMDb credits", "wikipedia_wording": "Wikipedia wording", "model": "model",
             "manual": "manual"}.get(r["fidelity_basis"] or "", "")
    classes = " ".join(c for c in ("neg" if neg else "", "same" if same_film else "") if c)
    same = '<span class="samefilm">↳ same film, another source</span>' if same_film else ""
    return f"""<tr {data}{f' class="{classes}"' if classes else ""}>
<td class="film">{same}{link(r["film_title"] or "(film unknown)", r["wikipedia_url"])}<span class="yr">{e(r["film_year"])}{" · " + e(label(r["medium"])) if r["medium"] and r["medium"] != "film" else ""}</span>{synopsis_html(r)}<span class="links">{title_links(r)}</span></td>
<td class="src">{link(r["source_title"] or "(title unknown)", r["source_url"])}{'<sup title="URL found by Haiku web search">*</sup>' if r["source_url_origin"] == "search" else ""}<span class="by">{e(r["source_author"])}</span>{also}<span class="by chain">{e(r["source_chain"])}</span></td>
<td>{e(pub)}<span class="by">{e(r["source_date"])}</span></td>
<td>{origin}</td>
<td>{tag(r["credited_source_type"], r["credited_source_type"] in NEGATIVE)}</td>
<td>{tag(r["publication_scope"])}</td>
<td>{tag(r["adaptation_fidelity"])}<span class="by">{e(basis)}</span></td>
<td>{imdb}<span class="by">{rating}</span></td>
<td class="num">{f'{r["imdb_votes"]:,}' if r["imdb_votes"] else ""}</td>
<td class="num">{rt}</td>
<td class="num">{f'{r["rt_reviews"]:,}' if r["rt_reviews"] else ""}</td>
<td class="num">{money(r["box_office_usd"])}<span class="by">{e(r["box_office_note"])}</span></td>
<td>{arch}</td>
<td class="seeds">{"<br>".join(e(s) for s in seeds_list if s)}</td>
<td class="note">{flags}{note}</td>
</tr>"""


def breakdown(title: str, counts: Counter, total: int) -> str:
    items = "".join(
        f'<tr><td>{e(label(k) or "unclassified")}</td><td class="num">{v}</td>'
        f'<td class="num muted">{v / total:.0%}</td></tr>'
        for k, v in counts.most_common())
    return f'<section class="box"><h3>{e(title)}</h3><table class="mini">{items}</table></section>'


def options(name: str, values) -> str:
    opts = "".join(f'<option value="{e(v)}">{e(label(v))}</option>' for v in values)
    return f'<select data-filter="{name}"><option value="">all {name}</option>{opts}</select>'


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
    scopes = Counter(r["publication_scope"] for r in rows)
    media = Counter(r["medium"] for r in rows)
    seed_counts = Counter(s for r in rows for s in (r["seed"] or "").split(",") if s)
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
    stat_html = "".join(
        f'<div class="stat"><div class="big">{e(v)}</div><div class="lbl">{e(k)}</div><div class="sub">{e(s)}</div></div>'
        for v, k, s in stats)
    seed_rows = "".join(
        f'<tr><td>{e(s)}</td><td class="num">{seed_counts.get(s, 0)}</td>'
        f'<td class="muted">{e(SEED_NOTES.get(s, ""))}</td></tr>' for s in seeds.ORDER + seeds.TV_ORDER)
    # A film with two sources (Argo: article + memoir) shows as a group: rows sharing a QID
    # are adjacent (sorted by film) and the second is marked
    body = "\n".join(row_html(r, i > 0 and bool(r["wikidata_qid"]) and r["wikidata_qid"] == rows[i - 1]["wikidata_qid"])
                     for i, r in enumerate(rows))
    records = [{k: r[k] for k in db.NAMES if k != "seed_context"} for r in rows]
    payload = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.format(
        today=date.today().isoformat(), n=n, stats=stat_html, seed_rows=seed_rows,
        b_type=breakdown("Origin type", types, n), b_credited=breakdown("Credited source", credited, n),
        b_scope=breakdown("Publication scope (journalism)", Counter(r["publication_scope"] for r in journalism),
                          max(1, len(journalism))),
        b_medium=breakdown("Medium", media, n),
        f_type=options("type", sorted(k for k in types if k)).replace("all type", "all origins"),
        version=VERSION, eval_line=eval_line(),
        f_scope=options("scope", sorted(k for k in scopes if k)),
        f_seed=options("seeds", [s for s in seeds.ORDER + seeds.TV_ORDER if seed_counts.get(s)]),
        f_medium=options("medium", [m for m in MEDIA if any(r["medium"] == m for r in rows)]),
        rows=body, data=payload)


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


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Longhorns v0</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
:root {{ --ink: #000; --paper: #fff; --rule: 3px solid var(--ink); --hair: 1px solid var(--ink); }}
* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{ margin: 0; background: var(--paper); color: var(--ink); font: 14px/1.4 Inter, ui-sans-serif, system-ui, sans-serif; font-feature-settings: "tnum" 1, "cv11" 1; }}
a {{ color: inherit; text-decoration-thickness: 1px; text-underline-offset: 2px; }}
a:hover {{ background: var(--ink); color: var(--paper); text-decoration: none; }}
main {{ max-width: 1680px; margin: 0 auto; padding: 32px 16px 64px; }}
header.top {{ display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 16px; border-bottom: var(--rule); padding-bottom: 16px; margin-bottom: 24px; }}
.tabs {{ display: flex; gap: 0; }}
.tabs a {{ border: var(--rule); padding: 8px 16px; font-weight: 900; font-size: 13px; letter-spacing: .08em; text-transform: uppercase; text-decoration: none; }}
.tabs a + a {{ border-left: 0; }}
.tabs a[aria-selected="true"] {{ background: var(--ink); color: var(--paper); }}
.hint {{ font-size: 12px; margin-top: 8px; }}
.controls select, .controls input[type=search] {{ max-width: 100%; min-width: 0; }}
header.top h1 {{ overflow-wrap: anywhere; }}
#stats .stats {{ margin-top: 16px; }}
h1 {{ font-size: clamp(40px, 8vw, 96px); font-weight: 900; letter-spacing: -0.04em; line-height: .9; margin: 0; text-transform: uppercase; }}
h2 {{ font-size: 13px; font-weight: 900; letter-spacing: .08em; text-transform: uppercase; margin: 40px 0 12px; }}
h3 {{ font-size: 12px; font-weight: 900; letter-spacing: .08em; text-transform: uppercase; margin: 0 0 8px; }}
.dek {{ font-size: 18px; font-weight: 500; max-width: 60ch; margin: 12px 0 0; }}
.meta {{ font-size: 12px; margin-top: 8px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); border: var(--rule); overflow: hidden; }}
.stat {{ padding: 16px; border-right: var(--rule); border-bottom: var(--rule); margin: 0 -3px -3px 0; }}
.big {{ font-size: 44px; font-weight: 900; letter-spacing: -0.03em; line-height: 1; }}
.lbl {{ font-weight: 700; text-transform: uppercase; font-size: 11px; letter-spacing: .08em; margin-top: 8px; }}
.sub {{ font-size: 12px; margin-top: 2px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }}
.box {{ border: var(--rule); padding: 12px 16px; }}
.box.wide {{ grid-column: 1 / -1; }}
table {{ border-collapse: collapse; width: 100%; }}
.mini td {{ padding: 3px 0; vertical-align: top; }}
.mini td + td {{ padding-left: 12px; }}
.num {{ text-align: right; white-space: nowrap; }}
.muted {{ font-size: 12px; overflow-wrap: anywhere; }}
.box {{ min-width: 0; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; border: var(--rule); border-bottom: 0; padding: 12px; }}
.controls input[type=search], .controls select {{ font: inherit; border: 2px solid var(--ink); border-radius: 0; background: var(--paper); color: var(--ink); padding: 6px 8px; min-height: 36px; }}
.controls input[type=search] {{ flex: 1 1 240px; }}
.controls label {{ display: inline-flex; gap: 6px; align-items: center; font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; cursor: pointer; }}
.controls input[type=checkbox] {{ accent-color: #000; width: 16px; height: 16px; }}
.count {{ margin-left: auto; font-weight: 900; font-size: 13px; }}
.scroll {{ overflow-x: auto; border: var(--rule); }}
.list {{ min-width: 1760px; }}
.list th {{ position: sticky; top: 0; background: var(--ink); color: var(--paper); text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; padding: 8px; cursor: pointer; user-select: none; white-space: nowrap; }}
.list th[aria-sort=ascending]::after {{ content: " ↑"; }}
.list th[aria-sort=descending]::after {{ content: " ↓"; }}
.list td {{ padding: 8px; vertical-align: top; border-bottom: var(--hair); }}
.list tr:last-child td {{ border-bottom: 0; }}
.list tr.neg td {{ font-style: italic; }}
.film a, .film {{ font-weight: 700; }}
.yr, .by {{ display: block; font-size: 12px; font-weight: 400; font-style: normal; }}
.src {{ min-width: 220px; max-width: 320px; }}
.chain {{ font-style: italic; }}
.samefilm {{ display: block; font-size: 11px; font-weight: 500; font-style: normal; margin-bottom: 2px; }}
tr.same td {{ border-top: 0; }}
.flag {{ display: inline-block; border: 1px dashed var(--ink); padding: 0 4px; margin: 0 4px 4px 0; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; font-style: normal; }}
sup {{ font-weight: 700; }}
.film {{ min-width: 240px; max-width: 320px; }}
.syn {{ display: block; font-size: 12px; font-weight: 400; margin-top: 6px; }}
.via {{ font-style: italic; white-space: nowrap; }}
.links {{ display: block; font-size: 11px; font-weight: 500; margin-top: 4px; }}
.note {{ max-width: 340px; font-size: 12px; }}
.seeds {{ font-size: 11px; white-space: nowrap; }}
.tag {{ display: inline-block; border: 2px solid var(--ink); padding: 0 6px; font-size: 11px; font-weight: 700; font-style: normal; text-transform: uppercase; letter-spacing: .04em; white-space: nowrap; }}
.tag.neg {{ background: var(--ink); color: var(--paper); }}
.tag.empty {{ border-style: dashed; }}
.miss {{ font-size: 12px; text-decoration: line-through; }}
footer {{ margin-top: 40px; border-top: var(--rule); padding-top: 12px; font-size: 12px; }}
@media (max-width: 640px) {{ .count {{ margin-left: 0; width: 100%; }} .controls select {{ flex: 1 1 140px; }} }}
</style>
</head>
<body>
<main>
<header class="top">
<h1>Longhorns {version}</h1>
<nav class="tabs" role="tablist">
<a href="#list" role="tab" data-tab="list">Articles</a>
<a href="#stats" role="tab" data-tab="stats">Statistics</a>
</nav>
</header>

<section id="list" class="tab">
<div class="controls">
<input type="search" placeholder="Filter: film, headline, author, outlet, note…" aria-label="Filter rows">
<select data-view aria-label="Which rows">
<option value="default" selected>journalism + local / regional / alt-weekly + short fiction (no books or podcasts)</option>
<option value="all">all rows</option>
<option value="journalism">journalism only</option>
<option value="local">local / regional / alt-weekly only</option>
<option value="negative">negatives: books, fiction, podcasts</option>
<option value="unsourced">unsourced</option>
</select>
{f_type}
{f_scope}
{f_medium}
{f_seed}
<label><input type="checkbox" data-toggle="review"> review queue</label>
<span class="count" aria-live="polite"></span>
</div>
<div class="scroll">
<table class="list">
<thead><tr>
<th data-key="film">Film</th><th data-key="src">Source</th><th data-key="pub">Publication</th>
<th data-key="type">Origin</th><th data-key="credited">Credited</th><th data-key="scope">Scope</th><th data-key="fidelity">Fidelity</th>
<th data-key="rating" data-num>IMDb</th><th data-key="votes" data-num>IMDb votes</th><th data-key="rt" data-num>RT critics</th><th data-key="reviews" data-num>RT reviews</th><th data-key="gross" data-num>Box office</th>
<th data-key="arch">Archive</th><th data-key="seeds">Seeds</th><th data-key="note">Review flags · classifier note</th>
</tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>
<p class="hint">Click a column heading to sort; click again to reverse. * = article URL found by web search on the outlet's own domain. Inverted tags and italic rows are negatives (book, short fiction or podcast origin).</p>
</section>

<section id="stats" class="tab">
<p class="dek">Every film or series named in the seed lists, linked to Wikipedia, Wikidata, IMDb and Rotten Tomatoes, with an archived copy of the source. A row counts as journalism by its origin, the first publication in the chain, whatever the credits name; books, short fiction and podcasts are kept as labeled negatives.</p>
<p class="meta">{version} · generated {today} · {n} rows, films and TV · seed lists only, no discovery yet</p>

<div class="stats">{stats}</div>

<div class="grid">
{b_type}
{b_credited}
{b_scope}
{b_medium}
<section class="box wide"><h3>Seeds</h3><table class="mini">{seed_rows}</table></section>
</div>

<footer>
Sources: Wikipedia list and category, Longform, Longreads, The Electric Typewriter, The Hollywood Reporter, IMDb (a list and a poll), Texas Monthly; IMDb ratings from the public dataset; Rotten Tomatoes critic scores (Tomatometer) from each title's RT page, OMDb as fallback; box office from Wikidata P2142 (worldwide unless noted), else OMDb (domestic).
Synopses come from IMDb (via OMDb), else Rotten Tomatoes, else are generated by Haiku from the Wikipedia lead, else from the source article; each is labeled with its source.
Scope comes from <a href="data/publications.csv">data/publications.csv</a>; medium and year from Wikidata; fidelity from credit wording (IMDb credits when saved, else Wikipedia), the model only downgrading. Rows are unreviewed.
{eval_line}
Data {version}: <a href="data/adaptations.csv">adaptations.csv</a> · <a href="data/publications.csv">publications.csv</a> · <a href="data/gold.csv">gold.csv</a> · <a href="data/eval.json">eval.json</a> · <a href="data/CHANGELOG.md">CHANGELOG</a> (also under <code>/data/{version}/</code>), and <code>script#data</code> on this page.
</footer>
</section>
</main>
<script type="application/json" id="data">{data}</script>
<script>
(() => {{
  const tbody = document.querySelector(".list tbody");
  const rows = [...tbody.rows];
  const q = document.querySelector(".controls input[type=search]");
  const selects = [...document.querySelectorAll("select[data-filter]")];
  const toggles = [...document.querySelectorAll("[data-toggle]")];
  const view = document.querySelector("select[data-view]");
  const count = document.querySelector(".count");
  const local = new Set(["local", "alt_weekly", "regional"]);
  rows.forEach(r => r._text = r.textContent.toLowerCase());
  function apply() {{
    const needle = q.value.trim().toLowerCase();
    const on = Object.fromEntries(toggles.map(t => [t.dataset.toggle, t.checked]));
    let shown = 0;
    for (const r of rows) {{
      const d = r.dataset;
      let ok = !needle || r._text.includes(needle);
      for (const s of selects) if (ok && s.value) ok = s.dataset.filter === "seeds" ? d.seeds.split(" ").includes(s.value) : d[s.dataset.filter] === s.value;
      const isLocal = local.has(d.scope) && d.neg === "0" && d.unsourced === "0";
      if (ok) ok = {{
        default: d.journalism === "1" || isLocal || d.type === "short_fiction",
        all: true,
        journalism: d.journalism === "1",
        local: isLocal,
        negative: d.neg === "1",
        unsourced: d.unsourced === "1",
      }}[view.value];
      if (ok && on.review) ok = d.review === "1";
      r.hidden = !ok;
      shown += ok;
    }}
    count.textContent = `${{shown}} of ${{rows.length}} rows`;
  }}
  [q, view, ...selects, ...toggles].forEach(el => el.addEventListener("input", apply));
  // Tabs: both sections are in the page (readable without JS); the hash picks the visible one
  const tabs = [...document.querySelectorAll("[data-tab]")];
  function showTab() {{
    const want = location.hash === "#stats" ? "stats" : "list";
    document.querySelectorAll("section.tab").forEach(s => s.hidden = s.id !== want);
    tabs.forEach(t => t.setAttribute("aria-selected", String(t.dataset.tab === want)));
  }}
  window.addEventListener("hashchange", showTab);
  showTab();
  document.querySelectorAll(".list th").forEach((th, i) => th.addEventListener("click", () => {{
    const asc = th.getAttribute("aria-sort") !== "ascending";
    document.querySelectorAll(".list th").forEach(h => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", asc ? "ascending" : "descending");
    const num = th.hasAttribute("data-num");
    const key = r => num ? parseFloat(r.dataset[th.dataset.key]) : r.cells[i].textContent.trim().toLowerCase();
    rows.sort((a, b) => {{
      const x = key(a), y = key(b);
      if (num) {{ if (isNaN(x)) return 1; if (isNaN(y)) return -1; return asc ? x - y : y - x; }}
      return asc ? x.localeCompare(y) : y.localeCompare(x);
    }});
    rows.forEach(r => tbody.appendChild(r));
  }}));
  apply();
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
