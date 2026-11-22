"""Polite crawler. Runs in its own thread with its own event loop.

Modes:
  spider - start at one URL and follow internal links (plus sitemap URLs at the end).
  list   - fetch only the URLs supplied (sitemap .xml lines are expanded); links are recorded, not followed.

Politeness: robots.txt (live, plus an optional stricter custom one), nofollow, a minimum interval between
request starts shared by all workers (1-5), Crawl-delay, URL cap. Progress is checkpointed so a paused or
interrupted crawl can be resumed.
"""
from __future__ import annotations

import asyncio
import gzip
import zlib
import hashlib
import json
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from . import db, safehttp
from .analysis import analyze, compute_indexability, summarize
from .checks import spelling
from .config import Settings, get_settings
from .crawlconfig import header_dict
from .extract import is_nofollow, parse_html, text_stats
from .render import Renderer, form_login
from .robots import RobotsMatcher, RobotsRules
from .urlutil import normalize, origin, site_key

MAX_BODY = 5 * 1024 * 1024
MAX_SITEMAPS = 25
MAX_SITEMAP_URLS = 50000
CHECKPOINT_EVERY = 20
SLOW_RETEST_MS = 1000  # responses slower than this are measured again on their own
# Parameters that never change the page: cache busters and trackers. Always removed from URLs.
ALWAYS_REMOVED_PARAMS = frozenset({"wpr_t", "ver", "_gl", "fbclid", "gclid", "msclkid",
                                   "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"})
# Links that change a shopping cart (or a wishlist): never followed, a crawler must not add or remove products.
CART_PARAMS = frozenset({"add-to-cart", "add_to_cart", "remove_item", "removed_item", "undo_item", "empty-cart",
                         "empty_cart", "add-to-wishlist", "add_to_wishlist", "remove_from_wishlist", "wc-ajax"})
RESOURCE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".css", ".js", ".mjs", ".woff",
                ".woff2", ".ttf", ".otf", ".eot", ".mp4", ".webm", ".mp3", ".ogg", ".mov", ".m4a", ".wav", ".zip")


def is_cart_url(url: str) -> bool:
    return any(k.lower() in CART_PARAMS for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True))


def looks_like_resource(url: str) -> bool:
    """Images, CSS, JS, fonts, media: they don't count towards the page limit (they are checked with HEAD)."""
    return urlsplit(url).path.lower().endswith(RESOURCE_EXT)
MAX_RETESTS = 100
HTML_TYPES = ("text/html", "application/xhtml+xml")
SKIP_HEADERS = {"set-cookie", "cookie", "authorization", "proxy-authorization"}


def _lines(text: str | None) -> list[str]:
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def _cookie_info(values: list[str]) -> list[dict]:
    """Set-Cookie headers -> name and security attributes (values are never stored)."""
    out = []
    for v in values[:50]:
        parts = [p.strip() for p in v.split(";")]
        name = parts[0].split("=", 1)[0] if parts and "=" in parts[0] else ""
        attrs = {p.split("=", 1)[0].lower(): (p.split("=", 1)[1] if "=" in p else True) for p in parts[1:] if p}
        if name:
            out.append({"name": name[:100], "secure": "secure" in attrs, "httponly": "httponly" in attrs,
                        "samesite": str(attrs.get("samesite") or "")[:10], "expires": bool(attrs.get("expires") or attrs.get("max-age"))})
    return out


def _is_html(ctype: str | None) -> bool:
    return bool(ctype) and ctype.split(";")[0].strip().lower() in HTML_TYPES


def _headers(resp: httpx.Response) -> dict:
    out = {}
    for k, v in resp.headers.multi_items():
        k = k.lower()
        if k in SKIP_HEADERS or len(out) >= 60:
            continue
        out[k] = (out[k] + ", " + v if k in out else v)[:500]
    return out


class MappedTransport(httpx.AsyncHTTPTransport):
    """Manual DNS: connect to a fixed IP for a host while keeping Host header and TLS SNI (migrations)."""

    def __init__(self, dns_map: dict[str, str], **kwargs):
        super().__init__(**kwargs)
        self.dns_map = dns_map

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        ip = self.dns_map.get(host)
        if ip:
            request.url = request.url.copy_with(host=ip)
            if request.url.scheme == "https":
                request.extensions = {**request.extensions, "sni_hostname": host}
        return await super().handle_async_request(request)


def parse_dns_map(text: str | None) -> dict[str, str]:
    out = {}
    for line in _lines(text):
        parts = line.split()
        if len(parts) == 2:
            out[parts[0].lower()] = parts[1]
    return out


def build_client(cfg: dict, settings: Settings) -> httpx.AsyncClient:
    headers = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8", **header_dict(cfg),
               "User-Agent": cfg.get("user_agent") or settings.user_agent}
    verify = bool(cfg.get("verify_ssl", True))
    proxy = settings.proxy if cfg.get("use_proxy") and settings.proxy else None
    transport = MappedTransport(parse_dns_map(cfg.get("dns_map")), verify=verify, proxy=proxy)
    return httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(float(cfg.get("timeout") or 20)),
                             follow_redirects=False, transport=transport)


