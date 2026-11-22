"""Focused tests for the core transformation: HTML -> signals -> issues -> exports."""
from app.analysis import analyze, compute_indexability, group_issues
from app.config import get_settings
from app.extract import is_noindex, parse_html
from app.report import _safe_cell, to_csv
from app.robots import RobotsRules
from app.urlutil import normalize, safe_filename, same_site
from app.validation import validate_crawl_form

BASE = "https://example.com"


def page(path, **kw):
    p = {"url": BASE + path, "status_code": 200, "content_type": "text/html", "title": f"Título suficientemente largo {path}",
         "meta_description": f"Descripción única {path}", "h1": [f"H1 {path}"], "canonical": BASE + path,
         "word_count": 300, "content_hash": f"hash{path}", "structured": {"jsonld_count": 1}}
    p.update(kw)
    return p


def link(src, dst, kind="a", nofollow=False):
    return {"source_url": BASE + src, "target_url": BASE + dst, "kind": kind, "internal": True, "nofollow": nofollow}


def codes(issues, url=None):
    return {i.code for i in issues if url is None or i.url == BASE + url}


# --- urls / validation ------------------------------------------------------

def test_normalize_and_scope():
    assert normalize("/a b#frag", "HTTPS://Example.COM:443/x/") == "https://example.com/a%20b"
    assert normalize("mailto:x@y.z") is None
    assert normalize("javascript:void(0)") is None
    assert normalize("https://example.com") == "https://example.com/"
    assert same_site("https://www.example.com/", "http://example.com/x")
    assert not same_site("https://blog.example.com/", "https://example.com/")


def test_safe_filename():
    assert safe_filename("https://mi sitio.es/../../etc/passwd") == "mi-sitio.es-etc-passwd"
    assert safe_filename("CON") == "export"
    assert safe_filename('a"b<>|c') == "a-b-c"


def test_validation_requires_permission_and_bounds():
    s = get_settings()
    cfg, errors = validate_crawl_form({"url": "misitio.es", "max_urls": "50", "delay": "1"}, s)
    assert cfg is None and "ack" in errors
    cfg, errors = validate_crawl_form({"url": "ftp://x", "max_urls": "-1", "delay": "-1", "ack": "yes"}, s)
    assert set(errors) == {"url", "max_urls", "delay"}
    cfg, errors = validate_crawl_form({"url": "https://user:pw@misitio.es", "ack": "yes"}, s)
    assert "url" in errors
    cfg, errors = validate_crawl_form({"url": "misitio.es", "max_urls": "50", "delay": "1,5", "ack": "yes"}, s)
    assert not errors and cfg["start_url"] == "https://misitio.es/" and cfg["delay"] == 1.5


# --- extraction ---------------------------------------------------------------

def test_parse_html_extracts_signals():
    html = """<html lang="es"><head><title> Cerrajero 24h </title>
      <meta name="description" content="Abrimos puertas">
      <meta name="robots" content="noindex, nofollow">
      <link rel="canonical" href="/canon">
      <script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"LocalBusiness"},{"@type":"WebPage"}]}</script>
      <script type="application/ld+json">{bad json</script>
      </head><body><h1>Uno</h1><h1>Dos</h1><h2>Sub</h2>
      <a href="/a">A</a><a href="https://otro.com/" rel="nofollow sponsored">Ext</a><a href="tel:600">Tel</a>
      <img src="/x.png"><img src="/y.png" alt="">
      <script>var hidden = "no cuenta";</script><p>texto visible aquí</p></body></html>"""
    d = parse_html(html, "https://example.com/p")
    assert d["title"] == "Cerrajero 24h"
    assert d["meta_description"] == "Abrimos puertas"
    assert is_noindex(d["meta_robots"])
    assert d["canonical"] == "https://example.com/canon"
    assert d["h1"] == ["Uno", "Dos"] and d["h2"] == ["Sub"]
    assert [l["href"] for l in d["links"]] == ["https://example.com/a", "https://otro.com/"]
    assert d["links"][1]["nofollow"] is True
    assert d["images"][0]["alt"] is None and d["images"][1]["alt"] == ""
    assert d["structured"]["jsonld_types"] == ["LocalBusiness", "WebPage"]
    assert d["jsonld_invalid"] == 1
    assert "no cuenta" not in d["text_excerpt"] and "texto visible" in d["text_excerpt"]
    assert d["lang"] == "es"
    assert d["extra"]["headings"] == [1, 1, 2] and d["extra"]["viewport"] is None
    assert d["extra"]["validation"]["doctype"] is False and d["extra"]["validation"]["title_count"] == 1


