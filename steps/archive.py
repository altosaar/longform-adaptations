"""Archive: find an existing snapshot of each source_url (never trigger a new capture).

Order: a manual archive_url wins → Wayback availability API, nudged to the snapshot nearest
publication → archive.ph/newest (one request per 5 s, brittle by nature) → 'none', which
puts the row in the human queue. With --excerpts, also save the first ~1,500 characters
of each Wayback snapshot to raw/articles/<id>.txt for the classifier.
"""

from __future__ import annotations

import logging
import re

import db
import fetch
from seeds import parse_html
from steps import cli

log = logging.getLogger("archive")
ARTICLES = fetch.RAW / "articles"
EXCERPT_CHARS = 1500


class LookupFailed(Exception):
    """The archive didn't answer (rate limit, timeout): leave the row for the next run."""


def wayback(url: str, date: str | None) -> str:
    """Closest Wayback snapshot to the publication date, or '' if there is none.

    The availability API is the documented route but rate-limits hard (429); the CDX API
    answers the same question (sort=closest) and is used when the first one fails.
    """
    stamp = re.sub(r"\D", "", date or "")
    params = {"url": url} | ({"timestamp": stamp} if stamp else {})
    r = fetch.get("https://archive.org/wayback/available", "wayback", params=params, retries=1)
    if r.ok:
        try:
            closest = r.json().get("archived_snapshots", {}).get("closest") or {}
            if closest.get("available") and str(closest.get("status", "200")).startswith(("2", "3")):
                return closest["url"].replace("http://web.archive.org", "https://web.archive.org")
            return ""
        except ValueError:
            pass
    cdx = {"url": url, "output": "json", "limit": "1", "fl": "timestamp,original",
           "filter": "statuscode:200"} | ({"sort": "closest", "closest": stamp} if stamp else {})
    r = fetch.get("https://web.archive.org/cdx/search/cdx", "wayback", params=cdx, retries=2)
    if not r.ok:
        raise LookupFailed(f"wayback {r.status}")
    try:
        found = r.json()[1:] if r.text.strip() else []
    except ValueError as exc:
        raise LookupFailed("wayback cdx: bad json") from exc
    return f"https://web.archive.org/web/{found[0][0]}/{found[0][1]}" if found else ""


def archive_is(url: str) -> str:
    r = fetch.get(f"https://archive.ph/newest/{url}", "archive_is", follow_redirects=False, retries=0)
    if r.status in (0, 429) or r.status >= 500:
        raise LookupFailed(f"archive.ph {r.status}")
    target = r.final_url if r.status in (301, 302, 303, 307, 308) else ""
    # A snapshot is archive.ph/<short id> or archive.ph/<14-digit timestamp>/<url>; anything
    # else (a captcha, the 'wip' capture page) is not one.
    ok = re.match(r"^https?://archive\.(?:ph|is|today|li|md|vn|fo)/(?:\d{14}/\S+|[A-Za-z0-9]{4,6})$", target)
    return target if ok else ""


def raw_snapshot(archive_url: str) -> str:
    """The same Wayback URL with id_ after the timestamp returns the page without the toolbar."""
    return re.sub(r"(/web/\d{8,14})(?:[a-z]{2}_)?/", r"\1id_/", archive_url, count=1)


def excerpt(html: str) -> str:
    tree = parse_html(html)
    for junk in tree.css("script, style, nav, header, footer, aside, form, noscript"):
        junk.decompose()
    paras = [re.sub(r"\s+", " ", p.text()).strip() for p in tree.css("p")]
    text = " ".join(p for p in paras if len(p) > 60)
    if len(text) < 300 and tree.body is not None:
        text = re.sub(r"\s+", " ", tree.body.text()).strip()
    return text[:EXCERPT_CHARS]


def main() -> None:
    a = cli(__doc__, excerpts={"action": "store_true", "help": "also fetch snapshot text for classify"},
            no_archive_is={"action": "store_true", "help": "skip the slow archive.ph lookups"})
    con = db.connect()
    rows = db.rows(con, limit=a.limit)
    counts: dict[str, int] = {}
    for r in rows:
        if r["archive_status"] == "manual" or (r["archive_url"] and not a.force):
            status = r["archive_status"] or ("manual" if r["archive_url"] else "none")
            if not r["archive_status"]:
                db.fill(con, r["id"], {"archive_status": status})
        elif not r["source_url"]:
            status = "none"
            db.fill(con, r["id"], {"archive_status": status}, force=a.force)
        else:
            try:
                snap = wayback(r["source_url"], r["source_date"])
                status = "wayback" if snap else ""
                if not snap and not a.no_archive_is:
                    snap = archive_is(r["source_url"])
                    status = "archive_is" if snap else ""
                status = status or "none"
            except LookupFailed as exc:
                log.warning("%s: %s; will retry next run", r["source_url"], exc)
                snap, status = "", None
            # 'none' is provisional: a later run (or a manual paste) may still fill it
            con.execute("UPDATE adaptations SET archive_url = ?, archive_status = ? WHERE id = ?",
                        (snap or None, status, r["id"]))
            r["archive_url"] = snap
        counts[status or "failed"] = counts.get(status or "failed", 0) + 1
        con.commit()

    if a.excerpts:
        ARTICLES.mkdir(parents=True, exist_ok=True)
        got = 0
        for r in db.rows(con, "archive_url LIKE '%web.archive.org/web/%'", limit=a.limit):
            out = ARTICLES / f"{r['id']}.txt"
            if out.exists() and not a.force:
                continue
            resp = fetch.get(raw_snapshot(r["archive_url"]), "snapshots")
            text = excerpt(resp.text) if resp.ok else ""
            if text:
                out.write_text(text)
                got += 1
        log.info("saved %d new excerpts to %s", got, ARTICLES)
    log.info("archive status: %s", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