class Crawler:
    def __init__(self, crawl_id: int, cfg: dict, client: httpx.AsyncClient, settings: Settings,
                 cancel: threading.Event):
        self.crawl_id = crawl_id
        self.cfg = cfg
        self.client = client
        self.settings = settings
        self.cancel = cancel
        self.mode = cfg.get("mode", "spider")
        self.start_url = cfg["start_url"]
        self.list_urls = cfg.get("urls") or []
        self.max_urls = int(cfg["max_urls"])
        self.delay = float(cfg["delay"])
        self.threads = max(1, min(5, int(cfg.get("threads") or 1)))
        self.ua = cfg.get("user_agent") or settings.user_agent
        self.token = cfg.get("robots_token") or self.ua
        self.custom_robots = RobotsMatcher(cfg["custom_robots"]) if (cfg.get("custom_robots") or "").strip() else None
        self.include = [re.compile(p) for p in _lines(cfg.get("include"))]
        self.exclude = [re.compile(p) for p in _lines(cfg.get("exclude"))]
        self.remove_params = {p.lower() for p in _lines(cfg.get("remove_params"))} | ALWAYS_REMOVED_PARAMS
        self.max_depth = int(cfg.get("max_depth") or 0)
        self.max_query_params = int(cfg.get("max_query_params") or 0)
        self.retries = int(cfg.get("retries_5xx") or 0)
        from . import dictionary
        self.custom_words = set(re.findall(r"[^\s,;]+", (cfg.get("custom_dictionary") or "").lower())) | dictionary.words()
        self.scope = {site_key(u) for u in [self.start_url, *self.list_urls]}
        self.auth = (settings.auth_user, settings.auth_password) if settings.auth_user else None
        self.robots: dict[str, RobotsRules] = {}
        self.queue: deque[tuple[str, int, str]] = deque()
        self.inflight: dict[str, tuple[str, int, str]] = {}
        self.seen: set[str] = set()
        self.pages_seen = 0  # URLs in seen that can be pages: what the URL limit counts
        self.sitemap_pending: list[str] = []
        self.sitemap_urls: set[str] = set()
        self._sitemap_fetches = 0  # workers fetching linked sitemaps (the rest wait for them instead of exiting)
        self.external_targets: set[str] = set()
        self.resources: set[str] = set()
        self.outside: set[str] = set()  # internal URLs outside the start folder, status-checked only
        self.skipped_rules = 0
        self.cap_hit = False
        self.notes: list[str] = []
        self.renderer: Renderer | None = None
        self.render_failures = 0
        self.session_cookies: list[dict] = []
        self.storage_origins: list[dict] = []  # localStorage from a recorded login (SPA tokens)
        self._last_request = 0.0
        self._turn_lock = asyncio.Lock()
        self.done = 0
        self.slow: list[tuple[str, str]] = []
        self.shots_dir = settings.screenshots_dir / str(crawl_id)
        # options from the configuration panel (old crawls without them get the defaults)
        from .options import DEFAULTS as OPT, path_limits
        self.opt = {k: cfg.get(k, v) for k, v in OPT.items()}
        rate = int(self.opt["max_urls_per_second"] or 0)
        if rate:
            self.delay = max(self.delay, 1.0 / rate)
        path = urlsplit(self.start_url).path
        self.start_folder = path if path.endswith("/") else (path.rsplit("/", 1)[0] + "/")
        self.per_depth: dict[int, int] = defaultdict(int)
        self.per_host: dict[str, int] = defaultdict(int)
        self.path_limits = path_limits(cfg)
        self.per_path: dict[str, int] = defaultdict(int)
        self.hops: dict[str, int] = {}
        self.hsts_hosts: set[str] = set()
        self.linked_sitemaps: set[str] = set()
        self.max_body = int(self.opt["max_page_size_kb"]) * 1024
        self.limit_notes: dict[str, int] = defaultdict(int)
        self.html_dir = settings.data_dir / "html" / str(crawl_id)
        self._local_start: bool | None = None
        self._private_cache: dict[str, bool] = {}

    # --- helpers ------------------------------------------------------------
    def in_scope(self, url: str | None) -> bool:
        if not url:
            return False
        key = site_key(url)
        if key in self.scope:
            return True
        return bool(self.opt.get("crawl_subdomains")) and any(key.endswith("." + s) for s in self.scope)

    def rewrite(self, url: str | None) -> str | None:
        """URL rewriting from the config: drop listed (or all) query parameters; HSTS upgrade to https."""
        if url and self.hsts_hosts and url.startswith("http://") and (urlsplit(url).hostname or "") in self.hsts_hosts:
            url = "https://" + url[len("http://"):]
        if not url or not (self.remove_params or self.cfg.get("strip_all_params")) or "?" not in url:
            return url
        parts = urlsplit(url)
        if self.cfg.get("strip_all_params"):
            query = ""
        else:
            query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                               if k.lower() not in self.remove_params])
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    def allowed_by_rules(self, url: str) -> bool:
        if self.include and not any(rx.search(url) for rx in self.include):
            return False
        if any(rx.search(url) for rx in self.exclude):
            return False
        if self.max_query_params and len(parse_qsl(urlsplit(url).query, keep_blank_values=True)) > self.max_query_params:
            return False
        return True

    def robots_allowed(self, rules: RobotsRules, url: str) -> tuple[bool, str | None]:
        """Live robots.txt always applies; a custom robots.txt can only add restrictions."""
        if not rules.allowed(url, self.token):
            return False, rules.reason or None
        if self.custom_robots and self.in_scope(url):
            ok, rule = self.custom_robots.verdict(url, self.token)
            if not ok:
                return False, f"robots.txt personalizado: {rule.label() if rule else ''}"
        return True, None

    def _auth(self, url: str):
        """Staging credentials go only to the audited site, never to third parties."""
        return self.auth if self.auth and self.in_scope(url) else None

    async def _wait_turn(self, extra: float = 0.0) -> None:
        """Minimum interval between request starts, shared by every worker."""
        async with self._turn_lock:
            delay = max(self.delay, extra)
            elapsed = time.monotonic() - self._last_request
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request = time.monotonic()

    async def robots_for(self, url: str) -> RobotsRules:
        key = origin(url)
        if key in self.robots:
            return self.robots[key]
        await self._wait_turn()
        try:
            resp, body = await safehttp.get_capped(self.client, key + "/robots.txt", safehttp.ROBOTS_MAX,
                                                   follow_redirects=True, auth=self._auth(url))
            if resp.status_code == 200:
                rules = RobotsRules.parse(key, safehttp.text_of(resp, body))
            elif 400 <= resp.status_code < 500:
                rules = RobotsRules.allow_all(key, f"robots.txt devolvió HTTP {resp.status_code} (sin restricciones)")
            else:
                rules = RobotsRules.disallow_all(key, f"robots.txt devolvió HTTP {resp.status_code}")
        except httpx.HTTPError as exc:
            rules = RobotsRules.disallow_all(key, f"robots.txt no accesible ({type(exc).__name__}: {str(exc)[:120]})")
        self.robots.setdefault(key, rules)
        if self.in_scope(url):
            db.update_crawl(self.crawl_id, robots={k: v.snapshot() for k, v in self.robots.items()
                                                   if self.in_scope(k + "/")})
        return self.robots[key]

    def _limited(self, url: str, depth: int, source: str, force: bool) -> str | None:
        """Which limit (if any) keeps this URL out. `force` = always-follow redirects/canonicals."""
        o = self.opt
        if len(url) > int(o["max_url_length"]):
            return "longitud máxima de URL"
        if source in ("start", "list"):
            return None
        path = urlsplit(url).path
        if not force:
            if not o["crawl_outside_folder"] and not path.startswith(self.start_folder) and site_key(url) == site_key(self.start_url):
                return "fuera de la carpeta inicial"
            if self.max_depth and depth > self.max_depth:
                return "profundidad máxima"
            folders = len([s for s in path.split("/") if s]) - (0 if path.endswith("/") else 1)
            if o["max_folder_depth"] and folders > int(o["max_folder_depth"]):
                return "máximo de carpetas"
        if o["max_urls_per_depth"] and depth >= 0 and self.per_depth[depth] >= int(o["max_urls_per_depth"]):
            return "máximo de URLs por nivel"
        host = urlsplit(url).hostname or ""
        if o["max_urls_per_subdomain"] and self.per_host[host] >= int(o["max_urls_per_subdomain"]):
            return "máximo de URLs por subdominio"
        for rx, n, pattern in self.path_limits:
            if rx.search(url) and self.per_path[pattern] >= n:
                return f"límite del patrón {pattern}"
        return None

    def enqueue(self, url: str | None, depth: int, source: str, force: bool = False) -> None:
        url = self.rewrite(url)
        if not url or url in self.seen or not self.in_scope(url):
            return
        if is_cart_url(url):  # «añadir al carrito» and the like: never followed
            self.limit_notes["enlace que cambia el carrito (no se sigue)"] += 1
            self.seen.add(url)
            return
        if source not in ("start", "list") and not force and not self.allowed_by_rules(url):
            self.skipped_rules += 1
            self.seen.add(url)  # count once
            return
        why = self._limited(url, depth, source, force)
        if why:
            self.limit_notes[why] += 1
            if why == "fuera de la carpeta inicial" and self.opt["check_outside_folder"]:
                self.outside.add(url)  # not crawled, but its status is checked at the end
            return
        page_like = not looks_like_resource(url)  # the limit counts pages, not images or CSS
        if self.max_urls and page_like and self.pages_seen >= self.max_urls:
            self.cap_hit = True
            return
        self.seen.add(url)
        self.pages_seen += page_like
        if depth >= 0:
            self.per_depth[depth] += 1
        self.per_host[urlsplit(url).hostname or ""] += 1
        for rx, _n, pattern in self.path_limits:
            if rx.search(url):
                self.per_path[pattern] += 1
        self.queue.append((url, depth, source))

    # --- checkpoints (pause / resume / crash recovery) ---------------------------
    def checkpoint(self) -> None:
        state = {
            "queue": list(self.inflight.values()) + list(self.queue), "inflight": list(self.inflight),
            "seen": sorted(self.seen), "sitemap_pending": self.sitemap_pending,
            "external_targets": sorted(self.external_targets), "resources": sorted(self.resources), "outside": sorted(self.outside),
            "done": self.done, "cap_hit": self.cap_hit, "skipped_rules": self.skipped_rules, "notes": self.notes,
            "sitemap_urls_loaded": bool(self.sitemap_urls) or not self.cfg.get("crawl_sitemaps", True),
            "sitemap_urls": sorted(self.sitemap_urls), "linked_sitemaps": sorted(self.linked_sitemaps), "hops": self.hops,
        }
        db.update_crawl(self.crawl_id, state_json=json.dumps(state))

    def restore(self, state: dict) -> None:
        self.queue = deque(tuple(x) for x in state.get("queue") or [])
        self.seen = set(state.get("seen") or [])
        self.pages_seen = sum(1 for u in self.seen if not looks_like_resource(u))
        self.sitemap_pending = state.get("sitemap_pending") or []
        self.external_targets = set(state.get("external_targets") or [])
        self.resources = set(state.get("resources") or [])
        self.outside = set(state.get("outside") or [])
        self.done = int(state.get("done") or 0)
        self.cap_hit = bool(state.get("cap_hit"))
        self.skipped_rules = int(state.get("skipped_rules") or 0)
        self.notes = list(state.get("notes") or [])
        self.sitemap_urls = set(state.get("sitemap_urls") or [])
        self.linked_sitemaps = set(state.get("linked_sitemaps") or [])
        self.hops = {k: int(v) for k, v in (state.get("hops") or {}).items()}
        inflight = state.get("inflight") or []
        if inflight:
            db.delete_links_from(self.crawl_id, inflight)
        self.notes.append(f"Rastreo reanudado con {len(self.queue)} URL(s) pendientes.")

    # --- sitemaps -------------------------------------------------------------
    async def fetch_sitemaps(self, todo: list[str]) -> set[str]:
        found: set[str] = set()
        visited: set[str] = set()
        while todo and len(visited) < MAX_SITEMAPS and not self.cancel.is_set():
            sm = normalize(todo.pop(0))
            if not sm or sm in visited or not self.in_scope(sm):
                continue
            visited.add(sm)
            if not self.robots_allowed(await self.robots_for(sm), sm)[0]:
                continue
            await self._wait_turn()
            try:
                resp, content = await safehttp.get_capped(self.client, sm, safehttp.SITEMAP_MAX,
                                                          follow_redirects=True, auth=self._auth(sm))
            except httpx.HTTPError:
                self.notes.append(f"Sitemap no accesible: {sm}")
                continue
            if resp.status_code != 200:
                continue
            if content[:2] == b"\x1f\x8b":
                try:
                    content = safehttp.gunzip_capped(content, safehttp.SITEMAP_MAX)
                except (OSError, zlib.error, safehttp.TooLarge) as exc:
                    self.notes.append(f"Sitemap ignorado ({exc}): {sm}")
                    continue
            if not safehttp.no_doctype(content):
                self.notes.append(f"Sitemap ignorado (declara un DOCTYPE o entidades XML): {sm}")
                continue
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                self.notes.append(f"Sitemap no válido (XML mal formado): {sm}")
                continue
            # Only <url><loc> / <sitemap><loc> are pages; <image:image><image:loc> are images (Yoast and co.)
            name = lambda el: el.tag.split("}")[-1]
            entries = []
            for child in root:
                loc, images = None, []
                for el in child:
                    if name(el) == "loc":
                        loc = (el.text or "").strip()
                    elif name(el) == "image":
                        images += [(sub.text or "").strip() for sub in el if name(sub) == "loc"]
                entries.append((loc, images))
            if name(root) == "sitemapindex":
                todo.extend(loc for loc, _ in entries if loc)
                continue
            links = []
            for loc, images in entries:
                u = normalize(loc)
                if u and self.in_scope(u) and len(found) < MAX_SITEMAP_URLS:
                    found.add(u)
                for img in images:
                    iu = normalize(img)
                    if iu and self.in_scope(iu):
                        self.resources.add(iu)
                        links.append({"source_url": sm, "target_url": iu, "anchor": "sitemap (image:loc)",
                                      "kind": "resource", "internal": True})
            db.save_links(self.crawl_id, links)
        return found

    async def load_sitemaps(self) -> None:
        rules = await self.robots_for(self.start_url)
        custom = [l.strip() for l in (self.opt.get("custom_sitemaps") or "").splitlines() if l.strip()]
        self.sitemap_urls = await self.fetch_sitemaps((list(rules.sitemaps) or [origin(self.start_url) + "/sitemap.xml"]) + custom)
        if self.sitemap_urls:
            db.save_sitemap_urls(self.crawl_id, self.sitemap_urls)
            self.sitemap_pending = sorted(self.sitemap_urls)

    # --- session: cookies / form login -------------------------------------------------
    async def setup_session(self) -> None:
        s = self.settings
        hosts = set()
        for key in self.scope:
            hosts |= {key, "www." + key}
        if s.cookies:
            for part in s.cookies.split(";"):
                if "=" in part:
                    name, value = part.split("=", 1)
                    for h in hosts:
                        self.client.cookies.set(name.strip(), value.strip(), domain=h)
                        self.session_cookies.append({"name": name.strip(), "value": value.strip(), "domain": h, "path": "/"})
            self.notes.append("Cookies de CRAWL_COOKIES aplicadas solo a tu dominio.")
        if self.cfg.get("use_saved_session", True):
            from . import sessions
            saved = sessions.load(site_key(self.start_url))
            if saved and (saved.get("cookies") or saved.get("origins")):
                for c in saved["cookies"]:
                    if self.in_scope("https://" + c["domain"].lstrip(".") + "/") or site_key(self.start_url).endswith(c["domain"].lstrip(".")):
                        self.client.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."), path=c.get("path", "/"))
                        self.session_cookies.append(c)
                self.storage_origins = saved.get("origins") or []
                self.notes.append(f"Sesión grabada el {saved.get('saved_at', '')[:16].replace('T', ' ')} UTC aplicada "
                                  f"({len(saved['cookies'])} cookies vigentes). Si ves la página de login en el rastreo, "
                                  "vuelve a grabarla.")
        if self.cfg.get("form_login"):
            if not (s.login_url and s.login_user and s.login_password):
                self.notes.append("Login por formulario activado pero faltan LOGIN_URL/LOGIN_USER/LOGIN_PASSWORD en .env.")
                return
            if not self.in_scope(s.login_url):
                self.notes.append("LOGIN_URL no pertenece al sitio rastreado; no se usa el login por formulario.")
                return
            try:
                cookies = await form_login(s.login_url, s.login_user, s.login_password, s.login_user_selector,
                                           s.login_password_selector, s.login_submit_selector, self.ua,
                                           bool(self.cfg.get("verify_ssl", True)))
                for c in cookies:
                    if self.in_scope("https://" + c["domain"].lstrip(".") + "/"):
                        self.client.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."), path=c.get("path", "/"))
                        self.session_cookies.append(c)
                self.notes.append(f"Sesión iniciada con el formulario de login ({len(self.session_cookies)} cookies).")
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"No se pudo iniciar sesión con el formulario: {str(exc).splitlines()[0][:200]}")

    # --- rendering --------------------------------------------------------------
    async def start_renderer(self) -> None:
        if not self.cfg.get("render"):
            if self.cfg.get("screenshots"):
                self.notes.append("Las capturas de pantalla necesitan el renderizado JavaScript activado.")
            return
        try:
            proxy = self.settings.proxy if self.cfg.get("use_proxy") and self.settings.proxy else None
            from .render import DEFAULT_BLOCKLIST
            blocklist = (list(DEFAULT_BLOCKLIST) if self.cfg.get("block_third_party", True) else []) + \
                [l.strip().lower().removeprefix("https://").removeprefix("http://")
                 for l in (self.cfg.get("block_hosts") or "").splitlines() if l.strip()]
            max_clicks = int(self.cfg.get("js_click_max") or 20) if self.cfg.get("js_click") else 0
            self.renderer = await Renderer.start(
                self.ua, int(self.cfg.get("render_timeout") or self.settings.render_timeout), self.auth,
                origin(self.start_url), header_dict(self.cfg), bool(self.cfg.get("verify_ssl", True)), proxy,
                self.session_cookies or None, blocklist, max_clicks, self.storage_origins or None)
            self.renderer.want_html = bool(self.opt["store_rendered_html"])
            self.notes.append(f"Renderizado JavaScript activo ({self.renderer.engine})."
                              + (f" Scripts de terceros bloqueados: {len(blocklist)} dominios." if blocklist else "")
                              + (f" Descubrimiento de enlaces por clic: hasta {max_clicks} clics por página, "
                                 "sin enviar formularios ni peticiones POST." if max_clicks else ""))
            if self.cfg.get("screenshots"):
                self.shots_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"Renderizado no disponible; se continúa solo con HTML. Motivo: {exc}")

    # --- main loop ----------------------------------------------------------------
    async def run(self, state: dict | None = None) -> None:
        await self.setup_session()
        if state:
            self.restore(state)
        elif self.mode == "list":
            xml = [u for u in self.list_urls if u.lower().split("?")[0].endswith((".xml", ".xml.gz"))]
            pages = [u for u in self.list_urls if u not in xml]
            if xml:
                expanded = await self.fetch_sitemaps(list(xml))
                self.notes.append(f"{len(expanded)} URL(s) obtenidas de {len(xml)} sitemap(s) de la lista.")
                pages += sorted(expanded)
            for u in pages:
                self.enqueue(u, 0, "list")
        else:
            self.enqueue(self.start_url, 0, "start")
            if self.cfg.get("crawl_sitemaps", True):
                await self.load_sitemaps()
        await self.start_renderer()
        db.update_crawl(self.crawl_id, phase="pages", urls_queued=len(self.seen))
        try:
            await asyncio.gather(*(self._worker() for _ in range(self.threads)))
        finally:
            if self.renderer:
                await self.renderer.close()
            self.checkpoint()
        if self.cancel.is_set():
            return
        if self.cap_hit:
            self.notes.append(
                f"Se alcanzó el tope de {self.max_urls} URLs: hay URLs sin rastrear. "
                "Los candidatos huérfanos y los duplicados pueden estar incompletos.")
        if self.skipped_rules:
            self.notes.append(f"{self.skipped_rules} URL(s) omitidas por las reglas de incluir/excluir o de parámetros.")
        for why, n in sorted(self.limit_notes.items()):
            self.notes.append(f"{n} URL(s) o páginas afectadas por el límite «{why}» de la configuración.")
        if self.cfg.get("check_resources") and not self.cancel.is_set():
            await self.check_urls(sorted(self.resources - self.seen), "resource")
        if self.outside and not self.cancel.is_set():
            await self.check_urls(sorted(self.outside - self.seen), "resource")
        if self.cfg.get("check_external") and not self.cancel.is_set():
            await self.check_urls(sorted(self.external_targets), "external")
        if not self.cancel.is_set():
            await self.retest_slow()

    async def _worker(self) -> None:
        while not self.cancel.is_set():
            if not self.queue:
                if self.inflight or self._sitemap_fetches:  # others may still add URLs
                    await asyncio.sleep(0.05)
                    continue
                if self.sitemap_pending:
                    pending, self.sitemap_pending = self.sitemap_pending, []
                    for u in pending:
                        self.enqueue(u, -1, "sitemap")
                    continue
                if self.linked_sitemaps:  # sitemaps found as links in the pages
                    todo, self.linked_sitemaps = sorted(self.linked_sitemaps), set()
                    self._sitemap_fetches += 1
                    try:
                        found = await self.fetch_sitemaps(todo) - self.sitemap_urls
                    finally:
                        self._sitemap_fetches -= 1
                    if found:
                        self.sitemap_urls |= found
                        db.save_sitemap_urls(self.crawl_id, found)
                        self.notes.append(f"{len(found)} URL(s) más en {len(todo)} sitemap(s) enlazado(s) desde las páginas.")
                        self.sitemap_pending = sorted(found)
                    continue
                return
            item = self.queue.popleft()
            self.inflight[item[0]] = item
            try:
                await self.process(*item)
            finally:
                self.inflight.pop(item[0], None)
                self.done += 1
                db.update_crawl(self.crawl_id, pages_done=self.done, urls_queued=len(self.seen))
                if self.done % CHECKPOINT_EVERY == 0:
                    self.checkpoint()

    async def process(self, url: str, depth: int, source: str) -> None:
        page: dict = {"url": url, "depth": depth if depth >= 0 else None, "source": source, "fetched_at": db.now()}
        rules = await self.robots_for(url)
        ok, reason = self.robots_allowed(rules, url)
        if not ok:
            page["blocked_by_robots"] = 1
            page["error"] = reason
            self._finish_page(page)
            return

        for attempt in range(max(self.retries, 1) + 1):
            page, body, charset = await self._fetch(page, url, rules)
            if self.cancel.is_set():
                break
            if page.get("status_code") is None and page.get("error") and attempt == 0:
                await asyncio.sleep(2)  # no answer at all (connection cut, timeout): one more try
                page.pop("error", None)
                continue
            if not (page.get("status_code") or 0) >= 500 or attempt >= self.retries:
                break

        links: list[dict] = []
        target = page.get("redirect_target")
        if target:
            internal = self.in_scope(target)
            links.append({"source_url": url, "target_url": target, "kind": "redirect", "internal": internal})
            hops = self.hops.get(url, 0) + 1
            if hops > int(self.opt["max_redirects"]):
                self.limit_notes["máximo de redirecciones en cadena"] += 1
            elif internal:
                self.hops[target] = hops
                self.enqueue(target, max(depth, 0), "redirect", force=bool(self.opt["always_follow_redirects"]))
            else:
                self.external_targets.add(target)

        if body:
            # Parsing is CPU work: do it off the event loop so it never inflates other requests' timings.
            data = await asyncio.to_thread(self._parse, body, url, charset)
            self._apply_html(page, data, depth, links)
            if self.opt["store_html"]:
                self._store_html(url, body, "original")
            if self.renderer:
                await self._render(page, depth, links)
        elif page.get("pdf"):
            page.setdefault("extra", {})["pdf"] = page.pop("pdf")
        if page.get("cookies") is not None:
            page.setdefault("extra", {})["cookies"] = page.pop("cookies")

        db.save_links(self.crawl_id, links)
        self._finish_page(page)

    def _parse(self, body: bytes, url: str, charset: str | None) -> dict:
        data = parse_html(body, url, charset, self.cfg.get("searches"), self.cfg.get("extractions"))
        if self.cfg.get("spelling", True):
            data["extra"]["spelling"] = spelling(data["text_excerpt"], data["lang"], self.custom_words)
        extra = data["extra"]
        for flag, key in (("extract_readability", "readability"), ("extract_text_ratio", "text_ratio"), ("extract_forms", "forms")):
            if not self.opt[flag]:
                extra.pop(key, None)
        if not self.opt["html_validation"]:
            extra.pop("validation", None)
        if not self.opt["extract_srcset"]:  # srcset-only images are not resources then
            data["resources"] = [r for r in data["resources"] if r["via"] != ["srcset"]]
        return data

    def _store_html(self, url: str, html: bytes | str, kind: str) -> None:
        """Keep the page source (gzip) for «Ver fuente». Only when the option is on; lives in data/html/<crawl>/."""
        try:
            self.html_dir.mkdir(parents=True, exist_ok=True)
            data = html.encode("utf-8") if isinstance(html, str) else html
            name = hashlib.sha1(url.encode()).hexdigest()[:24] + f".{kind}.html.gz"
            (self.html_dir / name).write_bytes(gzip.compress(data[: 20 * 1024 * 1024], compresslevel=6))
        except OSError as exc:
            self.notes.append(f"No se pudo guardar el HTML de {url}: {exc}")

    def _resource_mode(self, res: dict) -> tuple[bool, bool]:
        """(Rastrear, Almacenar) for this resource type: crawled ones are checked with HEAD; stored ones are only
        recorded as links of the page. Both off: ignored."""
        via, url = set(res["via"]), res["url"].lower().split("?")[0]
        o = self.opt
        if via & {"css"} or url.endswith(".css"):
            kind = "css"
        elif via & {"script"} or url.endswith((".js", ".mjs")):
            kind = "js"
        elif via & {"video", "audio", "source", "poster"} or url.endswith((".mp4", ".webm", ".mp3", ".ogg", ".mov", ".m4a", ".wav")):
            kind = "media"
        elif via & {"iframe"}:
            return True, True
        elif via & {"img", "img (lazy)", "srcset", "og:image", "twitter:image", "schema", "favicon"} \
                or url.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico")):
            kind = "images"
        else:
            return True, True
        crawl = bool(o["crawl_" + kind])
        return crawl, crawl or bool(o["store_" + kind])

    async def _fetch(self, page: dict, url: str, rules: RobotsRules) -> tuple[dict, bytes, str | None]:
        page = {k: v for k, v in page.items() if k in ("url", "depth", "source", "fetched_at")}
        await self._wait_turn(rules.crawl_delay(self.token))
        started = time.monotonic()
        body = b""
        charset = None
        try:
            async with self.client.stream("GET", url, auth=self._auth(url)) as resp:
                page["ttfb_ms"] = int((time.monotonic() - started) * 1000)  # time to first byte (headers received)
                page["status_code"] = resp.status_code
                ctype = resp.headers.get("content-type", "")
                page["content_type"] = ctype.split(";")[0].strip().lower() or None
                page["x_robots"] = ", ".join(resp.headers.get_list("x-robots-tag")) or None
                page["headers_json"] = json.dumps(_headers(resp), ensure_ascii=False)
                charset = resp.charset_encoding
                if resp.is_redirect and resp.headers.get("location"):
                    page["redirect_target"] = normalize(resp.headers["location"], url)
                if self.opt["respect_hsts"] and url.startswith("https://") and resp.headers.get("strict-transport-security"):
                    self.hsts_hosts.add(urlsplit(url).hostname or "")
                if self.opt["extract_cookies"] and self.in_scope(url):
                    page["cookies"] = _cookie_info(resp.headers.get_list("set-cookie"))
                is_html = _is_html(ctype) or (not ctype and self.opt["assume_html"])
                if not ctype and self.opt["assume_html"]:
                    page["content_type"] = "text/html"
                if resp.status_code == 200 and is_html:
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > self.max_body:
                            self.notes.append(f"HTML truncado a {self.max_body // 1024} KB: {url}")
                            break
                elif resp.status_code == 200 and "pdf" in ctype and self.opt["extract_pdf"]:
                    pdf = b""
                    async for chunk in resp.aiter_bytes():
                        pdf += chunk
                        if len(pdf) > 25 * 1024 * 1024:
                            break
                    from .extract import pdf_properties
                    page["pdf"] = pdf_properties(pdf)
                    page["size_bytes"] = len(pdf)
                page["size_bytes"] = page.get("size_bytes") or len(body) or int(resp.headers.get("content-length") or 0) or None
            if self.opt["cookie_storage"] == "none":  # don't carry the site's cookies to the next request
                self.client.cookies.clear()
                for c in self.session_cookies:
                    self.client.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."), path=c.get("path", "/"))
        except httpx.HTTPError as exc:
            page["error"] = f"{type(exc).__name__}: {exc}"[:300] or type(exc).__name__
        page["response_ms"] = int((time.monotonic() - started) * 1000)
        if page.get("status_code") and page["response_ms"] > SLOW_RETEST_MS and self.opt["ext_response_time"]:
            self.slow.append((url, "page"))
        if page.get("redirect_target"):
            page["redirect_target"] = self.rewrite(page["redirect_target"])
        return page, body, charset

    def _apply_html(self, page: dict, data: dict, depth: int, links: list[dict]) -> None:
        url = page["url"]
        spider = self.mode == "spider"
        mixed = [r["url"] for r in data["resources"] if url.startswith("https://") and r["url"].startswith("http://")]
        iframes = []
        for res in sorted(data["resources"], key=lambda r: r["url"]):
            check, keep = self._resource_mode(res) if self.in_scope(res["url"]) else (False, False)
            if keep:
                if res["via"] == ["iframe"]:
                    if self.opt["crawl_iframes"] or self.opt["store_iframes"]:
                        iframes.append(res["url"])  # an internal iframe is a page: crawl it as one
                    continue
                if check:
                    self.resources.add(res["url"])
                # anchor = where the resource was referenced (img, srcset, og:image, schema, favicon, css…)
                links.append({"source_url": url, "target_url": res["url"], "anchor": ", ".join(res["via"]),
                              "kind": "resource", "internal": True})
        extra = data["extra"]
        page["extra"] = extra
        page.update({
            "title": data["title"], "meta_description": data["meta_description"],
            "meta_robots": data["meta_robots"], "canonical": data["canonical"],
            "canonical_count": data["canonical_count"], "h1_json": json.dumps(data["h1"], ensure_ascii=False),
            "h2_json": json.dumps(data["h2"], ensure_ascii=False), "lang": data["lang"],
            "word_count": data["word_count"], "content_hash": data["content_hash"],
            "text_excerpt": data["text_excerpt"], "jsonld_invalid": data["jsonld_invalid"],
            "structured_json": json.dumps(data["structured"], ensure_ascii=False),
            "hreflang_json": json.dumps(data["hreflang"], ensure_ascii=False),
            "mixed_json": json.dumps(mixed[:50], ensure_ascii=False),
            "custom_json": json.dumps(data["custom"], ensure_ascii=False),
        })
        page_nofollow = is_nofollow(page.get("meta_robots")) or is_nofollow(page.get("x_robots"))
        next_depth = max(depth, 0) + 1
        cap = int(self.opt["max_links_per_page"])
        if len(data["links"]) > cap:
            self.limit_notes["máximo de enlaces por página"] += 1
        for link in data["links"][:cap]:
            href = self.rewrite(link["href"])
            internal = self.in_scope(href)
            if internal or self.opt["store_external"] or self.cfg.get("check_external"):
                links.append({"source_url": url, "target_url": href, "anchor": link["anchor"],
                              "nofollow": link["nofollow"], "rel": link.get("rel"), "internal": internal, "kind": "a"})
            blocked = link["nofollow"] or page_nofollow
            if internal and spider and (not blocked or self.opt["follow_nofollow_internal"]):
                self.enqueue(href, next_depth, "link")
            elif not internal and (not blocked or self.opt["follow_nofollow_external"]):
                self.external_targets.add(href)
            if internal and self.opt["crawl_linked_sitemaps"] and re.search(r"sitemap[^/]*\.xml(\.gz)?$", href.split("?")[0], re.I):
                self.linked_sitemaps.add(href)
            frag = link.get("fragment") or ""
            if self.cfg.get("crawl_fragments") and internal and spider and frag[:1] in ("/", "!"):
                self.enqueue(href + "#" + frag, next_depth, "fragment")
        for frame in iframes:
            if self.opt["store_iframes"]:
                links.append({"source_url": url, "target_url": frame, "anchor": "iframe", "kind": "iframe", "internal": True})
            if spider and self.opt["crawl_iframes"]:
                self.enqueue(frame, next_depth, "iframe")
        follow_targets = []
        canonical = data["canonical"]
        if canonical and canonical != url:
            if self.opt["store_canonicals"]:
                links.append({"source_url": url, "target_url": canonical, "kind": "canonical", "internal": self.in_scope(canonical)})
            if self.cfg.get("crawl_canonicals", True) or self.opt["always_follow_canonicals"]:
                follow_targets.append((canonical, max(depth, 0), "canonical"))
        for alt in data["hreflang"]:
            if alt["href"] != url:
                if self.opt["store_hreflang"]:
                    links.append({"source_url": url, "target_url": alt["href"], "anchor": alt["lang"],
                                  "kind": "hreflang", "internal": self.in_scope(alt["href"])})
                if self.cfg.get("crawl_hreflang", True):
                    follow_targets.append((alt["href"], max(depth, 0), "hreflang"))
        for rel in ("next", "prev"):
            if extra.get(rel) and extra[rel] != url:
                if self.opt["store_pagination"]:
                    links.append({"source_url": url, "target_url": extra[rel], "anchor": rel, "kind": "pagination",
                                  "internal": self.in_scope(extra[rel])})
                if self.opt["crawl_pagination"]:
                    follow_targets.append((extra[rel], next_depth, "pagination"))
        amphtml = normalize((extra.get("amp") or {}).get("amphtml"), url)
        if amphtml and amphtml != url:
            if self.opt["store_amp"]:
                links.append({"source_url": url, "target_url": amphtml, "kind": "amphtml", "internal": self.in_scope(amphtml)})
            if self.opt["crawl_amp"]:
                follow_targets.append((amphtml, max(depth, 0), "amphtml"))
        mobile = normalize((extra.get("amp") or {}).get("mobile_alt"), url)
        if mobile and mobile != url:
            if self.opt["store_mobile_alt"]:
                links.append({"source_url": url, "target_url": mobile, "anchor": "móvil", "kind": "mobile_alt",
                              "internal": self.in_scope(mobile)})
            if self.opt["crawl_mobile_alt"]:
                follow_targets.append((mobile, max(depth, 0), "mobile_alt"))
        refresh = extra.get("meta_refresh") or {}
        if refresh.get("target") and refresh["target"] == url and self.opt["respect_self_refresh"]:
            extra["meta_refresh"] = refresh = {}  # a page refreshing itself is not a redirect
        follow_refresh = bool(self.opt["crawl_meta_refresh"])
        if refresh.get("target") and refresh["target"] != url:
            if self.opt["store_meta_refresh"]:
                links.append({"source_url": url, "target_url": refresh["target"], "kind": "meta_refresh",
                              "internal": self.in_scope(refresh["target"])})
            if follow_refresh:
                follow_targets.append((refresh["target"], max(depth, 0), "meta_refresh"))
        force = {"canonical": bool(self.opt["always_follow_canonicals"]), "meta_refresh": bool(self.opt["always_follow_redirects"])}
        if spider:
            for target, d, src in follow_targets:
                self.enqueue(target, d, src, force=force.get(src, False))
        elif refresh.get("target") and follow_refresh:
            self.enqueue(refresh["target"], max(depth, 0), "meta_refresh")  # redirects are followed in list mode too
        db.save_images(self.crawl_id, url, data["images"])

    async def _render(self, page: dict, depth: int, links: list[dict]) -> None:
        url = page["url"]
        await self._wait_turn()
        shot = None
        if self.cfg.get("screenshots"):
            shot = str(self.shots_dir / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".jpg"))
        try:
            r = await self.renderer.render(url, shot)
            self.render_failures = 0
        except Exception as exc:  # noqa: BLE001
            page["render_error"] = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
            self.render_failures += 1
            if self.render_failures >= 3 and self.renderer:
                self.notes.append("El renderizado falló 3 veces seguidas y se desactivó para el resto del rastreo.")
                renderer, self.renderer = self.renderer, None
                await renderer.close()
            return
        if r.get("html"):
            self._store_html(url, r.pop("html"), "rendered")
        text = (r.get("content_text") or r.get("text") or "")[:20000]
        page.update({
            "rendered": 1, "rendered_title": r.get("title"), "rendered_robots": r.get("robots"),
            "rendered_canonical": normalize(r.get("canonical")), "rendered_text": text,
            "rendered_word_count": text_stats(text)[0],
        })
        extra = page.setdefault("extra", {})
        extra["rendered_words_total"] = text_stats(r.get("text") or "")[0]
        if r.get("blocked_requests"):
            extra["blocked_requests"] = r["blocked_requests"]
        if "clicks_tried" in r:
            extra["clicks_tried"] = r["clicks_tried"]
        if r.get("screenshot"):
            extra["screenshot"] = shot.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        final = normalize(r.get("final_url"))
        if final and final != url and final.split("#")[0] != url.split("#")[0] and not (extra.get("meta_refresh") or {}).get("target"):
            extra["js_redirect"] = final
            links.append({"source_url": url, "target_url": final, "kind": "js_redirect", "internal": self.in_scope(final)})
            self.enqueue(final, max(depth, 0), "js_redirect")
        raw_targets = {l["target_url"] for l in links if l["kind"] == "a"}
        page_nofollow = is_nofollow(page.get("meta_robots")) or is_nofollow(r.get("robots"))
        for link in r.get("links") or []:
            href = self.rewrite(normalize(link.get("href")))
            if not href or href in raw_targets:
                continue
            raw_targets.add(href)
            internal = self.in_scope(href)
            links.append({"source_url": url, "target_url": href, "anchor": link.get("anchor"),
                          "nofollow": link.get("nofollow"), "internal": internal, "kind": "rendered"})
            if internal and self.mode == "spider" and not link.get("nofollow") and not page_nofollow:
                self.enqueue(href, max(depth, 0) + 1, "rendered-link")
            elif not internal and not link.get("nofollow") and not page_nofollow:
                self.external_targets.add(href)
        # navigation found only by clicking (onclick, router buttons…): Google does not follow these
        click_targets = []
        for link in r.get("click_links") or []:
            href = self.rewrite(normalize(link.get("href")))
            if not href or href in raw_targets or href == url:
                continue
            raw_targets.add(href)
            internal = self.in_scope(href)
            click_targets.append(href)
            if self.opt["store_uncrawlable"]:
                links.append({"source_url": url, "target_url": href, "anchor": link.get("anchor"), "nofollow": False,
                              "internal": internal, "kind": "rendered", "rel": "js-click"})
            if internal and self.mode == "spider" and not page_nofollow:
                self.enqueue(href, max(depth, 0) + 1, "js-click")
            elif not internal:
                self.external_targets.add(href)
        if click_targets:
            extra["js_click_links"] = click_targets[:50]

    async def _points_to_local_network(self, url: str) -> bool:
        """External links into the local network are only checked when the audited site itself is local."""
        if self._local_start is None:
            self._local_start = await safehttp.is_private_host(urlsplit(self.start_url or next(iter(self.list_urls), "")).hostname or "")
        if self._local_start:
            return False
        host = urlsplit(url).hostname or ""
        if host not in self._private_cache:
            self._private_cache[host] = await safehttp.is_private_host(host)
        return self._private_cache[host]

    # --- resources & external links -------------------------------------------------------------
    async def check_urls(self, all_targets: list[str], kind: str) -> None:
        """One HEAD (GET if HEAD is refused) per unique URL: internal resources or external links.
        Robots.txt and the delay still apply; nothing is crawled further."""
        targets = list(all_targets)  # resources and external links: HEAD only, never limited by the page cap
        label = "recursos internos" if kind == "resource" else "enlaces externos"
        if len(all_targets) > len(targets):
            self.notes.append(f"Solo se comprobaron {len(targets)} de {len(all_targets)} {label} (tope).")
        db.update_crawl(self.crawl_id, phase="resources" if kind == "resource" else "externals",
                        ext_total=len(targets), ext_done=0)
        done = 0

        async def one(url: str) -> None:
            nonlocal done
            row: dict = {"url": url, "kind": kind}
            if kind != "resource" and await self._points_to_local_network(url):
                # a crawled page must not make this computer probe the router, NAS or other devices of your network
                row["error"] = "No comprobado: apunta a tu red local o a este equipo."
                db.save_external(self.crawl_id, row)
                done += 1
                db.update_crawl(self.crawl_id, ext_done=done)
                return
            rules = await self.robots_for(url)
            ok, reason = self.robots_allowed(rules, url)
            if not ok:
                row["blocked_by_robots"] = 1
                row["error"] = reason
            else:
                await self._wait_turn(rules.crawl_delay(self.token))
                started = time.monotonic()
                try:
                    resp = await self.client.head(url, auth=self._auth(url))
                    if resp.status_code in (403, 405, 501):
                        async with self.client.stream("GET", url, auth=self._auth(url)) as resp:
                            pass
                    row["status_code"] = resp.status_code
                    row["content_type"] = (resp.headers.get("content-type") or "").split(";")[0].strip() or None
                    row["size_bytes"] = int(resp.headers.get("content-length") or 0) or None
                    if resp.is_redirect and resp.headers.get("location"):
                        row["redirect_target"] = normalize(resp.headers["location"], url)
                except httpx.HTTPError as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"[:300] or type(exc).__name__
                row["response_ms"] = int((time.monotonic() - started) * 1000)
                if kind == "resource" and row.get("status_code") and row["response_ms"] > SLOW_RETEST_MS:
                    self.slow.append((url, "resource"))
            db.save_external(self.crawl_id, row)
            done += 1
            db.update_crawl(self.crawl_id, ext_done=done)

        pending = deque(targets)

        async def worker():
            while pending and not self.cancel.is_set():
                await one(pending.popleft())

        # At most 2 simultaneous checks: more parallel requests only measure our own queueing, not the server.
        await asyncio.gather(*(worker() for _ in range(min(self.threads, 2))))

    async def retest_slow(self) -> None:
        """Measure again, one at a time and with nothing else running, every response over the threshold.
        The lower value is kept (the first one is stored too), so crawl noise is not reported as a slow server."""
        todo = list(dict.fromkeys(self.slow))[:MAX_RETESTS]
        if not todo:
            return
        db.update_crawl(self.crawl_id, phase="retest", ext_total=len(todo), ext_done=0)
        changed = 0
        for n, (url, kind) in enumerate(todo, 1):
            if self.cancel.is_set():
                break
            rules = await self.robots_for(url)
            await self._wait_turn(rules.crawl_delay(self.token))
            samples = []  # pages: two more measurements, the median of the three is kept
            for _ in range(2 if kind == "page" else 1):
                if samples:
                    await self._wait_turn(rules.crawl_delay(self.token))
                started = time.monotonic()
                try:
                    if kind == "page":
                        async with self.client.stream("GET", url, auth=self._auth(url)) as resp:
                            read = 0
                            async for chunk in resp.aiter_bytes():
                                read += len(chunk)
                                if read > self.max_body:
                                    break
                    else:
                        await self.client.head(url, auth=self._auth(url))
                except httpx.HTTPError:
                    break
                samples.append(int((time.monotonic() - started) * 1000))
            if not samples:
                continue
            if db.update_timing(self.crawl_id, url, kind, samples[0], samples[1:]):
                changed += 1
            db.update_crawl(self.crawl_id, ext_done=n)
        self.notes.append(f"Tiempos de respuesta: {len(todo)} URL(s) de más de {SLOW_RETEST_MS} ms medidas otras dos veces en "
                          f"solitario; se guarda la mediana de las tres (en {changed} bajó: la primera era ruido del rastreo).")

    def _strip(self, page: dict) -> None:
        o = self.opt
        extra = page.get("extra") or {}
        for flag, key in (("ext_title", "title"), ("ext_description", "meta_description"), ("ext_meta_robots", "meta_robots"),
                          ("ext_x_robots", "x_robots"), ("ext_hash", "content_hash"), ("ext_page_size", "size_bytes"),
                          ("ext_response_time", "response_ms")):
            if not o[flag] and key in page:
                page[key] = None
        if not o["ext_word_count"]:
            page["word_count"] = None
            extra.pop("words_total", None)
        if not o["ext_h1"]:
            page["h1_json"] = "[]"
        if not o["ext_h2"]:
            page["h2_json"] = "[]"
        if not o["ext_keywords"]:
            extra.pop("meta_keywords", None)
        if not o["ext_a11y"]:
            extra.pop("a11y", None)
        if not o["ext_schema_validation"]:
            extra.pop("schema_problems", None)
        if page.get("structured_json") and not (o["ext_jsonld"] and o["ext_microdata"] and o["ext_rdfa"]):
            sd = json.loads(page["structured_json"])
            if not o["ext_jsonld"]:
                sd.update(jsonld_count=0, jsonld_types=[])
                page["jsonld_invalid"] = 0
            if not o["ext_microdata"]:
                sd["microdata"] = []
            if not o["ext_rdfa"]:
                sd["rdfa"] = []
            page["structured_json"] = json.dumps(sd, ensure_ascii=False)
        if page.get("headers_json") and not (o["ext_headers"] and o["ext_last_modified"]):
            headers = json.loads(page["headers_json"])
            if not o["ext_headers"]:
                headers = {k: v for k, v in headers.items() if k == "last-modified"}
            if not o["ext_last_modified"]:
                headers.pop("last-modified", None)
            page["headers_json"] = json.dumps(headers, ensure_ascii=False)

    def _finish_page(self, page: dict) -> None:
        if "ttfb_ms" in page:
            page.setdefault("extra", {})["ttfb_ms"] = page.pop("ttfb_ms")
        self._strip(page)
        if "extra" in page:
            page["extra_json"] = json.dumps(page["extra"], ensure_ascii=False)
        page["indexable"], page["indexability"] = compute_indexability(page)
        page["indexable"] = int(page["indexable"])
        db.save_page(self.crawl_id, page)