def test_parse_html_structure_and_custom():
    html = """<!DOCTYPE html><html><head><title>A</title><title>B</title><meta name="viewport" content="width=device-width">
      <meta name="keywords" content="cerrajero, madrid"><link rel="next" href="/p/2"><div>mal</div></head>
      <body><h1><img src="/logo.png" alt="Cerrajería Pérez"></h1><h3>Salto</h3>
      <span class="precio">49 €</span><a href="tel:+34600111222">Llamar</a> Teléfono 600 111 222</body></html>"""
    d = parse_html(html, "https://example.com/", searches=[{"name": "Tel", "mode": "contains", "text": "teléfono"}],
                   extractions=[{"name": "Precio", "type": "css", "expr": ".precio"},
                                {"name": "Móvil", "type": "regex", "expr": r"tel:\+34(\d{9})"}])
    x = d["extra"]
    assert x["h1_alt"] is True and x["headings"] == [1, 3]
    assert x["next"] == "https://example.com/p/2" and x["meta_keywords"] == "cerrajero, madrid"
    assert x["validation"]["doctype"] and x["validation"]["title_count"] == 2
    assert x["validation"]["invalid_in_head"] == ["div"]
    assert d["custom"]["search"] == {"Tel": 1}
    assert d["custom"]["extract"] == {"Precio": ["49 €"], "Móvil": ["600111222"]}


def test_robots_rules():
    r = RobotsRules.parse(BASE, "User-agent: *\nDisallow: /private/\nCrawl-delay: 2\nSitemap: https://example.com/sm.xml")
    assert r.allowed(BASE + "/ok", "Bot") and not r.allowed(BASE + "/private/x", "Bot")
    assert r.crawl_delay("Bot") == 2.0 and r.sitemaps == ["https://example.com/sm.xml"]
    assert not RobotsRules.disallow_all(BASE, "503").allowed(BASE + "/", "Bot")
    assert RobotsRules.allow_all(BASE, "404").allowed(BASE + "/private/", "Bot")


def test_robots_google_semantics_and_tester():
    from app.robots import test_urls
    text = ("User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\nDisallow: /*?s=\nDisallow: /*.pdf$\n\n"
            "User-agent: Googlebot\nDisallow: /solo-google/\n")
    r = RobotsRules.parse(BASE, text)
    assert not r.allowed(BASE + "/wp-admin/options.php", "letscrawl")
    assert r.allowed(BASE + "/wp-admin/admin-ajax.php", "letscrawl")  # longest match wins
    assert not r.allowed(BASE + "/?s=puertas", "letscrawl")            # wildcard
    assert not r.allowed(BASE + "/doc.pdf", "x") and r.allowed(BASE + "/doc.pdf?v=2", "x")  # $ anchor
    assert not r.allowed(BASE + "/solo-google/", "googlebot")
    assert r.allowed(BASE + "/wp-admin/", "googlebot")  # googlebot group replaces '*'
    res = test_urls(text, [BASE + "/wp-admin/x", BASE + "/ok"], "letscrawl")
    assert res[0]["allowed"] is False and "Disallow: /wp-admin/" in res[0]["rule"]
    assert res[1]["allowed"] is True


def test_crawl_config_validation():
    from app.crawlconfig import header_dict, user_agent
    s = get_settings()
    form = {"url": "misitio.es", "ack": "yes", "max_urls": "10", "delay": "1", "include": "/servicios/.*",
            "exclude": "(", "headers": "Cookie: x=1", "ua_preset": "custom", "ua_custom": "",
            "extract_expr_0": "a[", "extract_type_0": "css", "custom_robots": "Disallow: /"}
    cfg, errors = validate_crawl_form(form, s)
    assert cfg is None and {"exclude", "headers", "ua_custom", "extract_expr_0", "custom_robots"} <= set(errors)
    form.update(exclude=r"\?replytocom=", headers="Accept-Language: es-ES", ua_preset="googlebot_mobile",
                extract_expr_0="h1", custom_robots="User-agent: *\nDisallow: /privado/", render="yes",
                search_text_0="teléfono", search_name_0="Tel")
    cfg, errors = validate_crawl_form(form, s)
    assert not errors, errors
    assert cfg["robots_token"] == "googlebot" and "Googlebot" in cfg["user_agent"]
    assert cfg["render"] is True and cfg["check_external"] is False
    assert cfg["searches"] == [{"name": "Tel", "mode": "contains", "text": "teléfono"}]
    assert header_dict(cfg) == {"Accept-Language": "es-ES"}
    assert user_agent({"ua_preset": "default"}, s)[0] == s.user_agent


