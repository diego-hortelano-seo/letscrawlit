"""Tests for the advanced features: similarity, validators, health/priorities, new extraction signals."""
from app.analysis import analyze, compute_indexability
from app.checks import a11y_checks, amp_checks, hreflang_code_error, spelling, validate_schema
from app.extract import parse_html
from app.insights import health
from app.textsim import clusters, link_suggestions, near_duplicates, plagiarism
from bs4 import BeautifulSoup

BASE = "https://example.com"
LOCK = ("Nuestro servicio de cerrajería urgente abre puertas en Madrid sin romper la cerradura. "
        "Cambiamos bombines de seguridad, instalamos cerraduras multipunto y reparamos persianas. ") * 6


def test_near_duplicates_and_plagiarism():
    long_text = " ".join(f"{w}{i}" for i, w in enumerate(["puerta", "cerradura", "bombin", "llave", "madrid"] * 60))
    a = long_text + " Trabajamos en Chamberí todos los días del año."
    b = long_text + " Trabajamos en Salamanca todos los días del año."
    c = ("Las reformas de cocina incluyen muebles a medida, encimeras de cuarzo y electrodomésticos integrados. " * 8)
    res = near_duplicates({"a": a, "b": b, "c": c}, threshold=0.8)
    assert set(res) == {"a", "b"} and res["a"][0] == "b" and 0.8 <= res["a"][1] < 1.0
    found = plagiarism(" ".join(long_text.split()[:80]), {"/cerrajeria": a, "/cocinas": c})
    assert found and found[0]["url"] == "/cerrajeria" and found[0]["passage"]
    assert plagiarism("texto sin relación alguna con nada de lo anterior para nada en absoluto hoy", {"/x": a}) == []


def test_clusters_and_link_suggestions():
    docs = {
        "/cerrajero-madrid": LOCK,
        "/cambio-bombin": "Cambio de bombín de seguridad antibumping en Madrid. El bombín protege la cerradura. " * 10,
        "/cocinas": "Reformas de cocina con muebles a medida y encimeras de cuarzo para tu cocina. " * 10,
        "/banos": "Reformas de cocina y baño: platos de ducha, muebles de baño y encimeras de cuarzo. " * 10,
    }
    groups = clusters(docs, threshold=0.2)
    sets = [set(g["urls"]) for g in groups]
    assert {"/cocinas", "/banos"} in sets
    titles = {"/cambio-bombin": "Cambio de bombín", "/cerrajero-madrid": "Cerrajero Madrid", "/cocinas": "Cocinas", "/banos": "Baños"}
    sugg = link_suggestions(docs, titles, existing=set(), min_sim=0.05)
    pair = next(s for s in sugg if s["source"] == "/cerrajero-madrid" and s["target"] == "/cambio-bombin")
    assert pair["anchor"] and "bomb" in pair["sentence"].lower() and pair["similarity"] > 0
    # an existing link suppresses the suggestion
    sugg2 = link_suggestions(docs, titles, existing={("/cerrajero-madrid", "/cambio-bombin")}, min_sim=0.05)
    assert not any(s["source"] == "/cerrajero-madrid" and s["target"] == "/cambio-bombin" for s in sugg2)


def test_validators():
    assert hreflang_code_error("es-ES") is None and hreflang_code_error("x-default") is None
    assert hreflang_code_error("zh-Hant-TW") is None
    assert "región" in hreflang_code_error("es-SP") and "idioma" in hreflang_code_error("xx")
    assert hreflang_code_error("uk") is None  # Ukrainian is valid
    assert "región" in hreflang_code_error("en-UK")  # the UK region code is GB
    assert "guion" in hreflang_code_error("es_ES")
    probs = validate_schema([{"@context": "https://schema.org", "@type": "LocalBusiness", "name": "Cerrajería"},
                             {"@type": "Product", "name": "Bombín", "offers": {"@type": "Offer"}}])
    msgs = " | ".join(p["message"] for p in probs)
    assert "LocalBusiness: falta la propiedad obligatoria «address»" in msgs
    assert "Offer: falta la propiedad obligatoria «price» o «priceSpecification»" in msgs
    assert "sin @context" in msgs
    soup = BeautifulSoup("<html amp><head></head><body><img src=x><script>alert(1)</script></body></html>", "html.parser")
    amp = amp_checks(soup, "")
    assert amp["is_amp"] and any("amp-img" in e for e in amp["errors"]) and any("JavaScript" in e for e in amp["errors"])
    a11y = a11y_checks(BeautifulSoup('<input type=text><button></button><a href="/x"></a><h2></h2>'
                                     '<p id=a></p><p id=a></p><iframe src=y></iframe><div tabindex=3></div>', "html.parser"))
    assert a11y["unlabeled_inputs"] == 1 and a11y["unnamed_buttons"] == 1 and a11y["unnamed_links"] == 1
    assert a11y["empty_headings"] == 1 and a11y["duplicate_ids"] == ["a"] and a11y["iframes_no_title"] == 1
    assert a11y["positive_tabindex"] == 1
    sp = spelling("Las puertas de todos los clientes tienen una cerradura nueva y un bonbín rotto. Madrid", "es", {"rotto"})
    assert sp["words"] == ["bonbín"] and sp["count"] == 1  # inflections, proper nouns and custom words are skipped
    from app.checks import effective_spelling
    assert effective_spelling(sp, {"bonbín"})["count"] == 0
    assert spelling("texto", "tlh") is None


