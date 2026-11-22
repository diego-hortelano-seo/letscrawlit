"""Security hardening: foreign hosts, cross-site posts, browser headers, redirects, oversized and hostile downloads."""
import gzip

import pytest
from fastapi.testclient import TestClient

from app import safehttp
from app.main import _host_allowed, _local_path, app


def test_only_local_hosts_are_served(monkeypatch):
    for ok in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "192.168.1.20:8765", "letscrawl.localhost"):
        assert _host_allowed(ok), ok
    for bad in ("evil.example", "evil.example:8765", "127.0.0.1.nip.io:8765", ""):
        assert not _host_allowed(bad), bad
    monkeypatch.setenv("LETSCRAWL_ALLOWED_HOSTS", "mi-portatil.local")
    assert _host_allowed("mi-portatil.local:8765")
    with TestClient(app) as c:
        assert c.get("/", headers={"host": "rebind.evil.example:8765"}).status_code == 421


def test_cross_site_posts_blocked_and_headers_set():
    with TestClient(app) as c:
        assert c.post("/config/reset", headers={"origin": "https://evil.example"}).status_code == 403
        assert c.post("/config/reset", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        r = c.get("/")
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["referrer-policy"] == "same-origin"
        # a normal form submit from our own page (Chrome sends Origin: null with fetch metadata)
        assert c.post("/config/reset", headers={"origin": "null", "sec-fetch-site": "same-origin"}).status_code != 403
        assert c.post("/config/reset", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert c.post("/config/reset", headers={"origin": "null"}).status_code == 403


def test_no_open_redirects():
    for bad in ("//evil.example", "/\\evil.example", "https://evil.example", "/x\r\nSet-Cookie: a=b", "evil"):
        assert _local_path(bad) == "/", bad
    assert _local_path("/crawls/3?tab=ai") == "/crawls/3?tab=ai"
    with TestClient(app) as c:
        r = c.get("/lang/es", params={"next": "/\\evil.example"}, follow_redirects=False)
        assert r.headers["location"] == "/"


def test_gzip_bombs_and_dtds_are_refused():
    bomb = gzip.compress(b"\0" * (20 * 1024 * 1024))
    with pytest.raises(safehttp.TooLarge):
        safehttp.gunzip_capped(bomb, 1024 * 1024)
    assert safehttp.gunzip_capped(gzip.compress(b"<urlset/>"), 1024) == b"<urlset/>"
    laughs = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa">]><urlset>&a;</urlset>'
    assert not safehttp.no_doctype(laughs) and safehttp.no_doctype(b"<urlset><url><loc>x</loc></url></urlset>")


def test_schedule_command_quotes_the_url():
    from app.cli import schedule_command
    cmd = schedule_command('https://x.es/?a=1&b="2"', "03:00", True, None, None)
    tr = cmd[cmd.index("/TR") + 1]
    assert 'crawl "https://x.es/?a=1&b=%222%22" --i-own-this-site' in tr


def test_store_version_schedules_through_its_alias(monkeypatch):
    """Microsoft Store (MSIX): Task Scheduler cannot start a program inside WindowsApps, only its «letscrawlit.exe» alias."""
    from app import cli
    monkeypatch.setattr(cli, "FROZEN", True)
    monkeypatch.setattr(cli, "PACKAGED", True)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    tr = cli.schedule_command("https://x.es/", "03:00", True, None, None)
    tr = tr[tr.index("/TR") + 1]
    assert tr.startswith(r'"C:\Users\x\AppData\Local\Microsoft\WindowsApps\letscrawlit.exe" crawl "https://x.es/"')


def test_local_network_detection():
    import asyncio
    for local in ("127.0.0.1", "192.168.1.1", "10.0.0.5", "169.254.169.254", "localhost", "nas.local", "[::1]"):
        assert asyncio.run(safehttp.is_private_host(local)), local
    assert not asyncio.run(safehttp.is_private_host("8.8.8.8"))


def test_password_for_other_devices(monkeypatch, tmp_path):
    """From this computer nothing is asked; from another device only with LETSCRAWL_PASSWORD and after logging in."""
    from app import access
    monkeypatch.setattr(access, "_secret", lambda: b"k" * 32)
    assert access.is_local_client("127.0.0.1") and access.is_local_client("::1") and not access.is_local_client("192.168.1.9")
    monkeypatch.delenv("LETSCRAWL_PASSWORD", raising=False)
    assert not access.has_valid_cookie(access.token())          # no password: no cookie is ever valid
    monkeypatch.setenv("LETSCRAWL_PASSWORD", "una-clave-larga")
    assert access.has_valid_cookie(access.token()) and not access.has_valid_cookie("x")
    monkeypatch.setattr(access.time, "sleep", lambda s: None)
    assert not access.check_password("10.0.0.7", "mal") and access.check_password("10.0.0.7", "una-clave-larga")
    for _ in range(access.MAX_FAILS):
        access.check_password("10.0.0.8", "mal")
    assert access.locked("10.0.0.8") and not access.check_password("10.0.0.8", "una-clave-larga")
    with TestClient(app, client=("192.168.1.9", 5000)) as c:
        monkeypatch.delenv("LETSCRAWL_PASSWORD")
        assert c.get("/").status_code == 403
        monkeypatch.setenv("LETSCRAWL_PASSWORD", "una-clave-larga")
        r = c.get("/crawls", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.post("/login", data={"password": "una-clave-larga", "next": "/"}, follow_redirects=False).status_code == 303
        assert c.get("/").status_code == 200


def test_credentials_encrypted_on_disk(tmp_path):
    import json
    import sys
    from app import secretstore
    p = tmp_path / "s.json"
    secretstore.write_json(p, {"cookies": [{"name": "sid", "value": "secreto"}]})
    assert secretstore.read_json(p)["cookies"][0]["value"] == "secreto"
    if sys.platform == "win32":
        assert b"secreto" not in p.read_bytes() and p.read_bytes().startswith(secretstore.PREFIX)
        old = tmp_path / "old.json"
        old.write_text(json.dumps({"refresh_token": "abc"}), encoding="utf-8")
        assert secretstore.encrypt_existing([old]) == 1 and b"abc" not in old.read_bytes()
        assert secretstore.read_json(old)["refresh_token"] == "abc"


def test_no_inline_javascript_anywhere():
    """The security policy forbids inline code; no template may rely on it (it would silently stop working)."""
    import re
    from pathlib import Path
    for f in Path("app/templates").glob("*.html"):
        html = f.read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=", html), f"{f.name}: inline event handler"
        assert "javascript:" not in html.replace('startswith("javascript:")', ""), f"{f.name}: javascript: URL"
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, f"{f.name}: inline <script>"
    with TestClient(app) as c:
        csp = c.get("/").headers["content-security-policy"]
        script_src = re.search(r"script-src ([^;]+)", csp)[1]
        assert "unsafe-inline" not in script_src and "unsafe-eval" not in script_src
