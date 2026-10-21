"""Hand-kept TV rows from seeds/manual_tv.csv (same columns as the table). No public list
tracks series adapted from journalism, so this file is the TV seed; grow it by hand,
local and nonprofit-newsroom cases first."""

from __future__ import annotations

from pathlib import Path

from seeds.manual import read_csv

NAME = "manual_tv"
CSV = Path(__file__).with_name("manual_tv.csv")


def collect() -> list[dict]:
    return read_csv(CSV)
