"""Enrich: Wikipedia title → Wikidata QID → IMDb ID, release year and box office; IMDb
rating from the free ratings dump; Rotten Tomatoes (and a domestic box-office fallback)
from the title's own page (URL from Wikidata P1258, else an exact title+year RT search),
OMDb as fallback when OMDB_API_KEY is set. All keyless except OMDb, all cached under raw/wiki/.

Linking order per row: the seed's own Wikipedia link, else the seed's IMDb ID looked up on
Wikidata (haswbstatement:P345), else a MediaWiki search whose top hit must contain the
film title (otherwise left blank for review rather than guessed).
"""

from __future__ import annotations

import gzip
import logging
import os
import re
from urllib.parse import quote, unquote

import db
import fetch
import seeds
from seeds import parse_html
from steps import cli

log = logging.getLogger("enrich")
WP_API = "https://en.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"
USD = "http://www.wikidata.org/entity/Q4917"
RT = "https://www.rottentomatoes.com"
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
BUCKET = "wiki"


def _wp(**params) -> dict:
    r = fetch.get(WP_API, BUCKET, params={"format": "json", "formatversion": "2", **params})
    return r.json() if r.ok else {}


def _wd(**params) -> dict:
    r = fetch.get(WD_API, BUCKET, params={"format": "json", "formatversion": "2", **params})
    return r.json() if r.ok else {}


def page_title(url: str) -> str:
    return unquote(url.rsplit("/wiki/", 1)[-1]).replace("_", " ")


def page_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()',&!:")


# --- 1. find the film's Wikipedia page ---

def qid_for_imdb(tt: str) -> str:
    d = _wd(action="query", list="search", srsearch=f"haswbstatement:P345={tt}", srlimit="1")
    hits = d.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else ""


def is_tv(r: dict) -> bool:
    return (r["medium"] or "") not in ("", "film") or any(s in (r["seed"] or "") for s in seeds.TV_ORDER)


def disambiguations(titles: list[str]) -> set[str]:
    """Which of these Wikipedia titles are disambiguation pages."""
    if not titles:
        return set()
    d = _wp(action="query", prop="pageprops", ppprop="disambiguation", titles="|".join(titles[:50]))
    return {p["title"] for p in d.get("query", {}).get("pages", []) if "disambiguation" in p.get("pageprops", {})}


def search_page(film: str, year: int | None, tv: bool = False) -> str:
    """The best search hit that isn't a disambiguation page; it must contain the film title."""
    kind = "TV series" if tv else "film"
    q = f"{film} {year} {kind}" if year else f"{film} {kind}"
    d = _wp(action="query", list="search", srsearch=q, srlimit="3")
    hits = [h["title"] for h in d.get("query", {}).get("search", [])]
    skip = disambiguations(hits)
    hits = [h for h in hits if h not in skip]
    key = db.norm(film).replace(" ", "")  # 'Blackhawk Down' vs 'Black Hawk Down'
    if hits and key and key in db.norm(hits[0]).replace(" ", ""):
        return hits[0]
    return ""


def link_pages(con, rows: list[dict]) -> None:
    for r in rows:
        if r["wikipedia_url"] or not r["film_title"]:
            continue
        if r["imdb_id"]:
            qid = qid_for_imdb(r["imdb_id"])
            if qid:
                db.fill(con, r["id"], {"wikidata_qid": qid})
                r["wikidata_qid"] = qid
                continue  # the sitelink comes back with the entity below
        title = search_page(r["film_title"], r["film_year"], is_tv(r))
        if title:
            db.fill(con, r["id"], {"wikipedia_url": page_url(title)})
            r["wikipedia_url"] = page_url(title)
    con.commit()


# --- 2. Wikipedia page → QID (50 titles per call) ---

def link_qids(con, rows: list[dict]) -> None:
    # A disambiguation page (seed or search) is no film's page: search again, else leave blank
    linked = [r for r in rows if r["wikipedia_url"]]
    titles = list({page_title(r["wikipedia_url"]) for r in linked})
    bad = set().union(*(disambiguations(titles[i : i + 50]) for i in range(0, len(titles), 50)))
    for r in linked:
        if page_title(r["wikipedia_url"]) in bad:
            title = search_page(r["film_title"], r["film_year"], is_tv(r))
            r["wikipedia_url"], r["wikidata_qid"] = (page_url(title) if title else None), None
            con.execute("UPDATE adaptations SET wikipedia_url = ?, wikidata_qid = NULL WHERE id = ?",
                        (r["wikipedia_url"], r["id"]))
            log.info("%s: %s was a disambiguation page; now %s", r["film_title"], title or "(none)", r["wikipedia_url"])
    need = [r for r in rows if r["wikipedia_url"] and not r["wikidata_qid"]]
    by_title: dict[str, list[dict]] = {}
    for r in need:
        by_title.setdefault(page_title(r["wikipedia_url"]), []).append(r)
    titles = list(by_title)
    for i in range(0, len(titles), 50):
        d = _wp(action="query", prop="pageprops", ppprop="wikibase_item", redirects="1",
                titles="|".join(titles[i : i + 50]))
        q = d.get("query", {})
        alias = {x["to"]: x["from"] for x in q.get("redirects", []) + q.get("normalized", [])}
        for p in q.get("pages", []):
            qid = p.get("pageprops", {}).get("wikibase_item")
            src = p["title"]
            while src not in by_title and src in alias:
                src = alias[src]
            for r in by_title.get(src, []) if qid else []:
                db.fill(con, r["id"], {"wikidata_qid": qid})
                r["wikidata_qid"] = qid
    con.commit()


