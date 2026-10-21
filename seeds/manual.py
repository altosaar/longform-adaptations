"""Hand-curated rows from seeds/manual.csv (same columns as the table): hand-added cases,
local-newspaper ones first. An archive_url given here always wins (archive_status=manual)."""

from __future__ import annotations

import csv
from pathlib import Path

from db import NAMES

NAME = "manual"
CSV = Path(__file__).with_name("manual.csv")


def collect() -> list[dict]:
    return read_csv(CSV)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            row = {k: (v or "").strip() for k, v in r.items() if k in NAMES and k not in ("id", "seed")}
            if not row.get("film_title") and not row.get("source_title"):
                continue
            if row.get("film_year"):
                row["film_year"] = int(row["film_year"])
            if row.get("archive_url"):
                row["archive_status"] = "manual"
            if row.get("source_url"):
                row["source_url_origin"] = "manual"
            out.append(row)
    return out
