"""Collect: run every seed parser (raw pages cached under raw/<seed>/) and upsert the rows."""

from __future__ import annotations

import importlib
import logging

import db
import overrides
import seeds
from steps import cli

log = logging.getLogger("collect")


def main() -> None:
    a = cli(__doc__, seed={"choices": seeds.ORDER + seeds.TV_ORDER, "action": "append", "help": "only these seeds"},
            tv={"action": "store_true", "help": "run the TV seeds instead of the film seeds"})
    con = db.connect()
    for name in a.seed or (seeds.TV_ORDER if a.tv else seeds.ORDER):
        try:
            mod = importlib.import_module(f"seeds.{name}")
            rows = mod.collect()
        except Exception:
            log.exception("seed %s failed; skipping it", name)
            continue
        rows = rows[: a.limit] if a.limit else rows
        for r in rows:
            db.upsert(con, {**r, "seed": name, "status": "produced"}, force=a.force)
        # Seed hygiene (Addendum 2 #7): rows a parser rejects are removed and stay out
        dropped = sum(db.exclude(con, db.row_id(r["film_title"], r.get("source_title")), name, reason)
                      for r, reason in getattr(mod, "EXCLUDED", []))
        if dropped:
            log.info("%-20s removed %d rows its hygiene rules exclude", name, dropped)
        con.commit()
        log.info("%-20s %4d rows", name, len(rows))
    applied = overrides.apply(con)
    if applied:
        log.info("applied %d hand-set values from data/overrides.csv", applied)
    total = con.execute("SELECT count(*) FROM adaptations").fetchone()[0]
    log.info("table now has %d rows", total)


if __name__ == "__main__":
    main()
