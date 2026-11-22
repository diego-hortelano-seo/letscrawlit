"""URL normalisation, scope checks and safe filenames."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize(url: str | None, base: str | None = None) -> str | None:
    """Return an absolute, canonical-ish http(s) URL or None if not crawlable."""
    if not url:
        return None
    url = url.strip().replace("\n", "").replace("\r", "").replace("\t", "")
    if not url or url.startswith("#"):
        return None
    if base:
        try:
            url = urljoin(base, url)
        except ValueError:
            return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = parts.path or "/"
    path = re.sub(r" ", "%20", path)
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def site_key(url: str) -> str:
    """Host without leading 'www.' and port: example.com and www.example.com share scope."""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(a: str, b: str) -> bool:
    return bool(a and b) and site_key(a) == site_key(b)


def origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def safe_filename(value: str, default: str = "export", max_len: int = 80) -> str:
    """Filesystem- and header-safe name: ASCII letters, digits, dot, dash, underscore."""
    value = re.sub(r"^[a-z]+://", "", value or "", flags=re.I)
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    cleaned = re.sub(r"\.{2,}", "-", cleaned)  # no ".." path segments
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")[:max_len].strip("-._")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
    if not cleaned or cleaned.split(".")[0].lower() in reserved:
        return default
    return cleaned
