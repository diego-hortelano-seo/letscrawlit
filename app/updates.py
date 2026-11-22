"""«Hay una versión nueva»: asks GitHub which LetsCrawlit version is the latest published one.

One request to the public GitHub API, at most every 12 hours, only while the app is open and only if the user has
not turned the notice off (Vista › Avisar de versiones nuevas). Nothing about the user or their audits is sent:
GitHub only sees an ordinary request from this computer. Any failure (no internet, repository not public yet,
GitHub down) just means no notice.
"""
from __future__ import annotations

import re
import threading
import time

import httpx

from .config import VERSION

REPO = "diego-hortelano-seo/letscrawlit"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
EVERY = 12 * 3600
_cache: dict = {"at": 0.0, "result": None}
_lock = threading.Lock()


def parse(version: str) -> tuple[int, ...] | None:
    """'v0.4.0' / '0.4.0' -> (0, 4, 0); anything else (pre-releases, junk) -> None."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", (version or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def newer(latest: str, current: str = VERSION) -> bool:
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def _fetch() -> dict | None:
    r = httpx.get(API, timeout=5, follow_redirects=True,
                  headers={"Accept": "application/vnd.github+json", "User-Agent": f"LetsCrawlit/{VERSION}"})
    if r.status_code != 200:
        return None
    data = r.json()
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    url = str(data.get("html_url") or "")
    if not url.startswith(f"https://github.com/{REPO}/releases/"):
        url = PAGE  # only ever link to this project's own releases page
    return {"version": tag.lstrip("v"), "url": url} if parse(tag) else None


def check(now: float | None = None) -> dict:
    """{'current', 'latest', 'newer', 'url'}; GitHub is asked at most once every 12 hours."""
    now = time.time() if now is None else now
    with _lock:
        if now - _cache["at"] >= EVERY:
            try:
                _cache["result"] = _fetch()
            except (httpx.HTTPError, ValueError):
                _cache["result"] = None
            _cache["at"] = now
        latest = _cache["result"]
    return {"current": VERSION, "latest": latest["version"] if latest else None,
            "newer": bool(latest and newer(latest["version"])), "url": latest["url"] if latest else PAGE}