async def _pagespeed(url: str, key: str) -> dict:
    """Google PageSpeed Insights (Lighthouse, mobile) for one URL. Raises on any failure."""
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile", "category": "performance", "key": key},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    lh = resp.json().get("lighthouseResult", {})
    audits = lh.get("audits", {})
    score = lh.get("categories", {}).get("performance", {}).get("score")
    return {
        "url": url,
        "performance": round(score * 100) if isinstance(score, (int, float)) else None,
        "lcp_ms": audits.get("largest-contentful-paint", {}).get("numericValue"),
        "cls": audits.get("cumulative-layout-shift", {}).get("numericValue"),
        "tbt_ms": audits.get("total-blocking-time", {}).get("numericValue"),
        "fcp_ms": audits.get("first-contentful-paint", {}).get("numericValue"),
    }


async def run_pagespeed(crawl_id: int, cfg: dict, settings: Settings, notes: list[str], extras: dict,
                        cancel: threading.Event) -> None:
    """PageSpeed for the start URL plus the most-linked indexable pages, up to psi_max_urls."""
    if not settings.pagespeed_key:
        notes.append("PageSpeed omitido: falta PAGESPEED_API_KEY en .env.")
        return
    from .views import workbench  # local import: views depends on analysis
    crawl = db.get_crawl(crawl_id)
    rows = [r for r in workbench(crawl)["pages"] if r.get("indexable_label") == "Indexable"]
    urls = [cfg["start_url"]] + [r["url"] for r in sorted(rows, key=lambda r: -r["inlinks"]) if r["url"] != cfg["start_url"]]
    urls = urls[: max(1, int(cfg.get("psi_max_urls") or 1))]
    db.update_crawl(crawl_id, phase="pagespeed", ext_total=len(urls), ext_done=0)
    results, failures = {}, 0
    for n, u in enumerate(urls, 1):
        if cancel.is_set():
            break
        try:
            results[u] = await _pagespeed(u, settings.pagespeed_key)
        except Exception as exc:  # noqa: BLE001 - external API is optional
            failures += 1
            if failures == 1:
                notes.append(f"PageSpeed Insights falló para {u} ({exc}); el resto de la auditoría es válido.")
            if failures >= 3:
                notes.append("PageSpeed Insights se detuvo tras 3 fallos.")
                break
        db.update_crawl(crawl_id, ext_done=n)
    if results:
        extras["pagespeed_pages"] = results
        extras["pagespeed"] = results.get(cfg["start_url"]) or next(iter(results.values()))


