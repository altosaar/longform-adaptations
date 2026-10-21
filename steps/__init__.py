"""Pipeline steps. Each is `uv run python -m steps.<name>` and only fills the columns it owns."""

from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

from db import ROOT


def cli(description: str, **extra: dict) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--limit", type=int, default=None, help="only process the first N rows")
    p.add_argument("--force", action="store_true", help="overwrite non-blank values this step owns")
    for flag, kw in extra.items():
        p.add_argument(f"--{flag.replace('_', '-')}", **kw)
    a = p.parse_args()
    load_dotenv(ROOT / ".env")  # ANTHROPIC_API_KEY for BAML, CONTACT_EMAIL, OMDB_API_KEY
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return a