def test_extraction_new_signals():
    html = """<!doctype html><html lang="es"><head><meta http-equiv="refresh" content="0; url=/nueva">
      <link rel="alternate" hreflang="es-SP" href="/es"><script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization"}</script>
      </head><body><div typeof="schema:LocalBusiness"></div><h1>Hola</h1>
      <a href="/socio" rel="sponsored nofollow">Patrocinado</a><a href="/foro" rel="ugc">Comentario</a><a href="/app#/ruta">SPA</a>
      <span class="precio">49 €</span></body></html>"""
    d = parse_html(html, BASE + "/vieja", extractions=[{"name": "Precio", "type": "xpath", "expr": "//span[@class='precio']"}])
    assert d["extra"]["meta_refresh"] == {"delay": 0, "target": BASE + "/nueva"}
    rels = {l["href"].rsplit("/", 1)[-1]: l["rel"] for l in d["links"]}
    assert rels["socio"] == "sponsored nofollow" and rels["foro"] == "ugc"
    assert next(l for l in d["links"] if "app" in l["href"])["fragment"] == "/ruta"
    assert d["structured"]["rdfa"] == ["schema:LocalBusiness"]
    assert any("name" in p["message"] for p in d["extra"]["schema_problems"])
    assert d["custom"]["extract"]["Precio"] == ["49 €"]
    page = {"url": BASE + "/vieja", "status_code": 200, "content_type": "text/html", "extra": d["extra"]}
    assert compute_indexability(page)[1] == "Redirección (meta refresh)"


def test_new_issues_and_health():
    extra = {"meta_refresh": {"delay": 0, "target": BASE + "/n"}, "spelling": {"count": 4, "words": ["aa", "bb", "cc", "dd"]},
             "a11y": {"unlabeled_inputs": 2}, "schema_problems": [{"level": "error", "message": "X: falta"}],
             "amp": {"is_amp": True, "errors": ["Falta v0.js"]}}
    pages = [
        {"url": BASE + "/", "status_code": 200, "content_type": "text/html", "title": "Inicio de la web de prueba",
         "h1": ["Inicio"], "meta_description": "d", "canonical": BASE + "/", "extra": extra,
         "hreflang": [{"lang": "es-SP", "href": BASE + "/"}]},
        {"url": BASE + "/x", "status_code": None, "error": "ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]"},
    ]
    externals = [{"url": BASE + "/foto.jpg", "kind": "resource", "status_code": 200, "content_type": "image/jpeg", "size_bytes": 80000}]
    links = [{"source_url": BASE + "/", "target_url": BASE + "/foto.jpg", "kind": "resource", "internal": True}]
    analytics = [{"url": BASE + "/huerfana", "clicks": 25}]
    issues = analyze(pages, links, [], (), BASE + "/", None, externals, analytics=analytics,
                     near_dups={BASE + "/": [BASE + "/y", 0.93, 1]})
    codes = {i.code for i in issues}
    assert {"meta_refresh_redirect", "spelling_errors", "accessibility", "schema_error", "amp_error",
            "hreflang_invalid_code", "ssl_error", "legacy_image_format", "orphan_with_traffic", "near_duplicate"} <= codes
    h = health(pages, [i.as_dict() for i in issues], links, BASE + "/")
    assert 0 <= h["score"] < 100 and h["actions"][0]["severity"] in ("critical", "high")
    assert any("página de inicio" in " ".join(a["reasons"]) for a in h["actions"])