async def crawl(crawl_id: int, cancel: threading.Event, pause: threading.Event | None = None,
                resume: bool = False) -> None:
    settings = get_settings()
    record = db.get_crawl(crawl_id)
    if not record:
        return
    cfg = record["settings"]
    state = json.loads(record.get("state_json") or "null") if resume else None
    db.update_crawl(crawl_id, status="running", error=None, phase="setup",
                    **({} if resume else {"started_at": db.now()}))
    crawler: Crawler | None = None
    extras: dict = dict(record.get("extras") or {}) if resume else {}
    try:
        async with build_client(cfg, settings) as client:
            crawler = Crawler(crawl_id, cfg, client, settings, cancel)
            await crawler.run(state)

        if pause is not None and pause.is_set():
            crawler.notes.append("Rastreo en pausa: puedes reanudarlo cuando quieras.")
            db.update_crawl(crawl_id, phase="analysis")
            finalize(crawl_id, cfg, crawler.notes, extras, cancelled=True, status="paused")
            return
        if cfg.get("pagespeed") and not cancel.is_set():
            await run_pagespeed(crawl_id, cfg, settings, crawler.notes, extras, cancel)
        db.update_crawl(crawl_id, phase="analysis")
        finalize(crawl_id, cfg, crawler.notes, extras, cancelled=cancel.is_set())
    except Exception as exc:  # noqa: BLE001 - surface as recoverable error state
        notes = crawler.notes if crawler else []
        db.update_crawl(crawl_id, status="failed", finished_at=db.now(), notes=notes, phase=None,
                        error=f"Error inesperado durante el rastreo: {type(exc).__name__}: {exc}"[:500])


