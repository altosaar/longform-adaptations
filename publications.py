"""The publications lookup table (data/publications.csv): canonical outlet names, aliases,
scope, kind and own web domains. Tables decide; the model only fills what isn't here."""

from __future__ import annotations

import csv
import re
from functools import cache

from db import ROOT

CSV_PATH = ROOT / "data" / "publications.csv"
ARCHIVE_DOMAINS = ("web.archive.org", "archive.org", "archive.ph", "archive.is", "archive.today", "archive.li",
                   "archive.md", "archive.vn", "archive.fo")


def key(name: str | None) -> str:
    s = (name or "").lower().replace("&", " and ").replace("’", "'").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^the ", "", s)


@cache
def table() -> dict[str, dict]:
    """Alias key → publication row (the canonical name is its own alias)."""
    lines = [ln for ln in CSV_PATH.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    out: dict[str, dict] = {}
    for row in csv.DictReader(lines):
        row["domains"] = [d.strip().lower() for d in (row.get("domains") or "").split("|") if d.strip()]
        for name in [row["canonical_name"], *(row.get("aliases") or "").split("|")]:
            if key(name):
                out.setdefault(key(name), row)
    return out


def lookup(*names: str | None) -> dict | None:
    """The first of these names (as written, or each part of 'A / B') found in the table."""
    for name in names:
        if not name:
            continue
        bare = re.sub(r"\s*\([^)]*\)", "", name)  # "Vanity Fair (reprinted by …)" → "Vanity Fair"
        for candidate in [name, bare, *re.split(r"\s+/\s+", name), *re.split(r"\s+/\s+", bare)]:
            hit = table().get(key(candidate))
            if hit:
                return hit
    return None


def own_or_archive_domain(url: str, pub: dict | None) -> bool:
    """Is this URL on the outlet's own domain (or a subdomain), or on an archive?"""
    host = (re.match(r"^https?://([^/:?#]+)", url or "") or [None, ""])[1].lower()
    if not host:
        return False
    domains = [*ARCHIVE_DOMAINS, *(pub["domains"] if pub else [])]
    return any(host == d or host.endswith("." + d) for d in domains)
