# LetsCrawlit · referencia técnica

Detalles para quien quiere ejecutarlo desde el código, conocer cada opción o revisar cómo funciona por dentro. Para aprender a usarlo, las guías estarán en [letscrawlit.com](https://letscrawlit.com/) (próximamente).

[Volver al README](../README.md)

## Puesta en marcha desde el código (un comando)

Requisito: Python 3.12 instalado (`py -0p` debe mostrar 3.12).

```bash
py -3.12 run.py
```

En macOS/Linux: `python3.12 run.py`.

La primera vez, `run.py`:

1. crea `.venv/` e instala `requirements.txt` (vuelve a instalar solo si cambia el archivo);
2. copia `.env.example` → `.env` si no existe;
3. busca un navegador para el renderizado (Chromium de Playwright, Edge o Chrome). Si no hay ninguno, ejecuta `playwright install chromium`. Si eso falla, la app funciona igual sin renderizado JS;
4. arranca el servidor en <http://127.0.0.1:8765/> y abre el navegador.

Para probar sin tocar ningún sitio real hay un sitio de demostración con errores a propósito:

```bash
.venv\Scripts\python extras\demo\demo_site.py
```

y rastrea `http://127.0.0.1:8800/` desde la app.

### Tests

```bash
py -3.12 run.py test
```

(equivale a `.venv\Scripts\python -m pytest`). El test de renderizado se marca como *skipped* si no hay navegador.

---

## Uso

1. Elige el modo:
   - **Sitio web**: una URL inicial; sigue los enlaces internos y, al final, las URLs del sitemap.
   - **Lista**: pega URLs (una por línea). Si una línea es un sitemap `.xml`, se expanden sus URLs. Solo se piden esas URLs (y sus redirecciones); los enlaces se registran pero no se siguen.
2. Pulsa **Configuración** (ventana con menú lateral):
   - **Rastreador › Rastreo**: comprobar recursos internos (imágenes, CSS, JS, fuentes) y enlaces externos con HEAD; rastrear canonicals, hreflang y sitemaps.
   - **Rastreador › Límites**: tope de URLs, profundidad máxima de clics, máximo de parámetros en la URL.
   - **Rastreador › Renderizado**: JavaScript sí/no y tiempo máximo por página.
   - **Rastreador › Avanzado**: ignorar URLs no indexables en los problemas de contenido, tiempo de espera, reintentos 5xx.
   - **robots.txt**: un robots.txt propio que se aplica *además* del real (solo puede bloquear más) y un **probador** que dice, para cada URL, si queda bloqueada y qué regla decide (sin hacer peticiones).
   - **Reescritura de URL** (quitar parámetros concretos o todos), **Incluir** / **Excluir** (expresiones regulares), **Velocidad** (pausa).
   - **User-Agent**: predeterminado, Googlebot smartphone/ordenador, Bingbot, Chrome o personalizado; también decide qué grupo de robots.txt se aplica.
   - **Header HTTP**: cabeceras extra (p. ej. `Accept-Language`); cookies y credenciales no, esas van en `.env`.
   - **Búsqueda personalizada** (contiene / no contiene un texto en el HTML) y **Extracción personalizada** (selector CSS o regex), hasta 5 de cada.
   - **Guardar** la deja como configuración predeterminada (`data/config.json`), **Usar solo en el próximo rastreo** no la guarda y **Restablecer** vuelve a la de fábrica.
3. **Marca la casilla de permiso** (obligatoria; se valida también en el servidor). El texto y la hora quedan guardados en la auditoría y en el manifiesto exportado.
4. La auditoría se abre en una **mesa de trabajo** que se rellena en directo:
   - **Pestañas** arriba: Internos, Externo, Seguridad, Códigos de respuesta, URL, Títulos de página, Meta description, Meta keywords, H1, H2, Contenido, Imágenes, Canonicals, Paginación, Directivas, Hreflang, JavaScript, Enlaces, Datos estructurados, Sitemaps, Móvil, Validación, Búsqueda personalizada, Extracción personalizada y Errores. Con **Pestañas** eliges cuáles ver (se recuerda en tu navegador).
   - **Filtro** por pestaña (p. ej. Títulos → Ausente / Duplicado / >60 caracteres / >561 píxeles / Igual que el H1), búsqueda, columnas ordenables con clic y **Exportar › Esta vista en CSV** (exactamente lo que ves).
   - **Panel inferior** al hacer clic en una fila: Detalles, Inlinks, Outlinks, Imágenes, Cabeceras HTTP, Hreflang, Texto e Incidencias de esa URL.
   - **Panel lateral** con subpestañas: *Resumen* (sin nota de salud: solo datos; árbol Resumen → Datos del rastreo con URLs y % del total por cada pestaña y filtro; clic para abrirlo), *Errores* (nombre, tipo, prioridad, URLs y %), *Estructura del sitio* (árbol de carpetas y profundidad de clics) y *Tiempos de respuesta* (distribución por segundos).
   - **Incidencias**: agrupadas por gravedad con URLs afectadas, evidencia, por qué importa y cómo solucionarlo.
5. Exporta desde el menú *Exportar*: ZIP completo, informe HTML o cada CSV por separado.

Las longitudes en píxeles de títulos y descripciones son una aproximación (Arial 20 px / 14 px), igual que hacen otras herramientas.

### Funciones avanzadas

| Área | Qué hay |
|---|---|
| Redirecciones | 301/302/307/308 salto a salto, cadenas y bucles, **meta refresh** y **redirecciones por JavaScript** (el navegador no sigue el destino por su cuenta: lo decide el rastreador con robots.txt) |
| Enlaces | Inlinks/outlinks con ancla, **código de estado del destino** y atributos `nofollow`, `sponsored`, `ugc`; ruta de rastreo (camino de clics más corto) por URL |
| Bloqueos | Páginas, imágenes, CSS y JS bloqueados por robots.txt; **cargar el robots.txt real**, editarlo y probarlo; descargar el robots.txt usado |
| Seguridad | Contenido mixto, HSTS, CSP, X-Frame-Options, X-Content-Type-Options, **errores de certificado** y opción de no verificar SSL en desarrollo |
| Contenido | Duplicados exactos (hash) y **casi duplicados** con % de similitud (MinHash), **clústeres semánticos**, **ortografía** con diccionario personalizado, lorem ipsum |
| Imágenes | >100 KB, sin alt, alt vacío, **formato antiguo vs. WebP/AVIF**, imágenes rotas |
| Internacional | Hreflang: faltantes, retorno, autorreferencia, x-default, no 200 y **códigos ISO no válidos** |
| Datos estructurados | JSON-LD, microdatos y **RDFa**; **validación de propiedades obligatorias/recomendadas** para los tipos con resultados enriquecidos (LocalBusiness, Product, Offer, Review, Article, FAQ, Event, Recipe, Video…) |
| Accesibilidad | Comprobaciones automáticas WCAG: alt, etiquetas de formulario, nombres accesibles, enlaces sin texto, encabezados vacíos, IDs duplicados, iframes sin title, tabindex, autoplay, idioma, zoom |
| AMP | Detección y validación básica (v0.js, boilerplate, canonical, JS propio, `<img>`), rastreo de `rel=amphtml` |
| Renderizado | Chromium/Edge/Chrome sin interfaz, **capturas de pantalla** por URL, **gráfico de cascada** (cada recurso con su milisegundo de inicio y fin) |
| Modos | Sitio web, Lista (pegar URLs, sitemap XML o **subir CSV/TXT**), **Comparar** dos auditorías (nuevas, eliminadas, cambios por URL, evolución de incidencias) y **vista previa SERP** editable por dispositivo |
| Red | User-Agents (Googlebot, Bingbot, Chrome móvil/ordenador, **ChatGPT-User, GPTBot, ClaudeBot, PerplexityBot**, personalizado), cabeceras, cookies (`.env`), **login por formulario** (`.env`), **DNS manual** para migraciones, proxy HTTP, **1-5 hilos** con ritmo global limitado |
| Extracción | CSS, **XPath** y regex; búsqueda personalizada contiene/no contiene |
| Integraciones | **Search Console y Analytics por CSV** (huérfanas con tráfico, CTR bajo, posiciones 4-20), **PageSpeed masivo** (N URLs más enlazadas), **IA** (Claude, OpenAI, Gemini o un modelo local con Ollama, configurable desde la propia app) y **Copyscape** opcional |
| Prioridades | URLs sin problemas y lista «qué arreglar primero» (ordenada por gravedad, nº de URLs y si afecta a la home o a las URLs más enlazadas). Sin notas ni puntuaciones calculadas |
| Enlazado | **Sugerencias de enlazado interno**: páginas del mismo tema sin enlace, con el ancla y la frase exacta donde ponerlo |
| Plagio | Comparar un texto sospechoso con todas tus páginas (sin visitar la otra web) y búsqueda en toda la web con Copyscape |
| Histórico | **Tendencias** por sitio: URLs, indexables, incidencias, 4xx, redirecciones, enlaces por página y tiempo de respuesta, auditoría a auditoría |
| Gestión | **Pausar y reanudar** (también tras un corte de luz: punto de control cada 20 URLs), **archivo .seocrawl** para mover un rastreo a otro equipo, **sitemaps XML** (y de imágenes), **exportaciones masivas** (inlinks a 4xx/5xx/3xx/noindex, externos rotos, imágenes sin alt o pesadas, recursos rotos, cadenas, canonicals, contenido mixto…) |
| Automatización | Línea de comandos y tareas programadas (ver abajo) |

### Línea de comandos y programación

```bash
py -3.12 run.py crawl https://misitio.es/ --i-own-this-site --max-urls 500 --out D:\Auditorias
```

```bash
py -3.12 run.py schedule https://misitio.es/ --i-own-this-site --weekly MON --daily 03:00 --install
```

```bash
py -3.12 run.py resume 12
```

`crawl` usa la configuración predeterminada guardada y deja el ZIP en `--out`. `schedule` muestra el comando de
`schtasks` del Programador de tareas de Windows y lo crea solo con `--install` (en macOS/Linux muestra la línea de cron).

### Seguridad

LetsCrawlit está pensado para usarse en tu propio equipo:

- Escucha solo en `127.0.0.1` y rechaza peticiones que lleguen con otro nombre de host (protege del *DNS rebinding*:
  una web no puede usar su propio dominio para hablar con tu LetsCrawlit). Si lo abres en tu red, añade el nombre a
  `LETSCRAWL_ALLOWED_HOSTS`.
- Desde este equipo no pide nada. Desde otro dispositivo de la red solo funciona si pones `LETSCRAWL_PASSWORD` en `.env`
  (y entonces pide esa contraseña; tras 8 intentos fallidos bloquea 15 minutos). Si cambias `LETSCRAWL_HOST` a una
  dirección de red sin contraseña, la app no arranca.
- Bloquea los formularios enviados desde otras webs y envía cabeceras de seguridad (CSP, `X-Frame-Options`,
  `Referrer-Policy: same-origin`…). La CSP no permite JavaScript incrustado ni `eval`: todo el código está en
  `app/static/` y los botones se declaran con atributos `data-click`/`data-change` (ver `app/static/actions.js`). Las redirecciones solo van a rutas de la propia app.
- Todo lo que llega de los sitios rastreados se muestra escapado; el extractor visual enseña la página sin scripts y
  en un marco aislado.
- Las descargas tienen tope de tamaño (páginas, robots.txt, sitemaps y archivos `.seocrawl`), los gzip se
  descomprimen con límite y los XML con DOCTYPE o entidades se rechazan.
- Los enlaces externos que apuntan a tu red local (router, NAS…) no se comprueban, salvo que audites un sitio local.
- Las sesiones de login guardadas y el permiso de Google se cifran con tu usuario de Windows (DPAPI); en macOS/Linux
  quedan legibles solo por ti. Si copias `data/` a otro ordenador, tendrás que volver a grabar las sesiones y a
  conectar Google.
- Las páginas renderizadas se abren en Chromium con su *sandbox* activado (Playwright lo desactiva por defecto).
- Las claves van solo en `.env` (excluido de git), no se vuelven a mostrar y la IA solo se configura desde el propio
  equipo. Los CSV neutralizan fórmulas de Excel.

Si encuentras un fallo de seguridad, avísame en privado antes de publicarlo.

### Qué no incluye (y por qué)

- **Panel en la nube / colaboración en tiempo real y Looker Studio/Sheets nativos**: la especificación excluye un panel alojado; todo es local. Para compartir: informe HTML autocontenido, ZIP con CSV o el archivo `.seocrawl`. La resistencia a cortes la dan los puntos de control.
- **Métricas de backlinks (Ahrefs, Moz, Majestic)**: excluidas a propósito (datos de enlaces a escala web).
- **Revisión gramatical**: solo ortografía; la gramática necesitaría un servicio externo. El diccionario español incluido es limitado: se compensa con reglas de flexión y con el vocabulario repetido del propio sitio, pero puede haber falsos positivos (añádelos al diccionario personalizado).
- **Accesibilidad completa** (contraste de color, orden de foco real…) y **validación de Schema idéntica a la de Google**: se cubre el subconjunto que puede comprobarse de forma estática.
- **Proxies SOCKS**: solo HTTP/HTTPS.
- **Millones de URLs**: SQLite aguanta, pero la mesa de trabajo carga la auditoría en memoria; el tope configurable es 100.000 y lo cómodo son decenas de miles.

Estados de la interfaz: vacío (sin auditorías / sin resultados), cargando (progreso con sondeo HTMX; sin JavaScript se recarga sola), éxito (resumen) y errores recuperables (sitio caído, robots.txt inaccesible, app cerrada a mitad → botón *Reintentar con la misma configuración*).

### Qué respeta el rastreador

| Regla | Comportamiento |
|---|---|
| Permiso | No se crea ningún rastreo sin la confirmación explícita. |
| robots.txt | Se lee por origen. 200 → se aplican Disallow/Allow; 4xx → sin restricciones; 5xx o error de red → **no se rastrea nada** (criterio de Google). Las URLs bloqueadas se listan pero nunca se piden. |
| Ritmo | 1-5 hilos, pero con una pausa mínima global entre el inicio de dos peticiones (mínimo `LETSCRAWL_MIN_DELAY`) o el `Crawl-delay` de robots.txt si es mayor. El renderizado también respeta la pausa y el navegador no navega por su cuenta. |
| Alcance | Solo el mismo dominio (www y sin www cuentan como el mismo). Los externos y recursos solo se comprueban con una petición HEAD si lo activas, también respetando su robots.txt. |
| nofollow | No se siguen enlaces `rel="nofollow"` ni los de páginas con `meta robots`/`X-Robots-Tag` nofollow. |
| Redirecciones | Se siguen salto a salto (sin seguimiento automático) para registrar cadenas y bucles. |
| Canonical | Se registra y la URL canónica se rastrea para verificar que responde 200 e indexable. |
| Tope | Por defecto no hay tope de URLs. Si marcas «Limitar rastreo total», nunca se descubren más URLs que ese número (y nunca más que `LETSCRAWL_MAX_URLS_LIMIT`, si lo pones en `.env`). |

### Qué recoge

Estado HTTP, tiempo de respuesta, tamaño, tipo, destino de redirección, título, meta descripción, H1/H2,
canonical (y si hay varios), meta robots, X-Robots-Tag, idioma, enlaces (ancla, nofollow, interno/externo),
imágenes (alt ausente vs. vacío), JSON-LD (tipos y bloques inválidos), microdatos, número de palabras,
huella del texto y, con renderizado, título/robots/canonical/enlaces/texto tras ejecutar JavaScript.
Sitemaps declarados en robots.txt (o `/sitemap.xml`), incluidos índices y `.gz`.

### Qué detecta

Errores 4xx/5xx y URLs inaccesibles (con páginas de origen), bucles y cadenas de redirección, enlaces a redirecciones,
título/descripción/H1 ausentes, largos, cortos o duplicados, contenido duplicado exacto, contenido escaso,
candidatas a huérfanas (en el sitemap sin enlaces internos), conflictos de indexabilidad (noindex/no-200/canonicalizada/bloqueada en el sitemap,
canonical + noindex, canonical a 3xx/4xx/noindex/bloqueada, cadenas de canonical, varios canonical, canonical a otro dominio,
meta robots vs. X-Robots-Tag, robots/canonical/título cambiados por JavaScript, contenido que depende de JS),
URLs enlazadas pero bloqueadas, nofollow interno, imágenes sin alt, JSON-LD inválido, home sin datos estructurados,
respuesta lenta, URLs con mayúsculas/guiones bajos/tildes/parámetros/barras dobles/longitud excesiva,
páginas por HTTP, contenido mixto, falta de HSTS, hreflang sin retorno/sin autorreferencia/hacia URLs no 200,
enlaces externos rotos (si se activa la comprobación) y, opcionalmente, rendimiento móvil bajo con PageSpeed Insights.

### SEO local

**Solo se hace si lo pides**: marca «Hacer la auditoría de SEO local» en Configuración › SEO local, o
rellena el nombre, la ciudad o el teléfono del negocio. Si no, no aparecen la pestaña «SEO local» ni sus problemas
(en una web que no es un negocio local solo serían ruido). Los datos son opcionales: si no se indican, se deducen del
schema y del teléfono más repetido. Comprueba:

- ficha LocalBusiness (o subtipo: Locksmith, GeneralContractor…) en la portada y sus datos (dirección, horario, geo, areaServed…);
- que nombre, dirección y teléfono coincidan en todo el schema y con lo configurado, y que el teléfono del schema se vea en la web;
- teléfono visible en cada página y clicable (`tel:`), teléfonos «raros» que solo salen en pocas páginas (posibles números antiguos);
- WhatsApp, mapa (incrustado o enlace a Google Maps), página de contacto y que se enlace desde todas las páginas indexables;
- que cada página de servicio nombre la ciudad en el título y el H1 (y en la URL), sin distinguir tildes ni mayúsculas.
  Se excluyen la portada, legales, contacto, blog, etiquetas… y lo que añadas en «Páginas que no son de servicio».

Los rastreos anteriores a esta función no tienen estos datos: vuelve a rastrear.

### Webs con JavaScript (React, Angular, Vue…)

En Configuración › Rastreador › Renderizado:

- **Bloquear scripts de terceros** (activado por defecto): al renderizar no se cargan analítica, publicidad, píxeles
  sociales ni chats (Google Analytics/Tag Manager, Meta, LinkedIn, TikTok, Hotjar, Clarity, Adobe, HubSpot…; la lista
  completa se ve en la propia configuración). El rastreo va más rápido y no ensucia las estadísticas del sitio.
  Puedes añadir dominios propios (`widget.ejemplo.com` o `dominio.com/ruta`). La pestaña JavaScript muestra cuántas
  peticiones se bloquearon en cada página.
- **Enlaces que solo funcionan con clic** (desactivado por defecto): hace clic en botones y elementos con `onclick`,
  `role="link"`, `data-href`, `routerLink`… y anota a dónde intentan ir (navegación, `history.pushState` o
  `window.open`). Es seguro: no pulsa botones de formularios ni textos como «comprar», «eliminar», «enviar», «aceptar»
  o «cerrar sesión», cancela la navegación y bloquea toda petición que no sea de lectura (POST, PUT, DELETE…) mientras
  hace clic. Las URLs encontradas se rastrean, pero **no cuentan como enlaces** (Google no hace clic) y la página de
  origen se marca con el problema «Navegación que solo funciona con clic».

### Apariencia

Menú **Apariencia**, a la derecha de la barra de menús: modo oscuro (el predeterminado), claro o según el sistema; seis paletas (LetsCrawlit, Océano, Bosque,
Atardecer, Grafito, Cereza) o tres colores a tu gusto (principal, acento y detalle). El resto de tonos se calcula a
partir de ellos y el texto se pone en blanco o negro según el contraste. Se guarda solo en tu navegador.

### Notas

Marca una o varias URLs en la tabla («Seleccionar varias», Ctrl+clic o Mayús+clic) y pulsa **Anotar** (también en el
clic derecho, «Anotar sobre esta URL», y en el panel inferior). La ventana muestra los problemas que comparten esas
URLs, con su gravedad y cuántas los tienen (3/3), y unas frases rápidas: un clic añade la línea a la nota y otro la
quita. El botón **Notas** abre todas las de la auditoría como tarjetas, con buscador; se pueden editar y borrar, y
pasan al informe con «Exportar resumen» › «Incluir mis notas».

### Opciones avanzadas de rastreo

Todas en Configuración y todas opcionales (los valores por defecto sirven para la mayoría de webs):

- **Rastreador › Recursos y enlaces:** activar o no imágenes, CSS, JavaScript, vídeo/audio, iframes internos (se
  rastrean como páginas), paginación rel next/prev, versiones AMP y meta refresh.
- **Rastreador › Comportamiento:** quedarse en la carpeta de la URL inicial, incluir subdominios, seguir enlaces
  nofollow internos y externos, rastrear sitemaps enlazados desde las páginas y sitemaps propios.
- **Límites:** URLs por nivel de profundidad, nivel de carpetas, URLs por subdominio, redirecciones en cadena,
  longitud de URL, enlaces por página, tamaño de página y un tope por patrón (`/blog/ 50`). Velocidad: URLs por segundo.
- **Avanzado:** ignorar la paginación al buscar duplicados, seguir siempre redirecciones/canonicals, respetar
  noindex/canonical/next-prev (ocultan esas URLs de las tablas), HSTS, meta refresh a sí misma, srcset, validación
  HTML, huella de carbono (modelo Sustainable Web Design), tratar como HTML las respuestas sin tipo y cookies.
- **Extracción:** legibilidad (Fernández-Huerta en español, Flesch en otros idiomas), proporción texto/código,
  formularios (y los que envían sin HTTPS), cookies que pone cada página, propiedades de los PDF (pestaña PDF) y
  guardar el HTML original y renderizado (panel inferior › «Ver fuente»; se guarda en `data/html/`).
- **Preferencias:** todos los umbrales de los filtros y problemas (caracteres y píxeles de título y descripción, H1,
  H2, ALT, URL, palabras mínimas, peso de imágenes y recursos, enlaces salientes, profundidad), la lista de anclas no
  descriptivas y las frases que delatan un 404 «suave».

No incluye a propósito: ignorar robots.txt ni conectores de bases de backlinks a escala web (Majestic, Ahrefs, Moz).

### IA y datos de Google

- IA: menú **IA** › «Configurar la IA…» (o Configuración › «Inteligencia artificial (IA)…»). Eliges Claude,
  OpenAI, Gemini, Groq u Ollama, pegas la clave y se activa al momento, sin reiniciar. Se guarda en tu `.env` local
  (`AI_PROVIDER`, `AI_MODEL` y `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` o `GROQ_API_KEY`), que nunca se sube a git;
  la clave no vuelve a mostrarse en pantalla. Solo se puede cambiar desde el propio equipo. También puedes editar
  `.env` a mano como antes.
- Search Console y Analytics 4 sin CSV: crea un cliente OAuth «App de escritorio» en Google Cloud, activa las APIs de
  Search Console, Analytics Data y Analytics Admin, y pon `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET` en `.env`. En
  cada auditoría, «Importar datos» › «Conectar cuenta de Google» (solo lectura) y elige la propiedad. El permiso se
  guarda en `data/google_token.json` y se revoca con «Desconectar Google».

### Extractor visual

Configuración › Extracción personalizada › «Elegir el dato con el ratón»: abre una copia de una página de ejemplo
(sin scripts, en un marco aislado; los enlaces no navegan), haces clic en el precio, el autor o el stock y genera
el selector CSS (texto) o XPath (atributos como `href`, `src`, `content` o `data-price`). Puedes elegir «solo este
elemento» o «todos los parecidos», ves la vista previa de lo que extraerá y lo añade a la primera extracción libre.

### Tendencias

`/trends/<sitio>`: gráfico con fechas de cualquier métrica (errores 4xx/5xx, enlaces rotos, peso total y
medio de imágenes, peso del HTML, páginas con poco contenido, sin título o sin descripción, palabras, tiempos…),
tarjetas de todas las métricas, exportación a CSV (una fila por auditoría, lista para Looker Studio, Sheets o Excel)
y un formulario para crear la tarea programada de Windows que rastrea el sitio cada día, semana o mes.

### Title, meta description y datos estructurados con IA

Menú **IA** › «Escribir title, meta description y datos estructurados». La IA lee el contenido de cada página y propone:

- un title de 50-60 caracteres y una meta description de 140-155, en el idioma de la página;
- datos estructurados JSON-LD del tipo que encaja (Article, Product, Service, SoftwareApplication, FAQPage…), sin
  inventar precios, valoraciones, teléfonos ni direcciones, y teniendo en cuenta los tipos que la página ya declara.

En el panel de IA de una URL salen en una tarjeta con la longitud de cada texto (en verde si está en el rango
recomendado), el bloque `<script type="application/ld+json">` listo para pegar y un botón «Copiar» en cada uno.
En la pestaña «IA» salen como columnas al lado del title y la description actuales, y se exportan con la vista.
Revísalos siempre y valida el JSON-LD en la Prueba de resultados enriquecidos de Google.

### Fichas de cliente

Menú **IA** › «Clientes y su contexto…»: nombre, sector, público objetivo, tono y estilo de escritura, términos a
evitar y contexto del negocio. Al elegir el cliente («Escribir para», en «IA en bloque» y en el panel de IA de cada
URL), la IA lee su ficha antes de escribir, respeta el tono y nunca usa los términos vetados. El último cliente usado
en una web queda elegido para esa web. Se guardan en `data/clients.json` y valen para cualquier auditoría.

### IA en bloque

Aplica una tarea a muchas páginas a la vez (hasta 500): title, meta description y datos estructurados, clasificar la
intención de búsqueda, reescribir la meta description, palabra clave y grupo temático, resumen, traducción o **tu
propia instrucción**. Se abre desde el menú **IA**, el botón «IA en bloque» de la tabla o la cabecera de la pestaña «IA».

- **¿En qué URLs?** Solo las que hayas seleccionado en la tabla, o todas las de la vista actual (pestaña, filtro y
  búsqueda).
- **¿Para qué cliente?** La ficha de cliente que la IA debe seguir, o ninguna.

Los resultados se guardan como columnas en la pestaña «IA» (solo se muestran las que tienen datos, con filtros por
intención; un clic en una fila abre la propuesta completa) y salen en las exportaciones. Va de tres en tres páginas
tras la primera respuesta correcta (Ollama, de una en una). Si el proveedor pide ir más despacio (límite de los planes
gratuitos, como el de Groq), baja a una página cada vez, espera y sigue solo. Si fallan 3 páginas seguidas por otro
motivo (clave, saldo…), se detiene.

Las tareas se eligen con tarjetas que explican qué hace cada una y muestran un ejemplo de respuesta, aunque la IA aún
no esté activada. Tus propias instrucciones se pueden **guardar con un nombre** y quedan disponibles en todas las
auditorías (`data/ai_prompts.json`). Todas las instrucciones propias escriben en la misma columna «Mi instrucción»:
si ejecutas dos distintas sobre las mismas páginas, la segunda sustituye a la primera.

### Para principiantes y para el día a día

- **Idioma**: banderas 🇪🇸 / 🇬🇧 arriba a la derecha; toda la interfaz, los problemas y los informes cambian de idioma
  (los datos del sitio rastreado no se traducen; los CSV y las respuestas de la IA siguen en español).
- **Modo principiante** (Vista › Modo principiante): solo las pestañas esenciales.
- **Auditoría en 1 clic**: completa, solo enlaces rotos, solo peso de imágenes o solo el sitemap.
- **[?]** junto a cada problema y filtro: por qué importa y cómo se arregla.
- **Resumen para jefes**: PDF de 2 páginas con cuántas páginas están bien en los puntos básicos y las 3 acciones prioritarias.
- **Ctrl+C** sobre una fila de la tabla la copia con sus columnas, lista para pegar en Excel.

### Diccionario compartido

Las palabras correctas que la ortografía marca (marcas, jerga del cliente, barrios) se añaden con «+» en el panel
inferior › Calidad o en Configuración › Contenido. Con `LETSCRAWL_SHARED_DICTIONARY` en `.env` apuntando a un `.txt`
dentro de Dropbox, Google Drive u OneDrive, todo el equipo usa y alimenta el mismo diccionario. Se aplica también a
los rastreos ya hechos, sin volver a rastrear.

### Problemas agrupados por patrón de URL

Cada problema con 4 o más URLs muestra de qué plantilla salen, p. ej. «`/blog/*/page/{página}` 3.500 (88 %)»: los
números, años, IDs y parámetros se normalizan solos. Exportar › «Incidencias agrupadas por patrón de URL» lo da en CSV.

### PageRank interno

Columnas «PageRank interno (0-100)» y «% de autoridad» en Internos y Enlaces, con filtros para páginas indexables con
poca autoridad y autoridad gastada en URLs no indexables. Se calcula con los enlaces `<a>` internos únicos (sin
nofollow, sin autoenlaces ni enlaces que solo funcionan con clic); las redirecciones pasan su autoridad al destino;
amortiguación 0,85. Sin exportar a Gephi.

### Integración continua (GitHub Actions, GitLab, Jenkins)

```bash
py -3.12 run.py ci https://staging.misitio.es/ --i-own-this-site --fail-on critical,high --junit seo.xml --json seo.json
```

Devuelve **0** si todo está bien, **1** si hay problemas que bloquean y **2** si no se pudo rastrear. Siempre comprueba
que la URL inicial sea indexable (el típico `noindex` olvidado al publicar). Opciones: `--fail-on-code`,
`--ignore-code`, `--require-indexable URL` (repetible), `--baseline seo.json` (solo suspenden las incidencias nuevas),
`--markdown`, `--webhook URL` (Slack, Teams o Discord) y `--out` (ZIP). En GitHub Actions el resumen aparece en la
página de la ejecución. Ejemplos listos en [`extras/ci/`](extras/ci/). Si tu staging bloquea todo en robots.txt,
añade `User-agent: LetsCrawlit` + `Allow: /`: la herramienta respeta siempre robots.txt.

### Webs con inicio de sesión (SSO, doble factor, CAPTCHA)

Configuración › Autenticación › «Abrir navegador para iniciar sesión»: se abre una ventana real, inicias sesión tú
(con tu móvil, tu SSO o resolviendo el CAPTCHA) y pulsas «Guardar sesión». Se guardan solo las cookies y el
localStorage de tu dominio (los del proveedor de SSO se descartan) en `data/sessions/`, nunca en la base de datos ni
en las exportaciones, y los siguientes rastreos de ese sitio entran identificados hasta que la sesión caduque. También
sirven para `run.py crawl` y `run.py ci`. Para webs con usuario y contraseña simples sigue existiendo `LOGIN_*` en `.env`.

### Informe para cliente

Botón «Informe para cliente» en cada auditoría: una página en lenguaje sencillo para el dueño del negocio con cuántas
páginas están bien en los puntos básicos, los arreglos con más impacto, una revisión rápida (bien o mal), la lista de SEO local y el antes/después frente al
rastreo anterior del mismo sitio. Arriba se escriben el cliente, tu marca, tu contacto y el color (se recuerdan en
`data/branding.json`) y puedes subir el **logo de tu agencia** (PNG, JPG, WebP o SVG). Incluye gráficos (estado de las
URLs, avisos por importancia y evolución de la nota) y se descarga en **PDF** con un clic o como HTML autocontenido. También va dentro del ZIP (`informe-cliente.html`).

### Presentación y Excel para el cliente

En *Informes* hay dos descargas más, con **solo los fallos que siempre son un error** (enlaces rotos, errores 4xx/5xx,
cadenas y bucles de redirección, título o H1 ausente, canonical o hreflang roto, noindex en el sitemap, HTTP sin cifrar…):

- **Presentación (PowerPoint)**: portada, resumen con datos y una diapositiva por tipo de error (qué es, por qué importa,
  cómo se arregla y ejemplos). Como mucho 10 diapositivas; si un tipo de error no aparece, no tiene diapositiva.
- **Un Excel por tipo de error (ZIP)**: un `.xlsx` por cada tipo, con URL, evidencia, cómo arreglarlo y la columna
  «Anotación Consultor» con tu nota de esa URL.

Usan `python-pptx` y `openpyxl` (licencia MIT), instaladas en local con el resto de dependencias.
Además se guardan las cabeceras HTTP de cada URL (sin cookies ni cabeceras de autenticación), hreflang y recursos HTTP inseguros.
Las credenciales de staging (`CRAWL_AUTH_*`) solo se envían al sitio auditado, nunca a enlaces externos ni al renderizar otros orígenes.

---

## Arquitectura

```
run.py               lanzador de un comando (solo biblioteca estándar)
app/
  config.py          ajustes desde .env / variables de entorno
  validation.py      validación del formulario y texto de permiso
  urlutil.py         normalización de URLs, alcance, nombres de archivo seguros
  robots.py          robots.txt con semántica de Google (comodines, $, regla más larga) + probador
  crawlconfig.py     configuración del rastreo: valores por defecto, User-Agents, validación, data/config.json
  checks.py          validadores: hreflang ISO, Schema.org, AMP, accesibilidad, ortografía
  textsim.py         casi duplicados (MinHash/LSH), TF-IDF, clústeres, sugerencias de enlazado, plagio
  insights.py        prioridades (qué arreglar primero) y cálculos al final del rastreo
  history.py         comparar auditorías y tendencias
  localseo.py        SEO local: teléfonos, WhatsApp, mapa, LocalBusiness, NAP, servicio + ciudad
  clientreport.py    informe en lenguaje sencillo para el cliente (marca, logo, gráficos y PDF)
  patterns.py        agrupación de URLs por patrón (/blog/*/page/{página})
  pagerank.py        PageRank interno
  ci.py              puerta para CI/CD: aprobado/suspenso, JUnit, Markdown, JSON, webhook
  sessions.py        grabar un inicio de sesión manual y reutilizarlo
  integrations.py    CSV de Search Console/Analytics, Copyscape
  ai.py              IA opcional (Claude vía SDK de Anthropic, u Ollama local)
  crawlfile.py       exportar/importar .seocrawl
  cli.py             línea de comandos y programación
  extract.py         HTML → señales SEO (función pura)
  render.py          renderizado con Playwright (Chromium/Edge/Chrome), falla de forma controlada
  crawler.py         rastreo educado en un hilo propio + registro de trabajos + PageSpeed opcional
  analysis.py        transformación central: páginas + enlaces → incidencias priorizadas (funciones puras)
  views.py           filas enriquecidas, pestañas, filtros, columnas y resumen de la mesa de trabajo (con caché)
  report.py          CSV (seguro para Excel), informe HTML autocontenido, ZIP con manifiesto
  db.py              esquema y acceso SQLite
  main.py            rutas FastAPI + parciales HTMX
  templates/ static/ interfaz (htmx 1.9.12 incluido en static/)
extras/demo/          sitio local con errores para probar
tests/               tests de la transformación central, del renderizado y uno extremo a extremo
```

Flujo: formulario → `validation` → fila en `crawls` → hilo con `crawler.crawl()` → por cada URL: robots → pausa →
descarga → `extract` → (opcional) `render` → guardar en `pages/links/images` → al terminar, `analysis.analyze()` →
tabla `issues` → la interfaz y `report` leen de SQLite.

La interfaz funciona también sin JavaScript (formularios normales y recarga automática); HTMX solo mejora la experiencia.
Los POST desde otro origen se bloquean para que ninguna web pueda lanzar rastreos a través de tu app local.

---

## Permisos y límites deliberados

- **Solo sitios propios o con permiso explícito.** La herramienta no ofrece ningún modo para saltarse la confirmación, ignorar robots.txt o rastrear sin pausa.
- **Fuera de alcance a propósito:** rastrear sitios sin permiso, bases de datos de backlinks o palabras clave a escala web, y cualquier cambio automático en webs en producción (la herramienta solo lee).
- Sin cuentas, facturación, telemetría, analítica ni panel alojado. Los servicios externos son todos **opcionales y desactivados por defecto**, y si fallan se anota y el resto de la auditoría sigue siendo válido:
  - PageSpeed Insights (`PAGESPEED_API_KEY` + casilla): envía las URLs elegidas a Google.
  - IA con Claude (`AI_PROVIDER=anthropic`): envía el texto de la página a la API de Anthropic solo cuando pulsas un botón de IA. Usa `claude-opus-5-5` con reserva automática del servidor si el modelo declina una petición. Con `AI_PROVIDER=ollama` todo queda en tu equipo.
  - Copyscape (`COPYSCAPE_*`): envía la URL que eliges comprobar.
  - «Cargar el robots.txt actual» descarga solo `/robots.txt` del sitio que escribiste.
- Servidor ligado a `127.0.0.1`. No lo expongas en red: no tiene autenticación.

## Secretos

Van en `.env` (ignorado por git). Plantilla en `.env.example`:

- `CRAWL_AUTH_USER` / `CRAWL_AUTH_PASSWORD`: autenticación básica para un staging tuyo; solo se envían al sitio rastreado.
- `CRAWL_COOKIES`, `LOGIN_URL` / `LOGIN_USER` / `LOGIN_PASSWORD` (+ selectores opcionales): sesión para zonas privadas de tu sitio.
- `CRAWL_PROXY`: proxy HTTP (puede llevar usuario y contraseña).
- `PAGESPEED_API_KEY`, `AI_PROVIDER` / `AI_MODEL` / `ANTHROPIC_API_KEY` / `OLLAMA_URL`, `COPYSCAPE_USER` / `COPYSCAPE_KEY`: opcionales.

Nunca pongas credenciales en la URL (el formulario lo rechaza).

## Dónde se guardan los datos

Todo en local, en `LETSCRAWL_DATA_DIR` (por defecto `data/` dentro del proyecto; con el programa de Windows,
`%APPDATA%\LetsCrawlit\data`, y el `.env` en `%APPDATA%\LetsCrawlit`; la ruta exacta aparece al pie de la página de inicio):

- `data/letscrawl.db` — base SQLite (auditorías, páginas, enlaces, imágenes, sitemap, incidencias, snapshot de robots.txt, puntos de control, datos importados de GSC/GA, resultados de IA).
- `data/exports/` — cada ZIP exportado se guarda también aquí con nombre seguro `auditoria-<dominio>-<fecha>-<hora>.zip`.
- `data/screenshots/<id>/` — capturas de pantalla, si las activas.
- `data/config.json` — configuración predeterminada del rastreo.
- `data/branding.json` y `data/branding-logo.*` — tu marca, contacto, color y logo para el informe del cliente.
- `data/sessions/` y `data/google_token.json` — sesiones grabadas y el permiso de Google. Trátalos como contraseñas.
- `data/html/<id>/` — HTML guardado de cada página, si activas esa opción.

Cada ZIP contiene `pages.csv`, `issues.csv`, `links.csv`, `images.csv`, `externals.csv`, `report.html`, `informe-cliente.html` y `manifest.json`
(configuración, permiso, robots.txt usado, versiones, notas y SHA-256 de cada archivo) para poder reproducir y verificar la auditoría.

Eliminar una auditoría desde la interfaz borra sus filas de la base (pide confirmación); los ZIP ya exportados no se tocan.

## Copias de seguridad

1. Cierra la app para que SQLite no esté escribiendo.
2. Copia la carpeta `data/` entera (incluye `letscrawl.db` y, si existen, `letscrawl.db-wal`/`-shm`):

   ```bash
   robocopy data D:\Backups\letscrawl-data /MIR
   ```

   o, con la app abierta, haz una copia consistente con SQLite:

   ```bash
   .venv\Scripts\python -c "import sqlite3; s=sqlite3.connect('data/letscrawl.db'); d=sqlite3.connect('backup.db'); s.backup(d); d.close()"
   ```

3. Para restaurar, sustituye `data/` (o `data/letscrawl.db`) por la copia con la app cerrada.

> Si vienes de una versión anterior: la base `data/seospider.db` y las variables `SEOSPIDER_*` del `.env` se siguen
> reconociendo; no hace falta cambiar nada.

Los ZIP de `data/exports/` son autocontenidos: sirven como archivo histórico aunque pierdas la base.
Para guardar o mover una auditoría completa (y poder reanudarla), usa «Exportar › Archivo de rastreo (.seocrawl)».

## Configuración (`.env`)

| Variable | Por defecto | Descripción |
|---|---|---|
| `LETSCRAWL_HOST` / `LETSCRAWL_PORT` | `127.0.0.1` / `8765` | Dirección del servidor local |
| `LETSCRAWL_PASSWORD` | (vacío) | Contraseña para usar la app desde otros dispositivos de la red; sin ella solo funciona en este equipo |
| `LETSCRAWL_ALLOWED_HOSTS` | (vacío) | Nombres extra con los que se puede abrir la app (p. ej. `mi-portatil.local`); por defecto solo `localhost` y direcciones IP |
| `LETSCRAWL_OPEN_BROWSER` | `1` | Abrir el navegador al arrancar |
| `LETSCRAWL_DATA_DIR` | `data` | Carpeta de datos |
| `LETSCRAWL_USER_AGENT` | `LetsCrawlit/1.0.0` | User-Agent (también para robots.txt) |
| `LETSCRAWL_DEFAULT_MAX_URLS` / `LETSCRAWL_MAX_URLS_LIMIT` | `0` / `0` | Tope por defecto y tope de seguridad (0 = sin límite) |
| `LETSCRAWL_DEFAULT_DELAY` / `LETSCRAWL_MIN_DELAY` | `1.0` / `0.2` | Pausa por defecto y mínima (s) |
| `LETSCRAWL_RENDER_TIMEOUT` | `20` | Tiempo máximo de renderizado por página (s) |

## Software de terceros incluido

- [htmx](https://htmx.org/) 1.9.12 (`app/static/htmx.min.js`), licencia BSD 2-Clause.
- Fuente [IBM Plex Sans](https://github.com/IBM/plex) (`app/static/plex-sans-*.woff2`), licencia SIL Open Font License 1.1 (`app/static/plex-LICENSE.txt`).

Las dependencias de Python se instalan desde PyPI con `requirements.txt` y conservan sus propias licencias.
Este proyecto es independiente y no está afiliado a ninguna otra herramienta de rastreo SEO.
