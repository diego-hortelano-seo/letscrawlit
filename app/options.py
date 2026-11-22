"""Every crawl option beyond the basics: behaviour, limits, advanced switches, thresholds and extraction.

One table drives the defaults, the form validation and the configuration panel, so a new option is a single line.
"""
from __future__ import annotations

from .extract import NON_DESCRIPTIVE

# (key, default, section, label, hint)
BOOLS: list[tuple[str, bool, str, str, str]] = [
    # what to crawl
    ("crawl_images", True, "resources", "Imágenes", ""),
    ("crawl_css", True, "resources", "CSS", ""),
    ("crawl_js", True, "resources", "JavaScript", ""),
    ("crawl_media", False, "resources", "Vídeo y audio", ""),
    ("crawl_iframes", True, "resources", "Rastrear el contenido de los iframes internos", "como páginas"),
    ("crawl_pagination", True, "resources", "Seguir la paginación (rel next/prev)", ""),
    ("crawl_amp", True, "resources", "Seguir las versiones AMP (rel amphtml)", ""),
    ("crawl_meta_refresh", True, "resources", "Seguir las redirecciones meta refresh", ""),
    ("crawl_outside_folder", True, "behaviour", "Rastrear fuera de la carpeta de la URL inicial",
     "desactívalo para auditar solo, p. ej., /blog/"),
    ("check_outside_folder", True, "behaviour", "Comprobar enlaces fuera de la carpeta de inicio",
     "si no se rastrea fuera de la carpeta, se comprueba igualmente el estado de esas URLs"),
    ("crawl_mobile_alt", False, "resources", "Seguir la versión móvil (link rel=alternate media)", ""),
    ("crawl_subdomains", False, "behaviour", "Rastrear también los subdominios", "blog.misitio.es, tienda.misitio.es…"),
    ("follow_nofollow_internal", False, "behaviour", "Seguir los enlaces internos nofollow", ""),
    ("follow_nofollow_external", False, "behaviour", "Comprobar también los enlaces externos nofollow", ""),
    ("crawl_linked_sitemaps", False, "behaviour", "Rastrear los sitemaps XML enlazados desde las páginas", ""),
    # advanced
    ("ignore_pagination_duplicates", True, "advanced", "Ignorar las URLs de paginación al buscar duplicados",
     "/page/2 suele repetir título y descripción a propósito"),
    ("always_follow_redirects", False, "advanced", "Seguir siempre las redirecciones",
     "aunque el destino quede fuera de incluir/excluir, carpeta o profundidad"),
    ("always_follow_canonicals", False, "advanced", "Seguir siempre los canonicals", "igual, para los canonical"),
    ("respect_noindex", False, "advanced", "Respetar noindex", "las URLs noindex no aparecen en las tablas"),
    ("respect_canonicals", False, "advanced", "Respetar canonicals", "las URLs canonicalizadas no aparecen en las tablas"),
    ("respect_next_prev", False, "advanced", "Respetar rel next/prev", "solo aparece la primera página de cada serie"),
    ("respect_hsts", False, "advanced", "Respetar HSTS",
     "si el sitio envía Strict-Transport-Security, sus URLs http:// se piden directamente por https://"),
    ("respect_self_refresh", False, "advanced", "Respetar el meta refresh que apunta a la propia URL",
     "no lo trata como redirección ni como no indexable"),
    ("extract_srcset", True, "advanced", "Leer las imágenes del atributo srcset", ""),
    ("html_validation", True, "advanced", "Validar la estructura HTML", "doctype, <head>, <title> duplicados…"),
    ("carbon", False, "advanced", "Estimar la huella de carbono de cada página", "modelo Sustainable Web Design"),
    ("assume_html", False, "advanced", "Tratar como HTML las respuestas sin Content-Type", ""),
    # extraction
    ("extract_readability", True, "extraction", "Legibilidad del texto", "índice Fernández-Huerta (español) o Flesch"),
    ("extract_text_ratio", True, "extraction", "Proporción de texto y código", ""),
    ("extract_forms", True, "extraction", "Formularios", "cuántos hay y si envían datos sin HTTPS"),
    ("extract_cookies", False, "extraction", "Cookies que pone cada página", "nombre y atributos Secure/HttpOnly/SameSite"),
    ("store_html", False, "extraction", "Guardar el HTML original", "para verlo en «Ver fuente»; ocupa disco"),
    ("store_rendered_html", False, "extraction", "Guardar el HTML renderizado", "requiere renderizado JavaScript"),
    ("extract_pdf", False, "extraction", "Leer las propiedades de los PDF", "título, autor, páginas y fechas"),
    # «Almacenar»: keep the link/resource in the results even when it is not crawled («Rastrear» off)
    ("store_images", True, "crawl_store", "Almacenar imágenes", ""),
    ("store_media", False, "crawl_store", "Almacenar vídeo y audio", ""),
    ("store_css", True, "crawl_store", "Almacenar CSS", ""),
    ("store_js", True, "crawl_store", "Almacenar JavaScript", ""),
    ("store_external", True, "crawl_store", "Almacenar enlaces externos", ""),
    ("store_canonicals", True, "crawl_store", "Almacenar canonicals", ""),
    ("store_pagination", True, "crawl_store", "Almacenar paginación", ""),
    ("store_hreflang", True, "crawl_store", "Almacenar hreflang", ""),
    ("store_amp", True, "crawl_store", "Almacenar AMP", ""),
    ("store_mobile_alt", False, "crawl_store", "Almacenar alternativa móvil", ""),
    ("store_meta_refresh", True, "crawl_store", "Almacenar meta refresh", ""),
    ("store_iframes", True, "crawl_store", "Almacenar iframes", ""),
    ("store_uncrawlable", True, "crawl_store", "Almacenar enlaces no rastreables (javascript:, onclick)", ""),
    # what to keep from each page («Extracción»)
    ("ext_title", True, "ext", "Título de página", ""),
    ("ext_description", True, "ext", "Meta description", ""),
    ("ext_keywords", True, "ext", "Meta palabras clave", ""),
    ("ext_h1", True, "ext", "H1", ""),
    ("ext_h2", True, "ext", "H2", ""),
    ("ext_word_count", True, "ext", "Recuento de palabras", ""),
    ("ext_hash", True, "ext", "Valor de hash", ""),
    ("ext_page_size", True, "ext", "Tamaño de página", ""),
    ("ext_a11y", True, "ext", "Accesibilidad", ""),
    ("ext_response_time", True, "ext", "Tiempo de respuesta", ""),
    ("ext_last_modified", True, "ext", "Última modificación", ""),
    ("ext_headers", True, "ext", "Encabezados HTTP", ""),
    ("ext_meta_robots", True, "ext", "Meta robots", ""),
    ("ext_x_robots", True, "ext", "X-Robots-Tag", ""),
    ("ext_jsonld", True, "ext", "JSON-LD", ""),
    ("ext_microdata", True, "ext", "Microdatos", ""),
    ("ext_rdfa", True, "ext", "RDFa", ""),
    ("ext_schema_validation", True, "ext", "Validación de Schema.org y resultados enriquecidos", ""),
    # robots.txt view
    ("show_blocked_internal", True, "robots", "Mostrar URLs internas bloqueadas por robots.txt", ""),
    ("show_blocked_external", True, "robots", "Mostrar URLs externas bloqueadas por robots.txt", ""),
]

