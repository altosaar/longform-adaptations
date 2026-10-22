"""SQLite schema and the one write rule: fill blanks, never overwrite (unless forced).

One table, `adaptations`, one row per (source work, film) pair. `aliases` remembers rows
that the dedupe step folded into another, so re-running collect doesn't resurrect them.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "longhorns.sqlite"

# (name, sqlite type). Order is the CSV/HTML column order.
COLUMNS: list[tuple[str, str]] = [
    ("id", "TEXT PRIMARY KEY"),
    ("seed", "TEXT"),
    ("film_title", "TEXT"),
    ("film_year", "INTEGER"),
    ("source_title", "TEXT"),
    ("source_author", "TEXT"),
    ("publication", "TEXT"),
    ("publication_normalized", "TEXT"),
    ("source_date", "TEXT"),
    ("source_url", "TEXT"),
    ("source_url_origin", "TEXT"),  # seed | search | manual: who supplied source_url
    ("source_language", "TEXT"),  # ISO 639-1 code of a non-English source (hr); the page links a translation
    ("archive_url", "TEXT"),
    ("archive_status", "TEXT"),
    ("wikipedia_url", "TEXT"),
    ("wikidata_qid", "TEXT"),
    ("imdb_id", "TEXT"),
    ("imdb_rating", "REAL"),
    ("imdb_votes", "INTEGER"),  # number of IMDb user ratings behind imdb_rating
    ("rt_url", "TEXT"),
    ("rt_score", "INTEGER"),  # Tomatometer (critics), 0-100
    ("rt_reviews", "INTEGER"),  # number of critic reviews behind rt_score
    ("box_office_usd", "INTEGER"),
    ("box_office_note", "TEXT"),
    ("medium", "TEXT"),  # film | scripted_series | docuseries | tv_movie
    ("director", "TEXT"),  # Wikidata P57, else OMDb; several joined by ", "
    ("source_chain", "TEXT"),  # e.g. "article → podcast → series"
    ("synopsis", "TEXT"),  # one sentence
    ("synopsis_source", "TEXT"),  # imdb | rotten_tomatoes | wikipedia_generated | article_generated
    ("source_type", "TEXT"),  # the work this row names
    # Addendum 2 #1: a row is a journalism positive iff origin_type is a journalism type
    ("origin_type", "TEXT"),  # the FIRST publication in the chain
    ("origin_title", "TEXT"),  # its headline, when it isn't source_title
    ("credited_source_type", "TEXT"),  # what the screen credits name
    ("source_aliases", "TEXT"),  # other titles seeds used for this work, pipe-separated
    ("publication_scope", "TEXT"),
    ("scope_suggestion", "TEXT"),  # Haiku's scope for an outlet not in data/publications.csv
    ("adaptation_fidelity", "TEXT"),
    ("fidelity_basis", "TEXT"),  # imdb_credits | wikipedia_wording | model | manual
    ("fidelity_model", "TEXT"),  # Haiku's opinion; may only downgrade direct → loose
    ("classify_confidence", "REAL"),
    ("classify_notes", "TEXT"),
    ("status", "TEXT DEFAULT 'produced'"),
    ("reviewed", "INTEGER DEFAULT 0"),
    ("review_flags", "TEXT"),  # why a human should look, pipe-separated
    # Not in the spec's table: the seed's own words about the row (an infobox `based_on`,
    # a gallery caption), kept so the fill step can read gaps out of it later.
    ("seed_context", "TEXT"),
]
NAMES = [c for c, _ in COLUMNS]
# Addendum 2 #1: a row is a journalism positive iff its origin_type is one of these
JOURNALISM = ("news_article", "magazine_feature", "column_or_essay", "wire_or_brief")
NEGATIVE = ("book", "short_fiction", "podcast_or_other")


def exclude(con: sqlite3.Connection, raw_id: str, seed: str, reason: str) -> bool:
    """Remove a row a seed's hygiene rule rejects, unless another seed also lists it."""
    alias = con.execute("SELECT canonical FROM aliases WHERE alias = ?", (raw_id,)).fetchone()
    rid = alias["canonical"] if alias else raw_id
    row = con.execute("SELECT seed FROM adaptations WHERE id = ?", (rid,)).fetchone()
    if row is not None and {s for s in (row["seed"] or "").split(",") if s} - {seed}:
        return False
    con.execute("DELETE FROM adaptations WHERE id = ?", (rid,))
    for i in {raw_id, rid}:
        con.execute("INSERT OR REPLACE INTO excluded VALUES (?, ?)", (i, reason))
    return row is not None