# --- analysis -----------------------------------------------------------------

def test_indexability():
    assert compute_indexability(page("/"))[0]
    assert compute_indexability(page("/", meta_robots="noindex"))[1] == "noindex"
    assert compute_indexability(page("/", x_robots="none"))[1] == "noindex"
    assert compute_indexability(page("/", canonical=BASE + "/other"))[1] == "Canonicalizada"
    assert compute_indexability(page("/", status_code=301))[1] == "Redirección 301"
    assert compute_indexability(page("/", blocked_by_robots=1))[1] == "Bloqueada por robots.txt"
    assert compute_indexability(page("/", content_type="application/pdf"))[1] == "Recurso (no HTML)"


def test_analyze_detects_core_issues():
    pages = [
        page("/"),
        page("/a", title="Duplicado título igual", content_hash="same"),
        page("/b", title="Duplicado título igual", content_hash="same"),
        page("/old", status_code=301, content_type=None, redirect_target=BASE + "/mid"),
        page("/mid", status_code=302, content_type=None, redirect_target=BASE + "/a"),
        page("/missing", status_code=404, content_type="text/html"),
        page("/down", status_code=None, content_type=None, error="ConnectError"),
        page("/orphan"),
        page("/noidx", meta_robots="noindex"),
        page("/conflict", meta_robots="noindex", canonical=BASE + "/a"),
        page("/c2r", canonical=BASE + "/old"),
        page("/nometa", title=None, meta_description="", h1=[]),
        page("/js", rendered=1, meta_robots=None, rendered_robots="noindex", rendered_canonical=BASE + "/x"),
        page("/blocked", blocked_by_robots=1, status_code=None),
    ]
    links = [link("/", p) for p in ("/a", "/b", "/old", "/missing", "/down", "/noidx", "/conflict", "/c2r",
                                    "/nometa", "/js", "/blocked")]
    links.append(link("/", "/a", nofollow=True))
    images = [{"page_url": BASE + "/a", "src": "x.png", "alt": None}, {"page_url": BASE + "/a", "src": "y.png", "alt": ""}]
    issues = analyze(pages, links, images, {BASE + "/orphan", BASE + "/noidx", BASE + "/old"}, BASE + "/")

    assert "broken_link" in codes(issues, "/missing")
    assert "server_error" in codes(issues, "/down")
    assert "redirect_chain" in codes(issues, "/old")
    assert "redirect_chain" not in codes(issues, "/mid")  # reported once, from chain start
    assert "link_to_redirect" in codes(issues, "/old")
    assert {"duplicate_title", "duplicate_content"} <= codes(issues, "/a")
    assert "orphan_candidate" in codes(issues, "/orphan")
    assert "orphan_candidate" not in codes(issues, "/a")
    assert "noindex_in_sitemap" in codes(issues, "/noidx")
    assert "non200_in_sitemap" in codes(issues, "/old")
    assert "canonical_noindex_conflict" in codes(issues, "/conflict")
    assert "canonical_to_non200" in codes(issues, "/c2r")
    assert {"missing_title", "missing_description", "missing_h1"} <= codes(issues, "/nometa")
    assert {"rendered_robots_conflict", "rendered_canonical_conflict"} <= codes(issues, "/js")
    assert "blocked_but_linked" in codes(issues, "/blocked")
    assert "nofollow_internal" in codes(issues, "/")
    alt = [i for i in issues if i.code == "images_missing_alt"]
    assert len(alt) == 1 and alt[0].evidence.startswith("1 imagen")

    # every issue is explained and actionable
    for i in issues:
        assert i.url and i.evidence and i.remediation and i.why and i.severity

    # prioritized: severity order, then size
    groups = group_issues(issues)
    order = ["critical", "high", "medium", "low", "info"]
    assert [order.index(g["severity"]) for g in groups] == sorted(order.index(g["severity"]) for g in groups)


def test_redirect_loop():
    pages = [page("/x", status_code=301, redirect_target=BASE + "/y"),
             page("/y", status_code=301, redirect_target=BASE + "/x")]
    pages[0]["content_type"] = pages[1]["content_type"] = None
    issues = analyze(pages, [link("/", "/x")], [], (), BASE + "/")
    # both URLs are redirect targets, so no chain start exists; loop must still not crash
    assert all(i.code != "redirect_chain" for i in issues)
    issues = analyze(pages + [page("/s", status_code=301, content_type=None, redirect_target=BASE + "/x")], [], [], (), None)
    assert "redirect_loop" in codes(issues, "/s")


