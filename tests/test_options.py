"""Crawl options from the configuration panel: behaviour, limits, advanced switches, thresholds, extraction
and the extra integrations (OpenAI, Gemini, Google)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.extract import parse_html, pdf_properties, readability

LONG = " ".join(["Ofrecemos cerrajería urgente las veinticuatro horas con técnicos de confianza en toda la ciudad."] * 8)
PDF = (b"%PDF-1.4\n1 0 obj << /Type /Pages /Count 3 /Kids [] >> endobj\n"
       b"2 0 obj << /Title (Tarifas 2026) /Author (Cerrajer\\(i\\)a) /CreationDate (D:20260115120000) >> endobj\n%%EOF")


def page(title, body, head=""):
    return f"<!doctype html><html lang='es'><head><title>{title}</title>{head}</head><body>{body}</body></html>"


def routes(base):
    return {
        "/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n", {}),
        "/blog/": (200, "text/html", page("Blog de cerrajería y seguridad del hogar", f"<h1>Blog</h1><p>{LONG}</p>"
            "<a href='/blog/a'>A</a> <a href='/blog/b'>B</a> <a href='/tienda/x'>X</a> <a href='/tienda/y'>Y</a>"
            "<a href='/blog/oculta' rel='nofollow'>N</a> <a href='/blog/page/2'>2</a> <a href='/no-existe'>Z</a>"
            "<a href='/blog/a/b/c/d/'>Hondo</a> <a href='/tarifas.pdf'>PDF</a>"
            "<iframe src='/blog/widget'></iframe>"
            "<form action='http://127.0.0.1/enviar'><input name='q'></form>"), {"Set-Cookie": "sid=1; Path=/"}),
        "/blog/a": (200, "text/html", page("Un título claramente demasiado largo para el máximo configurado", "<h1>A</h1><p>texto</p>"), {}),
        "/blog/b": (200, "text/html", page("B", "<h1>B</h1>"), {}),
        "/blog/oculta": (200, "text/html", page("Oculta", "<h1>Oculta</h1>"), {}),
        "/blog/page/2": (200, "text/html", page("Blog de cerrajería y seguridad del hogar", "<h1>Blog 2</h1>",
                                                f"<link rel='prev' href='{base}/blog/'>"), {}),
        "/blog/widget": (200, "text/html", page("Widget", "<p>iframe</p>"), {}),
        "/blog/a/b/c/d/": (200, "text/html", page("Hondo", "<h1>Hondo</h1>"), {}),
        "/blog/vacia": (200, "text/html", page("Página no encontrada", "<h1>Ups</h1>"), {}),
        "/tienda/x": (200, "text/html", page("Tienda X", "<h1>X</h1> <a href='/blog/vacia'>v</a>"), {}),
        "/tienda/y": (200, "text/html", page("Tienda Y", "<h1>Y</h1>"), {}),
        "/tarifas.pdf": (200, "application/pdf", PDF, {}),
    }


@pytest.fixture
def site():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            status, ctype, body, headers = self.server.routes.get(self.path, (404, "text/html", "<h1>404</h1>", {}))
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        do_HEAD = do_GET

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    server.routes = routes(base)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield base, hits
    server.shutdown()


def run(base, **form):
    from app import crawler, db
    from app.config import get_settings
    from app.validation import ACK_TEXT, validate_crawl_form
    db.init_db()
    from app.options import BOOLS
    defaults_on = {k: "yes" for k, d, *_ in BOOLS if d}  # the panel sends every box that is ticked by default
    data = {**defaults_on, "url": base + "/blog/", "ack": "yes", "max_urls": "50", "delay": "0", "cfg_panel": "1",
            "check_resources": "yes", "crawl_canonicals": "yes", "crawl_iframes": "yes", "crawl_pagination": "yes",
            "crawl_meta_refresh": "yes", "crawl_images": "yes", "crawl_css": "yes", "crawl_js": "yes",
            "extract_readability": "yes", "extract_text_ratio": "yes", "extract_forms": "yes", "html_validation": "yes",
            "ignore_pagination_duplicates": "yes", "extract_srcset": "yes", "crawl_outside_folder": "yes", **form}
    cfg, errors = validate_crawl_form(data, get_settings())
    assert not errors, errors
    cid = db.create_crawl(cfg["start_url"], cfg, ACK_TEXT)
    crawler.run_sync(cid)
    pages = {p["url"].replace(base, ""): p for p in db.load_pages(cid)}
    issues = db.load_issues(cid)
    return cid, pages, issues


def test_behaviour_limits_and_extraction(site):
    base, hits = site
    cid, pages, issues = run(base, crawl_outside_folder="", path_limits="/blog/a 1", max_folder_depth="3",
                             extract_cookies="yes", extract_pdf="yes", store_html="yes", title_max="40", low_words="20")
    assert "/tienda/x" not in pages                                          # stays inside /blog/…
    from app import db
    checked = {r["url"].replace(base, "") for r in db.load_externals(cid)}
    assert "/tienda/x" in checked                                            # …but its status is checked (default)
    hits.clear()
    _cid, pages2, _ = run(base, crawl_outside_folder="", check_outside_folder="", max_urls="5")
    assert "/tienda/x" not in pages2 and "/tienda/x" not in hits            # neither crawled nor checked
    assert "/blog/oculta" not in pages                                       # nofollow not followed by default
    assert "/blog/widget" in pages and pages["/blog/widget"]["source"] == "iframe"
    assert "/blog/a/b/c/d/" not in pages                                    # deeper than 3 folders
    home = pages["/blog/"]
    extra = home["extra"]
    assert extra["readability"]["formula"] == "Fernández-Huerta" and extra["forms"]["count"] == 1
    assert extra["cookies"] == [{"name": "sid", "secure": False, "httponly": False, "samesite": "", "expires": False}]
    codes = {(i["code"], i["url"].replace(base, "")) for i in issues}
    assert ("title_too_long", "/blog/a") in codes                           # threshold from the panel (40)
    # page 2 repeats the title of page 1 on purpose: not reported as duplicate
    assert not any(c == "duplicate_title" for c, _ in codes)
    from app.main import stored_html
    assert stored_html(cid, base + "/blog/")[0][0] == "HTML original"
    from app import db, views
    rows = {r["url"].replace(base, ""): r for r in views.workbench(db.get_crawl(cid))["pages"]}
    assert rows["/blog/"]["readability"] is not None and rows["/blog/"]["forms_count"] == 1
    assert rows["/blog/"]["cookies_count"] == 1 and rows["/blog/a"]["th_title_long"]


def test_subdomains_nofollow_and_soft404(site):
    base, hits = site
    cid, pages, issues = run(base, follow_nofollow_internal="yes", crawl_pagination="", extract_pdf="yes",
                             respect_next_prev="yes", carbon="yes")
    assert "/blog/oculta" in pages and "/tienda/x" in pages                 # nofollow followed when asked
    assert pages["/tarifas.pdf"]["extra"]["pdf"]["title"] == "Tarifas 2026"
    assert pages["/tarifas.pdf"]["extra"]["pdf"]["pages"] == 3
    codes = {(i["code"], i["url"].replace(base, "")) for i in issues}
    assert ("soft_404", "/blog/vacia") in codes
    from app import db, views
    rows = {r["url"].replace(base, "") for r in views.workbench(db.get_crawl(cid))["pages"]}
    assert "/blog/page/2" not in rows                                       # «respetar rel next/prev» hides it
    assert ("deep_page", "/blog/a/b/c/d/") not in codes


def test_invalid_options_are_rejected():
    from app.config import get_settings
    from app.validation import validate_crawl_form
    _, errors = validate_crawl_form({"url": "https://misitio.es/", "ack": "yes", "path_limits": "/blog/ muchos",
                                     "title_min": "80", "title_max": "60", "custom_sitemaps": "sitemap.xml",
                                     "max_redirects": "99"}, get_settings())
    assert {"path_limits", "title_min", "custom_sitemaps", "max_redirects"} <= set(errors)


def test_readability_and_pdf_helpers():
    easy = readability("El gato come. El perro duerme. La casa es azul. " * 10, "es")
    hard = readability("La implementación de metodologías interdisciplinarias contemporáneas requiere consideraciones "
                       "epistemológicas extraordinariamente sofisticadas y multidimensionales " * 4, "es")
    assert easy["score"] > hard["score"] and easy["label"] in ("Muy fácil", "Fácil")
    assert readability("muy corto", "es") is None
    props = pdf_properties(PDF)
    assert props["valid"] and props["created"] == "2026-01-15" and props["author"] == "Cerrajer(i)a"
    assert pdf_properties(b"<html>")["valid"] is False
    d = parse_html("<html><body><form action='/ok'></form><p>hola</p></body></html>", "https://x.es/")
    assert d["extra"]["forms"] == {"count": 1, "insecure": [], "password_on_http": False} and d["extra"]["text_ratio"] > 0
    bad = parse_html("<html><body><form action='http://x.es/enviar'></form></body></html>", "https://x.es/")
    assert bad["extra"]["forms"]["insecure"] == ["http://x.es/enviar"]
    from app.analysis import analyze
    pg = {"url": "https://x.es/", "status_code": 200, "content_type": "text/html", **bad}
    assert any(i.code == "insecure_form" for i in analyze([pg], [], start_url="https://x.es/"))


def test_openai_and_gemini_providers(monkeypatch):
    import httpx
    from app import ai
    sent = []

    def fake_post(url, **kw):
        sent.append((url, kw))
        if "openai" in url:
            return httpx.Response(200, json={"choices": [{"message": {"content": "Título: A\nDescripción: B"}}]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Hola"}]}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    page = {"url": "https://x.es/", "title": "T", "text_excerpt": "texto"}
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert ai.run_task("meta", page)[0].startswith("Título")
    assert sent[-1][1]["headers"]["Authorization"] == "Bearer sk-test"
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert not ai.status()["enabled"]
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    assert ai.run_task("summary", page)[0] == "Hola" and "generativelanguage" in sent[-1][0]


def test_google_import_parsing(monkeypatch):
    from app import google
    monkeypatch.setattr(google, "_call", lambda method, url, **kw: {
        "rows": [{"keys": ["https://x.es/a"], "clicks": 3, "impressions": 40, "ctr": 0.075, "position": 7.2}]}
        if "searchAnalytics" in url else
        {"rows": [{"dimensionValues": [{"value": "x.es"}, {"value": "/a"}], "metricValues": [{"value": "12"}, {"value": "2"}]},
                  {"dimensionValues": [{"value": "x.es"}, {"value": "/a"}], "metricValues": [{"value": "3"}, {"value": "0"}]}]})
    assert google.gsc_pages("sc-domain:x.es") == [{"url": "https://x.es/a", "clicks": 3, "impressions": 40, "ctr": 0.075, "position": 7.2}]
    assert google.ga4_pages("123", "https://x.es/") == [{"url": "https://x.es/a", "sessions": 15, "conversions": 2}]
    with pytest.raises(google.GoogleError):
        google.ga4_pages("12;3", "https://x.es/")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    url = google.auth_url("http://127.0.0.1:8765/integrations/google/callback")
    assert "code_challenge_method=S256" in url and "webmasters.readonly" in url
    with pytest.raises(google.GoogleError):
        google.finish("estado-falso", "code")  # state must come from this app


def test_config_panel_lists_every_option():
    from fastapi.testclient import TestClient
    from app import options
    from app.main import app
    with TestClient(app) as client:
        html = client.get("/").text
    for key, *_ in options.BOOLS + options.TEXTS:
        assert f'name="{key}"' in html, key
    for key, *_ in options.NUMBERS:
        assert f'name="{key}"' in html, key
    assert 'name="cookie_storage"' in html and "Preferencias" in html and "Extracción" in html


def test_panel_round_trip_keeps_the_defaults():
    """Submitting the configuration panel untouched must give back the saved defaults (an option missing from the
    panel would silently turn off, because an unticked checkbox is not sent)."""
    from bs4 import BeautifulSoup
    from fastapi.testclient import TestClient
    from app import crawlconfig
    from app.config import get_settings
    from app.main import app
    with TestClient(app) as client:
        soup = BeautifulSoup(client.get("/").text, "html.parser")
    form = soup.find("form", id="crawl-form")
    data = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name or el.has_attr("disabled") or el.get("form"):
            continue
        if el.name == "input" and el.get("type") == "checkbox":
            if el.has_attr("checked"):
                data[name] = el.get("value", "on")
        elif el.name == "input" and el.get("type") == "file":
            continue
        elif el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            data[name] = opt.get("value") if opt else ""
        elif el.name == "textarea":
            data[name] = el.text
        else:
            data[name] = el.get("value", "")
    errors: dict = {}
    cfg = crawlconfig.parse_config(data, get_settings(), errors)
    assert not errors, errors
    defaults = crawlconfig.load_defaults(get_settings())
    for key in crawlconfig.BOOL_KEYS:
        if key in ("pagespeed", "form_login", "use_proxy"):
            continue  # disabled without their .env keys
        assert bool(cfg[key]) == bool(defaults[key]), key
    for key, value in defaults.items():
        if isinstance(value, int) and not isinstance(value, bool):
            assert int(cfg[key]) == int(value), key


def test_mobile_alternate_is_detected():
    from bs4 import BeautifulSoup
    from app.checks import amp_checks
    html = ('<html><head><link rel="alternate" hreflang="en" href="https://x.es/en/">'
            '<link rel="alternate" media="only screen and (max-width: 640px)" href="https://m.x.es/"></head></html>')
    assert amp_checks(BeautifulSoup(html, "html.parser"), html)["mobile_alt"] == "https://m.x.es/"
    assert amp_checks(BeautifulSoup("<html></html>", "html.parser"), "")["mobile_alt"] is None


def test_every_filter_help_points_to_a_known_problem():
    from app import views
    from app.analysis import CATALOG
    tabs = {t.key: {k for k, *_ in t.filters} for t in views.STATIC_TABS}
    for tab, mapping in views.FILTER_HELP_CODES.items():
        for f, code in mapping.items():
            assert code in CATALOG, code
            assert f in tabs[tab], (tab, f)
    for tab, f in views.FILTER_HELP_TEXT:
        assert f in tabs[tab], (tab, f)
    assert views.filter_help("h1", "missing")["fix"] and views.filter_help("internal", "html") is None
    assert set(views.SIMPLE_TABS) <= set(tabs) | {"issues"}


def test_one_click_audit_opens_on_its_answer():
    from app.config import get_settings
    from app.main import PRESET_LANDING
    from app.validation import validate_crawl_form
    base = {"url": "https://ejemplo.test/", "ack": "yes", "max_urls": "10", "delay": "1"}
    for preset in ("broken", "images", "sitemap"):
        cfg, errors = validate_crawl_form({**base, "preset": preset}, get_settings())
        assert not errors and cfg["preset"] == preset and preset in PRESET_LANDING
    cfg, _ = validate_crawl_form({**base, "preset": "<script>"}, get_settings())
    assert cfg["preset"] == ""