def drop_flag(con: sqlite3.Connection, rid: str, flag: str) -> None:
    old = con.execute("SELECT review_flags FROM adaptations WHERE id = ?", (rid,)).fetchone()
    flags = [f for f in ((old[0] if old else "") or "").split("|") if f and f != flag]
    con.execute("UPDATE adaptations SET review_flags = ? WHERE id = ?", ("|".join(flags) or None, rid))


def add_flag(con: sqlite3.Connection, rid: str, flag: str) -> None:
    """Append a review flag (idempotent): flags route a row to a human, they never change labels."""
    old = con.execute("SELECT review_flags FROM adaptations WHERE id = ?", (rid,)).fetchone()
    flags = [f for f in ((old[0] if old else "") or "").split("|") if f]
    if flag not in flags:
        con.execute("UPDATE adaptations SET review_flags = ? WHERE id = ?", ("|".join([*flags, flag]), rid))


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute(f"CREATE TABLE IF NOT EXISTS adaptations ({', '.join(f'{c} {t}' for c, t in COLUMNS)})")
    have = {r["name"] for r in con.execute("PRAGMA table_info(adaptations)")}
    if "source_url_via" in have and "source_url_origin" not in have:  # v0 → v0.1 rename
        con.execute("ALTER TABLE adaptations RENAME COLUMN source_url_via TO source_url_origin")
        con.execute("UPDATE adaptations SET source_url_origin = 'search' WHERE source_url_origin = 'web_search'")
        have = {r["name"] for r in con.execute("PRAGMA table_info(adaptations)")}
    for c, t in COLUMNS:  # forward-compatible: add new columns to an old file
        if c not in have:
            con.execute(f"ALTER TABLE adaptations ADD COLUMN {c} {t}")
    con.execute("CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, canonical TEXT NOT NULL)")
    # Rows a hygiene rule removed (Addendum 2 #7); collect never re-adds them
    con.execute("CREATE TABLE IF NOT EXISTS excluded (id TEXT PRIMARY KEY, reason TEXT)")
    return con


def blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


_PAREN = re.compile(r"\s*\((?:\d{4} )?(?:[\w ]+ )?(?:film|movie|tv series|miniseries)\)\s*$", re.I)


def norm(s: str | None) -> str:
    """Normalize a title for keys and matching: no accents, case, punctuation, leading article."""
    s = _PAREN.sub("", s or "")
    s = s.replace("\u2019", "'")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace("'", "").replace("\u2019", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def row_id(film_title: str | None, source_title: str | None) -> str:
    return hashlib.sha1(f"{norm(film_title)}|{norm(source_title)}".encode()).hexdigest()


def merge_seeds(a: str | None, b: str | None) -> str:
    seen: list[str] = []
    for s in f"{a or ''},{b or ''}".split(","):
        if s.strip() and s.strip() not in seen:
            seen.append(s.strip())
    return ",".join(seen)


def upsert(con: sqlite3.Connection, row: dict, force: bool = False) -> str:
    """Insert a row, or fill only the blank columns of the existing one. Returns the row id.

    `seed` is the exception: it accumulates every list that named the row.
    """
    row = {k: v for k, v in row.items() if k in NAMES}
    rid = row.get("id") or row_id(row.get("film_title"), row.get("source_title"))
    alias = con.execute("SELECT canonical FROM aliases WHERE alias = ?", (rid,)).fetchone()
    raw_id, rid = rid, (alias["canonical"] if alias else rid)
    row["id"] = rid
    if con.execute("SELECT 1 FROM excluded WHERE id IN (?, ?)", (raw_id, rid)).fetchone():
        return rid
    old = con.execute("SELECT * FROM adaptations WHERE id = ?", (rid,)).fetchone()
    if old is None:
        cols = [k for k, v in row.items() if not blank(v)]
        con.execute(
            f"INSERT INTO adaptations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [row[c] for c in cols],
        )
        return rid
    update = {"seed": merge_seeds(old["seed"], row.get("seed"))} if row.get("seed") else {}
    for k, v in row.items():
        if k in ("id", "seed") or blank(v):
            continue
        if force or blank(old[k]):
            update[k] = v
    if update:
        con.execute(
            f"UPDATE adaptations SET {', '.join(f'{k} = ?' for k in update)} WHERE id = ?",
            [*update.values(), rid],
        )
    return rid


def fill(con: sqlite3.Connection, rid: str, values: dict, force: bool = False) -> None:
    """Fill blank columns of one existing row (the enrich/archive/classify write path)."""
    upsert(con, {**values, "id": rid}, force=force)


def rows(con: sqlite3.Connection, where: str = "1", params: Iterable = (), limit: int | None = None) -> list[dict]:
    sql = f"SELECT * FROM adaptations WHERE {where} ORDER BY film_title COLLATE NOCASE, id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in con.execute(sql, list(params))]
