"""Rendering check. Skips (does not fail) when no Chromium/Edge/Chrome is available."""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.render import Renderer

PAGE = b"""<html><head><title>Antes</title></head><body><div id=app></div>
<script>document.title='Despues';document.getElementById('app').innerText='contenido inyectado por javascript';
var m=document.createElement('meta');m.name='robots';m.content='noindex';document.head.appendChild(m);</script></body></html>"""


REDIRECT = b"<html><body>Redirigiendo<script>setTimeout(function(){location.href='/destino'}, 10)</script></body></html>"


def test_renderer_sees_javascript_changes():
    requested = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(REDIRECT if self.path == "/js" else PAGE)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    async def run():
        try:
            r = await Renderer.start("test-agent", 15)
        except RuntimeError as exc:
            pytest.skip(f"Sin navegador para renderizar: {exc}")
        try:
            return await r.render(url), await r.render(url + "js")
        finally:
            await r.close()

    try:
        data, redirect = asyncio.run(run())
    finally:
        server.shutdown()
    assert data["title"] == "Despues"
    assert "inyectado por javascript" in data["text"]
    assert data["robots"] == "noindex"
    # JS redirect is detected but the browser does not fetch the target (the crawler decides)
    assert redirect["final_url"].endswith("/destino")
    assert "/destino" not in requested


SPA = b"""<html><head><title>SPA</title><script src="/tracker/analytics.js"></script></head><body>
<nav><a href="/normal">Normal</a></nav>
<div class="card" onclick="location.href='/servicio-oculto'">Ver servicio</div>
<button onclick="history.pushState({}, '', '/ruta-spa')">Ir a la ruta</button>
<span data-href="/con-data-href" role="link" tabindex="0">Pista en data-href</span>
<button onclick="fetch('/api/accion', {method: 'POST'})">Ver mas</button>
<button onclick="location.href='/borrado'">Eliminar cuenta</button>
<form><button type="submit" formaction="/enviado">Buscar</button></form>
</body></html>"""


def test_click_discovery_and_third_party_blocking():
    requested = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(("GET", self.path))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(SPA)

        def do_POST(self):
            requested.append(("POST", self.path))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    async def run():
        try:
            r = await Renderer.start("test-agent", 15, blocklist=["127.0.0.1/tracker"], max_clicks=20)
        except RuntimeError as exc:
            pytest.skip(f"Sin navegador para renderizar: {exc}")
        try:
            return await r.render(url)
        finally:
            await r.close()

    try:
        data = asyncio.run(run())
    finally:
        server.shutdown()
    found = {l["href"].replace(url.rstrip("/"), "") for l in data["click_links"]}
    assert {"/servicio-oculto", "/ruta-spa", "/con-data-href"} <= found
    assert "/borrado" not in found and "/normal" not in found      # risky text never clicked; real <a> not needed
    assert ("POST", "/api/accion") not in requested                  # no side effects while clicking
    assert not any(p in ("/servicio-oculto", "/ruta-spa", "/borrado", "/enviado") for _, p in requested)
    assert ("GET", "/tracker/analytics.js") not in requested and data["blocked_requests"] == {"127.0.0.1/tracker": 1}


def test_blocklist_matching():
    r = Renderer(None, None, None, "x", 5, blocklist=["google-analytics.com", "facebook.com/tr"])
    assert r.blocked("https://www.google-analytics.com/analytics.js") == "google-analytics.com"
    assert r.blocked("https://facebook.com/tr?id=1") == "facebook.com/tr"
    assert r.blocked("https://www.facebook.com/tienda") is None and r.blocked("https://notgoogle-analytics.com/x") is None
