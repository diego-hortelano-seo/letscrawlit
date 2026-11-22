"""End-to-end happy paths: real HTTP sites on localhost -> UI -> crawl -> workbench -> exports."""
import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient


def _html(title, body, head=""):
    return (f"<html lang='es'><head><title>{title}</title>"
            f"<meta name='description' content='Descripción de {title}'>{head}</head><body>{body}</body></html>")


LONG = " ".join(["contenido"] * 200)


def build_site(base, ext):
    return {
        "/robots.txt": (200, "text/plain", f"User-agent: *\nDisallow: /private/\nSitemap: {base}/sitemap.xml\n", {}),
        "/sitemap.xml": (200, "application/xml",
                         "<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9' "
                         "xmlns:image='http://www.google.com/schemas/sitemap-image/1.1'>"
                         + "".join(f"<url><loc>{base}{p}</loc></url>" for p in ("/", "/about", "/orphan", "/noindex"))
                         + f"<url><loc>{base}/about</loc><image:image><image:loc>{base}/galeria-1024x683.jpg</image:loc>"
                           "</image:image></url></urlset>", {}),
        "/galeria-1024x683.jpg": (200, "image/jpeg", "jpg", {}),
        "/slow": (200, "text/html", _html("Página lenta solo la primera vez", "<h1>Lenta</h1>" + LONG), {"X-Slow-Once": "1"}),
        "/": (200, "text/html", _html("Inicio del sitio de pruebas",
              "<h1>Inicio</h1><a href='/about'>Sobre</a><a href='/old'>Antigua</a><a href='/missing'>Rota</a>"
              "<a href='/private/secret'>Privada</a><a href='/dup1'>D1</a><a href='/dup2'>D2</a>"
              "<a href='/nf' rel='nofollow'>NF</a><a href='/noindex'>NI</a><img src='/logo.png'>"
              f"<a href='{ext}/ok'>Externo bien</a><a href='{ext}/gone'>Externo roto</a>"
              "<a href='/refresh'>Refresco</a><a href='/socio' rel='sponsored'>Socio</a><a href='/slow'>Lenta</a>" + LONG,
              f"<link rel='canonical' href='{base}/'>"
              "<script type='application/ld+json'>{\"@type\":\"LocalBusiness\"}</script>"), {}),
        "/about": (200, "text/html", _html("Sobre nosotros en el sitio", "<h1>Sobre</h1><a href='/'>Inicio</a>" + LONG,
                   f"<link rel='canonical' href='{base}/about'>"), {}),
        "/old": (301, "text/html", "", {"Location": "/older"}),
        "/older": (301, "text/html", "", {"Location": "/about"}),
        "/dup1": (200, "text/html", _html("Página duplicada idéntica", "<h1>Dup</h1>" + LONG), {}),
        "/dup2": (200, "text/html", _html("Página duplicada idéntica", "<h1>Dup</h1>" + LONG), {}),
        "/orphan": (200, "text/html", _html("Página huérfana del sitio", "<h1>Huérfana</h1>" + LONG), {}),
        "/noindex": (200, "text/html", _html("Página que no se indexa", "<h1>NI</h1>" + LONG,
                     "<meta name='robots' content='noindex'>"), {}),
        "/nf": (200, "text/html", _html("No debe rastrearse nunca", "<h1>NF</h1>"), {}),
        "/refresh": (200, "text/html", _html("Página que redirige con meta", "<h1>R</h1>",
                     "<meta http-equiv='refresh' content='0; url=/about'>"), {}),
        "/private/secret": (200, "text/html", _html("Secreto", "<h1>S</h1>"), {}),
    }


def serve(routes, hits, agents=None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, with_body):
            hits.append((self.command, self.path))
            if agents is not None:
                agents.append((self.headers.get("User-Agent"), self.headers.get("Accept-Language")))
                COOKIES.append(self.headers.get("Cookie") or "")
            status, ctype, body, headers = routes.get(self.path, (404, "text/html", "<h1>404</h1>", {}))
            if headers.get("X-Slow-Once") and sum(1 for _, p in hits if p == self.path) == 1:
                time.sleep(1.3)  # simulates crawl-time noise: only the first request is slow
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if with_body:
                self.wfile.write(data)

        def do_GET(self):
            self._send(True)

        def do_HEAD(self):
            self._send(False)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


