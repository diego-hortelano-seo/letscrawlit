"""Visual extractor, historical trends, bulk AI and the shared spelling dictionary."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

PRODUCT = """<!doctype html><html><head><title>Cerradura</title><script>alert(1)</script>
<meta http-equiv="refresh" content="5; url=/otra"></head>
<body onload="evil()"><h1>Cerradura antibumping</h1><span class="precio" data-price="49.90">49,90 €</span>
<a href="javascript:alert(2)">x</a><iframe src="/anuncio"></iframe></body></html>"""


@pytest.fixture
def product_site():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/robots.txt":
                body, ctype = b"User-agent: *\nDisallow: /privado\n", "text/plain"
            else:
                body, ctype = PRODUCT.encode(), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_visual_picker_returns_a_safe_copy(product_site):
    from app.main import app
    with TestClient(app) as client:
        data = client.post("/tools/pick", data={"pick_url": product_site + "/producto"}).json()
        assert "Cerradura antibumping" in data["html"] and 'class="precio"' in data["html"]
        for bad in ("<script", "onload=", "javascript:", "<iframe", "http-equiv"):
            assert bad not in data["html"], bad
        # addresses are made absolute on the server; a <base> tag would be refused by the app's security policy
        assert "<base" not in data["html"] and data["url"] == product_site + "/producto"
        assert "robots.txt" in client.post("/tools/pick", data={"pick_url": product_site + "/privado/x"}).json()["error"]
        assert "error" in client.post("/tools/pick", data={"pick_url": "no es una url"}).json()


def test_xpath_attribute_extraction_works_in_the_crawl_extractor():
    from app.extract import custom_data
    from bs4 import BeautifulSoup
    out = custom_data(BeautifulSoup(PRODUCT, "html.parser"), PRODUCT, [],
                      [{"name": "Precio", "type": "xpath", "expr": '//span[contains(concat(" ", normalize-space(@class), " "), " precio ")]/@data-price'},
                       {"name": "Texto", "type": "css", "expr": "span.precio"}])
    assert out["extract"] == {"Precio": ["49.90"], "Texto": ["49,90 €"]}


def _crawl_with_pages(urls):
    from app import db
    from app.validation import ACK_TEXT
    db.init_db()
    cid = db.create_crawl(urls[0], {"start_url": urls[0], "max_urls": 10, "delay": 0}, ACK_TEXT)
    for u in urls:
        db.save_page(cid, {"url": u, "depth": 0, "status_code": 200, "content_type": "text/html", "title": "T " + u,
                           "text_excerpt": "Comprar cerraduras baratas online", "h1_json": "[]", "h2_json": "[]"})
    db.update_crawl(cid, status="completed", finished_at=db.now(), pages_done=len(urls))
    return cid


def test_bulk_ai_fills_a_column(monkeypatch):
    from app import ai, aibulk, db, views
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "openai", "model": "m", "note": ""})
    seen = []
    monkeypatch.setattr(ai, "run_task", lambda task, page, prompt="": (seen.append((task, prompt)) or ("transaccional", "m")))
    cid = _crawl_with_pages(["https://x.es/", "https://x.es/a", "https://x.es/b"])
    aibulk.start(cid, "intent", ["https://x.es/", "https://x.es/a", "https://x.es/b"])
    deadline = time.time() + 10
    while aibulk.status(cid)["running"] and time.time() < deadline:
        time.sleep(0.05)
    assert aibulk.status(cid)["done"] == 3 and len(db.load_ai(cid)) == 3
    tab = views.tab_by_key(db.get_crawl(cid), "ai")
    rows = views.query(views.workbench(db.get_crawl(cid)), tab, "intent_transaccional")
    assert len(rows) == 3 and rows[0]["ai_intent"] == "transaccional"
    with pytest.raises(ai.AIUnavailable):
        aibulk.start(cid, "custom", ["https://x.es/"], "")          # custom needs an instruction
    with pytest.raises(ai.AIUnavailable):
        aibulk.start(cid, "faq", ["https://x.es/"])                 # not a column-friendly task
    # three failures in a row stop the job instead of burning 500 requests
    def boom(task, page, prompt=""):
        raise ai.AIUnavailable("Clave no válida")
    monkeypatch.setattr(ai, "run_task", boom)
    aibulk.start(cid, "topic", [f"https://x.es/{i}" for i in range(20)])
    while aibulk.status(cid)["running"]:
        time.sleep(0.05)
    assert aibulk.status(cid)["done"] == 3 and "3 errores seguidos" in aibulk.status(cid)["last_error"]


def test_trends_csv_chart_and_schedule_guard():
    from app import history
    from app.main import app
    cid1 = _crawl_with_pages(["https://tendencias.es/", "https://tendencias.es/a"])
    cid2 = _crawl_with_pages(["https://tendencias.es/", "https://tendencias.es/a", "https://tendencias.es/b"])
    csv_text = history.trends_csv("tendencias.es").decode("utf-8-sig")
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("fecha,auditoria,sitio,pages") and "images_mb" in lines[0] and len(lines) == 3
    ch = history.chart(history.trends("tendencias.es")["points"], "pages")
    assert [d["v"] for d in ch["dots"]] == [2, 3] and ch["path"].startswith("M")
    with TestClient(app) as client:
        page = client.get("/trends/tendencias.es?metric=pages").text
        assert "trend-chart" in page and f"/crawls/{cid2}" in page and ("Crear tarea programada" in page or "cron" in page)
        assert client.get("/trends/tendencias.es.csv").status_code == 200
        assert client.post("/trends/tendencias.es/schedule", data={"time": "03:00", "freq": "weekly"}).status_code == 400  # no ack
        assert client.post("/trends/tendencias.es/schedule", data={"ack": "yes", "time": "25:00", "freq": "weekly"}).status_code == 400


def test_shared_dictionary(tmp_path, monkeypatch):
    from app import dictionary
    shared = tmp_path / "Dropbox" / "equipo" / "diccionario.txt"
    monkeypatch.setenv("LETSCRAWL_SHARED_DICTIONARY", str(shared))
    assert dictionary.shared() and dictionary.words() == set()
    assert dictionary.add("Bombín multipunto") == 2 and dictionary.add("bombín") == 0
    shared.write_text(shared.read_text(encoding="utf-8") + "antibumping", encoding="utf-8")  # a teammate adds one
    assert dictionary.add("Cerrajería") == 1
    assert dictionary.words() == {"bombín", "multipunto", "antibumping", "cerrajería"}
    assert dictionary.replace("# jerga del cliente\nzeta\nalfa\nalfa\n") == 2
    assert shared.read_text(encoding="utf-8") == "# jerga del cliente\nalfa\nzeta\n"
    from app.main import app
    with TestClient(app) as client:
        assert "bombín" in client.post("/dictionary/add", data={"word": "Bombín"}).text
        assert client.post("/dictionary/add", data={"word": "dos palabras juntas y mas " * 20}).status_code in (200, 400)
    assert "bombín" in dictionary.words()


def test_dictionary_applies_to_existing_crawls(monkeypatch, tmp_path):
    """Words added later stop being reported without crawling again."""
    from app import db, dictionary, views
    monkeypatch.setenv("LETSCRAWL_SHARED_DICTIONARY", str(tmp_path / "d.txt"))
    cid = _crawl_with_pages(["https://orto.es/"])
    db.save_page(cid, {"url": "https://orto.es/", "depth": 0, "status_code": 200, "content_type": "text/html", "title": "T",
                       "h1_json": "[]", "h2_json": "[]", "extra_json": '{"spelling": {"lang": "es", "count": 2, "words": ["presupesto", "cerradra"]}}'})
    row = lambda: next(r for r in views.workbench(db.get_crawl(cid))["pages"] if r["url"] == "https://orto.es/")
    assert row()["spelling_count"] == 2
    dictionary.add("presupesto")
    assert row()["spelling_count"] == 1 and row()["spelling_words"] == "cerradra"