def test_url_security_and_hreflang_issues():
    from app.analysis import url_problems
    assert url_problems("https://e.com/Cerrajería_Madrid?x=1") == {
        "url_uppercase", "url_underscore", "url_non_ascii", "url_parameters"}
    assert url_problems("https://e.com/a//b") == {"url_multiple_slashes"}
    pages = [
        page("/es", hreflang=[{"lang": "es", "href": BASE + "/es"}, {"lang": "en", "href": BASE + "/en"}],
             mixed=["http://cdn.e.com/x.js"], headers={"content-type": "text/html"}),
        page("/en", hreflang=[{"lang": "en", "href": BASE + "/en"}], headers={"strict-transport-security": "max-age=1"}),
        page("/Mal_URL"),
    ]
    issues = analyze(pages, [], [], (), BASE + "/es", None,
                     [{"url": "https://otro.com/x", "status_code": 404}])
    assert {"mixed_content", "missing_hsts", "hreflang_no_return"} <= codes(issues, "/es")
    assert "missing_hsts" not in codes(issues, "/en")
    assert {"url_uppercase", "url_underscore"} <= codes(issues, "/Mal_URL")
    assert any(i.code == "broken_external" and i.url == "https://otro.com/x" for i in issues)


def test_workbench_rows_and_filters():
    from app.views import build_rows, overview, query, response_times, site_structure, summary, tabs_for, title_px
    TAB_BY_KEY = {t.key: t for t in tabs_for(None)}
    assert title_px("iiii") < title_px("WWWW")
    assert 0 < title_px("Cerrajero 24 horas en Madrid") < 561
    pages = [page("/", h1=["Uno", "Dos"]), page("/a", title="Igual"), page("/b", title="Igual", meta_description=""),
             page("/r", status_code=301, content_type=None, redirect_target=BASE + "/a"),
             page("/x", status_code=404, content_type="text/html")]
    links = [link("/", "/a"), link("/", "/b"), link("/b", "/a"), link("/", "/x"),
             {"source_url": BASE + "/", "target_url": "https://otro.com/", "kind": "a", "internal": False, "nofollow": False}]
    images = [{"page_url": BASE + "/", "src": BASE + "/i.png", "alt": None}]
    data = build_rows(pages, links, images, [{"url": "https://otro.com/", "status_code": 200}])
    rows = {r["url"]: r for r in data["pages"]}
    assert rows[BASE + "/a"]["inlinks"] == 2 and rows[BASE + "/a"]["unique_inlinks"] == 2
    assert rows[BASE + "/"]["outlinks"] == 3 and rows[BASE + "/"]["external_outlinks"] == 1
    assert rows[BASE + "/a"]["dup_title"] and not rows[BASE + "/"]["dup_title"]

    def addrs(tab, f):
        return [r["address"].replace(BASE, "") for r in query(data, TAB_BY_KEY[tab], f)]

    assert addrs("titles", "duplicate") == ["/a", "/b"]
    assert addrs("descriptions", "missing") == ["/b"]
    assert addrs("h1", "multiple") == ["/"]
    assert addrs("codes", "3xx") == ["/r"] and addrs("codes", "4xx") == ["/x"]
    assert addrs("images", "missing_alt") == ["/i.png"]
    assert [r["address"] for r in query(data, TAB_BY_KEY["external"], "2xx")] == ["https://otro.com/"]
    by_inlinks = [r["address"].replace(BASE, "") for r in query(data, TAB_BY_KEY["internal"], "", sort="inlinks", desc=True)]
    assert by_inlinks[0] == "/a"
    ov = {g["key"]: g for g in overview(data)}
    titles = {f["key"]: f for f in ov["titles"]["filters"]}
    assert ov["titles"]["total"] == 3 and titles["duplicate"]["count"] == 2 and titles["duplicate"]["pct"] == 66.67
    s = {r["label"]: r["count"] for r in summary(data)}
    assert s["URLs internas en total"] == 5 and s["URLs externas en total"] == 1
    assert s["Total de URLs internas indexables"] == 3
    st = site_structure(data)
    assert st["tree"][0]["path"] == "/" and st["tree"][0]["count"] == 4  # HTML pages incl. the 404
    assert sum(b["count"] for b in response_times(data)) == 0  # fixtures have no response_ms


