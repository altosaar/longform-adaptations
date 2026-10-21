"""Classify: one BAML ClassifyAdaptation call (Haiku) per row; typed output into the table.
Uses the archived excerpt in raw/articles/<id>.txt when `archive --excerpts` saved one.
Run `just test` (the hand-labeled BAML tests) before a full pass."""

from __future__ import annotations

import asyncio
import logging
import re

import db
import publications
from steps import cli
from steps.archive import ARTICLES

log = logging.getLogger("classify")


def excerpt_for(rid: str) -> str | None:
    p = ARTICLES / f"{rid}.txt"
    return p.read_text()[:1500] if p.exists() else None


async def _classify_all(rows: list[dict], workers: int) -> list[tuple[dict, object]]:
    from baml_sdk import ClassifyAdaptation_async

    sem = asyncio.Semaphore(workers)

    async def one(r: dict):
        async with sem:
            try:
                c = await ClassifyAdaptation_async(
                    r["film_title"] or "(unknown film)", r["film_year"], r["source_title"] or "(title unknown)",
                    r["source_author"], r["publication"], r["source_date"], excerpt_for(r["id"]),
                    r["seed_context"])
                return r, c
            except Exception as exc:
                log.warning("classify failed for %s: %s", r["film_title"], str(exc)[:200])
                return r, None

    return await asyncio.gather(*(one(r) for r in rows))


# Addendum 2 #6: these phrases route a row to review; they never change its labels
_UNSURE = re.compile(r"could not verify|couldn't verify|cannot verify|can't verify|unable to verify|may be fabricated",
                     re.I)
FLAG = "model-could-not-verify"
# The model's own judgement of the row, always replaced by the latest pass
MODEL_OWNED = ("scope_suggestion", "fidelity_model", "classify_confidence", "classify_notes")


def origin_from_chain(chain: str | None, source_type: str, model_origin: str, publication: str | None) -> str:
    """origin_type from the first element of source_chain (Addendum 2 #1); the model's own
    origin_type only where the chain's first word doesn't say."""
    first = re.split(r"\s*(?:→|->)\s*", (chain or "").strip().lower())[0]
    if "podcast" in first:
        return "podcast_or_other"
    if re.search(r"\b(book|memoir|novel|biography)\b", first):
        return "book"
    if re.search(r"\b(short story|story|fiction)\b", first):
        return "short_fiction"
    if re.search(r"\b(article|series|feature|column|essay|report|investigation|newspaper|magazine|piece)\b", first):
        for t in (source_type, model_origin):
            if t in db.JOURNALISM:
                return t
        pub = publications.lookup(publication)
        kind = pub["kind"] if pub else ""
        return {"daily": "news_article", "wire": "wire_or_brief"}.get(kind, "magazine_feature")
    return model_origin


def main() -> None:
    a = cli(__doc__, workers={"type": int, "default": 8},
            refresh={"action": "store_true", "help": "re-ask every row; seed and human values still win"})
    con = db.connect()
    # Rows missing any field this step owns are (re-)asked; fill semantics keep every value
    # already set (by a seed, a human, or an earlier pass)
    rows = db.rows(con, "1" if a.force or a.refresh else
                   "source_type IS NULL OR origin_type IS NULL OR credited_source_type IS NULL OR source_chain IS NULL",
                   limit=a.limit)
    log.info("classifying %d rows", len(rows))
    ok = 0
    for r, c in asyncio.run(_classify_all(rows, a.workers)):
        if c is None:
            continue
        source_type = r["source_type"] if r["source_type"] and not a.force else c.source_type.value.lower()
        chain = r["source_chain"] if r["source_chain"] and not a.force else c.source_chain
        known = publications.lookup(r["publication"], r["publication_normalized"], c.publication_normalized)
        values = {
            "source_type": source_type,
            "credited_source_type": c.credited_source_type.value.lower(),
            "origin_type": origin_from_chain(chain, source_type, c.origin_type.value.lower(),
                                             r["publication"] or c.publication_normalized),
            "origin_title": (c.origin_title if c.origin_title and db.norm(c.origin_title) != db.norm(r["source_title"])
                             else None),
            "source_chain": chain,
            "publication_normalized": known["canonical_name"] if known else c.publication_normalized,
            # Scope is the table's call; the model's answer is only a suggestion for outlets
            # the table doesn't have yet (the scope step routes those to review)
            "scope_suggestion": None if known else c.publication_scope.value.lower(),
            "fidelity_model": c.fidelity.value.lower(),
            "classify_confidence": round(max(0.0, min(1.0, c.confidence)), 2),
            "classify_notes": c.notes,
        }
        model = {k: values.pop(k) for k in MODEL_OWNED}
        db.fill(con, r["id"], values, force=a.force)
        con.execute(f"UPDATE adaptations SET {', '.join(f'{k} = ?' for k in model)} WHERE id = ?",
                    [*model.values(), r["id"]])
        db.drop_flag(con, r["id"], FLAG)
        if _UNSURE.search(c.notes or ""):
            db.add_flag(con, r["id"], FLAG)
        ok += 1
    con.commit()
    log.info("classified %d of %d rows", ok, len(rows))


if __name__ == "__main__":
    main()
