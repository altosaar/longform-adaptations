"""Dedupe: the same (film, source) named differently by different lists becomes one row.

Addendum 2 #4: a row is one (film, origin work). Rows are grouped by film (same Wikidata
QID, or same normalized title within a year); two rows of a film merge when they name the
same origin work: same normalized title, a loose title match (similar, contained, most
words shared), a blank title, or the same author with the same journalism origin_type.
The survivor (a hand-curated row, else the one naming the origin) keeps the other titles
in source_aliases and unions the seeds. A film with two genuinely different sources (Argo:
Bearman's article, Mendez's memoir) keeps two rows that share wikidata_qid.

Ids are then re-keyed to sha1(wikidata_qid | origin title); every old id stays in `aliases`
(and the id-keyed logs and excerpt files follow), so a later collect fills the right row.
"""

from __future__ import annotations

import hashlib
import logging
import re
from difflib import SequenceMatcher

import db
import seeds
from steps import cli
from steps.archive import ARTICLES

log = logging.getLogger("dedupe")
_STOP = {"the", "a", "an", "of", "in", "and", "to", "for", "on", "at", "by", "how", "who", "is", "was"}


def same_film(a: dict, b: dict) -> bool:
    if a["wikidata_qid"] and b["wikidata_qid"]:
        return a["wikidata_qid"] == b["wikidata_qid"]
    if db.norm(a["film_title"]) != db.norm(b["film_title"]) or not db.norm(a["film_title"]):
        return False
    return not (a["film_year"] and b["film_year"]) or abs(a["film_year"] - b["film_year"]) <= 1


def _words(s: str) -> set[str]:
    return {w for w in db.norm(s).split() if w not in _STOP}


def _surnames(s: str | None) -> set[str]:
    names = re.split(r",|&|\band\b", s or "")
    return {db.norm(n).split()[-1] for n in names if db.norm(n)}


def work_key(r: dict) -> str:
    """The work's normalized title. After the origin step source_title *is* the print origin
    (set by a lookup or a search). The classifier's own origin_title guess is deliberately not
    used: a merge is a decision, and the model doesn't make those."""
    return db.norm(r["source_title"])


def row_key(r: dict) -> str:
    """Addendum 2 #4: (film_qid, origin work). Rows without a QID keep the v0 key."""
    if not r["wikidata_qid"]:
        return r["id"]
    return hashlib.sha1(f"{r['wikidata_qid']}|{work_key(r)}".encode()).hexdigest()


def same_source(a: dict, b: dict) -> bool:
    if work_key(a) and work_key(a) == work_key(b):
        return True
    ta, tb = db.norm(a["source_title"]), db.norm(b["source_title"])
    if not ta or not tb:
        return True
    if ta in tb or tb in ta or SequenceMatcher(None, ta, tb).ratio() >= 0.7:
        return True
    wa, wb = _words(ta), _words(tb)
    if wa and wb and len(wa & wb) / min(len(wa), len(wb)) >= 0.6:
        return True
    # Same author, same journalism origin type: one piece under two headlines ("The Great
    # Escape" / "How the CIA Used a Fake Sci-Fi Flick"). An article and a memoir stay apart.
    return (bool(_surnames(a["source_author"]) & _surnames(b["source_author"]))
            and a["origin_type"] in db.JOURNALISM and a["origin_type"] == b["origin_type"])


_PRIORITY = ["manual", "manual_tv"] + [s for s in seeds.ORDER + seeds.TV_ORDER if not s.startswith("manual")]


def richness(r: dict) -> tuple:
    """Which row survives a merge: a hand-curated row always; then the most filled-in row."""
    seeds_of = [s for s in (r["seed"] or "").split(",") if s in _PRIORITY]
    rank = min((_PRIORITY.index(s) for s in seeds_of), default=99)
    if rank <= 1:
        return (1, 0, 0, 0)
    names_origin = r["source_type"] is not None and r["source_type"] == r["origin_type"]
    filled = sum(not db.blank(r[c]) for c in ("source_title", "source_author", "publication", "source_date",
                                               "source_url", "wikipedia_url", "imdb_id"))
    return (0, names_origin, filled, -rank)


def merge_into(con, keep: dict, other: dict) -> None:
    aliases = [a for a in (keep["source_aliases"] or "").split("|") if a]
    for t in [other["source_title"], *(other["source_aliases"] or "").split("|")]:
        not_outlet = db.norm(t) not in (db.norm(keep["publication"]), db.norm(keep["publication_normalized"]))
        if t and not_outlet and db.norm(t) != db.norm(keep["source_title"]) and db.norm(t) not in map(db.norm, aliases):
            aliases.append(t)
    keep["source_aliases"] = "|".join(aliases) or None
    con.execute("UPDATE adaptations SET source_aliases = ? WHERE id = ?", (keep["source_aliases"], keep["id"]))
    db.upsert(con, {**other, "id": keep["id"], "source_aliases": None})  # fills keep's blanks, unions seeds
    con.execute("DELETE FROM adaptations WHERE id = ?", (other["id"],))
    redirect(con, other["id"], keep["id"])


ID_TABLES = ("fill_log", "discover_log", "origin_log")


def redirect(con, old: str, new: str) -> None:
    """Every reference to a row id moves to its new id; the old id stays resolvable."""
    con.execute("INSERT OR REPLACE INTO aliases VALUES (?, ?)", (old, new))
    con.execute("UPDATE aliases SET canonical = ? WHERE canonical = ?", (new, old))
    for t in ID_TABLES:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone():
            con.execute(f"UPDATE OR IGNORE {t} SET id = ? WHERE id = ?", (new, old))
            con.execute(f"DELETE FROM {t} WHERE id = ?", (old,))
    src, dst = ARTICLES / f"{old}.txt", ARTICLES / f"{new}.txt"
    if src.exists() and not dst.exists():
        src.rename(dst)


def rekey(con) -> int:
    moved = 0
    taken = {x[0] for x in con.execute("SELECT id FROM adaptations")}
    for r in db.rows(con):
        new = row_key(r)
        if new == r["id"] or new in taken:
            continue
        con.execute("UPDATE adaptations SET id = ? WHERE id = ?", (new, r["id"]))
        redirect(con, r["id"], new)
        taken.add(new)
        moved += 1
    return moved


def main() -> None:
    cli(__doc__)
    con = db.connect()
    rows = db.rows(con)
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            if same_film(a, b) and same_source(a, b):
                parent[find(j)] = find(i)
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    merged = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        # A blank-title row can bridge two different sources of one film; split it back up
        titled = [m for m in members if m["source_title"]]
        distinct = []
        for m in titled:
            if not any(same_source(m, d) and db.norm(m["source_title"]) and db.norm(d["source_title"])
                       for d in distinct):
                distinct.append(m)
        clusters = [[d] for d in distinct] or [[]]
        for m in members:
            if m in distinct:
                continue
            home = next((c for c in clusters if c and same_source(m, c[0])), clusters[0])
            home.append(m)
        for c in clusters:
            if len(c) < 2:
                continue
            keep = max(c, key=richness)
            for other in c:
                if other is not keep:
                    merge_into(con, keep, other)
                    merged += 1
    moved = rekey(con)
    con.commit()
    total = con.execute("SELECT count(*) FROM adaptations").fetchone()[0]
    log.info("merged %d duplicate rows; re-keyed %d to (film_qid, origin work); %d rows remain", merged, moved, total)


if __name__ == "__main__":
    main()
