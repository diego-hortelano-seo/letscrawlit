"""Small local site with deliberate SEO problems, to try the crawler safely.

    .venv\\Scripts\\python extras\\demo\\demo_site.py      -> http://127.0.0.1:8800/
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8800
BASE = f"http://127.0.0.1:{PORT}"
TEXT = " ".join(["Cerrajería urgente en tu barrio con apertura de puertas sin romper."] * 25)


FOOTER = "<footer>Cerrajería Demo · Calle Mayor 1, Madrid · Tel. 985 123 456</footer>"  # phone as text, not clickable


def html(title, body, head="", footer=FOOTER):
    t = f"<title>{title}</title>" if title else ""
    return (f"<!doctype html><html lang='es'><head><meta charset='utf-8'>{t}{head}</head>"
            f"<body><nav><a href='/'>Inicio</a> <a href='/servicios'>Servicios</a> <a href='/contacto'>Contacto</a></nav>{body}{footer}</body></html>")


ROUTES = {
    "/robots.txt": (200, "text/plain", f"User-agent: *\nDisallow: /admin/\nSitemap: {BASE}/sitemap.xml\n", {}),
    "/sitemap.xml": (200, "application/xml", "<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                     + "".join(f"<url><loc>{BASE}{p}</loc></url>" for p in ("/", "/servicios", "/contacto", "/promo-vieja", "/gracias", "/antiguo"))
                     + "</urlset>", {}),
    "/": (200, "text/html", html("Cerrajero 24 horas en Madrid | Demo",
          "<h1>Cerrajero urgente</h1><p>" + TEXT + "</p><a href='/blog'>Blog</a> <a href='/antiguo'>Oferta</a> "
          "<a href='/precios-2019'>Precios</a> <a href='/admin/panel'>Admin</a> <a href='/legal' rel='nofollow'>Aviso legal</a>"
          " <a href='/oferta'>Oferta antigua</a> <a href='https://ejemplo-socio.test/' rel='sponsored'>Socio</a>"
          " <a href='/servicios'>leer más</a>"
          "<img src='/foto.jpg'> <img src='/logo.png' alt='Logo'>"
          " <a href='tel:+34985123456'>Llamar</a> <a href='https://wa.me/34985123456'>WhatsApp</a>",
          f"<meta name='description' content='Abrimos tu puerta en 20 minutos, 24 horas.'><link rel='canonical' href='{BASE}/'>"
          f"<link rel='alternate' hreflang='es-SP' href='{BASE}/'>"
          "<script type='application/ld+json'>{\"@context\":\"https://schema.org\",\"@type\":\"Locksmith\",\"name\":\"Demo\"}</script>"), {}),
    "/servicios": (200, "text/html", html("Servicios", "<h1>Servicios</h1><h1>Otro H1</h1><p>" + TEXT + "</p>",
                   "<meta name='description' content='Servicios'>"), {}),
    "/oferta": (200, "text/html", html("Redirigiendo a la oferta", "<p>Un momento…</p>",
                "<meta http-equiv='refresh' content='0; url=/servicios'>"), {}),
    "/contacto": (200, "text/html", html("Servicios", "<p>Llámanos</p><form><input type='text' name='tel'><button></button></form>",
                  f"<link rel='canonical' href='{BASE}/servicios'><meta name='robots' content='noindex'>"), {}),
    "/blog": (200, "text/html", html("Blog de cerrajería y seguridad del hogar", "<h1>Blog</h1><div id='app'></div>"
              "<script>document.getElementById('app').innerText='" + TEXT + "';</script>",
              "<meta name='description' content='Consejos de seguridad.'>"), {}),
    "/antiguo": (301, "text/html", "", {"Location": "/antiguo-2"}),
    "/antiguo-2": (302, "text/html", "", {"Location": "/servicios"}),
    "/promo-vieja": (200, "text/html", html("Promoción antigua de cerrajería urgente", "<h1>Promo</h1><p>" + TEXT + "</p>",
                     "<meta name='description' content='Promo antigua.'>",
                     footer="<footer>Cerrajería Demo · Llámanos al 611 222 333</footer>"), {}),  # old phone
    "/gracias": (200, "text/html", html("Gracias por contactar con nosotros", "<h1>Gracias</h1>",
                 "<meta name='robots' content='noindex'>"), {}),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, ctype, body, headers = ROUTES.get(self.path.split("?")[0], (404, "text/html", html("No encontrado", "<h1>404</h1>"), {}))
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"Sitio demo en {BASE}/  (Ctrl+C para salir)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
