"""FastAPI + HTMX interface."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

import asyncio
import os
from html import escape as html_escape
import re
import sys
from subprocess import list2cmdline as subprocess_list2cmdline

from fastapi.responses import FileResponse, JSONResponse

from . import access, i18n, safehttp
from . import ai, crawlconfig, crawlfile, crawler, db, dictionary, google, history, insights, integrations, report, sessions, textsim, views
from .analysis import ALWAYS_BAD, SEVERITY_LABEL, SEVERITY_ORDER, group_issues
from .config import VERSION, get_settings
from . import options
from .render import DEFAULT_BLOCKLIST, THIRD_PARTY

OPTION_NAV = {"resources": "crawl", "behaviour": "crawl", "crawl_store": "crawl", "ext": "extraction", "robots": "robots", "limits": "limits", "advanced": "advanced",
              "extraction": "extraction", "thresholds": "prefs", "speed": "speed"}
from .robots import test_urls
from .validation import ACK_TEXT, check_url, config_only, validate_crawl_form

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(version=VERSION, severity_label=SEVERITY_LABEL, quote=quote)

STATUS_LABEL = {
    "queued": "En cola", "running": "Rastreando", "completed": "Completado", "cancelled": "Detenido",
    "failed": "Error", "interrupted": "Interrumpido", "paused": "En pausa",
}
templates.env.globals["status_label"] = STATUS_LABEL
templates.env.globals["cell"] = views.cell
templates.env.globals["bulk_exports"] = report.BULK_EXPORTS
templates.env.globals["version"] = VERSION


def _diagnostics() -> dict:
    """Ayuda › Diagnóstico: what someone helping you needs to know. Never keys or audit data."""
    import platform
    s = get_settings()
    st = ai.status()
    return {"os": f"{platform.system()} {platform.release()} ({platform.version()})", "python": platform.python_version(),
            "data_dir": str(s.data_dir), "log": str(s.data_dir / "letscrawl.log"),
            "ai": (st.get("provider") or "ninguna") if isinstance(st, dict) and st.get("enabled") else "sin configurar"}


templates.env.globals["diagnostics"] = _diagnostics
templates.env.globals["simple_tabs"] = list(views.SIMPLE_TABS)
# cache-busting for /static: browsers reload CSS/JS after an update instead of using the old copy
class _StaticVersion:
    """Cache-busting stamp for /static links: the newest file time, read on every render so edits show up at once."""
    def __str__(self) -> str:
        return str(int(max(f.stat().st_mtime for f in (HERE / "static").iterdir() if f.is_file())))


templates.env.globals["static_v"] = _StaticVersion()
from . import help as ui_help  # noqa: E402
templates.env.globals["option_help"] = ui_help.OPTION_HELP
templates.env.globals["tab_help"] = ui_help.tab

templates.env.filters["evidence_parts"] = views.evidence_parts
templates.env.globals["issue_total"] = lambda crawl_id: len(db.load_issues(crawl_id))  # live count (old audits are re-checked)
PAGE_SIZE = 200


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    db.mark_interrupted()
    from . import secretstore
    data_dir = get_settings().data_dir
    secretstore.encrypt_existing([data_dir / "google_token.json", *(data_dir / "sessions").glob("*.json")])
    yield


app = FastAPI(title="LetsCrawlit", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def _host_allowed(host_header: str) -> bool:
    """Only localhost names, IP literals or hosts listed in LETSCRAWL_ALLOWED_HOSTS.
    A web page cannot reach this app through its own domain (DNS rebinding): that domain is not on the list."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        host = host[1:host.find("]")] if "]" in host else ""
    elif host.count(":") == 1:
        host = host.split(":")[0]
    if host in ("localhost", "testserver") or host.endswith(".localhost"):
        return True
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    extra = os.getenv("LETSCRAWL_ALLOWED_HOSTS", "")
    return host in {h.strip().lower() for h in extra.split(",") if h.strip()}


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",                       # no other site can frame the app (clickjacking)
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",                # audited URLs never leak to the sites you click through to
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https: http:; "
                                "img-src 'self' data: blob: https: http:; font-src 'self' data: https: http:; connect-src 'self'; "
                                "frame-src 'self'; frame-ancestors 'none'; form-action 'self' https://accounts.google.com; "
                                "base-uri 'self'; object-src 'none'"),
}


def _same_origin(request: Request) -> bool:
    """A POST must come from LetsCrawlit's own pages. Modern browsers say so in Sec-Fetch-Site; a plain form
    submit can carry `Origin: null`, so that header alone is not trusted either way."""
    fetch_site = request.headers.get("sec-fetch-site", "")
    if fetch_site:
        return fetch_site in ("same-origin", "none")
    origin = request.headers.get("origin", "")
    if origin == "null":
        return False
    origin = origin or request.headers.get("referer", "")
    return not origin or urlsplit(origin).netloc == request.url.netloc


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    """Block requests through a foreign host name and cross-site posts (another website trying to drive this
    local app), and add the browser security headers to every answer."""
    if not _host_allowed(request.headers.get("host", "")):
        return HTMLResponse("Host no permitido. Abre LetsCrawlit en http://127.0.0.1:8765/ "
                            "(o añade el nombre a LETSCRAWL_ALLOWED_HOSTS en .env).", status_code=421)
    client_ip = request.client.host if request.client else ""
    path = request.url.path
    if not access.is_local_client(client_ip) and not path.startswith("/static/") and path not in ("/login", "/health"):
        # another device on the network: only with LETSCRAWL_PASSWORD, and after logging in
        if not access.password():
            return HTMLResponse("LetsCrawlit solo se puede usar desde este equipo. Para abrirlo desde otro dispositivo "
                                "pon LETSCRAWL_PASSWORD en .env y reinicia la app.", status_code=403)
        if not access.has_valid_cookie(request.cookies.get(access.COOKIE)):
            if request.headers.get("hx-request") == "true":
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            return RedirectResponse("/login?next=" + quote(_local_path(path), safe="/"), status_code=303)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not _same_origin(request):
            return HTMLResponse("Petición de otro origen bloqueada.", status_code=403)
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


LOGIN_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entrar · LetsCrawlit</title><link rel="icon" href="/static/icono-app.svg" type="image/svg+xml"><link rel="stylesheet" href="/static/style.css"></head>
<body style="display:grid;place-items:center;min-height:100vh;background:#f9f6fe">
<form method="post" action="/login" class="card" style="width:min(360px,calc(100vw - 32px));padding:24px">
<h1 style="margin:0 0 10px"><img src="/static/logo-claro.svg" alt="LetsCrawlit" width="173" height="36"></h1>
<p class="muted" style="margin:0 0 14px">Escribe la contraseña de LetsCrawlit (LETSCRAWL_PASSWORD en .env).</p>
{error}<input type="hidden" name="next" value="{next}">
<input type="password" name="password" aria-label="Contraseña" autofocus required style="width:100%">
<button type="submit" style="margin-top:12px;width:100%">Entrar</button></form></body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page(next: str = "/"):
    return HTMLResponse(LOGIN_PAGE.format(error="", next=html_escape(_local_path(next))))


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    form = await request.form()
    ip = request.client.host if request.client else ""
    target = _local_path(str(form.get("next") or "/"))
    if access.locked(ip):
        msg = "Demasiados intentos. Espera 15 minutos."
    elif await asyncio.to_thread(access.check_password, ip, str(form.get("password") or "")):
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(access.COOKIE, access.token(), max_age=access.MAX_AGE, httponly=True, samesite="strict")
        return resp
    else:
        msg = "Contraseña incorrecta." if access.password() else "No hay contraseña configurada en .env."
    return HTMLResponse(LOGIN_PAGE.format(error=f'<p class="err" role="alert">{msg}</p>', next=html_escape(target)),
                        status_code=401)


def _local_path(target: str, default: str = "/") -> str:
    """A path on this app only: no //host, /\\host or scheme tricks (open redirect)."""
    t = target or ""
    if not t.startswith("/") or t[1:2] in ("/", "\\") or any(c in t for c in "\r\n\t\\") or len(t) > 2000:
        return default
    return t


