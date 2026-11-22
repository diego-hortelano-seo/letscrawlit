"""Client report: one printable page in plain language for the business owner.

How many pages pass the basics, the fixes with most impact, what is already right, a local-SEO checklist and before/after against
the previous crawl of the same site. Branding (your name, contact) is saved locally in data/branding.json.
"""
from __future__ import annotations

import base64
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import db
from .analysis import CATALOG
from .config import get_settings
from .history import compare, metrics
from .insights import health
from .localseo import audit
from .urlutil import safe_filename, site_key

TEMPLATES = Path(__file__).parent / "templates"
BRANDING_FIELDS = ("brand", "contact", "color")
DEFAULT_COLOR = "#331942"

# code -> (what it means for the owner, why it costs customers). Falls back to the technical catalogue.
PLAIN = {
    "server_error": ("Hay páginas que no cargan", "Quien llega a ellas ve un error y se va; Google acaba quitándolas."),
    "broken_link": ("Hay enlaces que llevan a páginas que no existen", "El cliente se encuentra un «página no encontrada» y Google pierde confianza en la web."),
    "broken_resource": ("Hay imágenes o archivos que no cargan", "La web se ve rota en esas páginas."),
    "redirect_chain": ("Algunas direcciones dan varios saltos antes de cargar", "Cada salto hace la web más lenta para clientes y para Google."),
    "redirect_loop": ("Hay direcciones que no terminan de cargar nunca", "Ni clientes ni Google pueden ver esas páginas."),
    "link_to_redirect": ("Enlaces internos que apuntan a direcciones antiguas", "Hacen la navegación un poco más lenta; es un arreglo rápido."),
    "missing_title": ("Páginas sin título para Google", "El título es lo primero que se ve en Google: sin él, casi nadie hace clic."),
    "duplicate_title": ("Varias páginas tienen el mismo título", "Google no sabe cuál mostrar y compiten entre ellas."),
    "title_too_long": ("Títulos demasiado largos", "Google los corta y el mensaje se pierde."),
    "title_too_short": ("Títulos demasiado cortos", "Desaprovechan espacio para decir qué haces y dónde."),
    "missing_description": ("Páginas sin descripción para Google", "Google inventa el texto bajo el título y suele ser poco atractivo."),
    "duplicate_description": ("Descripciones repetidas", "Todas las páginas se ven iguales en Google."),
    "missing_h1": ("Páginas sin titular principal", "Ni el cliente ni Google ven claro de qué trata la página."),
    "multiple_h1": ("Páginas con varios titulares principales", "Se diluye el tema principal de la página."),
    "duplicate_content": ("Páginas con el texto copiado de otras", "Google solo muestra una de ellas y no siempre la que quieres."),
    "near_duplicate": ("Páginas casi iguales entre sí", "Google puede tratarlas como copias y mostrar solo una."),
    "thin_content": ("Páginas con muy poco texto", "Google rara vez posiciona páginas que explican poco."),
    "orphan_candidate": ("Páginas a las que no se llega desde el menú ni otras páginas", "Ni clientes ni Google las encuentran con facilidad."),
    "noindex_in_sitemap": ("Páginas ocultas a Google pero listadas para él", "Señales contradictorias que confunden a Google."),
    "non200_in_sitemap": ("El mapa de la web para Google tiene direcciones que no funcionan", "Google pierde tiempo y confianza."),
    "missing_canonical": ("Falta indicar a Google la dirección oficial de cada página", "Pueden aparecer copias de la misma página en Google."),
    "http_url": ("Páginas sin conexión segura (HTTPS)", "El navegador muestra «No seguro» y el cliente desconfía."),
    "mixed_content": ("Páginas seguras que cargan elementos no seguros", "El navegador puede bloquear imágenes o mostrar avisos."),
    "ssl_error": ("El certificado de seguridad tiene un problema", "El navegador muestra un aviso a pantalla completa que espanta a los clientes."),
    "slow_response": ("Páginas que tardan en responder", "Cada segundo de espera hace que más gente se vaya."),
    "low_performance": ("La web va lenta en el móvil", "La mayoría de clientes locales buscan desde el móvil."),
    "large_image": ("Imágenes muy pesadas", "Son la causa más habitual de que la web cargue lenta en el móvil."),
    "legacy_image_format": ("Imágenes en formatos antiguos", "Con formatos modernos pesarían hasta la mitad."),
    "images_missing_alt": ("Imágenes sin descripción", "Google no sabe qué muestran y se pierden visitas desde Google Imágenes."),
    "missing_viewport": ("Páginas que no se adaptan al móvil", "Se ven diminutas en el teléfono y Google las penaliza."),
    "schema_error": ("Datos para Google con errores", "Google no puede mostrar resultados enriquecidos (estrellas, horarios…)."),
    "no_structured_data": ("La portada no tiene datos para Google", "Se pierde la opción de resultados enriquecidos."),
    "accessibility": ("Barreras de accesibilidad", "Hay personas que no pueden usar la web y algunas afectan a Google."),
    "spelling_errors": ("Posibles faltas de ortografía", "Restan confianza a quien está decidiendo si contratarte."),
    "heading_non_sequential": ("Titulares desordenados", "Dificulta entender la estructura de la página."),
    "non_descriptive_anchor": ("Enlaces con textos como «aquí» o «leer más»", "No le dicen nada a Google sobre la página de destino."),
    "url_non_ascii": ("Direcciones con tildes o eñes", "Pueden romperse al compartirlas."),
    "hreflang_invalid_code": ("El idioma de la página está mal indicado para Google", "Google ignora esa indicación de idioma o país."),
    "hreflang_no_return": ("Las versiones en otros idiomas no están bien enlazadas", "Google puede mostrar el idioma equivocado."),
    "hreflang_no_self": ("Las versiones en otros idiomas no están bien enlazadas", "Google puede mostrar el idioma equivocado."),
    "hreflang_non200": ("Las versiones en otros idiomas apuntan a páginas que no funcionan", "Google descarta esas versiones."),
    "multiple_canonicals": ("Páginas que le dan a Google indicaciones contradictorias", "Google acaba ignorándolas todas."),
    "canonical_to_non200": ("Páginas que remiten a Google a otra que no funciona", "Google ignora la indicación y puede mostrar la versión equivocada."),
    "canonical_to_noindex": ("Páginas que remiten a Google a una página oculta", "Ninguna de las dos aparecerá en Google."),
    "canonical_noindex_conflict": ("Páginas con instrucciones opuestas para Google", "Puede desaparecer de Google la página que sí quieres mostrar."),
    "canonical_chain": ("Páginas que remiten a Google a otra que a su vez remite a otra", "Google puede no seguir la cadena."),
    "invalid_jsonld": ("Los datos para Google tienen errores de formato", "Google los ignora por completo."),
    "schema_warning": ("A los datos para Google les faltan detalles recomendados", "Con ellos es más probable salir con estrellas, horarios o precios."),
    "multiple_titles": ("Páginas con varios títulos", "Google elige uno al azar o se inventa otro."),
    "missing_lang": ("Falta indicar el idioma de la página", "Ayuda a Google y a los lectores de pantalla."),
    "lang_mismatch": ("Páginas que dicen estar en un idioma y están en otro", "Google las muestra al público equivocado."),
    "blocked_but_linked": ("Páginas enlazadas que Google tiene prohibido visitar", "Pueden salir en Google sin descripción."),
    "blocked_in_sitemap": ("Páginas que se piden a Google y a la vez se le prohíben", "Señales contradictorias que confunden a Google."),
    "canonicalised_in_sitemap": ("El mapa de la web para Google incluye versiones duplicadas", "Google pierde tiempo con páginas que no deben salir."),
    "missing_hsts": ("Falta una protección de seguridad del servidor", "La primera visita podría llegar por una conexión no segura."),
    "meta_refresh_redirect": ("Redirecciones hechas de forma poco fiable", "Son más lentas y Google las sigue peor que una redirección normal."),
    "js_redirect": ("Redirecciones que dependen de JavaScript", "Google las descubre tarde y con menos fiabilidad."),
    "js_dependent_content": ("Texto que solo aparece tras cargar código", "Google lo ve más tarde y con más riesgo de no verlo."),
    "rendered_robots_conflict": ("El código de la página cambia lo que se le dice a Google", "Google puede ocultar la página sin que lo sepas."),
    "rendered_canonical_conflict": ("El código de la página cambia lo que se le dice a Google", "Señales contradictorias que confunden a Google."),
    "nofollow_internal": ("Enlaces internos marcados para que Google no los siga", "Se pierde fuerza hacia tus propias páginas."),
    "broken_external": ("Enlaces a otras webs que ya no existen", "Da imagen de web descuidada."),
    "url_uppercase": ("Direcciones con mayúsculas", "Es fácil crear copias y enlaces rotos."),
    "url_underscore": ("Direcciones con guiones bajos", "Google prefiere guiones normales para separar palabras."),
    "amp_error": ("La versión rápida para móvil (AMP) tiene errores", "Google deja de usarla."),
    "orphan_with_traffic": ("Páginas con visitas a las que no se llega desde la web", "Se desaprovecha una página que ya atrae clientes."),
    "local_no_schema": ("La web no tiene la ficha del negocio para Google", "Google no recibe nombre, dirección, teléfono y horario en un formato que entienda."),
    "local_schema_incomplete": ("La ficha del negocio para Google está incompleta", "Faltan datos que Google usa en las búsquedas locales."),
    "local_nap_inconsistent": ("El nombre, la dirección o el teléfono no coinciden", "Google desconfía de negocios con datos distintos en cada sitio."),
    "local_schema_phone_hidden": ("El teléfono que ve Google no es el que ven los clientes", "Puede que Google muestre un número antiguo."),
    "local_no_phone": ("La web no muestra ningún teléfono", "El cliente que quiere llamar no encuentra cómo."),
    "local_phone_not_clickable": ("El teléfono no se puede pulsar desde el móvil", "El cliente tiene que copiarlo a mano y muchos no llaman."),
    "local_phone_variant": ("Aparece un teléfono distinto al habitual", "Puede ser un número antiguo que ya no contesta."),
    "local_no_contact_page": ("No hay página de contacto", "Es de las páginas más visitadas por clientes locales."),
    "local_contact_not_linked": ("La página de contacto no está a un clic desde todas las páginas", "El cliente debe poder contactar desde cualquier sitio."),
    "local_no_whatsapp": ("No hay botón de WhatsApp", "Muchos clientes prefieren escribir antes que llamar."),
    "local_no_map": ("No hay mapa de ubicación", "El mapa transmite cercanía y refuerza dónde trabajas."),
    "local_city_missing": ("Las páginas de servicio no nombran la ciudad", "La gente busca «servicio + ciudad»; sin la ciudad compites peor."),
}
IMPACT = {"critical": "Urgente", "high": "Impacto alto", "medium": "Impacto medio", "low": "Mejora", "info": "Detalle"}

