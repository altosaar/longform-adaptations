"""Eval: per-field accuracy of the current table against data/gold.csv (Addendum 2 #6).

Gold rows match table rows on film title (spaces and punctuation ignored) and, when given,
source title or one of its aliases; a gold row without a source title takes the film's
journalism row, else its only row. Prints a table, writes eval.json (shown in the page
footer). Accuracy is reported over all gold rows and over hand-verified ones separately,
since the gold set starts out drafted, not verified.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date

import db
from steps import cli

log = logging.getLogger("eval")
GOLD = db.ROOT / "data" / "gold.csv"
OUT = db.ROOT / "eval.json"
FIELDS = ("origin_type", "publication_scope", "medium", "adaptation_fidelity", "film_year")
VERSION = "v0.1"


def tight(s: str | None) -> str:
    return db.norm(s).replace(" ", "")


def match(g: dict, rows: list[dict]) -> dict | None:
    film = [r for r in rows if tight(r["film_title"]) == tight(g["film_title"])]
    if g["source_title"]:
        want = tight(g["source_title"])
        for r in film:
            if want in {tight(r["source_title"]), *map(tight, (r["source_aliases"] or "").split("|"))}:
                return r
        return None
    return next((r for r in film if r["origin_type"] in db.JOURNALISM), film[0] if len(film) == 1 else None)


def main() -> None:
    cli(__doc__)
    con = db.connect()
    rows = db.rows(con)
    lines = [ln for ln in GOLD.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    gold = list(csv.DictReader(lines))
    stats = {f: {"n": 0, "correct": 0, "verified_n": 0, "verified_correct": 0} for f in FIELDS}
    misses, unmatched = [], []
    for g in gold:
        r = match(g, rows)
        if r is None:
            unmatched.append(f"{g['film_title']} / {g['source_title'] or '(any)'}")
            continue
        verified = bool(g["verified_by"].strip())
        for f in FIELDS:
            want = g[f].strip()
            if not want:
                continue
            got = "" if r[f] is None else str(r[f])
            ok = got == want
            s = stats[f]
            s["n"] += 1
            s["correct"] += ok
            s["verified_n"] += verified
            s["verified_correct"] += verified and ok
            if not ok:
                misses.append({"film": g["film_title"], "field": f, "gold": want, "got": got or None})
    for s in stats.values():
        s["accuracy"] = round(s["correct"] / s["n"], 3) if s["n"] else None
        s["verified_accuracy"] = round(s["verified_correct"] / s["verified_n"], 3) if s["verified_n"] else None
    result = {
        "version": VERSION, "date": date.today().isoformat(), "gold_rows": len(gold),
        "matched": len(gold) - len(unmatched), "verified": sum(1 for g in gold if g["verified_by"].strip()),
        "fields": stats, "unmatched": unmatched, "misses": misses,
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"gold {result['gold_rows']} rows · matched {result['matched']} · hand-verified {result['verified']}")
    for f, s in stats.items():
        acc = f"{s['accuracy']:.0%}" if s["accuracy"] is not None else "—"
        print(f"  {f:22} {acc:>5}  ({s['correct']}/{s['n']})")
    for u in unmatched:
        print(f"  unmatched: {u}")
    for m in misses:
        print(f"  miss: {m['film']:38.38} {m['field']:20} gold={m['gold']:16} got={m['got']}")


if __name__ == "__main__":
    main()
