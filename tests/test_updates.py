"""«Hay una versión nueva»: version comparison, the 12-hour cache and silent failures."""
import httpx

from app import updates


def test_parse_and_compare():
    assert updates.parse("v0.4.0") == (0, 4, 0)
    assert updates.parse("1.2") == (1, 2, 0)
    assert updates.parse("v1.0.0-beta") is None
    assert updates.newer("v0.4.0", "0.3.0")
    assert updates.newer("0.10.0", "0.9.9")
    assert not updates.newer("v0.3.0", "0.3.0")
    assert not updates.newer("v0.2.9", "0.3.0")
    assert not updates.newer("basura", "0.3.0")


def test_check_caches_and_only_links_to_this_project(monkeypatch):
    calls = []

    def fake_fetch():
        calls.append(1)
        return {"version": "9.0.0", "url": updates.PAGE}

    monkeypatch.setattr(updates, "_fetch", fake_fetch)
    monkeypatch.setattr(updates, "_cache", {"at": 0.0, "result": None})
    first = updates.check(now=100_000.0)
    assert first["newer"] and first["latest"] == "9.0.0" and first["url"].startswith("https://github.com/")
    updates.check(now=100_000.0 + 3600)          # within 12 h: GitHub is not asked again
    assert len(calls) == 1
    updates.check(now=100_000.0 + updates.EVERY)  # after 12 h it is
    assert len(calls) == 2


def test_no_internet_means_no_notice(monkeypatch):
    def boom():
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(updates, "_fetch", boom)
    monkeypatch.setattr(updates, "_cache", {"at": 0.0, "result": None})
    r = updates.check(now=100_000.0)
    assert r["newer"] is False and r["latest"] is None


def test_foreign_release_url_is_replaced(monkeypatch):
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"tag_name": "v9.0.0", "html_url": "https://evil.example/download"}

    monkeypatch.setattr(updates.httpx, "get", lambda *a, **k: R())
    assert updates._fetch() == {"version": "9.0.0", "url": updates.PAGE}