# What is already right: label -> issue codes that would make it wrong
# The client report looks only at the basics that matter to a business owner, in this order.
CORE = [
    ("Títulos", "Cada página tiene su propio título y se ve entero en Google",
     ("missing_title", "duplicate_title", "title_too_wide")),
    ("Descripciones", "Cada página tiene su propia descripción para Google",
     ("missing_description", "duplicate_description")),
    ("Titular principal (H1)", "Cada página tiene un único titular principal",
     ("missing_h1", "multiple_h1")),
    ("Páginas rotas (404)", "No hay páginas rotas ni enlaces a páginas que no existen",
     ("broken_link", "server_error", "soft_404")),
    ("Contenido pobre", "Las páginas tienen texto suficiente y no repetido",
     ("thin_content", "duplicate_content")),
    ("Redirecciones (301)", "Los enlaces van directos, sin redirecciones de por medio",
     ("redirect_chain", "redirect_loop", "link_to_redirect")),
    ("Indexación", "Google puede mostrar las páginas que quieres que muestre (sin noindex ni canonical por error)",
     ("noindex_in_sitemap", "blocked_in_sitemap", "canonical_noindex_conflict", "canonical_to_non200",
      "canonical_to_noindex", "robots_header_conflict")),
]
CORE_CODES = {c for _, _, cs in CORE for c in cs}

