"""English interface: the switch, the translation of pages and what must never be translated."""
from fastapi.testclient import TestClient
from app.i18n_en import EN

from app.i18n import tr, translate_html


def test_texts_and_patterns():
    assert tr("Configuración del rastreo") == "Crawl configuration"
    assert tr("▶ Empezar") == "▶ Start"
    assert tr("Ordenar por Código de respuesta") == "Sort by Status code"
    assert tr("13 fila(s)") == "13 row(s)"
    assert tr("Incidencias (10)") == "Issues (10)"
    assert tr("Nueva auditoría…") == "New audit…"
    assert tr("HTTP 404. Enlazada desde 1 página(s): https://x.es/") == "HTTP 404. Linked from 1 page(s): https://x.es/"
    assert tr("Cerrajería López") is None  # the crawled site's own words are left alone


def test_html_keeps_code_scripts_and_data():
    doc = ('<html lang="es"><body><button title="Cerrar">Cancelar</button><p>Las credenciales se guardan solo en <code>.env</code>'
           ', no aquí.</p><textarea placeholder="Buscar…">Borrar</textarea><td>Servicios de cerrajería</td>'
           "<script>var a = 'Por qué importa'; var b = 'Borrar';</script></body></html>")
    out = translate_html(doc, "en")
    assert '<html lang="en"' in out and 'title="Close"' in out and ">Cancel<" in out
    assert "Credentials are stored only in <code>.env</code>, not here." in out
    assert 'placeholder="Search…">Borrar</textarea>' in out          # what the user typed stays
    assert "<td>Servicios de cerrajería</td>" in out                  # site data stays
    assert "'Why it matters'" in out and "'Borrar'" in out            # only known script strings change
    assert translate_html(doc, "es") == doc


def test_language_switch_and_flags():
    from app.main import app
    with TestClient(app) as client:
        home = client.get("/").text
        assert 'class="lang-sw"' in home and 'title="Español"' in home and 'title="English"' in home
        r = client.get("/lang/en", params={"next": "/?cfg=crawl"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/?cfg=crawl" and "lang=en" in r.headers["set-cookie"]
        client.cookies.set("lang", "en")
        en = client.get("/").text
        assert "What do you want to check?" in en and "Crawl configuration" in en and '<html lang="en"' in en
        assert "¿Qué quieres revisar?" not in en
        assert 'title="Español"' in en  # each language keeps its own name on the flag
        evil = client.get("/lang/en", params={"next": "//evil.example/"}, follow_redirects=False)
        assert evil.headers["location"] == "/"
        assert client.get("/lang/xx").status_code == 404
        # French, Italian and Portuguese: every interface text and the language of the page
        for code, sample, crawl_cfg in (("fr", "Que voulez-vous vérifier ?", "Configuration de l'exploration"),
                                        ("it", "Cosa vuoi controllare?", "Configurazione della scansione"),
                                        ("pt", "O que quer verificar?", "Configuração do rastreamento"),
                                        ("de", "Was möchten Sie prüfen?", "Crawl-Konfiguration")):
            client.cookies.clear()
            client.cookies.set("lang", code)
            page = client.get("/").text
            assert sample in page and f'<html lang="{code}"' in page and "¿Qué quieres revisar?" not in page
            assert 'title="Français"' in page and 'title="Português"' in page


def test_other_languages_cover_every_text():
    import json
    from app import i18n
    for code in ("fr", "it", "pt", "de"):
        loc = i18n.LOCALES[code]
        assert set(loc["texts"]) == set(EN), code                      # nothing falls back to English
        assert set(loc["js"]) >= set(i18n.JS_TEXTS), code
        assert i18n.tr("27 URLs en esta vista", code) != "27 URLs in this view"
        assert "27" in i18n.tr("27 URLs en esta vista", code)
        assert json.dumps(loc)  # valid
    assert i18n.tr("6 páginas, incluida la portada", "fr") == "6 page(s), y compris la page d'accueil"
    assert i18n.tr("6 páginas", "it") == "6 pagina/e"
    assert i18n.tr("6 páginas, incluida la portada", "de") == "6 Seite(n), einschließlich der Startseite"
