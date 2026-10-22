"""Scope: publication_scope comes from data/publications.csv (Addendum 2 #2), never the model.
The table's `kind` also settles a journalism row's subtype (a daily runs news articles, a
magazine features); the model's call stands only for columns and for weeklies/online outlets.

Rows whose outlet is in the table get its scope and canonical name. Rows whose outlet isn't
get no scope: the classifier's guess (scope_suggestion) goes to data/outlet_queue.csv, and the
row is flagged, until a human adds the outlet to the table and re-runs this step.

A blank or coarser source date (2003, 2003-03) is filled from a dated article URL
(nytimes.com/2003/03/30/magazine/…) that agrees with it.
data/overrides.csv is applied first, so a hand-set outlet gets its scope in the same run.
"""

from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from urllib.parse import urlsplit

import db
import overrides
import publications
from steps import cli

log = logging.getLogger("scope")
QUEUE = db.ROOT / "data" / "outlet_queue.csv"
FLAG = "outlet-not-in-table"
MISSING = "outlet-missing"
_PLACEHOLDERS = {"unknown", "none", "n a", "not stated", "unspecified"}
_URL_DATE = re.compile(r"/((?:19|20)\d\d)/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/")


def url_date(url: str | None) -> str | None:
    m = _URL_DATE.search(urlsplit(url).path) if url else None
    return "-".join(m.groups()) if m else None


def subtype(model_type: str, kind: str) -> str:
    """Journalism subtype from the outlet's kind; the model's call stands only for a column or
    essay, and for weeklies and online outlets, where both news and features run."""
    if model_type == "column_or_essay":
        return model_type
    return {"daily": "news_article", "wire": "wire_or_brief", "magazine": "magazine_feature",
            "trade": "news_article"}.get(kind, model_type)


def main() -> None:
    cli(__doc__)
    con = db.connect()
    overrides.apply(con)
    known = unknown = dated = 0
    queue: dict[str, dict] = defaultdict(lambda: {"rows": 0, "films": [], "suggestions": defaultdict(int)})
    for r in db.rows(con):
        if r["reviewed"]:
            continue
        d = url_date(r["source_url"])
        if d and (not r["source_date"] or (len(r["source_date"]) < len(d) and d.startswith(r["source_date"]))):
            con.execute("UPDATE adaptations SET source_date = ? WHERE id = ?", (d, r["id"]))
            dated += 1
        pub = publications.lookup(r["publication"], r["publication_normalized"])
        db.drop_flag(con, r["id"], FLAG)
        db.drop_flag(con, r["id"], MISSING)
        if pub:
            con.execute("UPDATE adaptations SET publication_scope = ?, publication_normalized = ? WHERE id = ?",
                        (pub["scope"], pub["canonical_name"], r["id"]))
            # The table also knows what kind of outlet it is, so it settles news vs feature
            if r["origin_type"] in db.JOURNALISM:
                t = subtype(r["origin_type"], pub["kind"])
                both = r["source_type"] == r["origin_type"]
                con.execute("UPDATE adaptations SET origin_type = ? WHERE id = ?", (t, r["id"]))
                if both:
                    con.execute("UPDATE adaptations SET source_type = ? WHERE id = ?", (t, r["id"]))
            known += 1
            continue
        con.execute("UPDATE adaptations SET publication_scope = NULL WHERE id = ?", (r["id"],))
        name = next((n for n in (r["publication"], r["publication_normalized"])
                     if n and publications.key(n) not in _PLACEHOLDERS), None)
        if r["origin_type"] in db.JOURNALISM and not name:
            db.add_flag(con, r["id"], MISSING)
        if name and r["origin_type"] in db.JOURNALISM:  # only journalism outlets matter for scope
            q = queue[name]
            q["rows"] += 1
            q["films"].append(r["film_title"])
            q["suggestions"][r["scope_suggestion"] or "?"] += 1
            db.add_flag(con, r["id"], FLAG)
            unknown += 1
    con.commit()
    with QUEUE.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["outlet", "rows", "model_suggestion", "films"])
        for name, q in sorted(queue.items(), key=lambda kv: (-kv[1]["rows"], kv[0])):
            best = max(q["suggestions"].items(), key=lambda kv: kv[1])[0]
            w.writerow([name, q["rows"], best, " | ".join(q["films"][:5])])
    log.info("scope from the table for %d rows; %d journalism rows wait on %d outlets (%s); %d dates from URLs",
             known, unknown, len(queue), QUEUE.relative_to(db.ROOT), dated)


if __name__ == "__main__":
    main()
