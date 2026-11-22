"""Who may use the app.

On this computer (127.0.0.1) nothing is asked. From any other device on the network the app is only
available when LETSCRAWL_PASSWORD is set in .env, and then only after typing it once (a signed cookie keeps the
session for 30 days). Wrong passwords are slowed down and locked out after several tries.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time

from .config import get_settings

COOKIE = "lc_auth"
MAX_AGE = 30 * 24 * 3600
MAX_FAILS = 8
LOCK_SECONDS = 15 * 60
_fails: dict[str, list[float]] = {}
_lock = threading.Lock()


def password() -> str:
    return os.getenv("LETSCRAWL_PASSWORD", "")


def is_local_client(ip: str) -> bool:
    if ip in ("testclient", "localhost"):
        return True
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_loopback
    except ValueError:
        return False


def _secret() -> bytes:
    """Per-install random key (data/.auth_key), so cookies from another install or old password never work."""
    path = get_settings().data_dir / ".auth_key"
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    return key


def token() -> str:
    """Cookie value: changes whenever the password changes."""
    return hmac.new(_secret(), b"letscrawl:" + password().encode("utf-8"), hashlib.sha256).hexdigest()


def has_valid_cookie(value: str | None) -> bool:
    return bool(password()) and bool(value) and hmac.compare_digest(value, token())


def locked(ip: str) -> bool:
    now = time.time()
    with _lock:
        recent = [t for t in _fails.get(ip, []) if now - t < LOCK_SECONDS]
        _fails[ip] = recent
        return len(recent) >= MAX_FAILS


def check_password(ip: str, typed: str) -> bool:
    if not password() or locked(ip):
        return False
    ok = hmac.compare_digest(typed.encode("utf-8"), password().encode("utf-8"))
    if not ok:
        with _lock:
            _fails.setdefault(ip, []).append(time.time())
        time.sleep(1)  # slows down guessing
    else:
        with _lock:
            _fails.pop(ip, None)
    return ok