# (key, default, lo, hi, section, label)
NUMBERS: list[tuple[str, int, int, int, str, str]] = [
    ("max_urls_per_depth", 0, 0, 100000, "limits", "Máximo de URLs por nivel de profundidad (0 = sin límite)"),
    ("max_folder_depth", 0, 0, 50, "limits", "Máximo de carpetas en la ruta (0 = sin límite)"),
    ("max_urls_per_subdomain", 0, 0, 100000, "limits", "Máximo de URLs por subdominio (0 = sin límite)"),
    ("max_redirects", 10, 0, 20, "limits", "Máximo de redirecciones que seguir en cadena"),
    ("max_url_length", 10000, 20, 10000, "limits", "Longitud máxima de las URLs que rastrear"),
    ("max_links_per_page", 10000, 1, 100000, "limits", "Máximo de enlaces que leer en cada página"),
    ("max_page_size_kb", 5120, 50, 51200, "limits", "Tamaño máximo de página que descargar (KB)"),
    ("max_urls_per_second", 0, 0, 50, "speed", "Máximo de URLs por segundo (0 = sin límite; además de la pausa)"),
    # thresholds used by filters and issues
    ("title_min", 30, 1, 200, "thresholds", "Título: mínimo de caracteres"),
    ("title_max", 60, 10, 300, "thresholds", "Título: máximo de caracteres"),
    ("title_px_min", 200, 1, 2000, "thresholds", "Título: mínimo de píxeles"),
    ("title_px_max", 561, 100, 2000, "thresholds", "Título: máximo de píxeles"),
    ("desc_min", 70, 1, 500, "thresholds", "Meta description: mínimo de caracteres"),
    ("desc_max", 155, 20, 1000, "thresholds", "Meta description: máximo de caracteres"),
    ("desc_px_min", 400, 1, 3000, "thresholds", "Meta description: mínimo de píxeles"),
    ("desc_px_max", 985, 100, 3000, "thresholds", "Meta description: máximo de píxeles"),
    ("h1_max", 70, 10, 500, "thresholds", "H1: máximo de caracteres"),
    ("h2_max", 70, 10, 500, "thresholds", "H2: máximo de caracteres"),
    ("alt_max", 100, 10, 1000, "thresholds", "Texto ALT: máximo de caracteres"),
    ("url_max", 115, 20, 2000, "thresholds", "URL: máximo de caracteres"),
    ("low_words", 200, 0, 5000, "thresholds", "Contenido escaso: menos de N palabras"),
    ("image_max_kb", 200, 1, 50000, "thresholds", "Imagen pesada: más de N KB"),
    ("doc_max_mb", 2, 1, 100, "thresholds", "Documento HTML demasiado grande: más de N MB"),
    ("resource_max_mb", 2, 1, 500, "thresholds", "Recurso demasiado grande (CSS, JS, PDF…): más de N MB"),
    ("high_internal_outlinks", 1000, 1, 100000, "thresholds", "Demasiados enlaces internos salientes: más de N"),
    ("high_external_outlinks", 10, 1, 100000, "thresholds", "Demasiados enlaces externos salientes: más de N"),
    ("high_depth", 3, 1, 100, "thresholds", "Página muy profunda: más de N clics desde la inicial"),
]