# --- 3. QID → IMDb ID, year, box office, sitelink (50 entities per call) ---

def _claims(entity: dict, prop: str) -> list[dict]:
    return [c for c in entity.get("claims", {}).get(prop, []) if c.get("mainsnak", {}).get("snaktype") == "value"]


def release_year(entity: dict) -> int | None:
    """Earliest release date (P577); for a series without one, its start time (P580)."""
    for prop in ("P577", "P580"):
        years = []
        for c in _claims(entity, prop):
            m = re.match(r"[+-](\d{4})", c["mainsnak"]["datavalue"]["value"].get("time", ""))
            if m:
                years.append(int(m.group(1)))
        if years:
            return min(years)
    return None


def box_office(entity: dict) -> int | None:
    """P2142 in USD: the value without a P3005 (valid in place) qualifier, else the largest."""
    usd = [c for c in _claims(entity, "P2142") if c["mainsnak"]["datavalue"]["value"].get("unit") == USD]
    if not usd:
        return None
    unqualified = [c for c in usd if "P3005" not in c.get("qualifiers", {})]
    pool = unqualified or usd
    return max(int(float(c["mainsnak"]["datavalue"]["value"]["amount"])) for c in pool)


def _labels(qids: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = sorted(qids)
    for i in range(0, len(ids), 50):
        d = _wd(action="wbgetentities", ids="|".join(ids[i : i + 50]), props="labels", languages="en")
        for q, e in d.get("entities", {}).items():
            out[q] = e.get("labels", {}).get("en", {}).get("value", "")
    return out


# P31 (instance of) class label → medium. Most specific first: a TV film is also a "film".
_MEDIUM_RULES = [
    (re.compile(r"television film|tv film|made-for-tv|television movie"), "tv_movie"),
    (re.compile(r"documentary (television|tv|web)? ?series|docuseries|docudrama series"), "docuseries"),
    (re.compile(r"television (series|miniseries|program|programme|special)|miniseries|web series|tv series"),
     "scripted_series"),
    (re.compile(r"\bfilm\b"), "film"),
]


def medium_from_p31(labels: list[str]) -> str | None:
    for rx, medium in _MEDIUM_RULES:
        if any(rx.search(lbl) for lbl in labels):
            return medium
    return None


def link_entities(con, rows: list[dict]) -> None:
    by_qid: dict[str, list[dict]] = {}
    for r in rows:
        if r["wikidata_qid"]:
            by_qid.setdefault(r["wikidata_qid"], []).append(r)
    qids = list(by_qid)
    entities: dict[str, dict] = {}
    for i in range(0, len(qids), 50):
        d = _wd(action="wbgetentities", ids="|".join(qids[i : i + 50]), props="claims|sitelinks",
                sitefilter="enwiki")
        entities |= d.get("entities", {})
    classes = {c["mainsnak"]["datavalue"]["value"]["id"] for e in entities.values()
               for p in ("P31", "P136") for c in _claims(e, p)}
    class_label = {q: v.lower() for q, v in _labels(classes).items()}
    for qid, e in entities.items():
        imdb = next((c["mainsnak"]["datavalue"]["value"] for c in _claims(e, "P345")
                     if str(c["mainsnak"]["datavalue"]["value"]).startswith("tt")), None)
        gross = box_office(e)
        enwiki = e.get("sitelinks", {}).get("enwiki", {}).get("title")
        rt_id = next((c["mainsnak"]["datavalue"]["value"] for c in _claims(e, "P1258")), None)
        values = {
            "rt_url": f"{RT}/{rt_id.strip('/')}" if rt_id else None,
            "imdb_id": imdb,
            "film_year": release_year(e),
            "box_office_usd": gross,
            "box_office_note": "worldwide" if gross else None,
            "wikipedia_url": page_url(enwiki) if enwiki else None,
        }
        # Addendum 2 #3: medium and year are Wikidata's call, not a seed's or the model's.
        # Exception: when the only page is the podcast's (the show has none), the item
        # describes the podcast, not the screen work, so it sets neither.
        kinds = [class_label.get(c["mainsnak"]["datavalue"]["value"]["id"], "") for c in _claims(e, "P31")]
        genres = [class_label.get(c["mainsnak"]["datavalue"]["value"]["id"], "") for c in _claims(e, "P136")]
        year = values.pop("film_year")
        if any("podcast" in k for k in kinds):
            authoritative = {}
        else:
            medium = medium_from_p31(kinds)
            if medium == "scripted_series" and any("documentary" in g for g in genres):
                medium = "docuseries"
            authoritative = {k: v for k, v in (("film_year", year), ("medium", medium)) if v}
        for r in by_qid.get(qid, []):
            db.fill(con, r["id"], values)
            if authoritative:
                con.execute(f"UPDATE adaptations SET {', '.join(f'{k} = ?' for k in authoritative)} WHERE id = ?",
                            [*authoritative.values(), r["id"]])
            r.update({k: (r[k] or v) for k, v in values.items()} | authoritative)
    con.commit()


def link_directors(con, rows: list[dict]) -> None:
    """Director names from Wikidata (P57, in the item's order), else OMDb's Director field."""
    todo = [r for r in rows if not r["director"]]
    qids = sorted({r["wikidata_qid"] for r in todo if r["wikidata_qid"]})
    people: dict[str, list[str]] = {}
    for i in range(0, len(qids), 50):
        d = _wd(action="wbgetentities", ids="|".join(qids[i : i + 50]), props="claims")
        for q, e in d.get("entities", {}).items():
            people[q] = [c["mainsnak"]["datavalue"]["value"]["id"] for c in _claims(e, "P57")
                         if c.get("rank") != "deprecated"]
    name = _labels({p for ps in people.values() for p in ps})
    for r in todo:
        names = [name[p] for p in people.get(r["wikidata_qid"], []) if name.get(p)]
        if not names:
            omdb_names = omdb(r["imdb_id"]).get("Director") or ""
            names = [n.strip() for n in omdb_names.split(",") if n.strip() and omdb_names != "N/A"]
        if names:
            db.fill(con, r["id"], {"director": ", ".join(names)})
    con.commit()


# --- 4. IMDb rating from the daily dump ---

def link_ratings(con, rows: list[dict], force: bool) -> None:
    want = {r["imdb_id"] for r in rows
            if r["imdb_id"] and (force or r["imdb_rating"] is None or r["imdb_votes"] is None)}
    if not want:
        return
    path = fetch.download(RATINGS_URL, fetch.RAW / "imdb" / "title.ratings.tsv.gz")
    ratings = {}
    with gzip.open(path, "rt") as f:
        next(f)
        for line in f:
            tt, avg, votes = line.rstrip("\n").split("\t")
            if tt in want:
                ratings[tt] = (float(avg), int(votes))
    for r in rows:
        if r["imdb_id"] in ratings:
            avg, votes = ratings[r["imdb_id"]]
            db.fill(con, r["id"], {"imdb_rating": avg, "imdb_votes": votes}, force=force)
    con.commit()
    log.info("IMDb ratings: %d of %d IDs found in the dump", len(ratings), len(want))


# --- 5. Rotten Tomatoes: page URL (Wikidata P1258, else RT search), Tomatometer, synopsis ---

def rt_page(url: str) -> dict:
    """Tomatometer (critics score, 0-100), its review count, and synopsis from a cached RT page."""
    r = fetch.get(url, "rt")
    if r.status == 404:
        return {"missing": True}
    if not r.ok:
        return {}
    block = re.search(r'"criticsScore":\{([^{}]*)\}', r.text)
    score = re.search(r'"score":"(\d{1,3})"', block.group(1)) if block else None
    reviews = re.search(r'"reviewCount":(\d+)', block.group(1)) if block else None
    node = parse_html(r.text).css_first('[data-qa="synopsis-value"]')
    return {"score": int(score.group(1)) if score else None,
            "reviews": int(reviews.group(1)) if reviews else None,
            "synopsis": seeds.clean(node.text()) if node else ""}


def rt_search(title: str, year: int | None, tv: bool) -> str:
    """An RT search hit with the same title and (when we know it) the same year, ±1."""
    r = fetch.get(f"{RT}/search", "rt", params={"search": title})
    if not r.ok:
        return ""
    kind = "tvSeries" if tv else "movie"
    for row in parse_html(r.text).css(f"search-page-result[type={kind}] search-page-media-row"):
        a = row.css_first("a[data-qa=info-name]")
        if a is None or db.norm(a.text()) != db.norm(title):
            continue
        attrs = row.attributes
        hit_year = seeds.year_of(" ".join(attrs.get(k) or "" for k in (
            "release-year", "releaseyear", "start-year", "startyear")))
        # Same-title remakes are common: without both years to compare, don't claim a match
        if not (year and hit_year) or abs(hit_year - year) > 1:
            continue
        return (a.attributes.get("href") or "").split("?")[0]
    return ""


def link_rt(con, rows: list[dict]) -> None:
    for r in rows:
        if not r["film_title"]:
            continue
        url, page = r["rt_url"], {}
        if url:
            page = rt_page(url)
        if not url or page.get("missing"):  # no Wikidata ID, or a dead one: search RT instead
            url = rt_search(r["film_title"], r["film_year"], is_tv(r))
            page = rt_page(url) if url else {}
        if page.get("missing"):
            url = ""
        # rt_url is ours to correct: a dead link isn't data worth keeping
        con.execute("UPDATE adaptations SET rt_url = ? WHERE id = ?", (url or None, r["id"]))
        if page.get("score") is not None:
            # The page is the source of truth for its own score (OMDb's copy can lag), so it
            # overwrites; OMDb fills only titles whose page shows no Tomatometer.
            con.execute("UPDATE adaptations SET rt_score = ?, rt_reviews = ? WHERE id = ?",
                        (page["score"], page.get("reviews"), r["id"]))
    con.commit()


# --- 6. OMDb: Tomatometer fallback, domestic box office fallback (and IMDb's plot line) ---

def omdb(imdb_id: str | None) -> dict:
    """OMDb's record for an IMDb ID (cached); {} without a key or on a miss."""
    key = os.environ.get("OMDB_API_KEY", "").strip()
    if not key or not imdb_id:
        return {}
    resp = fetch.get("https://www.omdbapi.com/", "omdb", params={"i": imdb_id, "apikey": key})
    d = resp.json() if resp.ok else {}
    return d if d.get("Response") == "True" else {}


def link_imdb_by_title(con, rows: list[dict]) -> None:
    """Last resort for rows no Wikipedia page links: OMDb's exact title + year lookup,
    accepted only when the returned title matches ours exactly. No year, no match:
    same-title films are common."""
    for r in rows:
        if r["imdb_id"] or not r["film_title"] or not r["film_year"] or not os.environ.get("OMDB_API_KEY"):
            continue
        params = {"t": r["film_title"], "y": str(r["film_year"]), "apikey": os.environ["OMDB_API_KEY"]}
        resp = fetch.get("https://www.omdbapi.com/", "omdb", params=params)
        d = resp.json() if resp.ok else {}
        if d.get("Response") == "True" and db.norm(d.get("Title")) == db.norm(r["film_title"]):
            db.fill(con, r["id"], {"imdb_id": d["imdbID"], "film_year": seeds.year_of(d.get("Year"))})
            log.info("%s: IMDb %s by exact OMDb title match", r["film_title"], d["imdbID"])
    con.commit()


def link_omdb(con, rows: list[dict]) -> None:
    if not os.environ.get("OMDB_API_KEY", "").strip():
        log.info("OMDB_API_KEY not set: skipping the Tomatometer fallback and the domestic box-office fallback")
        return
    for r in rows:
        if r["rt_score"] is not None and r["box_office_usd"] is not None:
            continue
        d = omdb(r["imdb_id"])
        values: dict = {}
        rt = next((x["Value"] for x in d.get("Ratings", []) if x.get("Source") == "Rotten Tomatoes"), None)
        if rt and rt.rstrip("%").isdigit():
            values["rt_score"] = int(rt.rstrip("%"))
        # series: OMDb has no box office ("N/A"), which leaves the column blank
        bo = re.sub(r"[^\d]", "", d.get("BoxOffice") or "")
        if bo and r["box_office_usd"] is None:
            values |= {"box_office_usd": int(bo), "box_office_note": "domestic"}
        db.fill(con, r["id"], values)
    con.commit()


def main() -> None:
    a = cli(__doc__)
    con = db.connect()
    rows = db.rows(con, limit=a.limit)
    link_pages(con, rows)
    link_qids(con, rows)
    link_entities(con, rows)
    link_imdb_by_title(con, db.rows(con, limit=a.limit))
    link_directors(con, db.rows(con, limit=a.limit))
    rows = db.rows(con, limit=a.limit)
    link_ratings(con, rows, a.force)
    link_rt(con, db.rows(con, limit=a.limit))
    link_omdb(con, db.rows(con, limit=a.limit))
    stats = con.execute("""SELECT count(*), count(wikipedia_url), count(wikidata_qid), count(imdb_id),
        count(imdb_rating), count(box_office_usd), count(film_year), count(rt_url), count(rt_score)
        FROM adaptations""").fetchone()
    log.info("rows %d · wikipedia %d · qid %d · imdb %d · rating %d · box office %d · year %d · "
             "RT page %d · Tomatometer %d", *stats)


if __name__ == "__main__":
    main()