GOOD = [
    ("La web usa conexión segura (HTTPS)", ("http_url", "ssl_error", "mixed_content")),
    ("No hay páginas rotas ni enlaces a páginas que no existen", ("broken_link", "server_error", "broken_resource")),
    ("Las páginas responden rápido", ("slow_response", "low_performance")),
    ("Se ve bien en el móvil", ("missing_viewport",)),
    ("Cada página tiene su título y descripción", ("missing_title", "missing_description", "duplicate_title")),
    ("Cada página tiene su titular principal", ("missing_h1", "multiple_h1")),
    ("No hay contenido copiado entre páginas", ("duplicate_content", "near_duplicate")),
    ("Google puede encontrar todas las páginas", ("orphan_candidate", "non200_in_sitemap", "noindex_in_sitemap", "blocked_in_sitemap")),
    ("Sin redirecciones innecesarias", ("redirect_chain", "redirect_loop", "link_to_redirect")),
    ("Imágenes ligeras y descritas", ("large_image", "images_missing_alt")),
]


def _branding_path() -> Path:
    return get_settings().data_dir / "branding.json"


LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}
MAX_LOGO_BYTES = 1024 * 1024


def _logo_path() -> Path | None:
    for ext in LOGO_TYPES:
        p = get_settings().data_dir / f"branding-logo.{ext}"
        if p.exists():
            return p
    return None