@app.middleware("http")
async def interface_language(request: Request, call_next):
    """English interface: translate HTML pages and htmx fragments on the way out (see app/i18n.py)."""
    response = await call_next(request)
    lang = request.cookies.get(i18n.COOKIE, "es")
    if lang not in i18n.LANGS or lang == "es" or not response.headers.get("content-type", "").startswith("text/html"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    out = i18n.translate_html(body.decode("utf-8", "replace"), lang).encode("utf-8")
    new = Response(content=out, status_code=response.status_code)
    new.raw_headers = [(k, v) for k, v in response.raw_headers if k.lower() != b"content-length"] +                       [(b"content-length", str(len(out)).encode())]
    return new


@app.get("/lang/{code}")
async def set_language(code: str, next: str = "/"):
    """Language switch (the flags at the top). Stored in a cookie for a year; only local paths are followed."""
    if code not in i18n.LANGS:
        raise HTTPException(404)
    response = RedirectResponse(_local_path(next), status_code=303)
    response.set_cookie(i18n.COOKIE, code, max_age=365 * 24 * 3600, samesite="lax")
    return response


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(request: Request, name: str, ctx: dict, status_code: int = 200) -> HTMLResponse:
    """Partials are wrapped in the full layout when requested without HTMX (no-JS fallback)."""
    ctx = {"request": request, **ctx}
    if name.startswith("_") and not is_htmx(request):
        ctx["partial"] = name
        name = "wrapper.html"
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def get_crawl_or_404(crawl_id: int) -> dict:
    crawl = db.get_crawl(crawl_id)
    if not crawl:
        raise HTTPException(404, "Ese rastreo no existe o fue eliminado.")
    return crawl


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return templates.TemplateResponse(
        request, "error.html", {"request": request, "code": exc.status_code, "message": exc.detail},
        status_code=exc.status_code)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    return templates.TemplateResponse(
        request, "error.html",
        {"request": request, "code": 500, "message": f"Error inesperado: {type(exc).__name__}. Tus datos no se han perdido; vuelve a intentarlo."},
        status_code=500)


def _empty_workbench() -> dict:
    """Tabs, overview and summary with no data, for the start page (it looks like an empty audit)."""
    empty = views.build_rows([], [], [], [])
    tabs = [t for t in views.tabs_for(None) if t.source != "issues"]
    return {"empty_tabs": tabs, "empty_overview": views.overview(empty, tabs), "empty_summary": views.summary(empty)}


def index_context(form: dict | None = None, errors: dict | None = None, saved: str = "") -> dict:
    s = get_settings()
    defaults = crawlconfig.load_defaults(s)
    if form is None:
        form = {k: v for k, v in defaults.items() if not isinstance(v, list)}
        for key in crawlconfig.BOOL_KEYS:
            form[key] = "yes" if defaults.get(key) else ""
        for i, sr in enumerate(defaults.get("searches") or []):
            form.update({f"search_name_{i}": sr["name"], f"search_mode_{i}": sr["mode"], f"search_text_{i}": sr["text"]})
        for i, ex in enumerate(defaults.get("extractions") or []):
            form.update({f"extract_name_{i}": ex["name"], f"extract_type_{i}": ex["type"], f"extract_expr_{i}": ex["expr"]})
    return {
        "crawls": db.list_crawls(), "form": form, "errors": errors or {}, "ack_text": ACK_TEXT,
        "settings": s, "pagespeed_available": bool(s.pagespeed_key), "data_dir": str(s.data_dir),
        "ua_presets": crawlconfig.UA_PRESETS, "max_custom": crawlconfig.MAX_CUSTOM, "saved": saved,
        "third_party": THIRD_PARTY, "blocklist_total": len(DEFAULT_BLOCKLIST),
        "opt_bools": options.BOOLS, "opt_numbers": options.NUMBERS, "opt_texts": options.TEXTS, "opt_selects": options.SELECTS,
        "option_sections": {k: OPTION_NAV[sec] for k, _d, sec, *_ in options.BOOLS + options.TEXTS}
                           | {k: OPTION_NAV[sec] for k, _d, _lo, _hi, sec, _l in options.NUMBERS},
        "session_status": sessions.recorder.status(), "saved_sessions": sessions.list_saved(),
        "google_status": google.status(), "dict_path": str(dictionary.path()), "dict_shared": dictionary.shared(),
        "dict_count": len(dictionary.words()), "dict_text": dictionary.text()[:200000],
        **_empty_workbench(),
        "opt_defaults": options.DEFAULTS,
        "ai_keys": {"openai": bool(os.getenv("OPENAI_API_KEY")), "gemini": bool(os.getenv("GEMINI_API_KEY")), "groq": bool(os.getenv("GROQ_API_KEY")),
                    "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")) or s.ai_provider == "anthropic",
                    "ollama": s.ai_provider == "ollama", "copyscape": bool(s.copyscape_user and s.copyscape_key)},
        "config_error": any(k not in ("url", "urls", "ack") for k in (errors or {})),
        "proxy_available": bool(s.proxy), "login_available": bool(s.login_url), "cookies_set": bool(s.cookies),
        "ai": ai.status(),
    }


@app.post("/config")
async def save_config(request: Request):
    form = dict(await request.form())
    cfg, errors = config_only(form, get_settings())
    if errors:
        return render(request, "index.html", index_context(form, errors), status_code=422)
    crawlconfig.save_defaults(cfg)
    back = str(form.get("back") or "")
    if back.isdigit() and db.get_crawl(int(back)):  # saved from an audit's menu: back to that audit
        return RedirectResponse(f"/crawls/{back}?cfg_saved=1", status_code=303)
    return render(request, "index.html", index_context(saved="Configuración guardada. Los próximos rastreos empezarán con ella."))


@app.post("/config/reset")
async def reset_config(request: Request):
    crawlconfig.reset_defaults()
    return render(request, "index.html", index_context(saved="Configuración restablecida a los valores de fábrica."))


@app.post("/tools/robots-test", response_class=HTMLResponse)
async def robots_test(request: Request):
    form = dict(await request.form())
    text = (form.get("custom_robots") or "")[:20000]
    urls = []
    for line in [l.strip() for l in (form.get("robots_test_urls") or "").splitlines() if l.strip()][:200]:
        url, err = check_url(line)
        urls.append(url or line)
    s = get_settings()
    preset_cfg = {"ua_preset": form.get("ua_preset") or "default", "ua_custom": (form.get("ua_custom") or "").strip()}
    ua, token = crawlconfig.user_agent(preset_cfg, s)
    results = test_urls(text, urls, token) if text.strip() else []
    return templates.TemplateResponse(request, "_robots_test.html", {
        "request": request, "results": results, "token": token, "has_text": bool(text.strip()), "has_urls": bool(urls)})


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return render(request, "index.html", index_context())


async def _read_form(request: Request) -> dict:
    """Form fields as strings; an uploaded URL list (CSV/TXT) is merged into the 'urls' textarea."""
    raw = await request.form()
    form: dict = {}
    for key, value in raw.multi_items():
        if hasattr(value, "read"):
            if key == "urls_file" and value.filename:
                blob = (await value.read())[:5 * 1024 * 1024]
                text = blob.decode("utf-8-sig", errors="replace")
                found = re.findall(r"https?://[^\s,;\"'<>]+", text)
                if found:
                    form["urls"] = (form.get("urls", "") + "\n" + "\n".join(found)).strip()
                    form["mode"] = "list"
            continue
        if key == "urls" and form.get("urls"):
            form["urls"] = value + "\n" + form["urls"]
        else:
            form[key] = value
    return form


@app.post("/crawls")
async def create_crawl(request: Request):
    form = await _read_form(request)
    cfg, errors = validate_crawl_form(form, get_settings())
    if errors:
        return render(request, "index.html", index_context(form, errors), status_code=422)
    crawl_id = db.create_crawl(cfg["start_url"], cfg, ACK_TEXT)
    crawler.start_job(crawl_id)
    return RedirectResponse(f"/crawls/{crawl_id}", status_code=303)


def grid_context(crawl: dict, tab: str, f: str, q: str, sort: str, dir: str, page: int = 1,
                 start: int = 0, upto: int = 0) -> dict:
    """The table loads PAGE_SIZE rows and then more as you scroll (start = first row of the next chunk).
    `upto` keeps the rows already loaded when the table refreshes itself during a crawl."""
    tabs = views.tabs_for(crawl)
    t = next((x for x in tabs if x.key == tab), tabs[0])
    ctx = {"crawl": crawl, "tabs": tabs, "tab": t, "f": f, "q": q.strip()[:200], "sort": sort,
           "dir": "desc" if dir == "desc" else "asc", "active": crawl["status"] in db.ACTIVE_STATUSES}
    from .urlutil import site_key
    ctx["jn"] = db.journal_counts(db.note_scope(crawl["id"]))
    if t.source == "issues":
        ctx.update(issues_context(crawl, "", code=f))
        return ctx
    if f not in {k for k, _, _ in t.filters}:
        ctx["f"] = f = ""
    ctx["fhelp"] = views.any_filter_help(t.key, f, dict((k, l) for k, l, _ in t.filters).get(f, "")) if f else None
    rows = views.query(views.workbench(crawl), t, f, ctx["q"], sort, ctx["dir"] == "desc")
    total = len(rows)
    start = max(0, min(start or (max(1, page) - 1) * PAGE_SIZE, total))
    end = min(total, start + max(PAGE_SIZE, min(upto, 100_000)))
    ctx.update(rows=rows[start:end], total=total, start=start, next_start=end if end < total else 0)
    return ctx


@app.get("/crawls/{crawl_id}", response_class=HTMLResponse)
async def crawl_page(request: Request, crawl_id: int, tab: str = "", f: str = ""):
    crawl = get_crawl_or_404(crawl_id)
    if not tab:  # a one-click audit opens straight on its answer
        tab, f = PRESET_LANDING.get((crawl.get("settings") or {}).get("preset"), ("internal", f))
    ctx = grid_context(crawl, tab, f, "", "", "asc", 1)
    ctx.update(side_context(crawl, "overview"))
    from .urlutil import site_key
    ctx["site"] = site_key(crawl["start_url"])
    ctx["note_statuses"] = db.NOTE_STATUSES
    return render(request, "crawl.html", ctx)


# --- «Apuntes»: a notebook per site ------------------------------------------------------------------
def _issue_choices(crawl_id: int) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for i in db.load_issues(crawl_id):
        seen.setdefault(i["code"], i.get("title") or i["code"])
    return sorted(seen.items(), key=lambda kv: kv[1].lower())


SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _clean_urls(raw: str, limit: int = 500) -> list[str]:
    urls = [u.strip()[:2048] for u in str(raw or "").splitlines()]
    return list(dict.fromkeys(u for u in urls if u.startswith(("http://", "https://"))))[:limit]


def _journal_issues(crawl_id: int, sel_urls: list[str]) -> tuple[list[dict], list[dict]]:
    """The audit's problems grouped by type: first the ones the selected URLs have (with how many of them),
    then the rest. Each one carries its title and how to fix it, to prefill the note."""
    groups: dict[str, dict] = {}
    chosen = set(sel_urls)
    for i in db.load_issues(crawl_id):
        g = groups.setdefault(i["code"], {"code": i["code"], "title": i.get("title") or i["code"],
                                          "fix": (i.get("remediation") or "").strip(), "sev": i.get("severity") or "info",
                                          "urls": set()})
        if i.get("url") in chosen:
            g["urls"].add(i["url"])
    mine = [dict(g, count=len(g["urls"])) for g in groups.values() if g["urls"]]
    mine.sort(key=lambda g: (-g["count"], SEV_ORDER.get(g["sev"], 9), g["title"].lower()))
    rest = sorted((g for g in groups.values() if not g["urls"]), key=lambda g: g["title"].lower())
    return mine, rest