def _dictionary_words() -> set[str]:
    from . import dictionary
    return dictionary.words()


def finalize(crawl_id: int, cfg: dict, notes: list[str], extras: dict, cancelled: bool, status: str | None = None) -> None:
    from .insights import compute_insights  # heavy text analysis lives in its own module
    pages = db.load_pages(crawl_id)
    compute_insights(crawl_id, cfg, pages)
    issues = analyze(pages, db.load_links(crawl_id), db.load_images(crawl_id),
                     db.load_sitemap_urls(crawl_id), cfg["start_url"], extras, db.load_externals(crawl_id),
                     ignore_nonindexable=bool(cfg.get("ignore_nonindexable")),
                     analytics=db.load_analytics(crawl_id), near_dups=db.load_insight(crawl_id, "near_dups", {}),
                     spelling_vocab=set(db.load_insight(crawl_id, "spelling_vocab", [])) | _dictionary_words(), local=cfg, options=cfg)
    from .localseo import audit as local_audit
    local = local_audit(pages, db.load_links(crawl_id), cfg["start_url"], cfg)
    db.save_insight(crawl_id, "local", {k: local[k] for k in ("available", "pages", "site", "checklist")})
    db.replace_issues(crawl_id, issues)
    extras["summary"] = summarize(pages, issues)
    status, error = status or ("cancelled" if cancelled else "completed"), None
    start = next((p for p in pages if p["url"] == cfg["start_url"]), None)
    fetched_ok = [p for p in pages if p.get("status_code") and not p.get("blocked_by_robots")]
    if not fetched_ok and status != "paused":
        status = "failed"
        if start and start.get("blocked_by_robots") and start.get("error"):
            error = (f"No se pudo leer robots.txt ({start['error']}). Por precaución no se rastrea nada: "
                     "comprueba que el sitio está en línea y vuelve a intentarlo.")
        elif start and start.get("blocked_by_robots"):
            error = "No se rastreó nada: la URL inicial está bloqueada por una regla Disallow de robots.txt."
        elif start and start.get("error"):
            error = f"No se pudo conectar con la URL inicial: {start['error']}"
        else:
            error = "No se obtuvo ninguna respuesta del sitio."
    db.update_crawl(crawl_id, status=status, finished_at=db.now(), notes=notes, extras=extras, error=error, phase=None)