def logo_kind(data: bytes) -> str | None:
    """Detect the image type from its bytes (never trust the file name)."""
    head = data[:512].lstrip()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048]):
        return "svg"
    return None


def save_logo(data: bytes) -> str:
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError("El logo no puede pasar de 1 MB.")
    kind = logo_kind(data)
    if not kind:
        raise ValueError("El logo debe ser PNG, JPG, WebP o SVG.")
    old = _logo_path()
    if old:
        old.unlink()
    path = get_settings().data_dir / f"branding-logo.{kind}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return kind


def delete_logo() -> None:
    p = _logo_path()
    if p:
        p.unlink()


def logo_data_uri() -> str:
    """Embedded as <img src="data:…">: the report stays one file, and an SVG inside <img> cannot run scripts."""
    p = _logo_path()
    if not p:
        return ""
    return f"data:{LOGO_TYPES[p.suffix[1:]]};base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def load_branding() -> dict:
    out = {"brand": "", "contact": "", "color": DEFAULT_COLOR}
    try:
        saved = json.loads(_branding_path().read_text(encoding="utf-8"))
        out.update({k: str(saved[k])[:200] for k in BRANDING_FIELDS if isinstance(saved, dict) and saved.get(k)})
    except (OSError, ValueError):
        pass
    return out


def clean_branding(form: dict) -> dict:
    import re
    b = {"brand": (form.get("brand") or "").strip()[:80], "contact": (form.get("contact") or "").strip()[:160],
         "color": (form.get("color") or "").strip()}
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", b["color"]):
        b["color"] = DEFAULT_COLOR
    return b


def save_branding(b: dict) -> None:
    path = _branding_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean_branding(b), ensure_ascii=False, indent=2), encoding="utf-8")


def es_date(iso: str | None) -> str:
    """'2026-11-16T10:00' -> '16/11/2026'"""
    d = (iso or "")[:10]
    return "/".join(reversed(d.split("-"))) if len(d) == 10 else d


def previous_crawl(crawl: dict) -> dict | None:
    done = [c for c in db.crawls_for_site(site_key(crawl["start_url"]))
            if c["id"] < crawl["id"] and c["status"] in ("completed", "cancelled")]
    return max(done, key=lambda c: c["id"]) if done else None


def build(crawl_id: int, client: str = "", branding: dict | None = None, comment: str = "", notes: bool = False) -> dict:
    crawl = db.get_crawl(crawl_id)
    if crawl is None:
        raise KeyError(crawl_id)
    pages, all_issues, links = db.load_pages(crawl_id), db.load_issues(crawl_id), db.load_links(crawl_id)
    issues = [i for i in all_issues if i["code"] in CORE_CODES]  # only the basics (see CORE)
    h = health(pages, issues, links, crawl["start_url"])
    actions = []
    for a in h["actions"][:6]:
        title, why = PLAIN.get(a["code"], (CATALOG[a["code"]][1] if a["code"] in CATALOG else a["title"],
                                          CATALOG[a["code"]][2] if a["code"] in CATALOG else ""))
        actions.append({**a, "plain_title": title, "plain_why": why, "impact": IMPACT[a["severity"]]})
    urls_by_code: dict[str, set] = {}
    for i in issues:
        urls_by_code.setdefault(i["code"], set()).add(i["url"])
    good = []
    for name, label, cs in CORE:
        n = len(set().union(*(urls_by_code.get(c, set()) for c in cs)))
        good.append({"name": name, "label": label, "ok": not n, "count": n})
    local = audit(pages, links, crawl["start_url"], crawl.get("settings") or {})
    prev = previous_crawl(crawl)
    before_after = None
    if prev:
        ma, mb = metrics(prev), metrics(crawl)
        delta = compare(prev["id"], crawl_id)["issues"]
        if not (db.load_insight(prev["id"], "local") or {}).get("available"):  # older crawl had no local checks
            delta = [d for d in delta if not d["code"].startswith("local_")]
        before_after = {
            "prev": prev,
            "rows": [(label, ma.get(k), mb.get(k), better) for k, label, better in (
                ("errors_4xx", "Páginas rotas", "down"), ("avg_response_ms", "Tiempo de respuesta medio (ms)", "down"),
                ("indexable", "Páginas que Google puede mostrar", "up"))],
            "fixed": [PLAIN.get(d["code"], (d["title"],))[0] for d in delta if d["before"] and not d["after"] and d["code"] in CORE_CODES][:8],
            "new": [PLAIN.get(d["code"], (d["title"],))[0] for d in delta if d["after"] and not d["before"] and d["code"] in CORE_CODES][:8],
        }
    if before_after:
        before_after["date"] = es_date(prev["created_at"])
    charts = _charts(crawl, pages, issues)
    from .urlutil import site_key
    journal = []
    if notes:  # «Incluir mis notas» when downloading: each note with the URLs it is about
        for e in db.journal_list(db.note_scope(crawl["id"]))[:40]:
            journal.append(dict(e, urls=[u for u in (e.get("url") or "").split("\n") if u]))
    return {"journal": journal, "comment": comment.strip()[:3000], "crawl": crawl, "health": h, "actions": actions, "good": good, "local": local, "client": client.strip()[:120],
            "branding": branding or load_branding(), "before_after": before_after, "charts": charts,
            "logo": logo_data_uri(),
            "date": datetime.now().strftime("%d/%m/%Y"), "site": site_key(crawl["start_url"])}