AGENTS: list = []  # (User-Agent, Accept-Language) of every request to the audited site
COOKIES: list = []  # Cookie header of every request to the audited site


@pytest.fixture
def site():
    hits, ext_hits = [], []
    ext_routes = {"/ok": (200, "text/html", "<p>ok</p>", {})}
    ext_server = serve(ext_routes, ext_hits)
    ext = f"http://localhost:{ext_server.server_address[1]}"  # different host => external
    routes = {}
    AGENTS.clear()
    COOKIES.clear()
    server = serve(routes, hits, AGENTS)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    routes.update(build_site(base, ext))
    yield base, ext, hits, ext_hits
    server.shutdown()
    ext_server.shutdown()


def wait_done(client, crawl_url):
    deadline = time.time() + 60
    while time.time() < deadline:
        status = client.get(f"{crawl_url}/status").text
        if any(s in status for s in ("Completado", "Error", "Detenido")):
            return status
        time.sleep(0.2)
    raise AssertionError("crawl did not finish")


def test_happy_path(site):
    base, ext, hits, ext_hits = site
    from app.main import app

    with TestClient(app) as client:
        assert "Aún no hay auditorías" in client.get("/").text  # empty state

        # permission is mandatory
        r = client.post("/crawls", data={"url": base + "/", "max_urls": "50", "delay": "0"})
        assert r.status_code == 422 and "permiso" in r.text.lower()

        r = client.post("/crawls", data={"url": base + "/", "max_urls": "50", "delay": "0", "ack": "yes",
                                         "check_external": "yes"}, follow_redirects=False)
        assert r.status_code == 303
        crawl_url = r.headers["location"]
        crawl_id = int(crawl_url.rsplit("/", 1)[1])
        assert "Completado" in wait_done(client, crawl_url)

        # politeness: robots-disallowed and nofollow-only URLs were never requested
        paths = [p for _, p in hits]
        assert "/private/secret" not in paths and "/nf" not in paths
        # external links: one HEAD each, never crawled further
        assert ("HEAD", "/ok") in ext_hits and ("HEAD", "/gone") in ext_hits
        assert not any(m == "GET" and p in ("/ok", "/gone") for m, p in ext_hits)

        # workbench: tabs, overview, filters, sorting, detail pane
        page = client.get(crawl_url).text
        for label in ("Internos", "Externo", "Códigos de respuesta", "Títulos de página", "Meta description", "Canonicals",
                      "Directivas", "Hreflang", "Seguridad", "Paginación", "Enlaces", "Sitemaps", "Móvil", "Validación",
                      "Errores", "Resumen", "URLs encontradas en total", "Configurar pestañas"):
            assert label in page, label
        for pane in ("issues", "structure", "times"):
            side = client.get(f"{crawl_url}/side?pane={pane}", headers={"HX-Request": "true"})
            assert side.status_code == 200 and 'id="side"' in side.text
        side_issues = client.get(f"{crawl_url}/side?pane=issues", headers={"HX-Request": "true"}).text
        assert "Enlace interno roto" in side_issues
        # health score with a traffic light, and [?] help that explains each problem
        overview = client.get(f"{crawl_url}/side?pane=overview", headers={"HX-Request": "true"}).text
        assert 'class="hc hc-' not in overview and "/100" not in overview  # no calculated health score in the workbench
        assert 'class="qhelp"' in overview and "data-fix=" in overview
        assert 'class="qhelp"' in side_issues and "desperdician presupuesto de rastreo" in side_issues
        grid_help = client.get(f"{crawl_url}/grid?tab=h1&f=missing", headers={"HX-Request": "true"}).text
        assert 'class="qhelp"' in grid_help and "Modo principiante" in grid_help
        assert "Exportar resumen" in grid_help
        grid = client.get(f"{crawl_url}/grid?tab=sitemaps&f=orphan", headers={"HX-Request": "true"}).text
        assert base + "/orphan" in grid
        grid = client.get(f"{crawl_url}/grid?tab=codes&f=4xx", headers={"HX-Request": "true"}).text
        assert base + "/missing" in grid and base + "/about" not in grid and "<html" not in grid
        grid = client.get(f"{crawl_url}/grid?tab=titles&f=duplicate").text
        assert base + "/dup1" in grid and base + "/dup2" in grid
        grid = client.get(f"{crawl_url}/grid?tab=external&f=4xx", headers={"HX-Request": "true"}).text
        assert ext + "/gone" in grid and ext + "/ok" not in grid
        grid = client.get(f"{crawl_url}/grid?tab=internal&sort=inlinks&dir=desc", headers={"HX-Request": "true"}).text
        assert grid.index(base + "/about") < grid.index(base + "/orphan")
        detail = client.get(f"/crawls/{crawl_id}/detail", params={"url": base + "/missing", "sub": "inlinks"},
                            headers={"HX-Request": "true"}).text
        assert base + "/" in detail and "Rota" in detail

        errors_only = client.get(f"{crawl_url}/grid?tab=issues", headers={"HX-Request": "true"}).text
        assert "Enlace interno roto" in errors_only and "Título duplicado" not in errors_only  # «Errores»: only real faults
        assert "ver también los avisos" in errors_only
        issues = client.get(f"{crawl_url}/grid?tab=issues&f=_all", headers={"HX-Request": "true"}).text
        for title in ("Enlace interno roto", "Cadena de redirecciones", "Título duplicado",
                      "Candidata a página huérfana", "Página noindex incluida en el sitemap",
                      "Imágenes sin atributo alt", "Contenido duplicado exacto", "Enlace externo roto",
                      "Página servida por HTTP"):
            assert title in issues, title

        # exports
        csv_issues = client.get(f"{crawl_url}/export/issues.csv")
        assert csv_issues.status_code == 200 and "attachment" in csv_issues.headers["content-disposition"]
        assert "broken_link" in csv_issues.text and base + "/missing" in csv_issues.text
        view = client.get(f"{crawl_url}/export/view.csv?tab=codes&f=4xx")
        assert view.text.startswith("\ufeffURL;") and base + "/missing" in view.text
        assert base + "/about" not in view.text
        # client deck and one Excel per error type: only faults that are always errors
        import io, zipfile
        from pptx import Presentation
        from openpyxl import load_workbook
        deck = client.get(f"{crawl_url}/export/presentacion.pptx")
        assert deck.status_code == 200 and "presentacion.pptx" in deck.headers["content-disposition"]
        slides = Presentation(io.BytesIO(deck.content)).slides
        assert 3 <= len(slides) <= 10
        deck_text = " ".join(sh.text_frame.text for s in slides for sh in s.shapes if sh.has_text_frame)
        assert "Enlace interno roto" in deck_text and "Título duplicado" not in deck_text
        xz = zipfile.ZipFile(io.BytesIO(client.get(f"{crawl_url}/export/errores-excel.zip").content))
        assert xz.namelist() and all(n.endswith(".xlsx") for n in xz.namelist())
        first = load_workbook(io.BytesIO(xz.read(xz.namelist()[0]))).active
        assert [c.value for c in first[1]][:2] == ["URL", "Evidencia"] and first.max_row > 1
        report = client.get(f"{crawl_url}/export/report.html").text
        assert "Auditoría SEO" in report and "<style>" in report
        assert "src=\"http" not in report and "<link rel=\"stylesheet\"" not in report  # self-contained

        # sitemap <image:loc> is not crawled as a page; slow-once page is re-measured
        from app import db as _db
        urls = {p["url"]: p for p in _db.load_pages(crawl_id)}
        assert base + "/galeria-1024x683.jpg" not in urls and ("GET", "/galeria-1024x683.jpg") not in hits
        slow = urls[base + "/slow"]
        assert slow["response_ms"] < 1000 and slow["extra"]["response_ms_first"] >= 1000
        pages_csv = client.get(f"{crawl_url}/export/pages.csv").text
        assert "self_links" in pages_csv.splitlines()[0] and "words_total" in pages_csv.splitlines()[0]

        z = client.get(f"{crawl_url}/export/bundle.zip")
        zf = zipfile.ZipFile(io.BytesIO(z.content))
        manifest_name = next(n for n in zf.namelist() if n.endswith("manifest.json"))
        assert "self_links" in json.loads(zf.read(manifest_name))["counting_rules"]
        names = zf.namelist()
        assert {n.split("/")[-1] for n in names} == {"pages.csv", "issues.csv", "links.csv", "images.csv",
                                                     "externals.csv", "report.html", "informe-cliente.html", "manifest.json"}

        # client report: plain language, branding remembered locally, self-contained download
        saved = client.post(f"{crawl_url}/client-report/branding", data={"client": "Cerrajería Pérez", "brand": "Mi Agencia",
                                                                         "contact": "miagencia.test", "color": "#123456"},
                            follow_redirects=False)
        assert saved.status_code == 303
        cr = client.get(saved.headers["location"])
        assert cr.status_code == 200 and "¿Cómo está tu web, Cerrajería Pérez?" in cr.text
        assert "Lo primero que arreglaría" in cr.text and "Los 7 puntos básicos" in cr.text and "Indexación" in cr.text and "Tu negocio en Google" not in cr.text and "#123456" in cr.text
        again = client.get(f"{crawl_url}/client-report").text
        assert "Mi Agencia" in again  # saved to data/branding.json
        dl = client.get(f"{crawl_url}/client-report", params={"download": 1, "client": "Pérez"})
        assert "attachment" in dl.headers["content-disposition"] and "<form" not in dl.text and "src=\"http" not in dl.text
        # white-label: logo (type checked by its bytes), charts, PDF
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        up = client.post(f"{crawl_url}/client-report/logo", files={"file": ("logo.png", png, "image/png")}, follow_redirects=False)
        assert up.status_code == 303
        page_html = client.get(f"{crawl_url}/client-report").text
        assert "data:image/png;base64," in page_html and "Tu web en gráficos" in page_html and "Avisos por importancia" in page_html
        bad = client.post(f"{crawl_url}/client-report/logo", files={"file": ("x.png", b"<html><script>", "image/png")})
        assert bad.status_code == 400 and "PNG, JPG, WebP o SVG" in bad.text
        pdf = client.get(f"{crawl_url}/client-report/pdf")
        assert (pdf.status_code == 200 and pdf.content.startswith(b"%PDF")) or pdf.status_code == 503
        # two-page summary for a boss: good-vs-problems chart, only the top 3 fixes
        short = client.get(f"{crawl_url}/client-report", params={"summary": 1}).text
        assert "Páginas bien frente a páginas con algún problema" in short and "En resumen" in short
        assert "Tu negocio en Google" not in short and "Avisos por importancia" not in short
        assert short.count('<div class="action-title">') <= 3 and "page2" in short
        sdl = client.get(f"{crawl_url}/client-report", params={"summary": 1, "download": 1})
        assert "resumen-" in sdl.headers["content-disposition"]
        local_tab = client.get(f"{crawl_url}/grid", params={"tab": "local"})
        assert local_tab.status_code == 200

        # notes per URL: saved for this audit, shown in the grid, tab and CSV (column «Anotación Consultor»)
        target = base + "/missing"
        assert client.get(f"{crawl_url}/note", params={"url": target}).json()["status"] == "pendiente"
        saved = client.post(f"{crawl_url}/note", data={"url": target, "note": "Corregir este enlace", "status": "en curso"})
        assert saved.status_code == 200 and saved.json()["status"] == "en curso"
        assert client.post(f"{crawl_url}/note", data={"url": target, "note": "x", "status": "inventado"}).status_code == 400
        assert client.post(f"{crawl_url}/note", data={"url": "javascript:alert(1)", "note": "x"}).status_code == 400
        notes_tab = client.get(f"{crawl_url}/grid", params={"tab": "notes"}).text
        assert "Corregir este enlace" in notes_tab and "has-note" in notes_tab
        pages_csv = client.get(f"{crawl_url}/export/pages.csv").text
        assert "Corregir este enlace" in pages_csv and "Anotación Consultor" in pages_csv.splitlines()[0]
        assert "Corregir este enlace" in client.get(f"{crawl_url}/detail", params={"src": "pages", "url": target}).text
        client.post(f"{crawl_url}/note", data={"url": target, "note": "", "status": "pendiente"})  # empty = delete
        assert "Corregir este enlace" not in client.get(f"{crawl_url}/grid", params={"tab": "notes"}).text
        assert all(".." not in n and "\\" not in n for n in names)

        # recoverable errors
        assert client.get("/crawls/9999").status_code == 404
        assert client.post(f"{crawl_url}/cancel", headers={"Origin": "https://evil.example"}).status_code == 403


