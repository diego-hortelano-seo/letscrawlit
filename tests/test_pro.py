"""URL pattern clustering, internal PageRank, recorded login sessions, logo validation and the CI JUnit output."""
import time

from app import clientreport, sessions
from app.ci import junit_xml, markdown
from app.pagerank import compute
from app.patterns import cluster

B = "https://tienda.test"


def test_cluster_finds_the_template_behind_many_errors():
    urls = ([f"{B}/blog/post-{i}/page/{n}" for i in range(40) for n in range(2, 5)]
            + [f"{B}/tienda?color=rojo&p={i}" for i in range(12)] + [f"{B}/a", f"{B}/b"])
    groups = cluster(urls)
    assert groups[0]["pattern"] == "/blog/*/page/{página}" and groups[0]["count"] == 120 and groups[0]["pct"] == 90
    assert groups[1]["pattern"] == "/tienda?color=*&p=*" and groups[-1]["pattern"] == "otras"
    assert cluster([f"{B}/a", f"{B}/b"]) == []  # too few to say anything


def test_internal_pagerank():
    P = lambda u, **k: {"url": B + u, "status_code": 200, "content_type": "text/html", **k}
    L = lambda s, t, **k: {"source_url": B + s, "target_url": B + t, "kind": "a", "internal": True, **k}
    pages = [P("/"), P("/a"), P("/b"), P("/viejo", status_code=301, redirect_target=B + "/b"), P("/solo-clic")]
    links = [L("/", "/a"), L("/", "/viejo"), L("/a", "/"), L("/b", "/"), L("/a", "/a"),
             L("/", "/solo-clic", rel="js-click"), L("/a", "/solo-clic", nofollow=True)]
    pr = compute(pages, links)
    assert pr[B + "/"]["pagerank"] == 100 and pr[B + "/solo-clic"]["pagerank"] == 0
    assert pr[B + "/b"]["authority_pct"] > pr[B + "/solo-clic"]["authority_pct"]  # the redirect passes authority on
    assert abs(sum(v["authority_pct"] for v in pr.values()) - 100) < 0.1


def test_recorded_session_keeps_only_the_site():
    state = {"cookies": [
        {"name": "sid", "value": "1", "domain": ".tienda.test", "path": "/", "expires": time.time() + 3600},
        {"name": "old", "value": "1", "domain": "tienda.test", "path": "/", "expires": time.time() - 10},
        {"name": "sso", "value": "x", "domain": "login.microsoftonline.com", "path": "/", "expires": -1}],
        "origins": [{"origin": "https://www.tienda.test", "localStorage": [{"name": "token", "value": "t"}]},
                    {"origin": "https://accounts.google.com", "localStorage": [{"name": "g", "value": "1"}]}]}
    saved = sessions.save("tienda.test", state)
    assert saved["cookies"] == 2 and saved["storage"] == 1          # the SSO provider's data is dropped
    loaded = sessions.load("tienda.test")
    assert [c["name"] for c in loaded["cookies"]] == ["sid"]          # expired cookies are not sent
    assert sessions.list_saved()[0]["site"] == "tienda.test"
    sessions.delete("tienda.test")
    assert sessions.load("tienda.test") is None
    sessions.delete("../../etc")  # safe file names: never escapes data/sessions


def test_logo_type_is_checked_by_content():
    assert clientreport.logo_kind(b"\x89PNG\r\n\x1a\n....") == "png"
    assert clientreport.logo_kind(b"\xff\xd8\xff\xe0") == "jpg"
    assert clientreport.logo_kind(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>') == "svg"
    assert clientreport.logo_kind(b"<html><script>alert(1)</script>") is None


def test_ci_outputs():
    r = {"url": B + "/", "pages": 10, "passed": False, "baseline": False, "crawl_id": 1,
         "summary": {"critical": 0, "high": 2, "medium": 0, "low": 0, "info": 0},
         "checks": [{"name": "Rastreo completado", "ok": True, "detail": "completed"},
                    {"name": f"Indexable: {B}/", "ok": False, "detail": "noindex"}],
         "failing": [{"code": "broken_link", "title": "Enlace interno roto (4xx)", "severity": "high", "count": 2,
                      "remediation": "Arregla <a>", "urls": [B + "/x", B + "/y"], "evidence": "HTTP 404"}],
         "groups": []}
    xml = junit_xml(r)
    assert xml.count("<failure") == 2 and 'failures="2"' in xml and "&lt;a&gt;" in xml
    assert markdown(r).startswith("## ❌") and "| Alto | Enlace interno roto (4xx) | 2 |" in markdown(r)
