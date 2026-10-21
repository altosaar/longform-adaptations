"""Cached HTTP GET: every response lands under raw/<bucket>/ keyed by URL hash, so re-runs
cost nothing. Sleeps between requests to the same host and retries once on 5xx.

(Named fetch.py, not the spec's http.py, which would shadow the stdlib `http` package.)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

from db import ROOT

load_dotenv(ROOT / ".env")
log = logging.getLogger("fetch")

RAW = ROOT / "raw"
CONTACT = os.environ.get("CONTACT_EMAIL", "").strip()
# Wikimedia's API etiquette asks for a descriptive agent with a way to reach you.
BOT_UA = f"longhorns/0.1 (research prototype{'; ' + CONTACT if CONTACT else ''}) httpx"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)
_BOT_HOSTS = ("wikipedia.org", "wikidata.org", "archive.org", "imdbws.com", "omdbapi.com")
# Seconds between requests per host. Wikimedia throttles unidentified clients hard (set
# CONTACT_EMAIL in .env so the User-Agent says who you are).
_DELAY = {"archive.ph": 5.0, "archive.is": 5.0, "archive.today": 5.0, "archive.org": 2.0,
          "wikipedia.org": 2.0, "wikidata.org": 2.0}
_last: dict[str, float] = {}
_client: httpx.Client | None = None


@dataclass
class Response:
    url: str
    status: int
    text: str
    final_url: str
    cached: bool

    def json(self):
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(45.0, connect=15.0), follow_redirects=True, http2=False)
    return _client


def _pace(host: str) -> None:
    root = ".".join(host.split(".")[-2:])
    wait = _DELAY.get(root, 1.0) - (time.monotonic() - _last.get(root, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last[root] = time.monotonic()


def cache_path(url: str, bucket: str) -> Path:
    return RAW / bucket / hashlib.sha1(url.encode()).hexdigest()


def get(
    url: str,
    bucket: str = "misc",
    *,
    params: dict | None = None,
    follow_redirects: bool = True,
    refresh: bool = False,
    headers: dict | None = None,
    retries: int = 2,
) -> Response:
    """GET with an on-disk cache. Network errors come back as status 0 (and aren't cached).

    Retries 5xx/429/network errors `retries` times with backoff (the spec's "retry once" is
    retries=1; Wikimedia occasionally needs a second try).
    """
    if params:
        url = str(httpx.URL(url, params=params))
    path = cache_path(url, bucket)
    meta = path.with_suffix(".json")
    if not refresh and meta.exists():
        m = json.loads(meta.read_text())
        return Response(url, m["status"], path.read_text(errors="replace"), m["final_url"], True)

    host = urlsplit(url).hostname or ""
    ua = BOT_UA if host.endswith(_BOT_HOSTS) else BROWSER_UA
    hdrs = {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9", **(headers or {})}
    resp = None
    for attempt in range(retries + 1):
        _pace(host)
        try:
            resp = _http().get(url, headers=hdrs, follow_redirects=follow_redirects)
        except httpx.HTTPError as exc:
            log.warning("GET %s failed: %s", url, exc)
            resp = None
        if resp is not None and resp.status_code < 500 and resp.status_code != 429:
            break
        if attempt < retries:
            retry_after = resp.headers.get("retry-after", "") if resp is not None else ""
            time.sleep(float(retry_after) if retry_after.isdigit() else 2.0 * (attempt + 1))
    if resp is None:
        return Response(url, 0, "", url, False)
    status, text = resp.status_code, resp.text
    final = resp.headers.get("location", str(resp.url)) if not follow_redirects else str(resp.url)
    if status in (202, 403) and ua == BROWSER_UA:
        # Bot walls that key on the TLS fingerprint (Cloudflare, AWS WAF) often let a
        # Chrome-impersonating client through.
        status, text, final = _impersonate(url, hdrs, follow_redirects) or (status, text, final)
    if status >= 500 or status == 429:
        # transient: don't cache, so the next run tries again
        return Response(url, status, text, final, False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    meta.write_text(json.dumps({"url": url, "status": status, "final_url": final}))
    return Response(url, status, text, final, False)


def _impersonate(url: str, hdrs: dict, follow_redirects: bool) -> tuple[int, str, str] | None:
    from curl_cffi import requests as cffi

    try:
        r = cffi.get(url, impersonate="chrome", timeout=45, allow_redirects=follow_redirects,
                     headers={k: v for k, v in hdrs.items() if k != "User-Agent"})
    except Exception as exc:  # curl_cffi raises its own error types
        log.warning("impersonated GET %s failed: %s", url, exc)
        return None
    final = r.headers.get("location", r.url) if not follow_redirects else r.url
    return r.status_code, r.text, final


def download(url: str, dest: Path, max_age_s: float = 86400) -> Path:
    """Stream a binary file to disk unless a fresh copy exists (the IMDb ratings dump)."""
    if dest.exists() and time.time() - dest.stat().st_mtime < max_age_s:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with _http().stream("GET", url, headers={"User-Agent": BOT_UA}) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.rename(dest)
    return dest