STATUS_COLORS = [("Funcionan (200)", "#1f8a5b"), ("Redirigen (3xx)", "#2f6fb5"), ("No existen (4xx)", "#c0392b"),
                 ("Error del servidor (5xx)", "#7d1f6a"), ("Bloqueadas o sin respuesta", "#9a8aa6")]
SEV_COLORS = {"critical": "#c0392b", "high": "#e0613a", "medium": "#d9a21b", "low": "#8e7aa6"}


def _charts(crawl: dict, pages: list[dict], issues: list[dict]) -> dict:
    """Data for the inline SVG charts (no JavaScript, so they also print and convert to PDF)."""
    counts = [0] * 5
    for p in pages:
        s = p.get("status_code")
        if p.get("blocked_by_robots") or s is None:
            counts[4] += 1
        elif s < 300:
            counts[0] += 1
        elif s < 400:
            counts[1] += 1
        elif s < 500:
            counts[2] += 1
        else:
            counts[3] += 1
    total = sum(counts) or 1
    arcs, start = [], 0.0
    for (label, color), n in zip(STATUS_COLORS, counts):
        if n:
            frac = n / total
            arcs.append({"label": label, "color": color, "n": n, "pct": round(100 * frac),
                         "dash": round(frac * 301.59, 2), "offset": round(-start * 301.59, 2)})
            start += frac
    sev = Counter(i["severity"] for i in issues)
    top = max([sev.get(s, 0) for s in SEV_COLORS] + [1])
    bars = [{"label": IMPACT[s], "n": sev.get(s, 0), "color": c, "w": round(100 * sev.get(s, 0) / top)} for s, c in SEV_COLORS.items()]
    return {"status": arcs, "status_total": sum(counts), "severity": bars}


def render(crawl_id: int, client: str = "", branding: dict | None = None, for_download: bool = False,
           error: str = "", summary: bool = False, comment: str = "", notes: bool = False) -> str:
    """Full report, or with `summary` a two-page version for a boss: pages that are fine, two charts and the top 3 fixes."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    ctx = build(crawl_id, client, branding, comment, notes)
    fonts = {w: TEMPLATES.parent / "static" / f"plex-sans-{w}.woff2" for w in (400, 500, 600, 700)}
    font_uris = {w: "data:font/woff2;base64," + base64.b64encode(f.read_bytes()).decode("ascii") for w, f in fonts.items() if f.exists()}
    return env.get_template("client_report.html").render(**ctx, font_uris=font_uris, for_download=for_download, error=error,
                                                         summary=summary)


def filename(crawl: dict, client: str = "", ext: str = "html", summary: bool = False) -> str:
    return safe_filename(f"{'resumen' if summary else 'informe'}-{client or site_key(crawl['start_url'])}-{(crawl.get('created_at') or '')[:10]}") + "." + ext


async def to_pdf(html: str) -> bytes:
    """Print the self-contained report to an A4 PDF with the same headless browser used for rendering."""
    from .render import _launch, _playwright
    async with _playwright() as pw:
        browser, _ = await _launch(pw)
        try:
            page = await browser.new_page()

            async def offline(route):  # everything is embedded; nothing may load from outside
                if route.request.url.startswith(("data:", "about:")):
                    await route.continue_()
                else:
                    await route.abort()

            await page.route("**/*", offline)
            await page.set_content(html, wait_until="load")
            return await page.pdf(format="A4", print_background=True,
                                  margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        finally:
            await browser.close()