def test_list_mode(site):
    base, ext, hits, _ = site
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/crawls", data={"mode": "list", "urls": f"{base}/about\n{base}/missing\n\n{base}/about",
                                         "max_urls": "50", "delay": "0", "ack": "yes"}, follow_redirects=False)
        assert r.status_code == 303
        crawl_url = r.headers["location"]
        assert "Completado" in wait_done(client, crawl_url)
        grid = client.get(f"{crawl_url}/grid?tab=internal", headers={"HX-Request": "true"}).text
        assert "2 fila(s)" in grid  # deduplicated list, links not followed
        paths = [p for _, p in hits]
        assert "/" not in paths and "/dup1" not in paths

        r = client.post("/crawls", data={"mode": "list", "urls": "not a url", "ack": "yes"})
        assert r.status_code == 422 and "Línea 1" in r.text


def test_advanced_workflow(site, tmp_path):
    base, ext, hits, _ = site
    from app import db
    from app.cli import main as cli_main
    from app.main import app

    with TestClient(app) as client:
        hx = {"HX-Request": "true"}
        r = client.post("/crawls", data={"url": base + "/", "max_urls": "60", "delay": "0", "ack": "yes", "threads": "3",
                                         "check_resources": "yes"}, follow_redirects=False)
        crawl_url = r.headers["location"]
        cid = int(crawl_url.rsplit("/", 1)[1])
        assert "Completado" in wait_done(client, crawl_url)

        # sitemap image:loc becomes a resource with its origin
        img = client.get(f"{crawl_url}/grid?tab=internal&f=image&q=galeria", headers=hx).text
        assert "galeria-1024x683.jpg" in img and "sitemap (image:loc)" in img
        assert ("HEAD", "/galeria-1024x683.jpg") in hits

        # meta refresh is detected and followed; sponsored rel is recorded
        grid = client.get(f"{crawl_url}/grid?tab=codes&f=meta_refresh", headers=hx).text
        assert base + "/refresh" in grid
        assert base + "/" in client.get(f"{crawl_url}/grid?tab=links&f=sponsored", headers=hx).text

        # insights, graph, detail extras
        page = client.get(f"{crawl_url}/insights").text
        assert "/100" not in page and "Mejora estimada" not in page and "URLs sin problemas" in page and "Qué arreglar primero" in page and "Clústeres semánticos" in page
        g = client.get(f"{crawl_url}/graph.json").json()
        assert len(g["nodes"]) >= 5 and g["edges"] and g["root"] is not None
        path = client.get(f"/crawls/{cid}/detail", params={"url": base + "/about", "sub": "path"}, headers=hx).text
        assert "inicio" in path and base + "/about" in path
        for sub in ("serp", "quality", "screenshot", "waterfall", "headers"):
            assert client.get(f"/crawls/{cid}/detail", params={"url": base + "/", "sub": sub}, headers=hx).status_code == 200
        assert "Configurar la IA" in client.get(f"/crawls/{cid}/detail", params={"url": base + "/", "sub": "ai"}, headers=hx).text
        assert client.get(f"{crawl_url}/side?pane=issues", headers=hx).text.count("/100") == 0

        # exports
        assert base + "/missing" in client.get(f"{crawl_url}/export/bulk/inlinks_4xx.csv").text
        sm = client.get(f"{crawl_url}/export/sitemap.xml").text
        assert f"<loc>{base}/about</loc>" in sm and "/noindex" not in sm and "/missing" not in sm
        assert "Disallow: /private/" in client.get(f"{crawl_url}/export/robots.txt").text

        # portable crawl file round-trip, compare and trends
        blob = client.get(f"{crawl_url}/export/crawl.seocrawl").content
        r = client.post("/import", files={"file": ("a.seocrawl", blob, "application/gzip")}, follow_redirects=False)
        new_id = int(r.headers["location"].rsplit("/", 1)[1])
        assert len(db.load_pages(new_id)) == len(db.load_pages(cid))
        cmp = client.get(f"/compare?a={cid}&b={new_id}").text
        assert "Sin cambios en las URLs comunes" in cmp
        assert client.post("/import", files={"file": ("x.seocrawl", b"nope", "application/gzip")}).status_code == 400
        tr = client.get("/trends/127.0.0.1").text

        # Search Console CSV import -> orphan with traffic
        csv_text = f"Páginas principales,Clics,Impresiones,CTR,Posición\n{base}/orphan,12,340,3.5%,7.2\n"
        r = client.post(f"{crawl_url}/analytics", files={"file": ("gsc.csv", csv_text.encode(), "text/csv")}, follow_redirects=False)
        assert r.status_code == 303
        assert base + "/orphan" in client.get(f"{crawl_url}/grid?tab=analytics&f=orphan", headers=hx).text
        assert any(i["code"] == "orphan_with_traffic" for i in db.load_issues(cid))

        # plagiarism check against pasted text
        res = client.post(f"{crawl_url}/plagiarism", data={"text": "Sobre " + LONG[:600]}).text
        assert base + "/about" in res or base + "/" in res

        # resume from a checkpoint: only the pending URL is fetched
        cfg = db.get_crawl(cid)["settings"]
        rid = db.create_crawl(base + "/", cfg, "ack")
        state = {"queue": [[base + "/older", 1, "link"]], "seen": [base + "/", base + "/older"], "done": 1}
        import json as _json
        db.update_crawl(rid, status="paused", state_json=_json.dumps(state))
        before = len(hits)
        r = client.post(f"/crawls/{rid}/resume", follow_redirects=False)
        assert r.status_code == 303
        wait_done(client, f"/crawls/{rid}")
        fetched = [p for _, p in hits[before:] if p not in ("/robots.txt",)]
        assert "/older" in fetched and "/" not in fetched

    # command line: unattended crawl + export
    out = tmp_path / "out"
    assert cli_main(["crawl", base + "/", "--i-own-this-site", "--max-urls", "5", "--delay", "0", "--no-render",
                     "--out", str(out)]) == 0
    assert list(out.glob("*.zip"))
    assert cli_main(["crawl", base + "/"]) == 2  # permission flag is mandatory

    # a recorded login session is sent to the site on the next crawl (and nothing else)
    from app import sessions
    sessions.save("127.0.0.1", {"cookies": [{"name": "sid", "value": "grabada", "domain": "127.0.0.1", "path": "/",
                                             "expires": time.time() + 3600}], "origins": []})
    COOKIES.clear()
    assert cli_main(["crawl", base + "/", "--i-own-this-site", "--max-urls", "3", "--delay", "0", "--no-render"]) == 0
    assert COOKIES and all("sid=grabada" in c for c in COOKIES)
    sessions.delete("127.0.0.1")

    # CI gate: the test site has a broken link (high) -> the build fails, with JUnit + JSON
    import httpx
    sent = []
    real_post = httpx.post
    httpx.post = lambda url, json=None, timeout=None: (sent.append((url, json)), httpx.Response(200))[1]
    try:
        junit, result = tmp_path / "seo.xml", tmp_path / "seo.json"
        args = ["ci", base + "/", "--i-own-this-site", "--max-urls", "8", "--delay", "0", "--no-render"]
        assert cli_main(args + ["--junit", str(junit), "--json", str(result), "--webhook", "https://hooks.example/x"]) == 1
    finally:
        httpx.post = real_post
    xml = junit.read_text(encoding="utf-8")
    assert "<failure" in xml and "Indexable: " + base + "/" in xml
    data = json.loads(result.read_text(encoding="utf-8"))
    assert not data["passed"] and any(f["code"] == "broken_link" for f in data["failing"])
    assert sent and "❌" in sent[0][1]["text"] and sent[0][1]["passed"] is False
    # with the previous result as baseline nothing is new -> passes; unknown options -> 2
    assert cli_main(args + ["--baseline", str(result)]) == 0
    assert cli_main(args + ["--fail-on", "none"]) == 0
    assert cli_main(args + ["--fail-on", "fatal"]) == 2
    assert cli_main(["ci", base + "/", "--max-urls", "3"]) == 2  # permission flag is mandatory here too


