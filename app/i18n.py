"""Interface language: Spanish (source), English, French, Italian, Portuguese, German.

The templates are written in Spanish. When the visitor picks another language (cookie `lang`) every HTML response
is translated on the way out: visible text and user-facing attributes are looked up in the language's dictionary
(`i18n_en.EN` for English, `locales/<code>.json` for the others), and texts with numbers or names inside go through
the patterns below. A text missing in French, Italian, Portuguese or German falls back to English, never to a Spanish
fragment; the crawled site's own titles, URLs and data are never touched.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .i18n_en import EN

LANGS = {"es": "Español", "en": "English", "fr": "Français", "it": "Italiano", "pt": "Português", "de": "Deutsch"}
COOKIE = "lang"
LOCALES_DIR = Path(__file__).parent / "locales"


def _load(code: str) -> dict:
    """locales/<code>.json: {"texts": {es: x}, "patterns": {regex: template}, "phrases": {regex: repl}, "js": {es: x}}.
    In a template {1} is the matched text and {t1} the same text translated."""
    try:
        data = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {k: data.get(k) or {} for k in ("texts", "patterns", "phrases", "js")}


LOCALES = {code: _load(code) for code in LANGS if code not in ("es", "en")}

_WORD = r"[A-Za-zÁÉÍÓÚÜáéíóúüÑñ]"


def _t(group: str) -> str:
    return tr(group) or group


# (regex on the whole text, function building the English text from the match)
PATTERNS: list[tuple[re.Pattern, callable]] = [(re.compile(rx), fn) for rx, fn in [
    (r"^Ordenar por (.+)$", lambda m: "Sort by " + _t(m[1])),
    (r"^Buscar en url…$", lambda m: "Search URL…"),
    (r"^(\w+) ya no ofrece el modelo «(.+)» \(o no existe\)\. Elige otro en Configuración › Inteligencia artificial \(IA\), o deja el campo Modelo vacío para usar el recomendado\.$",
     lambda m: f"{m[1]} no longer offers the model «{m[2]}» (or it does not exist). Pick another in Configuration › Artificial intelligence (AI), or leave the Model field empty to use the recommended one."),
    (r"^(\w+) está saturado o caído ahora mismo \(no es un fallo tuyo\); prueba de nuevo en un minuto\.$",
     lambda m: f"{m[1]} is overloaded or down right now (not your fault); try again in a minute."),
    (r"^Apuntes \((\d+)\)$", lambda m: f"Notes ({m[1]})"),
    (r"^(\d+) apunte\(s\)$", lambda m: f"{m[1]} note(s)"),
    (r"^(\d+) pendiente\(s\)$", lambda m: f"{m[1]} to do"),
    (r"^(\d+) apunte\(s\) sobre esta URL$", lambda m: f"{m[1]} note(s) about this URL"),
    (r"^Guardada como «(.+)»\. La tienes en las tarjetas para cualquier auditoría\.$",
     lambda m: f"Saved as «{m[1]}». It is in the cards for any audit."),
    (r"^¿Borrar la instrucción guardada «(.+)»\?$", lambda m: f"Delete the saved instruction «{m[1]}»?"),
    (r"^Borrar «(.+)»$", lambda m: f"Delete «{m[1]}»"),
    (r"^(\d+) fila\(s\)$", lambda m: f"{m[1]} row(s)"),
    (r"^(\d+) URL\(s\)$", lambda m: f"{m[1]} URL(s)"),
    (r"^(\d+) URL\(s\) afectada\(s\)$", lambda m: f"{m[1]} affected URL(s)"),
    (r"^(\d+) avisos?$", lambda m: f"{m[1]} warning(s)"),
    (r"^(\d+) de (\d+) URLs sin problemas$", lambda m: f"{m[1]} of {m[2]} URLs without issues"),
    (r"^(\d+) páginas bien y (\d+) con problemas$", lambda m: f"{m[1]} good pages and {m[2]} with issues"),
    (r"^(\d+) URLs · (\d+) indexables · (\d+) incidencias$", lambda m: f"{m[1]} URLs · {m[2]} indexable · {m[3]} issues"),
    (r"^(\d+) páginas · (\d+) indexables · (\d+) incidencias$", lambda m: f"{m[1]} pages · {m[2]} indexable · {m[3]} issues"),
    (r"^Los (\d+) puntos básicos$", lambda m: f"The {m[1]} basics"),
    (r"^Páginas bien en los (\d+) puntos básicos: (\d+) de (\d+)\.$", lambda m: f"Pages fine on the {m[1]} basics: {m[2]} of {m[3]}."),
    (r"^Sin contar (\d+) problemas? que afectan? a toda la web y se arreglan? una sola vez: (.+)\.$",
     lambda m: f"Not counting {m[1]} problem(s) that affect the whole site and are fixed once: {m[2]}."),
    (r"^Nota para (\d+) URLs?$", lambda m: f"Note for {m[1]} URL{'s' if m[1] != '1' else ''}"),
    (r"^(\d+) nota\(s\) sobre esta URL$", lambda m: f"{m[1]} note(s) about this URL"),
    (r"^Notas antiguas por URL · (\d+)$", lambda m: f"Older notes per URL · {m[1]}"),
    (r"^\+(\d+) más$", lambda m: f"+{m[1]} more"),
    (r"^Notas \((\d+)\)$", lambda m: f"Notes ({m[1]})"),
    (r"^Para (\d+) URLs?:$", lambda m: f"For {m[1]} URL{'s' if m[1] != '1' else ''}:"),
    (r"^Notas por URL \(clic derecho en la tabla\) · (\d+)$", lambda m: f"Notes per URL (right click in the table) · {m[1]}"),
    (r"^(\d+) URLs en esta vista$", lambda m: f"{m[1]} URLs in this view"),
    (r"^Ninguna URL seleccionada · (\d+) URLs en esta vista$", lambda m: f"No URL selected · {m[1]} URLs in this view"),
    (r"^Ninguna URL seleccionada · (\d+) URLs$", lambda m: f"No URL selected · {m[1]} URLs"),
    (r"^(Nombre|Modo|Texto) búsqueda (\d+)$", lambda m: f"Search {dict(Nombre='name', Modo='mode', Texto='text')[m[1]]} {m[2]}"),
    (r"^(Nombre|Tipo|Expresión) extracción (\d+)$", lambda m: f"Extraction {dict(Nombre='name', Tipo='type', Expresión='expression')[m[1]]} {m[2]}"),
    (r"^Estado de las (\d+) direcciones revisadas$", lambda m: f"Status of the {m[1]} URLs checked"),
    (r"^(\d+) de (\d+) páginas están bien en los (\d+) puntos básicos\.$", lambda m: f"{m[1]} of {m[2]} pages are fine on the {m[3]} basics."),
    (r"^▲ (\d+) páginas? más que en la revisión del (.+)$", lambda m: f"▲ {m[1]} more page(s) than in the review of {m[2]}"),
    (r"^▼ (\d+) páginas? menos que en la revisión del (.+)$", lambda m: f"▼ {m[1]} fewer page(s) than in the review of {m[2]}"),
    (r"^Las mismas que en la revisión del (.+)$", lambda m: f"The same as in the review of {m[1]}"),
    (r"^Páginas sin ningún problema: (\d+) de (\d+)\.$", lambda m: f"Pages without any issue: {m[1]} of {m[2]}."),
    (r"^Revisión técnica del (.+) · (\d+) páginas$", lambda m: f"Technical review of {m[1]} · {m[2]} pages"),
    (r"^Revisión de (\d+) páginas de (\S+)\. (.+)$", lambda m: f"Review of {m[1]} pages of {m[2]}. {_t(m[3])}"),
    (r"^Resumen de la revisión de (\d+) páginas de (\S+): cómo están las páginas y las 3 cosas más importantes que arreglar\.$",
     lambda m: f"Summary of the review of {m[1]} pages of {m[2]}: how the pages are doing and the 3 most important things to fix."),
    (r"^(.+?)\s*Hay (\d+) mejora\(s\) más en el informe completo\.$", lambda m: f"{_t(m[1])} There are {m[2]} more improvement(s) in the full report."),
    (r"^Solo se muestran fallos que siempre son un error\. Hay (\d+) tipos de aviso más que dependen de cada caso:$",
     lambda m: f"Only failures that are always an error are shown. There are {m[1]} more warning types that depend on each case:"),
    (r"^Además hay (\d+) tipos de aviso que dependen de cada caso\.$", lambda m: f"There are also {m[1]} warning types that depend on each case."),
    (r"^El (\d+) % sale de$", lambda m: f"{m[1]} % comes from"),
    (r"^y (\d+) más$", lambda m: f"and {m[1]} more"), (r"^Página (\d+) de (\d+)$", lambda m: f"Page {m[1]} of {m[2]}"),
    (r"^(\d+) auditoría\(s\) de (\S+), de (\S+) a (\S+)\.$", lambda m: f"{m[1]} audit(s) of {m[2]}, from {m[3]} to {m[4]}."),
    (r"^(\d+) URL\(s\) rastreadas, el resto queda pendiente\. Los datos mostrados son parciales\.$",
     lambda m: f"{m[1]} URL(s) crawled, the rest is pending. The data shown is partial."),
    (r"^Clave de la API de (.+)$", lambda m: f"{_t(m[1])} API key"), (r"^Modelo de (.+)$", lambda m: f"{_t(m[1])} model"),
    (r"^Por defecto: (\S+)$", lambda m: f"Default: {m[1]}"),
    (r"^Se alcanzó el tope de (\d+) URLs: hay URLs sin rastrear\. Los candidatos huérfanos y los duplicados pueden estar incompletos\.$",
     lambda m: f"The {m[1]} URL cap was reached: some URLs were not crawled. Orphan candidates and duplicates may be incomplete."),
    (r"^Solo se comprobaron (\d+) de (\d+) (.+) \(tope\)\.$", lambda m: f"Only {m[1]} of {m[2]} {_t(m[3])} were checked (cap)."),
    (r"^Tiempos de respuesta: (\d+) URL\(s\) de más de (\d+) ms medidas otras dos veces en solitario; se guarda la mediana de las tres \(en (\d+) bajó: la primera era ruido del rastreo\)\.$",
     lambda m: f"Response times: {m[1]} URL(s) over {m[2]} ms measured twice more on their own; the median of the three is kept (it dropped in {m[3]}: the first was crawl noise)."),
    (r"^Tiempos de respuesta: (\d+) medición\(es\) de más de (\d+) ms repetidas en solitario; en (\d+) se conservó la segunda, más rápida \(la primera era ruido del rastreo\)\.$",
     lambda m: f"Response times: {m[1]} measurement(s) over {m[2]} ms repeated on their own; in {m[3]} the second, faster one was kept (the first was crawl noise)."),
    (r"^Renderizado JavaScript activo \(([^)]+)\)\.(?: Scripts de terceros bloqueados: (\d+) dominios\.)?(?: Descubrimiento de enlaces por clic: hasta (\d+) clics por página, sin enviar formularios ni peticiones POST\.)?$",
     lambda m: f"JavaScript rendering on ({m[1]})." + (f" Third-party scripts blocked: {m[2]} domains." if m[2] else "")
     + (f" Link discovery by click: up to {m[3]} clicks per page, without submitting forms or POST requests." if m[3] else "")),
    (r"^o abre una de tus (\d+) auditorías guardadas$", lambda m: f"or open one of your {m[1]} saved audits"),
    (r"^Ver la lista \((\d+) dominios\)$", lambda m: f"See the list ({m[1]} domains)"),
    (r"^← Auditoría · (.+)$", lambda m: f"← Audit · {m[1]}"),
    (r"^Perspectivas · (.+)$", lambda m: f"Insights · {m[1]}"), (r"^Visualización · (.+)$", lambda m: f"Visualisation · {m[1]}"),
    (r"^(Informe|Resumen) SEO · (.+)$", lambda m: f"SEO {'report' if m[1] == 'Informe' else 'summary'} · {m[2]}"),
    (r"^gravedad (\w+)$", lambda m: f"severity {_t(m[1].capitalize()).lower()}"),
    (r"^incluye URLs muy enlazadas \((\d+) inlinks\)$", lambda m: f"includes highly linked URLs ({m[1]} inlinks)"),
    (r"^(\d+) páginas?(, incluida la portada)?$", lambda m: f"{m[1]} page{'s' if m[1] != '1' else ''}{', including the home page' if m[2] else ''}"),
    (r"^En (\d+) páginas?$", lambda m: f"On {m[1]} page(s)"),
    (r"^Camino más corto desde la URL inicial: (\d+) clic\(s\)\.$", lambda m: f"Shortest path from the start URL: {m[1]} click(s)."),
    (r"^Mostrando 200 de (\d+)\. Exporta el CSV de incidencias para ver todas\.$", lambda m: f"Showing 200 of {m[1]}. Export the issues CSV to see them all."),
    (r"^(.+) \((\d+)\)$", lambda m: f"{_t(m[1])} ({m[2]})"),
    (r"^(.+): (\d+)$", lambda m: f"{_t(m[1])}: {m[2]}"),
]]


# Last resort for texts the crawler writes with data inside (issue evidence, notes): phrase by phrase.
PHRASES: list[tuple[re.Pattern, str]] = [(re.compile(rx), en) for rx, en in [
    (r"Enlazada desde (\d+) página\(s\)", r"Linked from \1 page(s)"), (r"Usado en (\d+) página\(s\)", r"Used on \1 page(s)"),
    (r"El sitemap la incluye y responde", "The sitemap includes it and it returns"), (r"Disallow en robots\.txt\.", "Disallow in robots.txt."),
    (r"Teléfono en texto sin enlace tel:", "Phone as text without a tel: link:"),
    (r"no aparece en el título ni en el H1 \(tampoco en la URL\)\. Título:", "does not appear in the title or the H1 (nor in the URL). Title:"),
    (r"no aparece en el H1 \(tampoco en la URL\)\.", "does not appear in the H1 (nor in the URL)."),
    (r"no aparece en el título \(tampoco en la URL\)\.", "does not appear in the title (nor in the URL)."),
    (r"falta la propiedad obligatoria", "missing required property"), (r"falta la propiedad recomendada", "missing recommended property"),
    (r"(\d+) de (\d+) páginas con teléfono", r"\1 of \2 pages with a phone"), (r"(\d+) de (\d+) páginas de servicio", r"\1 of \2 service pages"),
    (r"(\d+) de (\d+) páginas", r"\1 of \2 pages"), (r"(\d+) imagen\(es\) sin alt:", r"\1 image(s) without alt:"),
    (r"(\d+) enlace\(s\) internos nofollow hacia:", r"\1 internal nofollow link(s) to:"), (r"(\d+) enlace\(s\):", r"\1 link(s):"),
    (r"(\d+) palabras en HTML frente a (\d+) tras renderizar", r"\1 words in HTML vs \2 after rendering"),
    (r"no es un código de región ISO 3166-1", "is not an ISO 3166-1 region code"), (r"no es un código de idioma ISO 639-1", "is not an ISO 639-1 language code"),
    (r"^(\d+) s o más$", r"\1 s or more"), (r" con (\d+) s ", r" with \1 s "),
    (r"^Aparece (.+); el teléfono habitual es (.+) \((\d+) páginas\)$", r"\1 appears; the usual phone is \2 (\3 pages)"),
    (r"^La ciudad \((.+)\) en el título de cada servicio$", r"The city (\1) in the title of each service"),
    (r"^La ciudad \((.+)\) en el encabezado principal \(H1\) de cada servicio$", r"The city (\1) in the main heading (H1) of each service"),
    (r": falta ", ": missing "), (r"(\d+) palabra\(s\) desconocidas", r"\1 unknown word(s)"), (r" \(IA\)$", " (AI)"),
    (r"^Las notas se guardan por sitio: siguen apareciendo en los próximos rastreos de (\S+)\. Para borrar una nota, déjala vacía con estado «Pendiente»\.$",
     r"Notes are saved per site: they keep appearing in the next crawls of \1. To delete a note, leave it empty with status «Pending»."),
    (r" / renderizado: ", " / rendered: "), (r"^(\d+) H1:", r"\1 H1s:"), (r"^(\d+) caracteres:", r"\1 characters:"), (r"(\d+) car\. · ", r"\1 chars · "),
    (r"^(\d+) URL\(s\) o páginas afectadas por el límite «(.+)» de la configuración\.$", r"\1 URL(s) or pages affected by the «\2» limit of the configuration."),
]]

# Texts with numbers or names inside that were still in Spanish ({1} = as it is, {t1} = translated).
EXTRA_PATTERNS_EN = [
    ('^(Comprobando enlaces externos|Comprobando recursos internos|Repitiendo tiempos lentos|PageSpeed Insights) (\\d+) / (\\d+)$', "{t1} {2} / {3}"),
    ('^Detenido por ti · se analizaron (\\d+) URL\\(s\\)$', "Stopped by you · {1} URL(s) analysed"),
    ('^Conectado el (.+) UTC\\.$', "Connected on {1} UTC."),
    ('^Conectado el (.+) UTC\\. Importa los datos desde cada auditoría: Archivo › Importar datos\\.$', "Connected on {1} UTC. Import the data from each audit: File › Import data."),
    ('^Guardada (\\S+) · déjalo vacío para mantenerla$', "Saved {1} · leave it empty to keep it"),
    ('^páginas HTML de la vista actual \\(pestaña, filtro y búsqueda\\), como mucho (\\d+)\\. Una petición por página a (.+?)( \\(.+?\\))?(: el texto de cada página sale de tu equipo y tiene coste según tu plan)?\\.$', "HTML pages in the current view (tab, filter and search), at most {1}. One request per page to {2}{3}{4?: each page's text leaves your computer and costs according to your plan}."),
    ('^(.+?) — (El contenido de la página se envía a la API de .+\\.|Modelo local con Ollama; el contenido no sale de tu equipo\\.)$', "{1} — {t2}"),
    ('^Incidencias de esta URL \\((\\d+)\\)$', "Issues for this URL ({1})"),
    ('^Copyscape no encontró copias de (.+)\\.$', "Copyscape found no copies of {1}."),
    ('^(Allow|Disallow): (.*) \\(línea (\\d+)\\)$', "{1}: {2} (line {3})"),
    ('^(\\d+) caracteres \\(mínimo (\\d+)\\): «(.*)»$', "{1} characters (minimum {2}): «{3}»"),
    ('^(\\d+) palabras visibles \\(mínimo (\\d+)\\)$', "{1} visible words (minimum {2})"),
    (r'^Aparece ([+\d][\d\s().-]{5,})$', "{1} appears"),
    ('^(.+) \\((\\d+) saltos\\)$', "{1} ({2} hops)"),
    ('^(.+) a lo largo del tiempo$', "{t1} over time"),
    ('^Auditoría · inicio (.*) UTC · fin (.*) UTC · estado (\\S+) · generado (.+) con LetsCrawlit v(\\S+)$', "Audit · start {1} UTC · end {2} UTC · status {3} · generated {4} with LetsCrawlit v{5}"),
    ('^Tope de URLs: (.+) · pausa: (.+) s · JavaScript: sí · robots\\.txt respetado: sí$', "URL cap: {t1} · pause: {2} s · JavaScript: yes · robots.txt respected: yes"),
    ('^Tope de URLs: (.+) · pausa: (.+) s · JavaScript: no · robots\\.txt respetado: sí$', "URL cap: {t1} · pause: {2} s · JavaScript: no · robots.txt respected: yes"),
    ('^Permiso confirmado el (.+): «(.+)»$', "Permission confirmed on {1}: «{t2}»"),
    ('^(\\d+) \\((\\d+) sin alt\\)$', "{1} ({2} without alt)"),
    ('^(\\d+) inválido\\(s\\)$', "{1} invalid"),
    ('^Página AMP · (.+)$', "AMP page · {t1}"),
    ('^Versión AMP: (.+)$', "AMP version: {1}"),
    ('^título «(.*)» · robots (.*) · canonical (.*)$', "title «{1}» · robots {2} · canonical {3}"),
    ('^(\\d+) peticiones · (.+) KB · carga completa en (\\d+) ms$', "{1} requests · {2} KB · fully loaded in {3} ms"),
    ('^(\\d+) palabra\\(s\\) desconocidas \\((\\w+)\\)\\. Si alguna es correcta, añádela al diccionario \\(vale para todo el equipo\\):$', "{1} unknown word(s) ({2}). If any is correct, add it to the dictionary (it applies to the whole team):"),
]
PATTERNS += [(re.compile(rx), (lambda tpl: lambda m: _fill(tpl, m, "en"))(tpl)) for rx, tpl in EXTRA_PATTERNS_EN]



def _fill(template: str, m: re.Match, lang: str) -> str:
    """{1} = the matched text, {t1} = the same translated, {2?text} = «text» only if group 2 matched."""
    def one(g: re.Match) -> str:
        value = m[int(g[2])]
        if g[3] is not None:
            return g[3] if value else ""
        if g[1]:
            return (tr(value or "", lang) or value or "")
        return value or ""
    return re.sub(r"\{(t?)(\d+)(?:\?([^}]*))?\}", one, template)


def tr(text: str, lang: str = "en") -> str | None:
    """`text` in `lang` (English by default), or None when there is no translation."""
    t = " ".join(text.split())
    if not t:
        return None
    if lang not in LOCALES:
        return _tr_en(t)
    loc = LOCALES[lang]
    texts = loc["texts"]
    if t in texts:
        return texts[t]
    for rx, _fn in PATTERNS:
        m = rx.match(t)
        if m and rx.pattern in loc["patterns"]:
            return _fill(loc["patterns"][rx.pattern], m, lang)
    for suffix in ("…", ":", "."):
        if t.endswith(suffix) and len(t) > len(suffix) and t[:-len(suffix)] in texts:
            return texts[t[:-len(suffix)]] + suffix
    m = re.match(r"^([^\w¿¡«(<]+?)\s*(\S.*)$", t)  # leading symbol: «▶ Empezar», «📄 …»
    if m and re.search(_WORD, m[2]) and m[2] in texts:
        return f"{m[1]} {texts[m[2]]}"
    if " · " in t:
        parts = t.split(" · ")
        done = [tr(p, lang) for p in parts]
        if any(done):
            return " · ".join(d or p for d, p in zip(done, parts))
    out = t
    for rx, repl in loc["phrases"].items():
        out = re.sub(rx, repl, out)
    if out != t:
        return out
    return _tr_en(t)  # better English than a sentence half in Spanish


def _tr_en(t: str) -> str | None:
    """English for one piece of interface text, or None when there is no translation."""
    if t in EN:
        return EN[t]
    for rx, fn in PATTERNS:
        m = rx.match(t)
        if m:
            out = fn(m)
            if out != t:
                return out
    for suffix in ("…", ":", "."):
        if t.endswith(suffix) and len(t) > len(suffix) and t[:-len(suffix)] in EN:
            return EN[t[:-len(suffix)]] + suffix
    m = re.match(r"^([^\w¿¡«(<]+?)\s*(\S.*)$", t)  # leading symbol: «▶ Empezar», «⇪ Exportar», «📄 …»
    if m and re.search(_WORD, m[2]):
        rest = _tr_en(" ".join(m[2].split()))
        if rest:
            return f"{m[1]} {rest}"
    if " · " in t:
        parts = t.split(" · ")
        done = [_tr_en(p) for p in parts]
        if any(done):
            return " · ".join(d or p for d, p in zip(done, parts))
    out = t
    for rx, en in PHRASES:
        out = rx.sub(en, out)
    return out if out != t else None


def _swap(raw: str, escaped: bool, lang: str = "en") -> str:
    """Translate one HTML fragment (text node or attribute value) keeping its surrounding whitespace."""
    plain = html.unescape(raw) if escaped else raw
    if not re.search(_WORD, plain):
        return raw
    core = plain.strip()
    out = tr(core, lang)
    if out is None:
        return raw
    lead = plain[:len(plain) - len(plain.lstrip())]
    trail = plain[len(plain.rstrip()):]
    return lead + (html.escape(out, quote=True) if escaped else out) + trail


_ATTR = re.compile(r'(\s(?:placeholder|title|aria-label|data-why|data-fix|alt|label|data-crumb|data-label|hx-confirm|data-confirm|data-h1|data-h2)=")([^"]*)(")')
_TEXT = re.compile(r">([^<]+)<")
_RAW = re.compile(r"(<(script|style|textarea|pre|code|lc-raw)\b[^>]*>)(.*?)(</\2>)", re.S | re.I)  # lc-raw: already in the language (the manual)

# Strings built in JavaScript (messages of the start page, tooltip headings, the crumb of the settings dialog).
JS_STRINGS = {
    "'Por qué importa'": "'Why it matters'", "'Cómo se arregla'": "'How to fix it'",
    "'Configuración del rastreo'": "'Crawl configuration'",
    "'Primero escribe arriba la URL de la web que quieres revisar.'": "'First type the URL of the website you want to check above.'",
    "'La URL no es válida.'": "'The URL is not valid.'",
    "'» preparada. Marca «Tengo permiso» y pulsa Rastrear.'": "'» is ready. Tick «I have permission» and press Crawl.'",
    "'Empezando «'": "'Starting «'",
    "'Auditoría completa'": "'Full audit'", "'Solo enlaces rotos'": "'Broken links only'",
    "'Solo imágenes pesadas'": "'Heavy images only'", "'Solo el sitemap'": "'Sitemap only'",
    "'(la URL escrita arriba)'": "'(the URL typed above)'", "'¿Volver a la configuración de fábrica?'": "'Back to the factory configuration?'",
    "'¿Eliminar esta auditoría y todos sus datos locales? No se puede deshacer.'": "'Delete this audit and all its local data? This cannot be undone.'",
}
# The same texts for the scripts in /static, which ask for them with lcT('…') (see static/actions.js).
JS_TEXTS = {es.split("'", 1)[1].rsplit("'", 1)[0]: en.split("'", 1)[1].rsplit("'", 1)[0] for es, en in JS_STRINGS.items()}
JS_TEXTS["No se pudo guardar: "] = "Could not save: "
JS_TEXTS["Apuntes"] = "Notes"
JS_TEXTS["URL seleccionada: "] = "Selected URL: "
JS_TEXTS["Copiado"] = "Copied"
JS_TEXTS["1 URL seleccionada"] = "1 URL selected"
JS_TEXTS.update({"Ninguna URL seleccionada": "No URL selected", " URLs seleccionadas": " URLs selected",
                 "Copiar URLs": "Copy URLs", "URLs copiadas": "URLs copied", "Las URLs seleccionadas": "The selected URLs",
                 "El apunte quedará vinculado a estas URLs: ": "The note will be linked to these URLs: ",
                 "Cómo arreglarlo: ": "How to fix it: ", "Nota guardada": "Note saved", "Notas": "Notes",
                 "Descargar el resumen en PDF": "Download the summary as PDF",
                 "Descargar el informe completo en PDF": "Download the full report as PDF"})
JS_TEXTS.update({"Hay una versión nueva de LetsCrawlit: ": "A new version of LetsCrawlit is available: ", ". Tienes la ": ". You have ", "Descargar": "Download", "Ahora no": "Not now"})  # «Hay una versión nueva»
I18N_SLOT = '<script type="application/json" id="lc-i18n">{}</script>'


def js_texts(lang: str) -> dict:
    """The texts the scripts ask for with lcT('…'), in `lang` (English where a language lacks one)."""
    if lang == "en":
        return JS_TEXTS
    own = LOCALES.get(lang, {}).get("js", {})
    return {k: own.get(k, v) for k, v in JS_TEXTS.items()} | own


def translate_html(doc: str, lang: str) -> str:
    """The whole page (or htmx fragment) in `lang`. Spanish is returned untouched."""
    if lang not in LANGS or lang == "es" or not doc:
        return doc
    if I18N_SLOT in doc:
        data = json.dumps(js_texts(lang), ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        doc = doc.replace(I18N_SLOT, I18N_SLOT.replace("{}", data), 1)
    keep: list[str] = []

    def stash(m: re.Match) -> str:
        tag = m[2].lower()
        body = m[3]
        if tag == "script" and lang == "en":
            for es, en in JS_STRINGS.items():
                body = body.replace(es, en)
        elif tag in ("code", "pre", "textarea"):
            pass  # code, editable values and examples stay as written
        keep.append(m[1] + body + m[4])
        return f"<!--lc-keep-{len(keep) - 1}-->"  # looks like a tag, so the text around it splits cleanly

    # attributes of the raw blocks' opening tags are translated too (e.g. a textarea placeholder)
    doc = _RAW.sub(stash, doc)
    keep[:] = [_ATTR.sub(lambda a: a[1] + _swap(a[2], True, lang) + a[3], k) if not k.lower().startswith("<script") else k
               for k in keep]
    doc = _ATTR.sub(lambda a: a[1] + _swap(a[2], True, lang) + a[3], doc)
    doc = _TEXT.sub(lambda m: ">" + _swap(m[1], True, lang) + "<", doc)
    doc = re.sub(r'<html lang="es"', f'<html lang="{lang}"', doc, count=1)
    return re.sub(r"<!--lc-keep-(\d+)-->", lambda m: keep[int(m[1])], doc)


# The interface has no emoji or icon symbols any more (▶ ⚙ ✓ ↗…); the dictionaries were written with them («📄 Exportar resumen»).
# Every entry with an emoji also answers for the same text without it, so no translation is lost.
_EMOJI = re.compile("(?:[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF⭐⚡⚠✏✍✓✕●○⚙▶■↻↺⌫⬇⇪☑↗←]|❚❚)️? ?")


def _add_plain(d: dict) -> None:
    for k, v in list(d.items()):
        if isinstance(k, str) and isinstance(v, str) and _EMOJI.search(k):
            d.setdefault(" ".join(_EMOJI.sub("", k).split()), " ".join(_EMOJI.sub("", v).split()))


_add_plain(EN)
_add_plain(JS_STRINGS)
for _loc in LOCALES.values():
    _add_plain(_loc["texts"])
    _add_plain(_loc["js"])