def _journal_box(request: Request, crawl: dict, q: str = "", status: str = "", message: str = "", error: str = "",
                 sel_urls: list[str] | None = None, draft: str = "", draft_status: str = "nota", pick_issue: str = ""):
    from .urlutil import site_key
    site = db.note_scope(crawl["id"])
    choices = _issue_choices(crawl["id"])
    sel_urls = sel_urls or []
    sel_issues, other_issues = _journal_issues(crawl["id"], sel_urls)
    entries = db.journal_list(site, q.strip()[:200], status)
    if not status:  # «Todo»: things to do first, then notes, then what is done
        order = {"pendiente": 0, "nota": 1, "hecho": 2}
        entries.sort(key=lambda e: order.get(e["status"], 3))
    from collections import Counter
    by_status = Counter(e["status"] for e in db.journal_list(site))
    resp = templates.TemplateResponse(request, "_journal.html", {
        "crawl": crawl, "site": site, "entries": entries, "by_status": by_status,
        "url_notes": sorted(db.get_notes(site).items(), key=lambda kv: kv[1]["updated_at"], reverse=True) if not status else [],
        "counts": db.journal_counts(site), "issue_choices": choices, "issue_titles": dict(choices),
        "sel_urls": sel_urls, "sel_issues": sel_issues, "other_issues": other_issues, "draft": draft[:4000],
        "draft_status": draft_status if draft_status in db.JOURNAL_STATUSES else "nota", "pick_issue": pick_issue,
        "q": q, "status": status, "statuses": db.JOURNAL_STATUSES, "message": message, "error": error})
    resp.headers["HX-Trigger"] = "journalChanged"
    return resp


@app.get("/crawls/{crawl_id}/journal", response_class=HTMLResponse)
async def journal_view(request: Request, crawl_id: int, q: str = "", status: str = ""):
    return _journal_box(request, get_crawl_or_404(crawl_id), q, status)


QUICK_NOTES = ["Revisar con el cliente", "Cambiar el texto de la página", "Pendiente de la próxima auditoría"]


@app.post("/crawls/{crawl_id}/journal/compose", response_class=HTMLResponse)
async def journal_compose(request: Request, crawl_id: int):
    """«Apuntar»: the little window to write a note about the URLs selected in the table (a POST because
    the list of URLs can be long). It offers the problems those URLs have, to add them with one click."""
    crawl = get_crawl_or_404(crawl_id)
    form = await request.form()
    sel_urls = _clean_urls(str(form.get("urls") or ""))
    if not sel_urls:
        return HTMLResponse('<p class="muted">Marca antes una o varias URLs en la tabla.</p>')
    sel_issues, _ = _journal_issues(crawl_id, sel_urls)
    return templates.TemplateResponse(request, "_apuntar.html", {
        "crawl": crawl, "sel_urls": sel_urls, "sel_issues": sel_issues, "quick": QUICK_NOTES,
        "draft": str(form.get("text") or "")[:4000]})


@app.post("/crawls/{crawl_id}/journal", response_class=HTMLResponse)
async def journal_add(request: Request, crawl_id: int):
    from .urlutil import site_key
    crawl = get_crawl_or_404(crawl_id)
    form = await request.form()
    link = str(form.get("link") or "")
    url = issue = None
    if link.startswith("url:"):
        url = link[4:].strip()[:2048]
        if not url.startswith(("http://", "https://")):
            url = None
    elif link.startswith("issue:"):
        issue = link[6:] if link[6:] in dict(_issue_choices(crawl_id)) else None
    if not link or link == "urls":  # the drawer form: the selected URLs and, optionally, one problem
        url = "\n".join(_clean_urls(str(form.get("urls") or ""))) or None
        picked = str(form.get("issue") or "")
        issue = picked if picked in dict(_issue_choices(crawl_id)) else None
    try:
        db.journal_add(db.note_scope(crawl["id"]), str(form.get("text") or ""), str(form.get("status") or "nota"),
                       url, issue, form.get("in_report") == "yes")
    except ValueError as exc:
        return _journal_box(request, crawl, error=str(exc), sel_urls=_clean_urls(str(form.get("urls") or "")),
                            draft_status=str(form.get("status") or "nota"), pick_issue=str(form.get("issue") or ""))
    done = "Tarea añadida a «Por hacer»." if str(form.get("status") or "") == "pendiente" else "Nota guardada."
    return _journal_box(request, crawl, message=done)


@app.post("/crawls/{crawl_id}/journal/{entry_id}", response_class=HTMLResponse)
async def journal_change(request: Request, crawl_id: int, entry_id: int):
    from .urlutil import site_key
    crawl = get_crawl_or_404(crawl_id)
    site = db.note_scope(crawl["id"])
    form = await request.form()
    action = str(form.get("action") or "")
    try:
        if action == "delete":
            db.journal_delete(site, entry_id)
        elif action == "status":
            db.journal_update(site, entry_id, status=str(form.get("status") or ""))
        elif action == "report":
            db.journal_update(site, entry_id, in_report=form.get("in_report") == "yes")
        elif action == "edit":
            db.journal_update(site, entry_id, text=str(form.get("text") or ""))
        else:
            raise ValueError("Acción no válida.")
    except ValueError as exc:
        return _journal_box(request, crawl, str(form.get("q") or ""), str(form.get("fstatus") or ""), error=str(exc))
    return _journal_box(request, crawl, str(form.get("q") or ""), str(form.get("fstatus") or ""))


@app.get("/crawls/{crawl_id}/note")
async def get_note(crawl_id: int, url: str):
    from .urlutil import site_key
    crawl = get_crawl_or_404(crawl_id)
    return db.get_notes(db.note_scope(crawl["id"])).get(url) or {"note": "", "status": "pendiente"}


@app.post("/crawls/{crawl_id}/note")
async def save_note(request: Request, crawl_id: int):
    """Notes belong to this audit only: a new crawl of the same site starts without them."""
    from .urlutil import site_key
    crawl = get_crawl_or_404(crawl_id)
    form = await request.form()
    url = str(form.get("url") or "").strip()
    if not url.startswith(("http://", "https://")) or len(url) > 2048:
        raise HTTPException(400, "URL no válida.")
    status = str(form.get("status") or "pendiente")
    if status not in db.NOTE_STATUSES:
        raise HTTPException(400, "Estado no válido.")
    saved = db.set_note(db.note_scope(crawl["id"]), url, str(form.get("note") or ""), status)
    return saved or {"note": "", "status": "", "deleted": True}


@app.get("/crawls/{crawl_id}/grid", response_class=HTMLResponse)
async def crawl_grid(request: Request, crawl_id: int, tab: str = "internal", f: str = "", q: str = "",
                     sort: str = "", dir: str = "asc", page: int = 1, start: int = 0, upto: int = 0, more: int = 0):
    crawl = get_crawl_or_404(crawl_id)
    ctx = grid_context(crawl, tab, f, q, sort, dir, page, start, upto)
    return render(request, "_grid_rows.html" if more and "rows" in ctx else "_grid.html", ctx)


SIDE_PANES = [("overview", "Resumen"), ("issues", "Errores"), ("structure", "Estructura del sitio"),
              ("times", "Tiempos de respuesta")]
ISSUE_TYPE = {"critical": "Problema", "high": "Problema", "medium": "Advertencia", "low": "Advertencia", "info": "Oportunidad"}
ISSUE_PRIORITY = {"critical": "Alta", "high": "Alta", "medium": "Media", "low": "Baja", "info": "Baja"}


# One-click audits (start page): where each one opens when the crawl is done.
PRESET_LANDING = {"broken": ("issues", "broken_link"), "images": ("images", "over_kb"), "sitemap": ("codes", "")}


def side_context(crawl: dict, pane: str) -> dict:
    pane = pane if pane in dict(SIDE_PANES) else "overview"
    data = views.workbench(crawl)
    ctx = {"crawl": crawl, "pane": pane, "panes": SIDE_PANES}
    from .urlutil import site_key
    ctx["jn"] = db.journal_counts(db.note_scope(crawl["id"]))
    if pane == "overview":
        ctx["summary"] = views.summary(data)
        ctx["overview"] = views.overview(data, views.tabs_for(crawl))  # no health score: only real data
    elif pane == "issues":
        internal = max(1, len([r for r in data["pages"]]))
        all_issues = db.load_issues(crawl["id"])
        groups = group_issues(all_issues)
        ctx["other_issue_groups"] = sum(1 for g in groups if g["code"] not in ALWAYS_BAD)
        groups = [g for g in groups if g["code"] in ALWAYS_BAD]  # «Errores»: only what is always a fault
        ctx["issue_rows"] = [{"code": g["code"], "title": g["title"], "type": ISSUE_TYPE[g["severity"]],
                              "why": g["why"], "fix": g["remediation"],
                              "priority": ISSUE_PRIORITY[g["severity"]], "severity": g["severity"],
                              "count": len(g["items"]), "pct": round(100 * len(g["items"]) / internal, 2)} for g in groups]
    elif pane == "structure":
        ctx["structure"] = views.site_structure(data)
    else:
        ctx["times"] = views.response_times(data)
    return ctx


@app.get("/crawls/{crawl_id}/side", response_class=HTMLResponse)
async def crawl_side(request: Request, crawl_id: int, pane: str = "overview"):
    crawl = get_crawl_or_404(crawl_id)
    return render(request, "_side.html", side_context(crawl, pane))


