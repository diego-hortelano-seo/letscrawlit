"""Direct Google Search Console and Google Analytics 4 import (OAuth, read-only scopes).

Setup (once): in Google Cloud create an OAuth client of type «Desktop app», enable the Search Console API,
the Google Analytics Data API and the Google Analytics Admin API, and put GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
in .env. The refresh token is stored only in data/google_token.json. Only the data you import is requested.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from datetime import date, timedelta
from urllib.parse import quote, urlencode, urlsplit

import httpx

from . import secretstore
from .config import get_settings

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly", "https://www.googleapis.com/auth/analytics.readonly"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
_pending: dict[str, dict] = {}  # state -> {verifier, redirect, created}
_access: dict = {}


class GoogleError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def _token_path():
    return get_settings().data_dir / "google_token.json"


def connected() -> bool:
    return _token_path().exists()


def status() -> dict:
    if not configured():
        return {"state": "unconfigured",
                "message": "Añade GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET en .env (cliente OAuth de tipo «App de escritorio»)."}
    if not connected():
        return {"state": "disconnected", "message": "Sin conectar."}
    try:
        info = secretstore.read_json(_token_path())
    except (OSError, ValueError):
        info = {}
    return {"state": "connected", "message": f"Conectado el {info.get('connected_at', '')[:16].replace('T', ' ')} UTC."}


def auth_url(redirect_uri: str) -> str:
    """PKCE + state: the code can only be exchanged by this app, and only for this request."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    _pending[state] = {"verifier": verifier, "redirect": redirect_uri, "created": time.time()}
    for k in [k for k, v in _pending.items() if time.time() - v["created"] > 900]:
        _pending.pop(k, None)
    return AUTH_URL + "?" + urlencode({
        "client_id": os.getenv("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def finish(state: str, code: str) -> None:
    pending = _pending.pop(state or "", None)
    if not pending:
        raise GoogleError("La autorización caducó o no se inició desde esta app. Vuelve a pulsar «Conectar».")
    r = httpx.post(TOKEN_URL, timeout=30, data={
        "client_id": os.getenv("GOOGLE_CLIENT_ID"), "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "code": code, "code_verifier": pending["verifier"], "grant_type": "authorization_code",
        "redirect_uri": pending["redirect"]})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not data.get("refresh_token"):
        raise GoogleError(f"Google no devolvió el permiso ({data.get('error_description') or data.get('error') or r.status_code}).")
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secretstore.write_json(path, {"refresh_token": data["refresh_token"], "scope": data.get("scope"),
                                  "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())})
    _access.clear()


def disconnect() -> None:
    try:
        token = secretstore.read_json(_token_path()).get("refresh_token")
        if token:
            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": token}, timeout=15)
    except (OSError, ValueError, httpx.HTTPError):
        pass
    _token_path().unlink(missing_ok=True)
    _access.clear()


def _access_token() -> str:
    if _access.get("token") and _access.get("expires", 0) > time.time() + 60:
        return _access["token"]
    try:
        refresh = secretstore.read_json(_token_path())["refresh_token"]
    except (OSError, ValueError, KeyError):
        raise GoogleError("No hay ninguna cuenta de Google conectada.")
    r = httpx.post(TOKEN_URL, timeout=30, data={"client_id": os.getenv("GOOGLE_CLIENT_ID"),
                                                 "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                                                 "refresh_token": refresh, "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise GoogleError("Google rechazó el permiso guardado; vuelve a conectar la cuenta.")
    data = r.json()
    _access.update(token=data["access_token"], expires=time.time() + int(data.get("expires_in", 3600)))
    return _access["token"]


def _call(method: str, url: str, **kw) -> dict:
    try:
        r = httpx.request(method, url, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=60, **kw)
    except httpx.HTTPError as exc:
        raise GoogleError(f"No hay conexión con Google ({type(exc).__name__}).")
    if r.status_code == 403:
        raise GoogleError("Google denegó el acceso: revisa que la API esté activada en tu proyecto y que tu cuenta tenga acceso a esa propiedad.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise GoogleError(f"Google devolvió HTTP {r.status_code}. {msg}"[:300])
    return r.json()


def _range(days: int) -> tuple[str, str]:
    end = date.today() - timedelta(days=2)  # Search Console data lags ~2 days
    return (end - timedelta(days=max(1, days) - 1)).isoformat(), end.isoformat()


# --- Search Console -----------------------------------------------------------------------------
def gsc_sites() -> list[str]:
    return sorted(s["siteUrl"] for s in _call("GET", "https://www.googleapis.com/webmasters/v3/sites").get("siteEntry", [])
                  if s.get("permissionLevel") != "siteUnverifiedUser")


def gsc_pages(site: str, days: int = 28) -> list[dict]:
    start, end = _range(days)
    rows, offset = [], 0
    while True:
        data = _call("POST", f"https://www.googleapis.com/webmasters/v3/sites/{quote(site, safe='')}/searchAnalytics/query",
                     json={"startDate": start, "endDate": end, "dimensions": ["page"], "rowLimit": 25000, "startRow": offset})
        batch = data.get("rows") or []
        rows += [{"url": r["keys"][0], "clicks": r.get("clicks"), "impressions": r.get("impressions"),
                  "ctr": r.get("ctr"), "position": r.get("position")} for r in batch]
        if len(batch) < 25000 or len(rows) >= 100000:
            return rows
        offset += 25000


# --- Analytics 4 -----------------------------------------------------------------------------------
def ga4_properties() -> list[dict]:
    data = _call("GET", "https://analyticsadmin.googleapis.com/v1beta/accountSummaries", params={"pageSize": 200})
    return [{"id": p["property"].split("/")[-1], "name": f"{a.get('displayName', '')} › {p.get('displayName', '')}"}
            for a in data.get("accountSummaries", []) for p in a.get("propertySummaries", [])]


def ga4_pages(property_id: str, site_url: str, days: int = 28) -> list[dict]:
    if not property_id.isdigit():
        raise GoogleError("ID de propiedad de GA4 no válido.")
    start, end = _range(days)
    data = _call("POST", f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport", json={
        "dateRanges": [{"startDate": start, "endDate": end}], "dimensions": [{"name": "hostName"}, {"name": "pagePath"}],
        "metrics": [{"name": "sessions"}, {"name": "keyEvents"}], "limit": 100000})
    scheme = urlsplit(site_url).scheme or "https"
    totals: dict[str, dict] = {}
    for r in data.get("rows") or []:
        host, path = (d["value"] for d in r["dimensionValues"])
        url = f"{scheme}://{host}{path if path.startswith('/') else '/' + path}"
        m = totals.setdefault(url, {"url": url, "sessions": 0, "conversions": 0})
        m["sessions"] += int(float(r["metricValues"][0]["value"] or 0))
        m["conversions"] += int(float(r["metricValues"][1]["value"] or 0))
    return list(totals.values())