def test_crawl_configuration(site):
    base, ext, hits, _ = site
    from app.main import app

    with TestClient(app) as client:
        settings = {"max_urls": "50", "delay": "0", "exclude": "/dup", "ua_preset": "googlebot_desktop",
                    "custom_robots": "User-agent: *\nDisallow: /about", "headers": "Accept-Language: es-ES",
                    "search_name_0": "Contenido", "search_text_0": "contenido", "search_mode_0": "contains",
                    "extract_name_0": "H1", "extract_type_0": "css", "extract_expr_0": "h1", "check_resources": "yes"}
        # save as default, then the home page shows it
        r = client.post("/config", data=settings)
        assert r.status_code == 200 and "Configuración guardada" in r.text and "/dup" in r.text
        # invalid config reopens the dialog with the error
        r = client.post("/crawls", data={**settings, "url": base + "/", "ack": "yes", "include": "("})
        assert r.status_code == 422 and "\"openCfg\": true" in r.text and "no es válida" in r.text
        # robots tester (no network)
        r = client.post("/tools/robots-test", data={"custom_robots": "User-agent: *\nDisallow: /about",
                                                    "robots_test_urls": f"{base}/about\n{base}/"})
        assert r.text.count("Bloqueada") == 1 and "Permitida" in r.text

        r = client.post("/crawls", data={**settings, "url": base + "/", "ack": "yes"}, follow_redirects=False)
        crawl_url = r.headers["location"]
        assert "Completado" in wait_done(client, crawl_url)
        paths = [p for _, p in hits]
        assert "/dup1" not in paths and "/dup2" not in paths      # excluded
        assert "/about" not in paths                               # custom robots.txt (stricter)
        assert ("HEAD", "/logo.png") in hits                       # resource check
        assert all("Googlebot" in (ua or "") and lang == "es-ES" for ua, lang in AGENTS)
        grid = client.get(f"{crawl_url}/grid?tab=codes&f=blocked", headers={"HX-Request": "true"}).text
        assert base + "/about" in grid
        grid = client.get(f"{crawl_url}/grid?tab=search&f=s0", headers={"HX-Request": "true"}).text
        assert base + "/orphan" in grid
        grid = client.get(f"{crawl_url}/grid?tab=extract", headers={"HX-Request": "true"}).text
        assert "Huérfana" in grid
        grid = client.get(f"{crawl_url}/grid?tab=internal&f=image", headers={"HX-Request": "true"}).text
        assert base + "/logo.png" in grid  # 404 resource shown as internal image row
        r = client.post("/config/reset")
        assert "restablecida" in r.text