DETAIL_TABS = {
    "pages": [("details", "Detalles de URL"), ("inlinks", "Enlaces internos"), ("outlinks", "Enlaces salientes"), ("images", "Imágenes"),
              ("headers", "Cabeceras HTTP"), ("hreflang", "Hreflang"), ("path", "Ruta de rastreo"), ("serp", "Fragmento de SERP"),
              ("screenshot", "Captura"), ("waterfall", "Cascada"), ("quality", "Calidad"), ("ai", "IA"),
              ("text", "Texto"), ("source", "Ver fuente"), ("issues", "Incidencias")],
    "externals": [("details", "Detalles"), ("inlinks", "Inlinks")],
    "images": [("details", "Detalles"), ("inlinks", "Páginas que la usan")],
}


def stored_html(crawl_id: int, url: str) -> list[tuple[str, str]]:
    """HTML saved during the crawl (options «Guardar el HTML original/renderizado»), as text to show."""
    import gzip
    import hashlib
    base = get_settings().data_dir / "html" / str(crawl_id) / hashlib.sha1(url.encode()).hexdigest()[:24]
    out = []
    for kind, label in (("original", "HTML original"), ("rendered", "HTML renderizado")):
        path = base.parent / f"{base.name}.{kind}.html.gz"
        if path.exists():
            out.append((label, gzip.decompress(path.read_bytes()).decode("utf-8", "replace")[:2_000_000]))
    return out


@app.get("/crawls/{crawl_id}/detail", response_class=HTMLResponse)
async def crawl_detail(request: Request, crawl_id: int, url: str, src: str = "pages", sub: str = "details"):
    crawl = get_crawl_or_404(crawl_id)
    src = src if src in DETAIL_TABS else "pages"
    sub = sub if sub in dict(DETAIL_TABS[src]) else "details"
    data = views.workbench(crawl)
    row = next((r for r in data[src] if r["address"] == url), None)
    if row is None:
        raise HTTPException(404, "Esa URL no está en este rastreo.")
    ctx = {"crawl": crawl, "row": row, "src": src, "sub": sub, "subtabs": DETAIL_TABS[src], "url": url}
    from .urlutil import site_key
    ctx["url_journal"] = db.journal_list(db.note_scope(crawl["id"]), url=url)
    if src == "pages":
        ctx["outlinks"], ctx["inlinks"] = db.links_for(crawl_id, url)
        status_of = {r["url"]: r.get("status_code") for r in data["pages"]}
        status_of.update({r["url"]: r.get("status_code") for r in data["externals"]})
        for l in ctx["outlinks"]:
            l["target_status"] = status_of.get(l["target_url"])
        ctx["images"] = db.load_images(crawl_id, url)
        ctx["issues"] = [i for i in db.load_issues(crawl_id) if i["url"] == url]
        if sub == "path":
            ctx["crawl_path"] = views.crawl_path(db.load_links(crawl_id), crawl["start_url"], url)
        if sub == "ai":
            ctx["ai_results"], ctx["ai_status"], ctx["ai_tasks"] = db.load_ai(crawl_id, url), ai.status(), ai.TASKS
        if sub == "source":
            ctx["sources"] = stored_html(crawl_id, url)
        if sub == "screenshot" and not row.get("screenshot"):
            ctx["screenshot_hint"] = ("Activa «Capturas de pantalla» (requiere renderizado JS) en Configuración › Rastreador › Renderizado "
                                      "y vuelve a rastrear.")
    elif src == "externals":
        ctx["inlinks"] = db.links_for(crawl_id, url)[1]
    else:
        ctx["image_pages"] = sorted(row["pages"])
    return render(request, "_detail.html", ctx)


@app.get("/crawls/{crawl_id}/export/view.csv")
async def export_view(crawl_id: int, tab: str = "internal", f: str = "", q: str = "", sort: str = "", dir: str = "asc"):
    crawl = get_crawl_or_404(crawl_id)
    t = views.tab_by_key(crawl, tab)
    if not t or t.source == "issues":
        raise HTTPException(404, "Esa pestaña no se puede exportar así; usa el CSV de incidencias.")
    rows = views.query(views.workbench(crawl), t, f, q.strip()[:200], sort, dir == "desc")
    data = report.to_csv([{k: views.cell(r, k) for k, _ in t.columns} for r in rows], [k for k, _ in t.columns],
                         [label for _, label in t.columns], drop_empty=True)
    name = report.safe_filename(f"{report.base_name(crawl)}-{t.key}-{f or 'todas'}")
    return _download(data, f"{name}.csv", "text/csv; charset=utf-8")


@app.get("/crawls/{crawl_id}/status", response_class=HTMLResponse)
async def crawl_status(request: Request, crawl_id: int, was: str = ""):
    crawl = get_crawl_or_404(crawl_id)
    response = render(request, "_status.html", {"crawl": crawl})
    if was in db.ACTIVE_STATUSES and crawl["status"] not in db.ACTIVE_STATUSES:
        response.headers["HX-Refresh"] = "true"  # reload once to show results
    return response


def issues_context(crawl: dict, severity: str, code: str = "") -> dict:
    issues = db.load_issues(crawl["id"])
    show_all = code == "_all"  # «Ver también los avisos»
    if show_all:
        code = ""
    hidden = 0
    if not show_all and not code:  # «Errores»: only what is always a fault, the rest on request
        hidden = len({i["code"] for i in issues if i["code"] not in ALWAYS_BAD})
        issues = [i for i in issues if i["code"] in ALWAYS_BAD]
    counts = {s: 0 for s in SEVERITY_ORDER}
    for i in issues:
        counts[i["severity"]] += 1
    if severity in SEVERITY_ORDER:
        issues = [i for i in issues if i["severity"] == severity]
    if code:
        issues = [i for i in issues if i["code"] == code]
    return {"crawl": crawl, "groups": group_issues(issues), "counts": counts, "severity": severity, "code": code,
            "show_all": show_all, "hidden_groups": hidden}


@app.get("/crawls/{crawl_id}/issues", response_class=HTMLResponse)
async def crawl_issues(request: Request, crawl_id: int, severity: str = ""):
    crawl = get_crawl_or_404(crawl_id)
    return render(request, "_issues.html", issues_context(crawl, severity))


PAGE_FILTERS = {"all": "Todas", "html": "HTML 200", "indexable": "Indexables", "nonindexable": "No indexables",
                "errors": "Errores", "redirects": "Redirecciones", "blocked": "Bloqueadas"}