def reanalyze(crawl_id: int) -> None:
    """Recompute issues after importing analytics data (no network)."""
    crawl = db.get_crawl(crawl_id)
    if crawl and crawl["status"] not in db.ACTIVE_STATUSES:
        finalize(crawl_id, crawl["settings"], crawl["notes"], crawl["extras"], cancelled=crawl["status"] == "cancelled",
                 status=crawl["status"] if crawl["status"] in ("paused", "cancelled", "failed", "interrupted") else None)


# --- background job registry ------------------------------------------------
_jobs: dict[int, tuple[threading.Thread, threading.Event, threading.Event]] = {}
_lock = threading.Lock()


def _thread_main(crawl_id: int, cancel: threading.Event, pause: threading.Event, resume: bool) -> None:
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        loop.run_until_complete(crawl(crawl_id, cancel, pause, resume))
    finally:
        loop.close()
        with _lock:
            _jobs.pop(crawl_id, None)


def start_job(crawl_id: int, resume: bool = False) -> None:
    cancel, pause = threading.Event(), threading.Event()
    thread = threading.Thread(target=_thread_main, args=(crawl_id, cancel, pause, resume), daemon=True,
                              name=f"crawl-{crawl_id}")
    with _lock:
        _jobs[crawl_id] = (thread, cancel, pause)
    thread.start()


def cancel_job(crawl_id: int, pause: bool = False) -> bool:
    with _lock:
        job = _jobs.get(crawl_id)
    if job:
        if pause:
            job[2].set()
        job[1].set()
        return True
    return False


def is_running(crawl_id: int) -> bool:
    with _lock:
        return crawl_id in _jobs


def wait_job(crawl_id: int, timeout: float = 120) -> None:
    with _lock:
        job = _jobs.get(crawl_id)
    if job:
        job[0].join(timeout)


def run_sync(crawl_id: int, resume: bool = False) -> None:
    """Blocking run for the CLI."""
    cancel, pause = threading.Event(), threading.Event()
    _thread_main(crawl_id, cancel, pause, resume)
