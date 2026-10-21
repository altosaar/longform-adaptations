"""Hand-set values from data/overrides.csv: any table column, matched on film_title (and
source_title or an alias, when given). Applied after collect (so links like a corrected
wikipedia_url feed the rest of the pipeline) and again last in the fidelity step (so a
hand-set Disputed survives the credit-wording pass). A human's value always wins."""

from __future__ import annotations

import csv
import logging

import db

log = logging.getLogger("overrides")
CSV_PATH = db.ROOT / "data" / "overrides.csv"


def load() -> list[dict]:
    if not CSV_PATH.exists():
        return []
    lines = [ln for ln in CSV_PATH.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    return list(csv.DictReader(lines))


def apply(con) -> int:
    applied = 0
    rows = db.rows(con)
    for o in load():
        if o["field"] not in db.NAMES:
            log.warning("overrides.csv: unknown field %r", o["field"])
            continue
        for r in rows:
            titles = {db.norm(r["source_title"]), *map(db.norm, (r["source_aliases"] or "").split("|"))}
            if db.norm(r["film_title"]) != db.norm(o["film_title"]):
                continue
            if o.get("source_title") and db.norm(o["source_title"]) not in titles:
                continue
            if o["field"] == "review_flags":
                db.add_flag(con, r["id"], o["value"])
            else:
                con.execute(f"UPDATE adaptations SET {o['field']} = ? WHERE id = ?", (o["value"], r["id"]))
            if o["field"] == "adaptation_fidelity":
                con.execute("UPDATE adaptations SET fidelity_basis = 'manual' WHERE id = ?", (r["id"],))
            applied += 1
    con.commit()
    return applied