@app.get("/crawls/{crawl_id}/pages", response_class=HTMLResponse)
async def crawl_pages(request: Request, crawl_id: int, q: str = "", f: str = "all", page: int = 1):
    crawl = get_crawl_or_404(crawl_id)
    q = q.strip()[:200]
    f = f if f in PAGE_FILTERS else "all"
    page = max(1, min(page, 100000))
    per = 100
    rows, total = db.query_pages(crawl_id, q, f, (page - 1) * per, per)
    return render(request, "_pages.html", {
        "crawl": crawl, "rows": rows, "total": total, "q": q, "f": f, "page": page,
        "pages_count": max(1, -(-total // per)), "filters": PAGE_FILTERS})


@app.get("/crawls/{crawl_id}/page", response_class=HTMLResponse)
async def page_detail(request: Request, crawl_id: int, url: str):
    crawl = get_crawl_or_404(crawl_id)
    page = db.get_page(crawl_id, url)
    if not page:  # an image, CSS or JS file checked during the crawl: its details live in the detail panel, not in «pages»
        return RedirectResponse(f"/crawls/{crawl_id}/detail?src=pages&sub=details&url={quote(url, safe='')}", status_code=303)
    outlinks, inlinks = db.links_for(crawl_id, url)
    issues = [i for i in db.load_issues(crawl_id) if i["url"] == url]
    return render(request, "page.html", {
        "crawl": crawl, "p": page, "outlinks": outlinks, "inlinks": inlinks,
        "images": db.load_images(crawl_id, url), "issues": issues})


@app.post("/crawls/{crawl_id}/cancel")
async def cancel_crawl(crawl_id: int):
    get_crawl_or_404(crawl_id)
    crawler.cancel_job(crawl_id)
    return RedirectResponse(f"/crawls/{crawl_id}", status_code=303)


@app.post("/crawls/{crawl_id}/rerun")
async def rerun_crawl(crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    new_id = db.create_crawl(crawl["start_url"], crawl["settings"], crawl["ack_text"])
    crawler.start_job(new_id)
    return RedirectResponse(f"/crawls/{new_id}", status_code=303)


@app.post("/crawls/{crawl_id}/delete")
async def delete_crawl(crawl_id: int):
    get_crawl_or_404(crawl_id)
    if crawler.is_running(crawl_id):
        raise HTTPException(409, "Detén el rastreo antes de eliminarlo.")
    db.delete_crawl(crawl_id)
    return RedirectResponse("/", status_code=303)


def _require_finished(crawl: dict) -> None:
    if crawl["status"] in db.ACTIVE_STATUSES:
        raise HTTPException(409, "El rastreo sigue en curso; espera a que termine para exportar.")


def _download(data: bytes, filename: str, media_type: str) -> Response:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-.") or "export"   # header-safe, no path parts
    return Response(data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/crawls/{crawl_id}/export/{kind}.csv")
async def export_csv(crawl_id: int, kind: str):
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    if kind not in report.EXPORT_KINDS:
        raise HTTPException(404, "Tipo de exportación desconocido.")
    return _download(report.csv_export(crawl_id, kind), f"{report.base_name(crawl)}-{kind}.csv", "text/csv; charset=utf-8")


@app.get("/crawls/{crawl_id}/export/report.html")
async def export_html(crawl_id: int, download: int = 1):
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    html = report.html_report(crawl_id)
    if not download:
        return HTMLResponse(html)
    return _download(html.encode("utf-8"), f"{report.base_name(crawl)}.html", "text/html; charset=utf-8")


@app.get("/crawls/{crawl_id}/client-report")
async def client_report(crawl_id: int, client: str = "", brand: str | None = None, contact: str | None = None,
                        color: str | None = None, download: int = 0, summary: int = 0,
                        comment: str = "", notes: int = 0):
    """Plain-language report for the business owner. Branding comes from data/branding.json (the query only
    previews another one: saving it is a POST, so no other website can change it through a link)."""
    from . import clientreport
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    branding = clientreport.load_branding()
    if brand is not None or contact is not None or color is not None:
        branding = clientreport.clean_branding({"brand": brand if brand is not None else branding["brand"],
                                                "contact": contact if contact is not None else branding["contact"],
                                                "color": color or branding["color"]})
    html = clientreport.render(crawl_id, client, branding, for_download=bool(download), summary=bool(summary),
                               comment=comment[:3000], notes=bool(notes))
    if not download:
        return HTMLResponse(html)
    return _download(html.encode("utf-8"), clientreport.filename(crawl, client, summary=bool(summary)), "text/html; charset=utf-8")


@app.post("/crawls/{crawl_id}/client-report/branding")
async def client_report_branding(request: Request, crawl_id: int):
    """«Aplicar y recordar marca»: saves your brand, contact and colour, then shows the report again."""
    from . import clientreport
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    form = await request.form()
    clientreport.save_branding({k: str(form.get(k) or "") for k in ("brand", "contact", "color")})
    client = str(form.get("client") or "")[:200]
    return RedirectResponse(f"/crawls/{crawl_id}/client-report?client={quote(client)}"
                            + ("&summary=1" if form.get("summary") else ""), status_code=303)


@app.get("/crawls/{crawl_id}/client-report/pdf")
async def client_report_pdf(request: Request, crawl_id: int, client: str = "", summary: int = 0,
                            comment: str = "", notes: int = 0):
    from . import clientreport
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    html = clientreport.render(crawl_id, client, for_download=True, summary=bool(summary),
                               comment=comment[:3000], notes=bool(notes))
    html = i18n.translate_html(html, request.cookies.get(i18n.COOKIE, "es"))
    try:
        pdf = await clientreport.to_pdf(html)
    except Exception as exc:  # noqa: BLE001 - no browser available: the HTML version still works
        raise HTTPException(503, f"No se pudo generar el PDF ({str(exc)[:160]}). Usa «Descargar HTML» y "
                                 "luego Imprimir › Guardar como PDF en tu navegador.")
    return _download(pdf, clientreport.filename(crawl, client, "pdf", summary=bool(summary)), "application/pdf")


@app.post("/crawls/{crawl_id}/client-report/logo", response_class=HTMLResponse)
async def client_report_logo(request: Request, crawl_id: int, client: str = ""):
    from . import clientreport
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    form = await request.form()
    error = ""
    if form.get("remove"):
        clientreport.delete_logo()
    else:
        upload = form.get("file")
        data = await upload.read(clientreport.MAX_LOGO_BYTES + 1) if hasattr(upload, "read") else b""
        try:
            clientreport.save_logo(data)
        except ValueError as exc:
            error = str(exc)
    if error:
        return HTMLResponse(clientreport.render(crawl_id, client, error=error), status_code=400)
    from urllib.parse import quote
    return RedirectResponse(f"/crawls/{crawl_id}/client-report?client={quote(client)}", status_code=303)


@app.get("/crawls/{crawl_id}/export/bundle.zip")
async def export_zip(crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    filename, data, _ = report.bundle(crawl_id)
    return _download(data, filename, "application/zip")


@app.get("/crawls/{crawl_id}/export/presentacion.pptx")
async def export_pptx(crawl_id: int):
    """Short deck for the client: cover, summary and one slide per type of error that is always an error."""
    from . import officeexport
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    data = await asyncio.to_thread(officeexport.presentation, crawl_id)
    return _download(data, f"{report.base_name(crawl)}-presentacion.pptx",
                     "application/vnd.openxmlformats-officedocument.presentationml.presentation")


@app.get("/crawls/{crawl_id}/export/errores-excel.zip")
async def export_excel(crawl_id: int):
    """One .xlsx per type of error (only faults that are always errors), all in a ZIP."""
    from . import officeexport
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    data = await asyncio.to_thread(officeexport.excel_zip, crawl_id)
    return _download(data, f"{report.base_name(crawl)}-errores-excel.zip", "application/zip")


@app.get("/health")
async def health_check():
    return {"ok": True, "version": VERSION}


@app.get("/updates")
async def updates_check():
    """«Hay una versión nueva»: the page asks this once it has loaded, unless the notice is turned off."""
    from . import updates
    return await asyncio.to_thread(updates.check)


# --- pause / resume -------------------------------------------------------------------------------
@app.post("/crawls/{crawl_id}/pause")
async def pause_crawl(crawl_id: int):
    get_crawl_or_404(crawl_id)
    crawler.cancel_job(crawl_id, pause=True)
    return RedirectResponse(f"/crawls/{crawl_id}", status_code=303)


@app.post("/crawls/{crawl_id}/resume")
async def resume_crawl(crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    if crawl["status"] in db.ACTIVE_STATUSES or crawler.is_running(crawl_id):
        raise HTTPException(409, "El rastreo ya está en marcha.")
    if not crawl.get("state_json"):
        raise HTTPException(409, "Este rastreo no tiene un punto de reanudación; usa «Volver a rastrear».")
    # marked as queued before the redirect: otherwise the page can still read «paused», render no live status
    # and stay stuck on «En pausa» (without Pausar/Detener) while the crawl carries on in the background
    db.update_crawl(crawl_id, status="queued")
    crawler.start_job(crawl_id, resume=True)
    return RedirectResponse(f"/crawls/{crawl_id}", status_code=303)


# --- detail extras: crawl path, screenshot, waterfall, AI, copyscape ---------------------------------
@app.get("/crawls/{crawl_id}/screenshot/{name}")
async def screenshot(crawl_id: int, name: str):
    if not re.fullmatch(r"[0-9a-f]{20}\.jpg", name):
        raise HTTPException(404, "Captura no encontrada.")
    path = get_settings().screenshots_dir / str(crawl_id) / name
    if not path.is_file():
        raise HTTPException(404, "Captura no encontrada.")
    return FileResponse(path, media_type="image/jpeg")


def _run_coro_in_thread(coro_factory):
    """Playwright needs a loop that supports subprocesses; run it in a private loop."""
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()


@app.post("/crawls/{crawl_id}/waterfall", response_class=HTMLResponse)
async def waterfall_view(request: Request, crawl_id: int, url: str):
    crawl = get_crawl_or_404(crawl_id)
    if not db.get_page(crawl_id, url):
        raise HTTPException(404, "Esa URL no está en este rastreo.")
    from .render import waterfall
    cfg, s = crawl["settings"], get_settings()
    auth = (s.auth_user, s.auth_password) if s.auth_user else None
    from .urlutil import origin
    try:
        data = await asyncio.to_thread(_run_coro_in_thread, lambda: waterfall(
            url, cfg.get("user_agent") or s.user_agent, int(cfg.get("render_timeout") or 30),
            bool(cfg.get("verify_ssl", True)), auth, origin(crawl["start_url"])))
        error = None
    except Exception as exc:  # noqa: BLE001 - browser missing, timeout…
        data, error = None, f"No se pudo cargar la página en el navegador: {str(exc).splitlines()[0][:200]}"
    return templates.TemplateResponse(request, "_waterfall.html", {"request": request, "data": data, "error": error, "url": url})


@app.post("/crawls/{crawl_id}/ai", response_class=HTMLResponse)
async def ai_task(request: Request, crawl_id: int, url: str, task: str):
    get_crawl_or_404(crawl_id)
    page = db.get_page(crawl_id, url)
    if not page:
        raise HTTPException(404, "Esa URL no está en este rastreo.")
    try:
        result, model = await asyncio.to_thread(ai.run_task, task, page)
        db.save_ai(crawl_id, url, task, result, model)
        error = None
    except ai.AIUnavailable as exc:
        error = str(exc)
    return templates.TemplateResponse(request, "_ai_results.html", {
        "request": request, "results": db.load_ai(crawl_id, url), "error": error, "tasks": ai.TASKS})


def _consult_box(request: Request, crawl_id: int, error: str | None = None, url: str = ""):
    from . import consultant
    crawl = db.get_crawl(crawl_id) if crawl_id else None
    if url and not (crawl and db.get_page(crawl["id"], url)):
        url = ""   # only URLs of this audit
    return templates.TemplateResponse(request, "_consultant.html", {
        "request": request, "ai": ai.status(), "crawl": crawl, "error": error, "url": url,
        "msgs": consultant.history(crawl["id"] if crawl else 0)})


@app.get("/consultor", response_class=HTMLResponse)
async def consultant_box(request: Request, crawl: int = 0, url: str = ""):
    return _consult_box(request, crawl, url=url)


@app.post("/consultor", response_class=HTMLResponse)
async def consultant_ask(request: Request, crawl: int = 0):
    from . import consultant
    form = await request.form()
    q = str(form.get("q") or "").strip()[:ai.MAX_CHAT_MESSAGE]
    cid = crawl if crawl and db.get_crawl(crawl) else 0
    url = str(form.get("url") or "").strip()
    if url and not (cid and db.get_page(cid, url)):
        url = ""
    if not q:
        return _consult_box(request, cid, url=url)
    msgs = consultant.history(cid)
    asked = {"role": "user", "content": q, "url": url,
             "model_content": f"[Pregunta sobre la URL {url}]\n{q}" if url else q}
    try:
        ctx = await asyncio.to_thread(consultant.context, cid)
        page = await asyncio.to_thread(consultant.page_context, cid, url) if url else ""
        answer, _model = await asyncio.to_thread(ai.chat, msgs + [asked], ctx, page)
    except ai.AIUnavailable as exc:
        return _consult_box(request, cid, error=str(exc), url=url)
    msgs.append(asked)
    msgs.append({"role": "assistant", "content": answer, "html": consultant.to_html(answer)})
    return _consult_box(request, cid, url=url)


@app.post("/consultor/clear", response_class=HTMLResponse)
async def consultant_clear(request: Request, crawl: int = 0, url: str = ""):
    from . import consultant
    consultant.clear(crawl)
    return _consult_box(request, crawl, url=url)


AI_PROVIDER_INFO = {
    "anthropic": {"name": "Claude (Anthropic)", "icon": "C", "color": "#d97757", "blurb": "Muy bueno redactando en español.",
                  "key_url": "https://console.anthropic.com/settings/keys", "key_hint": "sk-ant-…",
                  "models": ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"), "default": ai.DEFAULT_CLAUDE_MODEL,
                  "privacy": "El texto de cada página se envía a Anthropic y tiene coste según tu plan."},
    "openai": {"name": "OpenAI (ChatGPT)", "icon": "AI", "color": "#10a37f", "blurb": "La IA de ChatGPT.",
               "key_url": "https://platform.openai.com/api-keys", "key_hint": "sk-…",
               "models": ("gpt-4.1-mini", "gpt-4.1"), "default": ai.DEFAULT_OPENAI_MODEL,
               "privacy": "El texto de cada página se envía a OpenAI y tiene coste según tu plan."},
    "gemini": {"name": "Gemini (Google)", "icon": "G", "color": "#7c4dff", "blurb": "Tiene un nivel gratuito.",
               "key_url": "https://aistudio.google.com/api-keys", "key_hint": "AIza…",
               "models": ("gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest"), "default": ai.DEFAULT_GEMINI_MODEL,
               "privacy": "El texto de cada página se envía a Google y tiene coste según tu plan."},
    "groq": {"name": "Groq", "icon": "Gq", "color": "#f55036", "blurb": "Gratis con límites de uso. Muy rápido.",
             "key_url": "https://console.groq.com/keys", "key_hint": "gsk_…",
             "models": ai.GROQ_MODELS, "default": ai.DEFAULT_GROQ_MODEL,
             "privacy": "El texto de cada página se envía a Groq. El plan gratuito no pide tarjeta; si pasas sus límites, toca esperar un poco."},
    "ollama": {"name": "Ollama (en tu equipo)", "icon": "O", "color": "#331942", "blurb": "Gratis y privado, más lento.",
               "models": ("llama3.1", "qwen2.5", "mistral"), "default": "llama3.1",
               "privacy": "Nada sale de tu equipo: el modelo funciona en tu ordenador."},
}


def _ai_setup(request: Request, message: str = "", ok: bool = False, provider: str = ""):
    from . import envfile
    s = get_settings()
    providers = {k: {**v, "key_var": envfile.KEY_VARS.get(k), "saved": envfile.masked(envfile.KEY_VARS[k]) if k in envfile.KEY_VARS else ""}
                 for k, v in AI_PROVIDER_INFO.items()}
    resp = templates.TemplateResponse(request, "_ai_setup.html", {
        "ai": ai.status(), "providers": providers, "current": provider or s.ai_provider or "anthropic",
        "saved_provider": s.ai_provider, "model": s.ai_model, "ollama_url": s.ollama_url, "message": message, "ok": ok})
    if ok:
        resp.headers["HX-Trigger"] = "aiChanged"
    return resp


@app.get("/ai/setup", response_class=HTMLResponse)
async def ai_setup_box(request: Request):
    return _ai_setup(request)


@app.post("/ai/setup", response_class=HTMLResponse)
async def ai_setup_save(request: Request):
    from . import envfile
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost", "testclient"):
        # LETSCRAWL_HOST may expose the app on the network: keys are only written from this computer
        return _ai_setup(request, "Por seguridad, la IA solo se puede configurar desde el propio equipo donde corre LetsCrawlit.")
    form = await request.form()
    provider = str(form.get("provider", ""))
    key = str(form.get(f"key_{provider}", "")).strip()
    model = str(form.get(f"model_{provider}", "")).strip()
    ollama_url = str(form.get("ollama_url", "")).strip().rstrip("/")
    error = envfile.check_ai(provider, key, model, ollama_url)
    if error:
        return _ai_setup(request, error, provider=provider)
    values = {"AI_PROVIDER": provider, "AI_MODEL": model}
    if key and provider in envfile.KEY_VARS:
        values[envfile.KEY_VARS[provider]] = key
    if provider == "ollama" and ollama_url:
        values["OLLAMA_URL"] = ollama_url
    try:
        envfile.set_values(values)
    except (OSError, ValueError) as exc:
        return _ai_setup(request, f"No se pudo guardar en .env: {exc}", provider=provider)
    st = ai.status()
    if not st["enabled"]:
        return _ai_setup(request, st["reason"], provider=provider)
    return _ai_setup(request, f"Listo: IA activada con {AI_PROVIDER_INFO[provider]['name']} ({st['model']}).", ok=True)


@app.post("/ai/setup/test", response_class=HTMLResponse)
async def ai_setup_test(request: Request):
    page = {"url": "https://example.com/", "title": "Prueba", "text_excerpt": "Página de prueba de LetsCrawlit."}
    try:
        answer, model = await asyncio.to_thread(ai.run_task, "custom", page, "Responde solo con la palabra OK.")
    except ai.AIUnavailable as exc:
        return _ai_setup(request, f"La prueba ha fallado: {exc}")
    return _ai_setup(request, f"Conexión correcta con {model}. Respuesta: «{answer.strip()[:60]}».", ok=True)


AI_TASK_CARDS = {  # what each bulk task does, in plain words, with an example of the answer
    "intent": ("", "Intención de búsqueda", "¿La página quiere informar, vender o atraer clientes de la zona?", "transaccional"),
    "meta_rewrite": ("", "Nueva meta description", "Propone una descripción de 140–155 caracteres con llamada a la acción.",
                     "Cerrajero urgente en Madrid 24 h. Llegamos en 20 minutos y sin romper la puerta. ¡Llama ya!"),
    "topic": ("", "Palabra clave y tema", "La palabra clave principal y el grupo de páginas al que pertenece.",
              "cerrajero urgente | servicios de urgencia"),
    "summary": ("", "Resumen para el cliente", "Explica la página en 3 frases, sin tecnicismos.",
                "Esta página presenta el servicio de apertura de puertas…"),
    "translate_en": ("", "Traducir al inglés", "Título y descripción en inglés, manteniendo la intención SEO.",
                     "Title: Emergency locksmith in Madrid…"),
    "custom": ("", "Tu propia instrucción", "Escribe lo que quieras que la IA mire o haga en cada página.",
               "¿Tiene teléfono visible? Sí"),
}


def _bulk_box(request: Request, crawl: dict, message: str = "", count: int | None = None, ok: str = "",
              task: str = "", prompt: str = ""):
    from . import aibulk, aiprompts
    return templates.TemplateResponse(request, "_ai_bulk.html", {
        "crawl": crawl, "job": aibulk.status(crawl["id"]), "message": message, "ok": ok, "count": count, "ai": ai.status(),
        "cards": AI_TASK_CARDS, "saved_prompts": aiprompts.load(), "max_pages": aibulk.MAX_PAGES,
        "task": task or "intent", "prompt": prompt})


@app.post("/crawls/{crawl_id}/ai/prompts", response_class=HTMLResponse)
async def ai_prompt_save(request: Request, crawl_id: int):
    from . import aiprompts
    crawl = get_crawl_or_404(crawl_id)
    form = await request.form()
    count = len(_bulk_urls(crawl, str(form.get("tab") or "internal"), str(form.get("f") or ""), str(form.get("q") or "")))
    prompt = str(form.get("prompt") or "")
    try:
        item = aiprompts.add(str(form.get("prompt_name") or ""), prompt)
    except (ValueError, OSError) as exc:
        return _bulk_box(request, crawl, str(exc), count, task="custom", prompt=prompt)
    return _bulk_box(request, crawl, count=count, ok=f"Guardada como «{item['name']}». La tienes en las tarjetas para cualquier auditoría.",
                     task="saved:" + item["id"])


@app.post("/crawls/{crawl_id}/ai/prompts/{pid}/delete", response_class=HTMLResponse)
async def ai_prompt_delete(request: Request, crawl_id: int, pid: str):
    from . import aiprompts
    crawl = get_crawl_or_404(crawl_id)
    form = await request.form()
    aiprompts.delete(pid)
    count = len(_bulk_urls(crawl, str(form.get("tab") or "internal"), str(form.get("f") or ""), str(form.get("q") or "")))
    return _bulk_box(request, crawl, count=count)


@app.get("/crawls/{crawl_id}/ai/bulk", response_class=HTMLResponse)
async def ai_bulk_box(request: Request, crawl_id: int, tab: str = "internal", f: str = "", q: str = ""):
    crawl = get_crawl_or_404(crawl_id)
    return _bulk_box(request, crawl, count=len(_bulk_urls(crawl, tab, f, q)))


def _bulk_urls(crawl: dict, tab: str, f: str, q: str) -> list[str]:
    t = views.tab_by_key(crawl, tab)
    if not t or t.source != "pages":
        t = views.tab_by_key(crawl, "internal")
    return [r["url"] for r in views.query(views.workbench(crawl), t, f, q) if r.get("html200")]


@app.post("/crawls/{crawl_id}/ai/bulk", response_class=HTMLResponse)
async def ai_bulk_start(request: Request, crawl_id: int):
    from . import aibulk
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    form = await request.form()
    urls = _bulk_urls(crawl, str(form.get("tab") or "internal"), str(form.get("f") or ""), str(form.get("q") or ""))
    from . import aiprompts
    task, prompt, label = str(form.get("task") or ""), str(form.get("prompt") or "")[:ai.MAX_CUSTOM_PROMPT], ""
    if task.startswith("saved:"):
        saved = aiprompts.get(task[6:])
        if not saved:
            return _bulk_box(request, crawl, "Esa instrucción guardada ya no existe.", len(urls))
        task, prompt, label = "custom", saved["prompt"], saved["name"]
    try:
        aibulk.start(crawl_id, task, urls, prompt, label)
    except ai.AIUnavailable as exc:
        return _bulk_box(request, crawl, str(exc), len(urls), task=str(form.get("task") or ""), prompt=prompt)
    return _bulk_box(request, crawl)


@app.post("/crawls/{crawl_id}/ai/bulk/cancel", response_class=HTMLResponse)
async def ai_bulk_cancel(request: Request, crawl_id: int):
    from . import aibulk
    crawl = get_crawl_or_404(crawl_id)
    aibulk.cancel(crawl_id)
    return _bulk_box(request, crawl, "Deteniendo…")


@app.post("/crawls/{crawl_id}/copyscape", response_class=HTMLResponse)
async def copyscape(request: Request, crawl_id: int):
    get_crawl_or_404(crawl_id)
    url = str((await request.form()).get("url") or request.query_params.get("url") or "")
    if not db.get_page(crawl_id, url):
        raise HTTPException(400, "Solo se pueden comprobar URLs de este rastreo.")
    try:
        results, error = await asyncio.to_thread(integrations.copyscape_search, url), None
    except RuntimeError as exc:
        results, error = [], str(exc)
    return templates.TemplateResponse(request, "_copyscape.html", {"request": request, "results": results, "error": error, "url": url})


# --- insights: health, priorities, link suggestions, clusters, near duplicates, plagiarism ------------------
@app.get("/crawls/{crawl_id}/insights", response_class=HTMLResponse)
async def insights_page(request: Request, crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    pages = db.load_pages(crawl_id)
    h = insights.health(pages, db.load_issues(crawl_id), db.load_links(crawl_id), crawl["start_url"])
    near = db.load_insight(crawl_id, "near_dups", {})
    return render(request, "insights.html", {
        "crawl": crawl, "health": h, "suggestions": db.load_insight(crawl_id, "link_suggestions", [])[:200],
        "clusters": db.load_insight(crawl_id, "clusters", []), "copyscape": bool(get_settings().copyscape_user),
        "near": sorted(([u, *v] for u, v in near.items()), key=lambda r: -r[2])[:300]})


@app.post("/crawls/{crawl_id}/plagiarism", response_class=HTMLResponse)
async def plagiarism(request: Request, crawl_id: int):
    get_crawl_or_404(crawl_id)
    form = await request.form()
    text = str(form.get("text") or "")[:200000]
    docs = {p["url"]: insights.page_text(p) for p in db.load_pages(crawl_id) if p.get("indexable")}
    results = textsim.plagiarism(text, docs) if len(text.split()) >= 15 else None
    return templates.TemplateResponse(request, "_plagiarism.html", {"request": request, "results": results})


# --- visualisations -------------------------------------------------------------------------------------
@app.get("/crawls/{crawl_id}/visual", response_class=HTMLResponse)
async def visual_page(request: Request, crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    return render(request, "visual.html", {"crawl": crawl})


@app.get("/crawls/{crawl_id}/graph.json")
async def graph_json(crawl_id: int, limit: int = 1500):
    crawl = get_crawl_or_404(crawl_id)
    return JSONResponse(views.graph(views.workbench(crawl), db.load_links(crawl_id), crawl["start_url"], min(limit, 3000)))


# --- compare & trends -------------------------------------------------------------------------------------
@app.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request, a: int = 0, b: int = 0):
    crawls = [c for c in db.list_crawls(300) if c["status"] not in db.ACTIVE_STATUSES]
    result = None
    if a and b:
        if a == b:
            raise HTTPException(400, "Elige dos auditorías distintas.")
        get_crawl_or_404(a), get_crawl_or_404(b)
        result = history.compare(a, b)
    return render(request, "compare.html", {"crawls": crawls, "a": a, "b": b, "result": result})


@app.get("/manual", response_class=HTMLResponse)
async def user_manual(request: Request):
    """Ayuda › Manual de uso: the basics. The full guides live on letscrawlit.com."""
    return render(request, "manual.html", {})


@app.get("/manual/{path:path}")
async def old_manual_pages(path: str):
    return RedirectResponse("/manual", status_code=301)  # links from 0.3.0 betas


@app.get("/trends", response_class=HTMLResponse)
async def trends_index(request: Request):
    sites = history.sites()
    if sites:  # straight to the most recently audited site instead of an empty page with only buttons
        return RedirectResponse(f"/trends/{quote(sites[0]['site'], safe='')}", status_code=303)
    return render(request, "trends.html", {"sites": sites, "trend": None})


@app.get("/trends/{site}.csv")
async def trends_csv(site: str):
    if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", site):
        raise HTTPException(404, "Sitio no válido.")
    from .urlutil import safe_filename
    return _download(await asyncio.to_thread(history.trends_csv, site), safe_filename(f"tendencias-{site}") + ".csv",
                     "text/csv; charset=utf-8")


@app.get("/trends/{site}", response_class=HTMLResponse)
async def trends_site(request: Request, site: str, metric: str = "pages", scheduled: str = ""):
    if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", site):
        raise HTTPException(404, "Sitio no válido.")
    t = await asyncio.to_thread(history.trends, site)
    if not t["points"]:  # e.g. only a paused or failed audit: explain instead of an error page
        return render(request, "trends.html", {"sites": history.sites(), "trend": None, "missing": site})
    metric = metric if metric in dict(history.TREND_SERIES) else "pages"
    from .cli import schedule_command
    start_url = db.get_crawl(t["points"][-1]["id"])["start_url"]
    return render(request, "trends.html", {
        "sites": history.sites(), "trend": t, "svg_line": history.svg_line, "metric": metric,
        "metric_label": dict(history.TREND_SERIES)[metric], "chart": history.chart(t["points"], metric),
        "lower_better": metric in history.LOWER_IS_BETTER, "start_url": start_url, "scheduled": scheduled,
        "schedule_preview": subprocess_list2cmdline(schedule_command(start_url, "03:00", True, "MON", None)),
        "is_windows": sys.platform == "win32"})


@app.post("/trends/{site}/schedule")
async def trends_schedule(request: Request, site: str):
    """Create a Windows scheduled task that audits the site periodically (needs the permission checkbox)."""
    if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", site):
        raise HTTPException(404, "Sitio no válido.")
    form = await request.form()
    if form.get("ack") != "yes":
        raise HTTPException(400, "Confirma que el sitio es tuyo o que tienes permiso para rastrearlo periódicamente.")
    when = str(form.get("time") or "03:00")
    freq = str(form.get("freq") or "weekly")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", when) or freq not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, "Hora o frecuencia no válidas.")
    points = history.trends(site)["points"]
    if not points:
        raise HTTPException(404)
    start_url = db.get_crawl(points[-1]["id"])["start_url"]
    if sys.platform != "win32":
        raise HTTPException(400, "En macOS/Linux usa cron: py run.py schedule " + start_url + " --i-own-this-site")
    from .cli import schedule_command
    cmd = schedule_command(start_url, when, freq == "daily", "MON" if freq == "weekly" else None, 1 if freq == "monthly" else None)
    import subprocess
    proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
    status = "ok" if proc.returncode == 0 else "error"
    return RedirectResponse(f"/trends/{site}?scheduled={status}", status_code=303)


# --- analytics import ---------------------------------------------------------------------------------
@app.post("/crawls/{crawl_id}/analytics")
async def import_analytics(request: Request, crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    form = await request.form()
    up = form.get("file")
    if not up or not hasattr(up, "read") or not up.filename:
        raise HTTPException(400, "Elige el CSV exportado de Search Console o Analytics.")
    blob = (await up.read())[:50 * 1024 * 1024]
    try:
        source, rows = integrations.parse_metrics_csv(blob, crawl["start_url"])
    except ValueError as exc:
        raise HTTPException(400, f"No se pudo leer el CSV: {exc}")
    db.save_analytics(crawl_id, source, rows)
    await asyncio.to_thread(crawler.reanalyze, crawl_id)
    return RedirectResponse(f"/crawls/{crawl_id}?tab=analytics", status_code=303)


# --- Google Search Console / Analytics 4 (OAuth) -----------------------------------------------------------
@app.get("/integrations/google/connect")
async def google_connect(request: Request, back: str = "/"):
    from . import google
    if not google.configured():
        raise HTTPException(400, "Falta GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en .env.")
    s = get_settings()
    redirect = f"http://127.0.0.1:{s.port}/integrations/google/callback"
    request.app.state.google_back = _local_path(back)
    return RedirectResponse(google.auth_url(redirect), status_code=303)


@app.get("/integrations/google/callback", response_class=HTMLResponse)
async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    from . import google
    if error:
        return render(request, "error.html", {"code": 400, "message": f"Google no concedió el permiso ({error})."}, status_code=400)
    try:
        await asyncio.to_thread(google.finish, state, code)
    except google.GoogleError as exc:
        return render(request, "error.html", {"code": 400, "message": str(exc)}, status_code=400)
    return RedirectResponse(getattr(request.app.state, "google_back", "/") or "/", status_code=303)


@app.post("/integrations/google/disconnect")
async def google_disconnect(back: str = "/"):
    from . import google
    await asyncio.to_thread(google.disconnect)
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/", status_code=303)


@app.get("/crawls/{crawl_id}/google", response_class=HTMLResponse)
async def google_panel(request: Request, crawl_id: int):
    """Lazy panel in «Importar datos»: lists your Search Console properties and GA4 properties."""
    from . import google
    crawl = get_crawl_or_404(crawl_id)
    ctx = {"crawl": crawl, "g": google.status(), "sites": [], "props": [], "error": ""}
    if ctx["g"]["state"] == "connected":
        try:
            ctx["sites"] = await asyncio.to_thread(google.gsc_sites)
            ctx["props"] = await asyncio.to_thread(google.ga4_properties)
        except google.GoogleError as exc:
            ctx["error"] = str(exc)
    from .urlutil import site_key
    key = site_key(crawl["start_url"])
    ctx["best_site"] = next((s for s in ctx["sites"] if key in s), "")
    return render(request, "_google.html", ctx)


@app.post("/crawls/{crawl_id}/google/{kind}")
async def google_import(request: Request, crawl_id: int, kind: str):
    from . import google
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    form = await request.form()
    days = int(form.get("days") or 28) if str(form.get("days") or "28").isdigit() else 28
    days = max(1, min(480, days))
    try:
        if kind == "gsc":
            rows = await asyncio.to_thread(google.gsc_pages, str(form.get("site") or ""), days)
            source = "gsc"
        elif kind == "ga4":
            rows = await asyncio.to_thread(google.ga4_pages, str(form.get("property") or ""), crawl["start_url"], days)
            source = "ga"
        else:
            raise HTTPException(404)
    except google.GoogleError as exc:
        raise HTTPException(400, str(exc))
    db.save_analytics(crawl_id, source, rows)
    await asyncio.to_thread(crawler.reanalyze, crawl_id)
    return RedirectResponse(f"/crawls/{crawl_id}?tab=analytics", status_code=303)


# --- extra exports & crawl files --------------------------------------------------------------------------
@app.get("/crawls/{crawl_id}/export/bulk/{kind}.csv")
async def export_bulk(crawl_id: int, kind: str):
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    if kind not in report.BULK_EXPORTS:
        raise HTTPException(404, "Exportación masiva desconocida.")
    return _download(report.bulk_export(crawl_id, kind), f"{report.base_name(crawl)}-{kind}.csv", "text/csv; charset=utf-8")


@app.get("/crawls/{crawl_id}/export/sitemap.xml")
async def export_sitemap(crawl_id: int, images: int = 0):
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    files = report.sitemap_xml(crawl_id, bool(images))
    if len(files) == 1:
        return _download(files[0][1], files[0][0], "application/xml")
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return _download(buf.getvalue(), f"{report.base_name(crawl)}-sitemaps.zip", "application/zip")


@app.get("/crawls/{crawl_id}/export/robots.txt")
async def export_robots(crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    snap = next(iter(crawl["robots"].values()), {}) if crawl["robots"] else {}
    if not snap.get("text"):
        raise HTTPException(404, "No se guardó ningún robots.txt en este rastreo (el sitio no tenía o no respondió).")
    return _download(snap["text"].encode("utf-8"), "robots.txt", "text/plain; charset=utf-8")


@app.get("/crawls/{crawl_id}/export/crawl.seocrawl")
async def export_crawlfile(crawl_id: int):
    crawl = get_crawl_or_404(crawl_id)
    _require_finished(crawl)
    return _download(crawlfile.export_crawl(crawl_id), f"{report.base_name(crawl)}.seocrawl", "application/gzip")


@app.post("/import")
async def import_crawlfile(request: Request):
    form = await request.form()
    up = form.get("file")
    if not up or not hasattr(up, "read") or not up.filename:
        raise HTTPException(400, "Elige un archivo .seocrawl.")
    try:
        new_id = crawlfile.import_crawl(await up.read())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse(f"/crawls/{new_id}", status_code=303)


# --- visual extractor (point and click) -------------------------------------------------------------
@app.post("/tools/pick")
async def pick_page(request: Request):
    """One GET of the page typed in the form (robots.txt respected), returned without scripts so the picker
    can show it safely inside a sandboxed frame."""
    import httpx
    from bs4 import BeautifulSoup
    from .robots import RobotsRules
    from .urlutil import origin
    form = await request.form()
    url, err = check_url(str(form.get("pick_url") or form.get("url") or ""))
    if err:
        return {"error": "Escribe arriba la URL de una página de tu sitio (por ejemplo, una ficha de producto)."}
    s = get_settings()
    headers = {"User-Agent": s.user_agent}
    token = s.user_agent.split("/")[0].split()[0].lower()
    try:
        # redirects are followed by hand: every address on the way is checked against its own robots.txt
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers=headers) as client:
            rules_by_origin: dict = {}
            for _hop in range(6):
                o = origin(url)
                if o not in rules_by_origin:
                    robots, rbody = await safehttp.get_capped(client, o + "/robots.txt", safehttp.ROBOTS_MAX,
                                                              follow_redirects=True)
                    rules_by_origin[o] = (RobotsRules.parse(o, safehttp.text_of(robots, rbody)) if robots.status_code == 200
                                          else RobotsRules.allow_all(o, ""))
                if not rules_by_origin[o].allowed(url, token):
                    return {"error": "robots.txt no permite descargar esa URL."}
                r, content = await safehttp.get_capped(client, url, 5 * 1024 * 1024)
                if not (r.is_redirect and r.headers.get("location")):
                    break
                url, err = check_url(urljoin(str(r.url), r.headers["location"]))
                if err:
                    return {"error": "La página redirige a una dirección no válida."}
            else:
                return {"error": "Demasiadas redirecciones."}
    except httpx.HTTPError as exc:
        return {"error": f"No se pudo descargar la página ({type(exc).__name__})."}
    if "html" not in r.headers.get("content-type", ""):
        return {"error": f"Esa URL no es una página HTML (responde {r.status_code}, {r.headers.get('content-type', 'sin tipo')})."}
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "noscript", "iframe", "object", "embed", "frame", "frameset", "meta"]):
        if tag.name == "meta" and (tag.get("http-equiv") or "").lower() != "refresh":
            continue
        tag.decompose()
    for el in soup.find_all(True):
        for attr in [a for a in el.attrs if a.lower().startswith("on") or (a.lower() in ("href", "src", "action") and str(el.get(a)).strip().lower().startswith("javascript:"))]:
            del el[attr]
    # absolute addresses instead of a <base> tag (the app's security policy only allows its own base URL)
    page_url = str(r.url)
    for tag in soup(["base"]):
        tag.decompose()
    for el in soup.find_all(True):
        for attr in ("href", "src", "poster"):
            v = el.get(attr)
            if isinstance(v, str) and v.strip() and not v.strip().startswith(("#", "data:")):
                el[attr] = urljoin(page_url, v.strip())
        if isinstance(el.get("srcset"), str):
            el["srcset"] = ", ".join(" ".join([urljoin(page_url, p.split()[0]), *p.split()[1:]])
                                     for p in el["srcset"].split(",") if p.strip())
    return {"html": str(soup), "url": page_url, "status": r.status_code}


# --- shared spelling dictionary -------------------------------------------------------------------
@app.post("/dictionary", response_class=HTMLResponse)
async def dictionary_save(request: Request):
    from . import dictionary
    form = await request.form()
    n = dictionary.replace(str(form.get("words") or "")[:500000])
    return HTMLResponse(f'<span class="ok-msg">Guardado: {n} palabra(s) en {html_escape(str(dictionary.path()))}</span>')


@app.post("/dictionary/add", response_class=HTMLResponse)
async def dictionary_add(request: Request):
    from . import dictionary
    form = await request.form()
    word = str(form.get("word") or "")
    if not dictionary.clean(word):
        return HTMLResponse('<span class="err">Palabra no válida</span>', status_code=400)
    dictionary.add(word)
    return HTMLResponse(f'<span class="badge st-completed" title="Añadida al diccionario">{html_escape(dictionary.clean(word)[0])} (añadida)</span>')


# --- recorded login sessions ----------------------------------------------------------------------
def _session_box(request: Request, message: str = "", status_code: int = 200):
    rec = sessions.recorder.status()
    if message:
        rec = {**rec, "message": message, "status": "error"}
    return templates.TemplateResponse(request, "_session.html", {"rec": rec, "saved": sessions.list_saved()},
                                      status_code=status_code)


@app.get("/sessions/status", response_class=HTMLResponse)
async def session_status(request: Request):
    return _session_box(request)


@app.post("/sessions/record", response_class=HTMLResponse)
async def session_record(request: Request):
    form = await request.form()
    url, err = check_url(str(form.get("url") or ""))
    if err:
        return _session_box(request, "Escribe primero arriba la URL de tu sitio (la página de login o la portada).")
    try:
        sessions.recorder.start(url, get_settings().user_agent)
    except RuntimeError as exc:
        return _session_box(request, str(exc))
    return _session_box(request)


@app.post("/sessions/save", response_class=HTMLResponse)
async def session_save(request: Request):
    sessions.recorder.save()
    for _ in range(50):  # the browser thread writes the file; wait up to ~5 s
        if sessions.recorder.status()["status"] not in ("recording", "starting"):
            break
        await asyncio.sleep(0.1)
    return _session_box(request)


@app.post("/sessions/cancel", response_class=HTMLResponse)
async def session_cancel(request: Request):
    sessions.recorder.cancel()
    await asyncio.sleep(0.5)
    return _session_box(request)


@app.post("/sessions/delete", response_class=HTMLResponse)
async def session_delete(request: Request):
    form = await request.form()
    sessions.delete(str(form.get("site") or ""))
    return _session_box(request)


@app.post("/tools/fetch-robots", response_class=HTMLResponse)
async def fetch_robots(request: Request):
    """Load the live robots.txt of the site typed in the form into the editor (one GET, nothing else)."""
    form = await request.form()
    url, err = check_url(str(form.get("url") or ""))
    text, message = str(form.get("custom_robots") or ""), None
    if err:
        message = "Escribe primero la URL del sitio arriba."
    else:
        from .urlutil import origin
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                         headers={"User-Agent": get_settings().user_agent}) as client:
                r, body = await safehttp.get_capped(client, origin(url) + "/robots.txt", safehttp.ROBOTS_MAX)
            if r.status_code == 200:
                text, message = safehttp.text_of(r, body)[:20000], f"Cargado el robots.txt actual de {origin(url)}. Edítalo y pruébalo."
            else:
                message = f"El sitio respondió HTTP {r.status_code} a /robots.txt."
        except httpx.HTTPError as exc:
            message = f"No se pudo descargar: {type(exc).__name__}"
    return templates.TemplateResponse(request, "_robots_editor.html", {"request": request, "text": text, "message": message})