SOFT_404 = ["404 not found", "404 error", "error 404", "410 gone", "page not found", "página no encontrada",
            "pagina no encontrada", "no se ha encontrado la página", "no encontramos la página", "esta página no existe",
            "la página que buscas no existe", "can't find the page", "couldn't find the page", "nothing found",
            "no results found", "no se encontraron resultados", "ups, algo salió mal"]

# (key, default, section, label, rows)
TEXTS: list[tuple[str, str, str, str, int]] = [
    ("custom_sitemaps", "", "behaviour", "Rastrear también estos sitemaps (uno por línea)", 3),
    ("path_limits", "", "limits", "Límite por patrón de URL: «expresión regular número» por línea, p. ej. /blog/ 50", 3),
    ("nondescriptive_anchors", "\n".join(sorted(NON_DESCRIPTIVE)), "thresholds", "Textos de ancla no descriptivos (uno por línea)", 6),
    ("soft404_phrases", "\n".join(SOFT_404), "thresholds", "Frases que delatan un 404 «suave» (página 200 que en realidad es un error)", 6),
]

SELECTS = {"cookie_storage": ("session", {"session": "Guardar las cookies durante el rastreo (sesión)",
                                          "none": "No guardar cookies del sitio entre peticiones"}, "advanced",
                              "Cookies que envía el sitio")}

