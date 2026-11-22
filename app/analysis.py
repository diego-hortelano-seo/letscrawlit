"""Core transformation: crawled pages + links -> prioritized, explained issues. Pure functions."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

from urllib.parse import unquote, urlsplit

from .extract import NON_DESCRIPTIVE, is_noindex
from .urlutil import same_site

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEVERITY_LABEL = {"critical": "Crítico", "high": "Alto", "medium": "Medio", "low": "Bajo", "info": "Info"}

# code: (severity, title, why it matters, concrete remediation)
CATALOG: dict[str, tuple[str, str, str, str]] = {
    "server_error": ("critical", "Error de servidor o URL inaccesible",
        "Google y los usuarios no pueden acceder; si persiste, la URL acaba saliendo del índice.",
        "Revisa los logs del servidor para esa URL, corrige el error 5xx o el fallo de conexión y vuelve a rastrear."),
    "broken_link": ("high", "Enlace interno roto (4xx)",
        "Los enlaces rotos desperdician presupuesto de rastreo, pierden autoridad interna y frustran al usuario.",
        "Actualiza o elimina el enlace en las páginas de origen indicadas, o redirige (301) la URL rota a su equivalente vigente."),
    "redirect_loop": ("critical", "Bucle de redirecciones",
        "La URL nunca llega a un destino: ni usuarios ni buscadores pueden ver el contenido.",
        "Corrige las reglas de redirección (.htaccess, servidor o CMS) para que la cadena termine en una URL 200."),
    "redirect_chain": ("medium", "Cadena de redirecciones",
        "Cada salto añade latencia y Google deja de seguir cadenas largas; se diluyen señales.",
        "Redirige la primera URL directamente al destino final y actualiza los enlaces internos para que apunten a él."),
    "link_to_redirect": ("low", "Enlaces internos que apuntan a una redirección",
        "Obliga a un salto extra en cada visita y en cada rastreo.",
        "Cambia el href de los enlaces de origen por la URL de destino final."),
    "missing_title": ("high", "Falta la etiqueta <title>",
        "El título es la señal on-page más visible en resultados de búsqueda.",
        "Añade un <title> único y descriptivo de 30–60 caracteres con la palabra clave principal."),
    "duplicate_title": ("medium", "Título duplicado",
        "Varias páginas compiten por la misma intención y Google puede elegir la que no quieres.",
        "Escribe un título único por página que refleje su contenido específico, o consolida las páginas con canonical/301."),
    "title_too_long": ("info", "Título demasiado largo",
        "Google lo truncará en los resultados y puede reescribirlo.",
        "Acorta el título a unos 60 caracteres dejando la palabra clave al principio."),
    "title_too_short": ("low", "Título demasiado corto",
        "Desaprovecha espacio para explicar la página y captar clics.",
        "Amplía el título con el servicio y la ubicación o marca (30–60 caracteres)."),
    "missing_description": ("medium", "Falta la meta descripción",
        "Google generará un fragmento automático que puede no invitar al clic.",
        "Añade <meta name=\"description\"> única de 70–160 caracteres con una propuesta clara."),
    "duplicate_description": ("low", "Meta descripción duplicada",
        "Los fragmentos idénticos no diferencian las páginas en los resultados.",
        "Redacta una descripción única para cada página."),
    "description_too_long": ("info", "Meta descripción demasiado larga",
        "Se truncará en los resultados.",
        "Recorta la descripción a 160 caracteres como máximo."),
    "missing_h1": ("medium", "Falta el encabezado H1",
        "El H1 indica el tema principal de la página a usuarios y buscadores.",
        "Añade un único <h1> visible que describa el contenido principal."),
    "multiple_h1": ("low", "Varios H1 en la misma página",
        "Diluye cuál es el tema principal de la página.",
        "Deja un solo <h1> y convierte el resto en <h2>/<h3> según la jerarquía."),
    "duplicate_content": ("high", "Contenido duplicado exacto",
        "Páginas idénticas compiten entre sí y Google indexará solo una, no necesariamente la correcta.",
        "Elige la URL principal: redirige (301) las demás o añade rel=canonical hacia ella; si deben existir, diferencia su contenido."),
    "thin_content": ("low", "Contenido escaso",
        "Las páginas con poco texto rara vez posicionan y pueden considerarse de baja calidad.",
        "Amplía el contenido con información útil y específica, o marca la página como noindex si no aporta valor de búsqueda."),
    "orphan_candidate": ("medium", "Candidata a página huérfana",
        "Está en el sitemap pero ninguna página rastreada la enlaza: recibe poca autoridad y se descubre tarde.",
        "Enlázala desde páginas relevantes (menú, categorías o contenido relacionado), o quítala del sitemap si ya no es necesaria."),
    "noindex_in_sitemap": ("high", "Página noindex incluida en el sitemap",
        "Señales contradictorias: el sitemap pide indexar y la página pide no indexar.",
        "Quita la URL del sitemap o elimina el noindex si la página debe posicionar."),
    "non200_in_sitemap": ("medium", "URL del sitemap que no devuelve 200",
        "El sitemap debe listar solo URLs finales e indexables; los errores y redirecciones restan confianza.",
        "Sustituye la URL por su destino final 200 o elimínala del sitemap."),
    "canonicalised_in_sitemap": ("low", "URL canonicalizada incluida en el sitemap",
        "El sitemap debe contener la versión canónica, no las alternativas.",
        "Incluye en el sitemap la URL canónica y elimina esta."),
    "blocked_in_sitemap": ("high", "URL del sitemap bloqueada por robots.txt",
        "Pides indexar una URL que prohíbes rastrear.",
        "Desbloquéala en robots.txt o quítala del sitemap."),
    "multiple_canonicals": ("high", "Varias etiquetas canonical distintas",
        "Con canonicals contradictorios Google ignora todos.",
        "Deja una sola <link rel=\"canonical\"> en el <head> (revisa plugins SEO duplicados)."),
    "canonical_noindex_conflict": ("high", "Canonical a otra URL + noindex",
        "Combinar noindex con canonical a otra página envía señales opuestas y puede desindexar la canónica.",
        "Usa solo una señal: canonical hacia la URL principal (recomendado) o noindex, pero no ambas."),
    "canonical_to_non200": ("high", "Canonical apunta a una URL que no devuelve 200",
        "Google ignora canonicals hacia redirecciones o errores.",
        "Apunta el canonical a la URL final que responde 200."),
    "canonical_to_noindex": ("high", "Canonical apunta a una página noindex",
        "La URL canónica pide no ser indexada: ninguna de las dos posicionará.",
        "Elimina el noindex de la canónica o cambia el canonical a una URL indexable."),
    "canonical_blocked": ("high", "Canonical apunta a una URL bloqueada por robots.txt",
        "Google no puede rastrear la canónica para confirmarla.",
        "Desbloquea la URL canónica en robots.txt o elige otra canónica rastreable."),
    "canonical_chain": ("medium", "Cadena de canonicals",
        "La canónica declara a su vez otra canónica; Google puede no seguir la cadena.",
        "Haz que todas las variantes apunten directamente a la canónica final."),
    "canonical_cross_domain": ("medium", "Canonical a otro dominio",
        "Cede la indexación de esta página a otro sitio; correcto solo si es intencionado (sindicación).",
        "Verifica que es intencionado; si no, cambia el canonical a la propia URL."),
    "missing_canonical": ("low", "Sin etiqueta canonical",
        "Sin canonical, parámetros o variantes (http/https, www, barra final) pueden indexarse por separado.",
        "Añade <link rel=\"canonical\"> autorreferenciado con la URL preferida."),
    "robots_header_conflict": ("medium", "Conflicto entre meta robots y X-Robots-Tag",
        "Las dos fuentes dicen cosas distintas; Google aplica la más restrictiva.",
        "Unifica la directiva en un solo lugar (cabecera del servidor o meta en HTML)."),
    "rendered_robots_conflict": ("high", "JavaScript cambia la directiva robots",
        "El HTML inicial y el renderizado tienen distinto noindex: Google puede actuar sobre cualquiera de los dos.",
        "Sirve la directiva robots correcta en el HTML del servidor y no la modifiques con JavaScript."),
    "rendered_canonical_conflict": ("high", "JavaScript cambia o inyecta el canonical",
        "Un canonical distinto tras renderizar envía señales contradictorias.",
        "Incluye el canonical definitivo en el HTML del servidor y evita alterarlo con scripts."),
    "rendered_title_changed": ("low", "JavaScript cambia el título",
        "Google puede indexar el título inicial o el renderizado de forma inconsistente.",
        "Sirve el título definitivo en el HTML del servidor."),
    "js_dependent_content": ("medium", "Contenido que depende de JavaScript",
        "La mayor parte del texto solo aparece tras renderizar; se indexa más tarde y con más riesgo.",
        "Renderiza el contenido principal en el servidor (SSR/prerender) o inclúyelo en el HTML inicial."),
    "blocked_but_linked": ("low", "URL enlazada pero bloqueada por robots.txt",
        "Google puede indexarla sin ver su contenido (ni su noindex).",
        "Si no debe aparecer en Google, desbloquéala y usa noindex; si debe aparecer, quítala del Disallow."),
    "nofollow_internal": ("low", "Enlaces internos con nofollow",
        "Nofollow interno corta el flujo de autoridad hacia tus propias páginas.",
        "Quita rel=\"nofollow\" de los enlaces internos (salvo áreas como login o carrito)."),
    "images_missing_alt": ("low", "Imágenes sin atributo alt",
        "Afecta a la accesibilidad y a la búsqueda de imágenes.",
        "Añade alt descriptivo a cada imagen informativa, o alt=\"\" si es puramente decorativa."),
    "invalid_jsonld": ("medium", "JSON-LD no válido",
        "Los datos estructurados con errores de sintaxis se ignoran por completo.",
        "Corrige el JSON (comas, comillas, llaves) y valídalo en la Prueba de resultados enriquecidos de Google."),
    "no_structured_data": ("info", "Página de inicio sin datos estructurados",
        "Schema.org ayuda a obtener resultados enriquecidos (negocio local, valoraciones, FAQ).",
        "Añade JSON-LD de tipo LocalBusiness/Organization con nombre, dirección, teléfono y horario."),
    "slow_response": ("low", "Respuesta lenta del servidor (>1,5 s)",
        "Un TTFB alto empeora la experiencia y reduce el rastreo.",
        "Activa caché de página, revisa plugins/consultas lentas o mejora el alojamiento."),
    "broken_external": ("medium", "Enlace externo roto",
        "Enlazar a páginas que ya no existen empeora la experiencia y la confianza en tu contenido.",
        "Actualiza el enlace a la nueva dirección del recurso externo o elimínalo de las páginas de origen."),
    "http_url": ("high", "Página servida por HTTP (sin cifrar)",
        "Los navegadores la marcan como «No segura» y HTTPS es una señal de posicionamiento.",
        "Instala un certificado SSL, redirige (301) todo HTTP a HTTPS y actualiza los enlaces internos."),
    "mixed_content": ("high", "Contenido mixto (recursos HTTP en página HTTPS)",
        "El navegador bloquea o avisa de los recursos inseguros: imágenes, estilos o scripts pueden no cargar.",
        "Cambia las URLs de esos recursos a https:// (o rutas relativas) y verifica que existen por HTTPS."),
    "missing_hsts": ("info", "Falta la cabecera Strict-Transport-Security",
        "Sin HSTS, la primera visita puede llegar por HTTP y ser interceptada.",
        "Añade en el servidor: Strict-Transport-Security: max-age=31536000; includeSubDomains."),
    "url_uppercase": ("low", "URL con mayúsculas",
        "Las URLs distinguen mayúsculas: es fácil crear duplicados y enlaces rotos.",
        "Usa URLs en minúsculas y redirige (301) las variantes con mayúsculas."),
    "url_underscore": ("low", "URL con guiones bajos",
        "Google recomienda guiones (-) para separar palabras en las URLs.",
        "Usa guiones en lugar de guiones bajos en las URLs nuevas; en las existentes, valora redirigir."),
    "url_non_ascii": ("low", "URL con caracteres no ASCII (tildes, ñ, espacios)",
        "Se codifican de forma distinta según el sistema y generan enlaces rotos o duplicados.",
        "Usa solo letras sin tilde, números y guiones en las URLs (p. ej. «cerrajeria» en vez de «cerrajería»)."),
    "url_parameters": ("info", "URL con parámetros",
        "Los parámetros pueden generar muchas variantes de la misma página.",
        "Comprueba que las variantes con parámetros tienen canonical a la URL limpia."),
    "url_too_long": ("low", "URL demasiado larga",
        "Las URLs largas son difíciles de compartir y se truncan en los resultados.",
        "Acorta la URL a lo esencial (servicio y ubicación) y redirige la antigua."),
    "url_multiple_slashes": ("low", "URL con barras dobles (//)",
        "Suele deberse a enlaces mal construidos y crea duplicados.",
        "Corrige la plantilla o el enlace que genera la doble barra y redirige la variante."),
    "hreflang_non200": ("medium", "Hreflang apunta a una URL que no devuelve 200",
        "Google ignora las anotaciones hreflang hacia redirecciones o errores.",
        "Apunta cada hreflang a la URL final 200 de esa versión de idioma."),
    "hreflang_no_return": ("medium", "Hreflang sin enlace de retorno",
        "Si la versión alternativa no enlaza de vuelta, Google descarta la pareja.",
        "Añade en la página alternativa el hreflang recíproco hacia esta URL."),
    "hreflang_no_self": ("low", "Hreflang sin autorreferencia",
        "Cada página con hreflang debe incluirse a sí misma en el conjunto.",
        "Añade un <link rel=\"alternate\" hreflang=\"xx\"> que apunte a la propia URL."),
    "broken_resource": ("high", "Recurso interno roto (imagen, CSS o JS)",
        "Imágenes que no cargan o estilos/scripts ausentes rompen el diseño y la experiencia.",
        "Sube de nuevo el archivo o corrige la ruta en las páginas que lo usan (ver Inlinks)."),
    "large_image": ("low", "Imagen pesada",
        "Las imágenes pesadas son la causa más habitual de un LCP lento en móvil.",
        "Comprime la imagen y sírvela en WebP/AVIF al tamaño en que se muestra."),
    "missing_viewport": ("medium", "Falta la etiqueta meta viewport",
        "Sin viewport la página se ve en miniatura en móviles y Google la considera no adaptada.",
        "Añade <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> en el <head>."),
    "multiple_titles": ("medium", "Varias etiquetas <title>",
        "Google elegirá una al azar o reescribirá el título.",
        "Deja un único <title> en el <head> (suele duplicarlo un plugin o el tema)."),
    "missing_lang": ("low", "Falta el atributo lang en <html>",
        "Ayuda a buscadores y lectores de pantalla a identificar el idioma.",
        "Añade <html lang=\"es\"> (o el idioma correspondiente)."),
    "lang_mismatch": ("high", "El idioma del texto no coincide con el atributo lang",
        "Google y los lectores de pantalla tratan la página en un idioma que no es el suyo; suele delatar plantillas o traducciones mal enlazadas.",
        "Corrige <html lang> o el contenido: cada versión de idioma con su texto, su título y su atributo lang."),
    "heading_non_sequential": ("low", "Encabezados no secuenciales",
        "Saltos de nivel (p. ej. H2 → H4) o empezar por un H2 dificultan entender la estructura.",
        "Ordena los encabezados empezando por un H1 y sin saltarte niveles."),
    "non_descriptive_anchor": ("info", "Enlaces internos con texto ancla no descriptivo",
        "Textos como «aquí» o «leer más» no dicen nada del destino a Google ni al usuario.",
        "Usa anclas que describan la página destino (p. ej. «cambio de cerradura en Madrid»)."),
    "ssl_error": ("critical", "Error de certificado SSL/TLS",
        "Los navegadores muestran una advertencia a pantalla completa y Google puede dejar de rastrear el sitio.",
        "Renueva o corrige el certificado (cadena completa, dominio correcto, no caducado) y vuelve a rastrear."),
    "meta_refresh_redirect": ("medium", "Redirección por meta refresh",
        "Google la sigue, pero es más lenta y menos fiable que una 301 y empeora la experiencia.",
        "Sustitúyela por una redirección 301 en el servidor."),
    "js_redirect": ("medium", "Redirección por JavaScript",
        "Solo funciona si se ejecuta JavaScript; se descubre tarde y transmite señales con menos fiabilidad.",
        "Usa una redirección 301 en el servidor en lugar de location.href/window.location."),
    "legacy_image_format": ("info", "Imagen en formato antiguo (JPG/PNG/GIF)",
        "WebP y AVIF pesan entre un 25 y un 50 % menos con la misma calidad.",
        "Convierte la imagen a WebP o AVIF (o sírvela con <picture> y fallback)."),
    "hreflang_invalid_code": ("high", "Código de idioma/región hreflang no válido",
        "Google ignora las anotaciones con códigos que no son ISO 639-1 / ISO 3166-1.",
        "Corrige el código (p. ej. «es-ES», «en-GB», no «es-SP» ni «uk» para inglés británico)."),
    "schema_error": ("medium", "Datos estructurados: faltan propiedades obligatorias",
        "Sin las propiedades obligatorias el marcado no es apto para resultados enriquecidos.",
        "Completa las propiedades indicadas y valida en la Prueba de resultados enriquecidos de Google."),
    "schema_warning": ("info", "Datos estructurados: faltan propiedades recomendadas",
        "Las propiedades recomendadas mejoran la probabilidad y la calidad del resultado enriquecido.",
        "Añade las propiedades recomendadas que apliquen a tu negocio."),
    "amp_error": ("high", "Página AMP no válida",
        "Una página AMP con errores no se sirve desde la caché AMP y puede perder su función.",
        "Corrige los errores indicados o retira la versión AMP y su link rel=amphtml."),
    "accessibility": ("medium", "Barreras de accesibilidad (WCAG)",
        "Impiden usar la web a personas con lectores de pantalla o teclado, y algunas afectan al SEO.",
        "Corrige cada punto indicado (criterio WCAG entre paréntesis): etiquetas en formularios, nombres accesibles, IDs únicos…"),
    "spelling_errors": ("low", "Posibles errores ortográficos",
        "Las faltas de ortografía restan confianza a usuarios y clientes potenciales.",
        "Revisa las palabras indicadas; si son correctas (marcas, términos locales) añádelas al diccionario personalizado."),
    "near_duplicate": ("medium", "Contenido casi duplicado",
        "Páginas muy parecidas compiten entre sí y Google puede tratarlas como duplicadas.",
        "Diferencia el contenido (servicio, ubicación, ejemplos propios) o consolida con canonical/301."),
    "orphan_with_traffic": ("high", "URL con tráfico sin enlaces internos",
        "Recibe visitas o clics en Google pero ninguna página rastreada la enlaza: estás desaprovechando autoridad.",
        "Enlázala desde páginas relevantes y el menú o, si ya no existe, redirígela."),
    "low_performance": ("medium", "Rendimiento móvil bajo (PageSpeed)",
        "Las Core Web Vitals influyen en la experiencia y en el posicionamiento.",
        "Revisa el informe de PageSpeed: optimiza imágenes (WebP, tamaño), difiere JS y reduce CSS bloqueante."),
    "description_too_short": ("low", "Meta descripción demasiado corta",
        "Desaprovecha el espacio para convencer de hacer clic.",
        "Amplía la descripción con el beneficio principal y una llamada a la acción."),
    "title_too_wide": ("low", "Título demasiado ancho en píxeles",
        "Google corta por ancho, no por caracteres: se verá con «…» en los resultados.",
        "Acorta el título o usa palabras más estrechas hasta quedar por debajo del máximo de píxeles."),
    "h1_too_long": ("info", "H1 demasiado largo",
        "Un titular largo diluye el tema principal y se lee peor.",
        "Resume el H1 en una frase corta con la palabra clave principal."),
    "h2_too_long": ("info", "H2 demasiado largo",
        "Los subtítulos largos dificultan escanear la página.",
        "Acorta los H2 a una idea por subtítulo."),
    "soft_404": ("high", "Posible 404 «suave»",
        "La página dice que no existe pero responde 200: Google la trata como error y desperdicia rastreo.",
        "Haz que esas URLs devuelvan 404 o 410 (o redirige con 301 a una alternativa real)."),
    "large_document": ("low", "Documento HTML demasiado grande",
        "Un HTML muy pesado tarda en descargarse y Google puede no procesarlo entero.",
        "Elimina código incrustado innecesario (SVG, CSS o JSON enormes) y carga lo secundario aparte."),
    "large_resource": ("low", "Recurso demasiado pesado (CSS, JS, PDF…)",
        "Los archivos grandes ralentizan la carga, sobre todo en móvil.",
        "Comprime y minifica el archivo, divídelo o cárgalo solo donde haga falta."),
    "high_internal_outlinks": ("info", "Demasiados enlaces internos salientes",
        "Con cientos de enlaces cada uno transmite menos autoridad y la página es difícil de usar.",
        "Reduce los menús y bloques de enlaces repetidos a lo esencial."),
    "high_external_outlinks": ("info", "Demasiados enlaces externos salientes",
        "Muchos enlaces a otros sitios pueden parecer poco naturales y sacan al usuario de tu web.",
        "Revisa si todos aportan; marca los de pago como sponsored y los de usuarios como ugc."),
    "deep_page": ("low", "Página muy profunda",
        "Las páginas a muchos clics de la inicial reciben menos autoridad y se rastrean menos.",
        "Enlázala desde el menú, categorías o páginas con más autoridad para acercarla."),
    "insecure_form": ("high", "Formulario que envía datos sin cifrar",
        "Los navegadores avisan al usuario y los datos viajan sin protección.",
        "Cambia el action del formulario a https:// (o a una ruta relativa)."),
    "js_click_navigation": ("medium", "Navegación que solo funciona con clic (sin <a href>)",
        "Google no hace clic: las páginas a las que solo se llega con botones u onclick no reciben enlaces internos "
        "y pueden no descubrirse ni posicionar.",
        "Convierte esos elementos en enlaces reales <a href=\"/destino\"> (en React: <Link>, en Angular: routerLink sobre <a>), "
        "manteniendo el JavaScript si hace falta."),
    # --- SEO local ---
    "local_no_schema": ("medium", "SEO local: sin datos estructurados LocalBusiness",
        "Es la ficha que le dice a Google nombre, dirección, teléfono y horario del negocio; ayuda en el mapa y en búsquedas «cerca de mí».",
        "Añade en la página de inicio un JSON-LD del subtipo más concreto (Locksmith, GeneralContractor, Plumber…) con name, "
        "address, telephone, openingHoursSpecification, geo, url, image y areaServed."),
    "local_schema_incomplete": ("low", "SEO local: ficha LocalBusiness incompleta",
        "Sin dirección, horario o coordenadas Google no puede usar la ficha para resultados locales.",
        "Completa las propiedades indicadas con los mismos datos que en Google Business Profile."),
    "local_nap_inconsistent": ("high", "SEO local: nombre, dirección o teléfono no coinciden",
        "Google cruza estos datos entre tu web, tu ficha de Google y los directorios; si no coinciden pierde confianza en el negocio.",
        "Usa exactamente el mismo nombre, dirección y teléfono en toda la web, en el schema y en Google Business Profile."),
    "local_schema_phone_hidden": ("medium", "SEO local: el teléfono del schema no aparece en la web",
        "El teléfono declarado a Google no es el que ven los usuarios (o está desactualizado).",
        "Pon en el schema el mismo teléfono que muestras en la web, o muéstralo en la cabecera o el pie."),
    "local_no_phone": ("medium", "SEO local: la web no muestra ningún teléfono",
        "En un negocio local la llamada es la conversión principal; cada página sin teléfono pierde clientes.",
        "Muestra el teléfono en la cabecera o el pie de todas las páginas (con enlace tel:)."),
    "local_phone_not_clickable": ("medium", "SEO local: teléfono que no se puede pulsar",
        "En el móvil el cliente tiene que copiarlo a mano: muchos no llaman.",
        "Envuelve el número en un enlace: <a href=\"tel:+34985123456\">985 123 456</a>."),
    "local_phone_variant": ("low", "SEO local: teléfono distinto al habitual",
        "Un número que solo aparece en algunas páginas suele ser antiguo o un error, y confunde a clientes y a Google.",
        "Comprueba si ese número sigue siendo válido; si no, sustitúyelo por el teléfono principal."),
    "local_no_contact_page": ("medium", "SEO local: no hay página de contacto",
        "Es una de las páginas más visitadas por clientes locales y Google la usa para confirmar los datos del negocio.",
        "Crea /contacto con teléfono, WhatsApp, dirección, horario, mapa y un formulario sencillo."),
    "local_contact_not_linked": ("medium", "SEO local: la página de contacto no se enlaza desde toda la web",
        "El cliente debe poder llegar al contacto desde cualquier página en un clic.",
        "Añade «Contacto» al menú principal o al pie de todas las plantillas."),
    "local_no_whatsapp": ("info", "SEO local: sin botón de WhatsApp",
        "Muchos clientes de servicios locales prefieren escribir antes que llamar.",
        "Añade un botón flotante o un enlace https://wa.me/34XXXXXXXXX con un mensaje predefinido."),
    "local_no_map": ("info", "SEO local: sin mapa de ubicación",
        "El mapa ayuda al cliente a ver que estás cerca y refuerza la ubicación del negocio.",
        "Incrusta el mapa de tu ficha de Google en la página de contacto (o enlaza a ella)."),
    "local_city_missing": ("medium", "SEO local: la ciudad no aparece en el título o el H1",
        "Las búsquedas locales son «servicio + ciudad»; si la página no nombra la ciudad compite peor por esas búsquedas.",
        "Incluye servicio y ciudad en el título y el H1 (p. ej. «Cerrajero urgente en Gijón 24 h») y, si es posible, en la URL."),
}


@dataclass
class Issue:
    code: str
    severity: str
    title: str
    url: str
    evidence: str
    remediation: str
    why: str

    def as_dict(self) -> dict:
        return asdict(self)


def _fmt_urls(urls: Iterable[str], limit: int = 5) -> str:
    urls = list(urls)
    shown = ", ".join(urls[:limit])
    return shown + (f" (+{len(urls) - limit} más)" if len(urls) > limit else "")


def is_html(page: dict) -> bool:
    return "html" in (page.get("content_type") or "")


def page_noindex(page: dict) -> bool:
    return is_noindex(page.get("meta_robots")) or is_noindex(page.get("x_robots")) or is_noindex(page.get("rendered_robots"))


def compute_indexability(page: dict) -> tuple[bool, str]:
    status = page.get("status_code")
    if page.get("blocked_by_robots"):
        return False, "Bloqueada por robots.txt"
    if status is None:
        return False, "Sin respuesta"
    if 300 <= status < 400:
        return False, f"Redirección {status}"
    if status != 200:
        return False, f"HTTP {status}"
    if not is_html(page):
        return False, "Recurso (no HTML)"  # same state as resources checked with HEAD
    extra = page.get("extra") or {}
    if (extra.get("meta_refresh") or {}).get("target") and (extra["meta_refresh"].get("delay") or 0) <= 5:
        return False, "Redirección (meta refresh)"
    if extra.get("js_redirect"):
        return False, "Redirección (JavaScript)"
    if page_noindex(page):
        return False, "noindex"
    canonical = page.get("canonical")
    if canonical and canonical != page.get("url"):
        return False, "Canonicalizada"
    return True, "Indexable"


def title_px(text: str | None) -> int:
    from .views import title_px as _px  # the pixel table lives with the grid columns
    return _px(text)


_PAGINATED = re.compile(r"/(page|pagina|página)/\d+/?$|[?&](page|paged|pagina|pg)=\d+", re.I)


def is_paginated(page: dict) -> bool:
    """Page 2+ of a series: rel=prev, /page/N or ?page=N."""
    return bool((page.get("extra") or {}).get("prev")) or bool(_PAGINATED.search(page.get("url") or ""))


def url_problems(url: str, max_len: int = 115) -> set[str]:
    """URL hygiene checks (path + query only, scheme/host ignored)."""
    parts = urlsplit(url)
    path = parts.path
    found = set()
    if any(c.isupper() for c in path):
        found.add("url_uppercase")
    if "_" in path:
        found.add("url_underscore")
    if any(ord(c) > 127 for c in unquote(path)) or "%20" in path or " " in path:
        found.add("url_non_ascii")
    if parts.query:
        found.add("url_parameters")
    if len(url) > max_len:
        found.add("url_too_long")
    if "//" in path:
        found.add("url_multiple_slashes")
    return found


def analyze(pages: list[dict], links: list[dict], images: list[dict] | None = None,
            sitemap_urls: Iterable[str] = (), start_url: str | None = None,
            extras: dict | None = None, externals: list[dict] | None = None,
            ignore_nonindexable: bool = False, analytics: list[dict] | None = None,
            near_dups: dict | None = None, spelling_vocab: Iterable[str] = (),
            local: dict | None = None, options: dict | None = None) -> list[Issue]:
    from .options import thresholds
    th = thresholds(options if options is not None else local)
    vocab = set(spelling_vocab)
    images = images or []
    externals = externals or []
    analytics = analytics or []
    near_dups = near_dups or {}
    sitemap = set(sitemap_urls)
    by_url = {p["url"]: p for p in pages}
    for p in pages:
        p["_indexable"], p["_reason"] = compute_indexability(p)

    inbound: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link["kind"] in ("a", "rendered") and link.get("internal") and link["source_url"] != link["target_url"] \
                and link.get("rel") != "js-click":  # Google doesn't click, so these are not inbound links
            inbound[link["target_url"]].add(link["source_url"])

    issues: list[Issue] = []

    def add(code: str, url: str, evidence: str) -> None:
        sev, title, why, remediation = CATALOG[code]
        issues.append(Issue(code, sev, title, url, evidence, remediation, why))

    def sources(url: str) -> str:
        srcs = sorted(inbound.get(url, ()))
        return f"Enlazada desde {len(srcs)} página(s): {_fmt_urls(srcs)}" if srcs else "Sin enlaces internos entrantes"

    # --- availability ------------------------------------------------------
    for p in pages:
        if p.get("blocked_by_robots"):
            if inbound.get(p["url"]):
                add("blocked_but_linked", p["url"], f"Disallow en robots.txt. {sources(p['url'])}")
            continue
        status = p.get("status_code")
        if status is None:
            add("server_error", p["url"], f"Sin respuesta: {p.get('error') or 'error desconocido'}. {sources(p['url'])}")
        elif status >= 500:
            add("server_error", p["url"], f"HTTP {status}. {sources(p['url'])}")
        elif status >= 400:
            add("broken_link", p["url"], f"HTTP {status}. {sources(p['url'])}")

    # --- redirects -----------------------------------------------------------
    def is_redirect(p: dict | None) -> bool:
        return bool(p) and 300 <= (p.get("status_code") or 0) < 400 and bool(p.get("redirect_target"))

    redirect_targets = {p["redirect_target"] for p in pages if is_redirect(p)}
    for p in pages:
        if not is_redirect(p):
            continue
        url = p["url"]
        if inbound.get(url):
            add("link_to_redirect", url, f"HTTP {p['status_code']} → {p['redirect_target']}. {sources(url)}")
        if url in redirect_targets:
            continue  # report chains once, from their first URL
        chain, loop, cur = [url], False, url
        while is_redirect(by_url.get(cur)) and len(chain) <= 20:
            nxt = by_url[cur]["redirect_target"]
            if nxt in chain:
                chain.append(nxt)
                loop = True
                break
            chain.append(nxt)
            cur = nxt
        final = by_url.get(chain[-1])
        final_status = final.get("status_code") if final else "no rastreada"
        path = " → ".join(chain)
        if loop:
            add("redirect_loop", url, f"{path} (vuelve a una URL ya visitada)")
        elif len(chain) - 1 >= 2:
            add("redirect_chain", url, f"{path} ({len(chain) - 1} saltos; destino final: {final_status})")

    # --- on-page metadata (HTML 200 pages) ------------------------------------
    html_ok = [p for p in pages if p.get("status_code") == 200 and is_html(p) and not p.get("blocked_by_robots")]
    on_page = [p for p in html_ok if p["_indexable"]] if ignore_nonindexable else html_ok
    for p in on_page:
        url = p["url"]
        extra = p.get("extra") or {}
        if extra:
            if not extra.get("viewport"):
                add("missing_viewport", url, "No hay <meta name=\"viewport\">")
            if th["html_validation"] and (extra.get("validation") or {}).get("title_count", 0) > 1:
                add("multiple_titles", url, f"{extra['validation']['title_count']} etiquetas <title>")
            if not p.get("lang"):
                add("missing_lang", url, "<html> sin atributo lang")
            elif extra.get("text_lang") and extra["text_lang"] != p["lang"].split("-")[0].strip().lower():
                add("lang_mismatch", url, f"lang={p['lang']} pero el texto principal está en «{extra['text_lang']}»")
            levels = extra.get("headings") or []
            jumps = [f"H{a} → H{b}" for a, b in zip(levels, levels[1:]) if b > a + 1]
            if levels and (levels[0] != 1 or jumps):
                add("heading_non_sequential", url,
                    ("Empieza por H" + str(levels[0]) + ". " if levels[0] != 1 else "") + ", ".join(jumps[:4]))
        title = (p.get("title") or "").strip()
        if not title:
            rendered = (p.get("rendered_title") or "").strip()
            add("missing_title", url, "No hay <title> en el HTML" + (f"; solo aparece tras JavaScript: «{rendered}»" if rendered else ""))
        elif len(title) > th["title_max"]:
            add("title_too_long", url, f"{len(title)} caracteres (máximo {th['title_max']}): «{title}»")
        elif len(title) < th["title_min"]:
            add("title_too_short", url, f"{len(title)} caracteres (mínimo {th['title_min']}): «{title}»")
        if title and title_px(title) > th["title_px_max"]:
            add("title_too_wide", url, f"{title_px(title)} píxeles (máximo {th['title_px_max']}): «{title}»")
        desc = (p.get("meta_description") or "").strip()
        if not desc:
            add("missing_description", url, "No hay <meta name=\"description\"> o está vacía")
        elif len(desc) > th["desc_max"]:
            add("description_too_long", url, f"{len(desc)} caracteres (máximo {th['desc_max']})")
        elif len(desc) < th["desc_min"]:
            add("description_too_short", url, f"{len(desc)} caracteres (mínimo {th['desc_min']}): «{desc}»")
        h1 = [h for h in (p.get("h1") or []) if h]
        if not h1:
            add("missing_h1", url, "Ningún <h1> con texto en el HTML")
        elif len(h1) > 1:
            add("multiple_h1", url, f"{len(h1)} H1: " + " | ".join(f"«{h[:60]}»" for h in h1[:4]))
        if h1 and len(h1[0]) > th["h1_max"]:
            add("h1_too_long", url, f"{len(h1[0])} caracteres (máximo {th['h1_max']}): «{h1[0][:120]}»")
        long_h2 = [h for h in (p.get("h2") or []) if len(h) > th["h2_max"]]
        if long_h2:
            add("h2_too_long", url, f"{len(long_h2)} H2 de más de {th['h2_max']} caracteres: «{long_h2[0][:100]}»")
        if (p.get("size_bytes") or 0) > th["doc_max_mb"] * 1024 * 1024:
            add("large_document", url, f"{round(p['size_bytes'] / 1048576, 1)} MB (máximo {th['doc_max_mb']} MB)")
        forms = extra.get("forms") or {}
        if forms.get("insecure") or forms.get("password_on_http"):
            add("insecure_form", url, ("Formulario con contraseña en página HTTP. " if forms.get("password_on_http") else "")
                + (f"Envía a: {_fmt_urls(forms['insecure'], 2)}" if forms.get("insecure") else ""))
        if (p.get("depth") or 0) > th["high_depth"]:
            add("deep_page", url, f"{p['depth']} clics desde la URL inicial (máximo recomendado {th['high_depth']})")
        if p.get("jsonld_invalid"):
            add("invalid_jsonld", url, f"{p['jsonld_invalid']} bloque(s) <script type=\"application/ld+json\"> no se pueden parsear")
        if (p.get("response_ms") or 0) > 1500:
            add("slow_response", url, f"{p['response_ms']} ms hasta descargar el HTML")

    # --- duplicates (indexable pages only) ------------------------------------
    indexable = [p for p in html_ok if p["_indexable"]]

    paginated = {p["url"] for p in indexable if th["ignore_pagination_duplicates"] and is_paginated(p)}

    def duplicates(key, code: str, label: str) -> None:
        groups: dict[str, list[str]] = defaultdict(list)
        for p in indexable:
            if p["url"] in paginated and code != "duplicate_content":
                continue  # /page/2 repeating the title of /page/1 is expected
            value = key(p)
            if value:
                groups[value].append(p["url"])
        for value, urls in groups.items():
            if len(urls) > 1:
                for u in urls:
                    others = [o for o in urls if o != u]
                    add(code, u, f"{label} «{value[:80]}» compartido con: {_fmt_urls(others)}")

    duplicates(lambda p: (p.get("title") or "").strip().lower(), "duplicate_title", "Título")
    duplicates(lambda p: (p.get("meta_description") or "").strip().lower(), "duplicate_description", "Descripción")
    duplicates(lambda p: p.get("content_hash") if (p.get("word_count") or 0) >= 20 else None,
               "duplicate_content", "Huella de texto")

    for p in indexable:
        words = max(p.get("word_count") or 0, p.get("rendered_word_count") or 0)
        if words < th["low_words"] and not re.search(
                r"contact|aviso-legal|legal|privacidad|privacy|cookies|politica|terminos|condiciones|sobre-nosotros|"
                r"quienes-somos|about|gracias|thank", p["url"], re.I):
            add("thin_content", p["url"], f"{words} palabras visibles (mínimo {th['low_words']})")

    # soft 404: answers 200 but says it does not exist
    for p in html_ok:
        haystack = " ".join([(p.get("title") or ""), " ".join(p.get("h1") or [])]).lower()
        body = (p.get("text_excerpt") or "")[:3000].lower()
        hit = next((ph for ph in th["soft404"] if ph in haystack or (ph in body and (p.get("word_count") or 0) < 250)), None)
        if hit:
            add("soft_404", p["url"], f"Responde 200 y contiene «{hit}»")

    # links per page
    out_int, out_ext = defaultdict(set), defaultdict(set)
    for link in links:
        if link["kind"] in ("a", "rendered") and link.get("rel") != "js-click":
            (out_int if link.get("internal") else out_ext)[link["source_url"]].add(link["target_url"])
    for p in html_ok:
        if len(out_int[p["url"]]) > th["high_internal_outlinks"]:
            add("high_internal_outlinks", p["url"], f"{len(out_int[p['url']])} enlaces internos únicos (máximo {th['high_internal_outlinks']})")
        if len(out_ext[p["url"]]) > th["high_external_outlinks"]:
            add("high_external_outlinks", p["url"], f"{len(out_ext[p['url']])} enlaces externos únicos (máximo {th['high_external_outlinks']})")

    # --- sitemap & orphans -------------------------------------------------------
    for u in sorted(sitemap):
        p = by_url.get(u)
        if not p:
            continue  # not crawled (URL cap)
        if p.get("blocked_by_robots"):
            add("blocked_in_sitemap", u, "Listada en el sitemap y bloqueada por Disallow")
        elif p.get("status_code") != 200:
            extra = f" → {p['redirect_target']}" if p.get("redirect_target") else ""
            add("non200_in_sitemap", u, f"El sitemap la incluye y responde {p.get('status_code') or 'sin respuesta'}{extra}")
        elif page_noindex(p):
            add("noindex_in_sitemap", u, f"Directiva robots: {p.get('meta_robots') or p.get('x_robots') or p.get('rendered_robots')}")
        elif p.get("canonical") and p["canonical"] != u:
            add("canonicalised_in_sitemap", u, f"Canonical → {p['canonical']}")
        if p["_indexable"] and u != start_url and not inbound.get(u):
            add("orphan_candidate", u, "Aparece en el sitemap pero ninguna página rastreada la enlaza")

    # --- canonical & robots signals ----------------------------------------------
    for p in html_ok:
        url = p["url"]
        canonical = p.get("canonical")
        if (p.get("canonical_count") or 0) > 1:
            add("multiple_canonicals", url, f"{p['canonical_count']} URLs canonical distintas en el HTML")
        if canonical and canonical != url:
            if page_noindex(p):
                add("canonical_noindex_conflict", url, f"noindex + canonical → {canonical}")
            if not same_site(canonical, url):
                add("canonical_cross_domain", url, f"Canonical → {canonical}")
            target = by_url.get(canonical)
            if target:
                if target.get("blocked_by_robots"):
                    add("canonical_blocked", url, f"Canonical → {canonical} (bloqueada por robots.txt)")
                elif target.get("status_code") != 200:
                    add("canonical_to_non200", url, f"Canonical → {canonical} (responde {target.get('status_code') or 'sin respuesta'})")
                elif page_noindex(target):
                    add("canonical_to_noindex", url, f"Canonical → {canonical}, que tiene noindex")
                elif target.get("canonical") and target["canonical"] != canonical:
                    add("canonical_chain", url, f"{url} → {canonical} → {target['canonical']}")
        elif not canonical and p["_indexable"]:
            add("missing_canonical", url, "No hay <link rel=\"canonical\">")

        meta_ni, header_ni = is_noindex(p.get("meta_robots")), is_noindex(p.get("x_robots"))
        if p.get("meta_robots") and p.get("x_robots") and meta_ni != header_ni:
            add("robots_header_conflict", url, f"meta robots: «{p['meta_robots']}» / X-Robots-Tag: «{p['x_robots']}»")

        if p.get("rendered"):
            if is_noindex(p.get("meta_robots")) != is_noindex(p.get("rendered_robots")):
                add("rendered_robots_conflict", url,
                    f"HTML: «{p.get('meta_robots') or '(ninguna)'}» / renderizado: «{p.get('rendered_robots') or '(ninguna)'}»")
            rc = p.get("rendered_canonical")
            if rc and rc != canonical:
                add("rendered_canonical_conflict", url, f"HTML: {canonical or '(ninguno)'} / renderizado: {rc}")
            rt = (p.get("rendered_title") or "").strip()
            if rt and (p.get("title") or "").strip() and rt != (p.get("title") or "").strip():
                add("rendered_title_changed", url, f"HTML: «{p.get('title')}» / renderizado: «{rt}»")
            raw_w, rend_w = p.get("word_count") or 0, p.get("rendered_word_count") or 0
            if rend_w >= 2 * raw_w + 50:
                add("js_dependent_content", url, f"{raw_w} palabras en HTML frente a {rend_w} tras renderizar")

    # --- links & images ------------------------------------------------------------
    nofollow_by_source: dict[str, list[str]] = defaultdict(list)
    for link in links:
        if link["kind"] in ("a", "rendered") and link.get("internal") and link.get("nofollow"):
            nofollow_by_source[link["source_url"]].append(link["target_url"])
    for src, targets in sorted(nofollow_by_source.items()):
        add("nofollow_internal", src, f"{len(targets)} enlace(s) internos nofollow hacia: {_fmt_urls(sorted(set(targets)))}")

    js_click: dict[str, list[str]] = defaultdict(list)
    for link in links:
        if link.get("rel") == "js-click" and link.get("internal"):
            js_click[link["source_url"]].append(f"«{(link.get('anchor') or '')[:40]}» → {link['target_url']}")
    for src, items in sorted(js_click.items()):
        add("js_click_navigation", src, f"{len(items)} destino(s) solo accesibles con clic: {_fmt_urls(items, 3)}")

    missing_alt: dict[str, list[str]] = defaultdict(list)
    for img in images:
        if img.get("alt") is None:
            missing_alt[img["page_url"]].append(img.get("src") or "(sin src)")
    for page_url, srcs in sorted(missing_alt.items()):
        add("images_missing_alt", page_url, f"{len(srcs)} imagen(es) sin alt: {_fmt_urls(srcs, 3)}")

    # --- url hygiene & security --------------------------------------------------
    for p in html_ok:
        url = p["url"]
        for code in sorted(url_problems(url, th["url_max"])):
            add(code, url, url)
        if url.startswith("http://"):
            add("http_url", url, "Se sirve sin HTTPS")
        else:
            mixed = p.get("mixed") or []
            if mixed:
                add("mixed_content", url, f"{len(mixed)} recurso(s) HTTP: {_fmt_urls(mixed, 3)}")
            headers = p.get("headers") or {}
            if headers and "strict-transport-security" not in headers:
                add("missing_hsts", url, "La respuesta HTTPS no incluye Strict-Transport-Security")

    # --- hreflang --------------------------------------------------------------------
    for p in html_ok:
        alts = p.get("hreflang") or []
        if not alts:
            continue
        url = p["url"]
        if url not in {a["href"] for a in alts}:
            add("hreflang_no_self", url, "Idiomas declarados: " + ", ".join(sorted({a["lang"] for a in alts})))
        for a in alts:
            target = by_url.get(a["href"])
            if a["href"] == url or not target:
                continue
            if target.get("status_code") != 200 or target.get("blocked_by_robots"):
                add("hreflang_non200", url, f"hreflang «{a['lang']}» → {a['href']} (responde {target.get('status_code') or 'sin respuesta'})")
            elif url not in {b["href"] for b in (target.get("hreflang") or [])}:
                add("hreflang_no_return", url, f"hreflang «{a['lang']}» → {a['href']}, que no enlaza de vuelta")

    # --- external links ------------------------------------------------------------------
    ext_sources: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if not link.get("internal") and link["kind"] in ("a", "rendered", "redirect"):
            ext_sources[link["target_url"]].add(link["source_url"])
    res_sources: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link["kind"] == "resource":
            res_sources[link["target_url"]].add(link["source_url"])
    for e in externals:
        status = e.get("status_code")
        if e.get("blocked_by_robots"):
            continue
        is_res = e.get("kind") == "resource"
        if status is None or status >= 400:
            srcs = sorted((res_sources if is_res else ext_sources).get(e["url"], ()))
            label = f"HTTP {status}" if status else f"Sin respuesta ({e.get('error') or 'error'})"
            add("broken_resource" if is_res else "broken_external", e["url"],
                f"{label}. {'Usado' if is_res else 'Enlazado'} en {len(srcs)} página(s): {_fmt_urls(srcs)}")
        elif is_res and (e.get("size_bytes") or 0) > th["image_max_kb"] * 1024 and (e.get("content_type") or "").startswith("image/"):
            srcs = sorted(res_sources.get(e["url"], ()))
            add("large_image", e["url"], f"{e['size_bytes'] // 1024} KB (máximo {th['image_max_kb']} KB). "
                                         f"Usada en {len(srcs)} página(s): {_fmt_urls(srcs, 3)}")
        elif is_res and (e.get("size_bytes") or 0) > th["resource_max_mb"] * 1048576:
            srcs = sorted(res_sources.get(e["url"], ()))
            add("large_resource", e["url"], f"{round(e['size_bytes'] / 1048576, 1)} MB (máximo {th['resource_max_mb']} MB). "
                                            f"Usado en {len(srcs)} página(s): {_fmt_urls(srcs, 3)}")

    weak: dict[str, list[str]] = defaultdict(list)
    for link in links:
        if link["kind"] in ("a", "rendered") and link.get("internal") and link.get("rel") != "js-click" and \
                (link.get("anchor") or "").strip().lower().strip(".:»›→ ") in th["anchors"] and \
                not re.search(r"cookie|privacidad|privacy|legal|politica|terminos|condiciones", link["target_url"], re.I):
            weak[link["source_url"]].append(f"«{link['anchor']}» → {link['target_url']}")
    for src, items in sorted(weak.items()):
        add("non_descriptive_anchor", src, f"{len(items)} enlace(s): {_fmt_urls(items, 3)}")

    home = by_url.get(start_url or "")
    if home and home in html_ok:
        sd = home.get("structured") or {}
        if not sd.get("jsonld_count") and not sd.get("microdata"):
            add("no_structured_data", home["url"], "Sin JSON-LD ni microdatos en la página de inicio")

    psi_pages = dict((extras or {}).get("pagespeed_pages") or {})
    psi = (extras or {}).get("pagespeed") or {}
    if psi and psi.get("url") not in psi_pages:
        psi_pages[psi.get("url") or start_url or ""] = psi
    for u, r in psi_pages.items():
        if isinstance(r.get("performance"), int) and r["performance"] < 50:
            lcp = r.get("lcp_ms")
            add("low_performance", u, f"Puntuación móvil {r['performance']}/100" + (f"; LCP {lcp / 1000:.1f} s" if lcp else ""))

    # --- transport, redirects by meta/JS ----------------------------------------------------------
    for p in pages:
        err = p.get("error") or ""
        if "CERTIFICATE_VERIFY_FAILED" in err or "SSL" in err.upper() and "certificate" in err.lower():
            add("ssl_error", p["url"], err[:200])
        extra = p.get("extra") or {}
        refresh = extra.get("meta_refresh") or {}
        if refresh.get("target"):
            add("meta_refresh_redirect", p["url"], f"<meta http-equiv=\"refresh\"> con {refresh.get('delay', 0)} s → {refresh['target']}")
        if extra.get("js_redirect"):
            add("js_redirect", p["url"], f"Tras ejecutar JavaScript la página cambia a {extra['js_redirect']}")

    # --- page-level checks stored in `extra` -----------------------------------------------------------
    from .checks import A11Y_LABELS, hreflang_code_error
    for p in on_page:
        url = p["url"]
        extra = p.get("extra") or {}
        bad = [f"«{a['lang']}»: {err}" for a in (p.get("hreflang") or []) if (err := hreflang_code_error(a["lang"]))]
        if bad:
            add("hreflang_invalid_code", url, "; ".join(bad[:4]))
        probs = extra.get("schema_problems") or []
        errors = [x["message"] for x in probs if x["level"] == "error"]
        warnings = [x["message"] for x in probs if x["level"] == "warning"]
        if errors:
            add("schema_error", url, "; ".join(errors[:4]))
        if warnings:
            add("schema_warning", url, "; ".join(warnings[:4]))
        amp = extra.get("amp") or {}
        if amp.get("is_amp") and amp.get("errors"):
            add("amp_error", url, "; ".join(amp["errors"][:4]))
        a11y = extra.get("a11y") or {}
        found = []
        for key, (label, crit) in A11Y_LABELS.items():
            value = a11y.get(key)
            n = len(value) if isinstance(value, list) else (value or 0)
            if n and key != "images_no_alt":  # missing alt already has its own issue
                found.append(f"{label}: {n} ({crit})")
        if found:
            add("accessibility", url, "; ".join(found))
        from .checks import effective_spelling
        sp = effective_spelling(extra.get("spelling"), vocab) or {}
        if (sp.get("count") or 0) >= 3:
            add("spelling_errors", url, f"{sp['count']} palabra(s) desconocidas: {', '.join(sp.get('words', [])[:8])}")
        nd = near_dups.get(url)
        if nd and nd[1] < 1.0:
            add("near_duplicate", url, f"{round(nd[1] * 100)} % similar a {nd[0]}" + (f" (y {nd[2] - 1} más)" if nd[2] > 1 else ""))

    legacy = defaultdict(list)
    for e in externals:
        if e.get("kind") == "resource" and e.get("status_code") == 200 and \
                (e.get("content_type") or "") in ("image/jpeg", "image/png", "image/gif"):
            name = e["url"].rsplit("/", 1)[-1].lower()
            if (e.get("size_bytes") or 0) < 40 * 1024 or re.search(r"favicon|icon|logo|-og\b|og-|og\.|share|social", name):
                continue  # small images, icons and social-sharing images don't need WebP
            legacy[e["url"]] = sorted(res_sources.get(e["url"], ()))
    for u, srcs in sorted(legacy.items()):
        add("legacy_image_format", u, f"Formato {u.rsplit('.', 1)[-1][:5]}. Usada en {len(srcs)} página(s): {_fmt_urls(srcs, 3)}")

    # --- analytics / Search Console data ---------------------------------------------------------------
    traffic: dict[str, float] = defaultdict(float)
    for a in analytics:
        traffic[a["url"]] += (a.get("clicks") or 0) + (a.get("sessions") or 0)
    for u, t in sorted(traffic.items(), key=lambda kv: -kv[1]):
        if t <= 0 or u == start_url or inbound.get(u):
            continue
        p = by_url.get(u)
        state = "no se encontró en el rastreo" if not p else "ninguna página rastreada la enlaza"
        add("orphan_with_traffic", u, f"{int(t)} clics/sesiones según los datos importados; {state}")

    # --- local SEO (business data, phone, contact, service + city) --------------------------------------
    if local is not None:
        from .localseo import audit
        for code, url, evidence in audit(pages, links, start_url, local)["issues"]:
            add(code, url, evidence)

    issues.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.code, i.url))
    return issues


def group_issues(issues: Iterable[dict | Issue]) -> list[dict]:
    """Group per issue type, ordered by severity then number of affected URLs (priority)."""
    groups: dict[str, dict] = {}
    for item in issues:
        i = item.as_dict() if isinstance(item, Issue) else item
        g = groups.setdefault(i["code"], {
            "code": i["code"], "severity": i["severity"], "severity_label": SEVERITY_LABEL[i["severity"]],
            "title": i["title"], "why": i["why"], "remediation": i["remediation"], "items": [],
        })
        g["items"].append(i)
    from .patterns import cluster
    for g in groups.values():  # «3.500 de estos 404 están en /blog/*/page/{página}»
        g["clusters"] = cluster([i["url"] for i in g["items"]]) if len(g["items"]) >= 4 else []
    return sorted(groups.values(), key=lambda g: (SEVERITY_ORDER[g["severity"]], -len(g["items"]), g["code"]))


def summarize(pages: list[dict], issues: list[Issue]) -> dict:
    by_sev = {s: 0 for s in SEVERITY_ORDER}
    for i in issues:
        by_sev[i.severity] += 1
    statuses: dict[str, int] = defaultdict(int)
    for p in pages:
        s = p.get("status_code")
        key = "bloqueadas" if p.get("blocked_by_robots") else ("sin respuesta" if s is None else f"{s // 100}xx")
        statuses[key] += 1
    return {
        "pages": len(pages),
        "indexable": sum(1 for p in pages if compute_indexability(p)[0]),
        "issues": len(issues),
        "issue_types": len({i.code for i in issues}),
        "by_severity": by_sev,
        "statuses": dict(sorted(statuses.items())),
    }


# «Errores» shows only what is ALWAYS a fault, whatever the site (principle: no opinions, no arbitrary thresholds).
# Everything else (lengths, duplicates, «posible…», recommendations) is a warning that depends on the case:
# still detected and exported, but out of the default «Errores» view.
ALWAYS_BAD = frozenset({
    "server_error", "ssl_error", "broken_link", "broken_external", "broken_resource", "redirect_loop", "redirect_chain",
    "missing_title", "multiple_titles", "missing_h1",
    "noindex_in_sitemap", "non200_in_sitemap", "blocked_in_sitemap",
    "multiple_canonicals", "canonical_noindex_conflict", "canonical_to_non200", "canonical_to_noindex", "canonical_blocked",
    "hreflang_non200", "hreflang_invalid_code", "invalid_jsonld", "schema_error", "amp_error", "lang_mismatch",
    "http_url", "mixed_content", "insecure_form",
})