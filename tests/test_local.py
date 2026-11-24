"""Local SEO: phone/WhatsApp/map/schema extraction and the crawl-level audit."""
from app.analysis import analyze
from app.extract import parse_html
from app.localseo import audit, norm_phone

BASE = "https://cerrajeria.test"
SCHEMA = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Locksmith","name":"Cerrajería López",'
          '"telephone":"+34 985 123 456","address":{"@type":"PostalAddress","streetAddress":"Calle Mayor 1",'
          '"addressLocality":"Gijón","postalCode":"33201"}}</script>')
FOOTER = "<footer>Cerrajería López · Tel. 985 12 34 56</footer>"


def test_norm_phone_variants():
    assert norm_phone("+34 985 12 34 56") == norm_phone("0034985123456") == norm_phone("985.123.456") == "985123456"


def test_extract_local_signals():
    html = (f"<html><head>{SCHEMA}</head><body><header><a href='tel:+34985123456'>Llamar</a></header>"
            "<main><p>Precio 1.250 € · CP 33201</p><a href='https://wa.me/34985123456'>WhatsApp</a>"
            "<iframe src='https://www.google.com/maps/embed?pb=1'></iframe></main>"
            "<footer>Móvil 611 22 33 44</footer></body></html>")
    local = parse_html(html, BASE + "/")["extra"]["local"]
    assert local["tel"] == ["985123456"] and local["phones"] == ["611223344"]  # prices / postcodes are not phones
    assert local["whatsapp"] and local["map_embed"]
    biz = local["biz"][0]
    assert biz["type"] == "Locksmith" and biz["telephone"] == "985123456" and biz["address"]["locality"] == "Gijón"
    assert not biz["hours"] and not biz["geo"]


def page(path, title, h1, body, head=""):
    html = f"<html><head><title>{title}</title>{head}</head><body><nav><a href='/contacto'>Contacto</a></nav><h1>{h1}</h1>{body}{FOOTER}</body></html>"
    d = parse_html(html, BASE + path)
    return {"url": BASE + path, "status_code": 200, "content_type": "text/html", **d}


ON = {"local_enabled": True}


def test_audit_finds_local_problems():
    pages = [
        page("/", "Cerrajero en Gijón 24 h", "Cerrajeros en Gijon", "<a href='tel:985123456'>Llamar</a>", SCHEMA),
        page("/apertura-de-puertas", "Apertura de puertas", "Apertura de puertas urgente", "<p>texto</p>"),
        page("/cambio-de-cerradura-gijon", "Cambio de cerradura en Gijón", "Cambio de cerradura en Gijón", "<p>texto</p>"),
        page("/contacto", "Contacto", "Contacto", "<p>Escríbenos</p>"),
        page("/aviso-legal", "Aviso legal", "Aviso legal", "<p>texto</p>"),
    ]
    links = [{"source_url": p["url"], "target_url": l["href"], "kind": "a", "internal": True} for p in pages for l in p["links"]]
    res = audit(pages, links, BASE + "/", ON)
    codes = {(c, u.replace(BASE, "")) for c, u, _ in res["issues"]}
    # city comes from the schema (addressLocality) and matches without accents
    assert ("local_city_missing", "/apertura-de-puertas") in codes
    assert ("local_city_missing", "/cambio-de-cerradura-gijon") not in codes and ("local_city_missing", "/") not in codes
    assert ("local_city_missing", "/aviso-legal") not in codes  # legal pages are not service pages
    # footer phone is text only -> not clickable on every page except home (which has the tel: link)
    assert ("local_phone_not_clickable", "/contacto") in codes and ("local_phone_not_clickable", "/") not in codes
    assert ("local_schema_incomplete", "/") in codes and ("local_no_schema", "/") not in codes
    assert ("local_no_whatsapp", "/") in codes and ("local_no_map", "/contacto") in codes
    assert not any(c == "local_contact_not_linked" for c, _ in codes)
    row = res["pages"][BASE + "/apertura-de-puertas"]
    assert row["local_city_title"] == "no" and row["local_service"] == "sí" and row["local_clickable"] == "no"
    assert res["site"]["cities"] == ["Gijón"] and res["site"]["main_phone"] == "985 123 456"
    labels = {c["label"]: c["ok"] for c in res["checklist"]}
    assert labels["Botón de WhatsApp"] is False and labels["Teléfono visible en todas las páginas"] is True

    # configured data overrides: another city and a phone that differs from the schema
    res2 = audit(pages, links, BASE + "/", {"local_city": "Oviedo", "local_phone": "611 000 000", "local_name": "Otra Marca"})
    codes2 = [c for c, _, _ in res2["issues"]]
    assert codes2.count("local_nap_inconsistent") == 2 and "local_city_missing" in codes2

    # analyze() includes them with severity, explanation and remediation
    issues = analyze(pages, links, start_url=BASE + "/", local=ON)
    i = next(i for i in issues if i.code == "local_phone_not_clickable")
    assert i.severity == "medium" and "tel:" in i.remediation


def test_local_audit_runs_only_when_asked():
    pages = [page("/", "Cerrajero en Gijón 24 h", "Cerrajeros en Gijon", "<a href='tel:985123456'>Llamar</a>", SCHEMA),
             page("/contacto", "Contacto", "Contacto", "<p>Escríbenos</p>")]
    assert audit(pages, [], BASE + "/", {})["issues"] == []  # not ticked, nothing filled in: no local SEO
    assert not any(i.code.startswith("local_") for i in analyze(pages, [], start_url=BASE + "/", local={}))
    assert audit(pages, [], BASE + "/", {"local_city": "Gijón"})["issues"]  # filling in the details is asking for it
    from app.views import tabs_for
    assert "local" not in [t.key for t in tabs_for({"settings": {}})]
    assert "local" in [t.key for t in tabs_for({"settings": ON})]


def test_audit_without_local_data_is_silent():
    old = [{"url": BASE + "/", "status_code": 200, "content_type": "text/html", "extra": {}}]
    assert audit(old, [], BASE + "/", {}) == {"available": False, "issues": [], "pages": {}, "site": {}, "checklist": []}
    assert audit(old[:0], [], BASE + "/", {})["available"] is False
