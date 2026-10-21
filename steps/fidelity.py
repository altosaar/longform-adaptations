"""Fidelity from credit wording (Addendum 2 #5). The wording decides; the model may only
downgrade direct → loose (its reasons are in classify_notes), never upgrade.

  "based on the article/book … by"      → direct
  "inspired by"                         → inspired_by
  "suggested by" / "loosely based on" /
  "based on a story by"                 → loose
  no source credit                      → inspired_by, flagged for review

Where the wording comes from (fidelity_basis):
  imdb_credits       a saved IMDb full-credits page (raw/imdb/**/*.html, from a browser:
                     IMDb blocks automated fetches)
  wikipedia_wording  else the film's Wikipedia lead sentence that names this source, then
                     its infobox `based_on`
  model              no page to read: the classifier's call (fidelity_model)
  manual             data/overrides.csv, applied last (Disputed is set only there)
"""

from __future__ import annotations

import json
import logging
import re

import db
import fetch
import overrides
import publications
from seeds import parse_html
from steps import cli
from steps.enrich import page_title

log = logging.getLogger("fidelity")
CREDITS_DIR = fetch.RAW / "imdb"
NO_CREDIT = "no-source-credit"
DOWNGRADED = "model-downgraded-fidelity"


def wording(text: str) -> str | None:
    t = text.lower()
    if "inspired by" in t:
        return "inspired_by"
    if re.search(r"suggested by|loosely (based|adapted)|based on a story by|loosely inspired", t):
        return "loose"
    if re.search(r"based on|adapted from|adaptation of", t):
        return "direct"
    return None


def source_terms(r: dict) -> list[str]:
    """Words that identify this row's source in prose: author surnames, titles, a distinctive outlet."""
    terms = [db.norm(a).split()[-1] for a in re.split(r",|&|\band\b", r["source_author"] or "") if db.norm(a)]
    for t in [r["source_title"], *(r["source_aliases"] or "").split("|")]:
        if t and len(db.norm(t)) > 6:
            terms.append(db.norm(t))
    pub = publications.lookup(r["publication"], r["publication_normalized"])
    if pub and len(db.norm(pub["canonical_name"]).split()) >= 2 and pub["canonical_name"] != "New York":
        terms.append(db.norm(pub["canonical_name"]))
    return [t for t in terms if len(t) > 3]


def mentions(text: str, terms: list[str]) -> bool:
    n = f" {db.norm(text)} "
    return any(f" {t} " in n for t in terms)


# --- IMDb full credits (saved by hand) ---

def imdb_credits() -> dict[str, str]:
    """tt-id → writer-credits text, from any saved full-credits page under raw/imdb/."""
    out: dict[str, str] = {}
    for page in CREDITS_DIR.rglob("*.html"):
        text = page.read_text(errors="replace")
        m = re.search(r"/title/(tt\d+)/fullcredits", text)
        if not m or "fullcredits" not in page.name.lower() + text[:5000].lower():
            continue
        tree = parse_html(text)
        writers = ""
        for sec in tree.css("section, table, div"):
            head = sec.css_first("h3, h4, .ipc-title__text")
            if head and re.search(r"writ", head.text(), re.I):
                writers = sec.text(separator=" ")
                break
        out[m.group(1)] = writers
    return out


def from_credits(writers: str, r: dict) -> str:
    for line in re.split(r"\n|\)\s+", writers):
        if mentions(line, source_terms(r)) and (w := wording(line)):
            return w
    return "inspired_by"  # credits exist but name no source


# --- Wikipedia wording ---

class LookupFailed(Exception):
    """Wikipedia didn't answer: leave the row as it is for the next run (never fall back)."""


def _page(url: str, **params) -> dict:
    r = fetch.get("https://en.wikipedia.org/w/api.php", "wiki", params={
        "action": "query", "redirects": "1", "titles": page_title(url), "format": "json", "formatversion": "2",
        **params})
    if not r.ok:
        raise LookupFailed(f"wikipedia {r.status}")
    pages = r.json().get("query", {}).get("pages", [])
    return pages[0] if pages else {}


def wikipedia_lead(url: str) -> str:
    return (_page(url, prop="extracts", exintro="1", explaintext="1").get("extract") or "")[:3000]


def infobox_based_on(url: str) -> str:
    page = _page(url, prop="revisions", rvprop="content", rvslots="main")
    text = (page.get("revisions") or [{}])[0].get("slots", {}).get("main", {}).get("content", "")
    m = re.search(r"\|\s*based[_ ]on\s*=(.*?)(?=\n\s*\||\n\}\})", text, re.S | re.I)
    return m.group(1) if m else ""


def from_wikipedia(r: dict) -> str | None:
    if not r["wikipedia_url"]:
        return None
    terms = source_terms(r)
    lead = wikipedia_lead(r["wikipedia_url"])
    for sentence in re.split(r"(?<=[.!?])\s+", lead):
        if mentions(sentence, terms) and (w := wording(sentence)) in ("inspired_by", "loose"):
            return w
    if mentions(re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", infobox_based_on(r["wikipedia_url"])), terms):
        return "direct"
    for sentence in re.split(r"(?<=[.!?])\s+", lead):
        if mentions(sentence, terms) and wording(sentence) == "direct":
            return "direct"
    return "inspired_by"


def main() -> None:
    cli(__doc__)
    con = db.connect()
    credits = imdb_credits()
    counts: dict[str, int] = {}
    for i, r in enumerate(db.rows(con)):
        if r["reviewed"]:
            continue
        if i % 20 == 0:
            con.commit()
        if r["imdb_id"] in credits:
            fid, basis = from_credits(credits[r["imdb_id"]], r), "imdb_credits"
        else:
            try:
                fid = from_wikipedia(r)
            except LookupFailed as exc:
                log.warning("%s: %s; left for the next run", r["film_title"], exc)
                counts["retry"] = counts.get("retry", 0) + 1
                continue
            basis = "wikipedia_wording" if fid is not None else "model"
            fid = fid if fid is not None else r["fidelity_model"]
        db.drop_flag(con, r["id"], NO_CREDIT)
        db.drop_flag(con, r["id"], DOWNGRADED)
        if basis != "model" and fid == "inspired_by" and r["origin_type"] in db.JOURNALISM:
            db.add_flag(con, r["id"], NO_CREDIT)
        if fid == "direct" and r["fidelity_model"] == "loose":  # the one move the model may make
            fid = "loose"
            db.add_flag(con, r["id"], DOWNGRADED)
        con.execute("UPDATE adaptations SET adaptation_fidelity = ?, fidelity_basis = ? WHERE id = ?",
                    (fid, basis if fid else None, r["id"]))
        counts[f"{basis}:{fid}"] = counts.get(f"{basis}:{fid}", 0) + 1
    applied = overrides.apply(con)
    con.commit()
    log.info("fidelity: %s; %d manual overrides applied", json.dumps(dict(sorted(counts.items()))), applied)


if __name__ == "__main__":
    main()