DEFAULTS = {k: d for k, d, *_ in BOOLS} | {k: d for k, d, *_ in NUMBERS} | {k: d for k, d, *_ in TEXTS} | \
    {k: v[0] for k, v in SELECTS.items()}
BOOL_KEYS = tuple(k for k, *_ in BOOLS)


def thresholds(cfg: dict | None) -> dict:
    """Threshold values for a crawl (old crawls without them get the defaults)."""
    cfg = cfg or {}
    out = {k: int(cfg.get(k) if cfg.get(k) not in (None, "") else d) for k, d, _, _, sec, _ in NUMBERS if sec == "thresholds"}
    out["anchors"] = {a.strip().lower() for a in (cfg.get("nondescriptive_anchors") or DEFAULTS["nondescriptive_anchors"]).splitlines() if a.strip()}
    out["soft404"] = [a.strip().lower() for a in (cfg.get("soft404_phrases") or DEFAULTS["soft404_phrases"]).splitlines() if a.strip()]
    out["ignore_pagination_duplicates"] = cfg.get("ignore_pagination_duplicates", True)
    out["html_validation"] = cfg.get("html_validation", True)
    return out


def parse_into(cfg: dict, form: dict, errors: dict, panel: bool) -> None:
    """Validate the extra options from the form into cfg (booleans are handled by crawlconfig with BOOL_KEYS)."""
    import re
    for key, default, lo, hi, _sec, label in NUMBERS:
        raw = str(form.get(key, "")).strip()
        if raw == "":
            cfg[key] = cfg.get(key, default) if not panel else default
            continue
        try:
            v = int(raw)
            if not lo <= v <= hi:
                raise ValueError
            cfg[key] = v
        except ValueError:
            errors[key] = f"{label}: número entre {lo} y {hi}."
            cfg[key] = default
    if cfg.get("title_min", 0) >= cfg.get("title_max", 1):
        errors["title_min"] = "Título: el mínimo debe ser menor que el máximo."
    if cfg.get("desc_min", 0) >= cfg.get("desc_max", 1):
        errors["desc_min"] = "Meta description: el mínimo debe ser menor que el máximo."
    for key, default, _sec, label, _rows in TEXTS:
        if key in form or panel:
            cfg[key] = str(form.get(key) or "")[:20000]
        else:
            cfg.setdefault(key, default)
    for line in [l.strip() for l in (cfg.get("path_limits") or "").splitlines() if l.strip()]:
        parts = line.rsplit(None, 1)
        try:
            re.compile(parts[0])
            if len(parts) != 2 or int(parts[1]) < 0:
                raise ValueError
        except (re.error, ValueError):
            errors["path_limits"] = f"Límite por patrón: «{line[:60]}» debe ser «expresión número», p. ej. /blog/ 50"
            break
    for line in [l.strip() for l in (cfg.get("custom_sitemaps") or "").splitlines() if l.strip()]:
        if not re.match(r"https?://", line):
            errors["custom_sitemaps"] = f"Sitemaps: «{line[:60]}» debe ser una URL completa (https://…)."
            break
    for key, (default, choices, _sec, _label) in SELECTS.items():
        value = form.get(key) or cfg.get(key) or default
        cfg[key] = value if value in choices else default


def path_limits(cfg: dict) -> list[tuple]:
    import re
    out = []
    for line in [l.strip() for l in (cfg.get("path_limits") or "").splitlines() if l.strip()]:
        try:
            pattern, n = line.rsplit(None, 1)
            out.append((re.compile(pattern), int(n), pattern))
        except (ValueError, re.error):
            continue
    return out