def test_resources_and_custom_tabs():
    from app.views import build_rows, query, tabs_for
    pages = [page("/", custom={"search": {"Tel": 0}, "extract": {"Precio": ["49 €"]}},
                  extra={"viewport": None, "headings": [2, 4], "h1_alt": True, "validation": {"doctype": False}})]
    links = [{"source_url": BASE + "/", "target_url": BASE + "/app.css", "kind": "resource", "internal": True}]
    externals = [{"url": BASE + "/app.css", "kind": "resource", "status_code": 404, "content_type": "text/css"},
                 {"url": BASE + "/big.jpg", "kind": "resource", "status_code": 200, "content_type": "image/jpeg", "size_bytes": 300_000}]
    images = [{"page_url": BASE + "/", "src": BASE + "/big.jpg", "alt": "Foto"}]
    data = build_rows(pages, links, images, externals)
    crawl = {"settings": {"searches": [{"name": "Tel", "mode": "not_contains", "text": "600"}],
                          "extractions": [{"name": "Precio", "type": "css", "expr": ".precio"}]}}
    tabs = {t.key: t for t in tabs_for(crawl)}
    a = lambda tab, f: [r["address"].replace(BASE, "") for r in query(data, tabs[tab], f)]
    assert a("internal", "css") == ["/app.css"] and a("codes", "4xx") == ["/app.css"]
    assert a("images", "over100kb") == ["/big.jpg"]
    assert a("h1", "alt_text") == ["/"] and a("h1", "non_sequential") == ["/"] and a("h1", "missing") == []
    assert a("mobile", "missing") == ["/"] and a("validation", "no_doctype") == ["/"]
    assert a("search", "s0") == ["/"]
    assert a("extract", "e0") == ["/"] and data["pages"][0]["extract::Precio"] == "49 €"
    issues = analyze(pages, links, images, (), BASE + "/", None, externals)
    assert {"broken_resource", "large_image"} <= {i.code for i in issues}
    assert {"missing_viewport", "heading_non_sequential"} <= codes(issues, "/")


# --- export -------------------------------------------------------------------

def test_csv_is_excel_safe():
    assert _safe_cell("=HYPERLINK(\"evil\")").startswith("'=")
    assert _safe_cell(["a", "b"]) == "a | b"
    data = to_csv([{"url": "https://e.com/", "title": "+cmd"}], ["url", "title"]).decode("utf-8")
    assert data.startswith("\ufeffurl;title") and "'+cmd" in data
    assert to_csv([{"a": "x", "n": 1.21, "e": ""}], ["a", "n", "e"], drop_empty=True).decode("utf-8") == "\ufeffa;n\r\nx;1,21\r\n"


def test_max_urls_unlimited_by_default():
    import dataclasses
    s = dataclasses.replace(get_settings(), max_urls_limit=0, default_max_urls=0)
    cfg, errors = validate_crawl_form({"url": "misitio.es", "delay": "1", "ack": "yes"}, s)
    assert not errors and cfg["max_urls"] == 0  # unticked box = no limit
    cfg, errors = validate_crawl_form({"url": "misitio.es", "max_urls": "300", "delay": "1", "ack": "yes"}, s)
    assert not errors and cfg["max_urls"] == 300
    capped = dataclasses.replace(s, max_urls_limit=1000)
    cfg, errors = validate_crawl_form({"url": "misitio.es", "delay": "1", "ack": "yes"}, capped)
    assert not errors and cfg["max_urls"] == 1000  # a safety cap in .env still applies
    cfg, errors = validate_crawl_form({"url": "misitio.es", "max_urls": "5000", "delay": "1", "ack": "yes"}, capped)
    assert "max_urls" in errors

def test_headings_skip_site_frame_and_language_detection():
    from bs4 import BeautifulSoup
    from app.crawler import is_cart_url, looks_like_resource
    from app.extract import page_headings, text_language
    s = BeautifulSoup('<div class="xoo-wsc-cart"><h2>Tu carrito (artículos: 0)</h2></div><h1>Kimono</h1>'
                      '<main><h2>Detalles</h2></main><nav><h2>Menú</h2></nav><footer><h2>Contacto</h2></footer>', "lxml")
    assert page_headings(s, "h1") == ["Kimono"] and page_headings(s, "h2") == ["Detalles"]
    assert text_language("El kimono es una prenda de Japón que se usa en la vida diaria y en las fiestas. " * 6) == "es"
    assert text_language("pocas palabras") is None
    assert is_cart_url("https://t.es/tienda/?add-to-cart=12") and not is_cart_url("https://t.es/tienda/?page=2")
    assert looks_like_resource("https://t.es/a.jpg") and not looks_like_resource("https://t.es/blog/")