"""Optional JavaScript rendering with Playwright. Fails soft: callers get None + reason.

Also: form login (returns session cookies), screenshots, and a network waterfall for one URL."""
from __future__ import annotations

from typing import Any

_EXTRACT_JS = """
() => {
  const metas = (n) => Array.from(document.querySelectorAll('meta[name]'))
      .filter(m => m.getAttribute('name').trim().toLowerCase() === n)
      .map(m => (m.getAttribute('content') || '').trim()).filter(Boolean).join(', ');
  const canon = document.querySelector('link[rel~="canonical" i]');
  // Main content only (same rule as the HTML extractor): <main>, else <article>, else body minus boilerplate.
  const contentText = () => {
    const mains = [...document.querySelectorAll('main, [role="main"]')];
    if (mains.length) return mains.map(m => m.innerText).join('\\n');
    const arts = [...document.querySelectorAll('article')];
    if (arts.length) return arts.map(a => a.innerText).join('\\n');
    if (!document.body) return '';
    const hidden = [...document.body.querySelectorAll('nav, header, footer, aside, [role="navigation"], [role="banner"], [role="contentinfo"], [role="complementary"]')]
      .filter(el => !el.closest('main, article'));
    const saved = hidden.map(el => [el, el.style.display]);
    hidden.forEach(el => { el.style.display = 'none'; });
    const text = document.body.innerText;
    saved.forEach(([el, d]) => { el.style.display = d; });
    return text;
  };
  return {
    content_text: contentText(),
    title: document.title || null,
    robots: [metas('robots'), metas('googlebot')].filter(Boolean).join(', ') || null,
    canonical: canon ? canon.href : null,
    final_url: location.href,
    text: document.body ? document.body.innerText : '',
    links: Array.from(document.querySelectorAll('a[href]')).slice(0, 5000).map(a => ({
      href: a.href, anchor: (a.innerText || a.title || '').trim().slice(0, 300),
      nofollow: (a.getAttribute('rel') || '').toLowerCase().split(/\\s+/).includes('nofollow')
    })),
  };
}
"""
# Third-party scripts blocked while rendering (by default): they slow the crawl and never change the SEO content.
THIRD_PARTY = {
    "Analítica": ["google-analytics.com", "analytics.google.com", "googletagmanager.com", "stats.g.doubleclick.net",
                  "clarity.ms", "mc.yandex.ru", "matomo.cloud", "plausible.io", "static.hotjar.com", "script.hotjar.com",
                  "assets.adobedtm.com", "omtrdc.net", "demdex.net", "2o7.net", "segment.com", "cdn.segment.com",
                  "mixpanel.com", "cdn.amplitude.com", "heapanalytics.com", "fullstory.com", "mouseflow.com",
                  "luckyorange.com", "crazyegg.com", "newrelic.com", "nr-data.net"],
    "Publicidad": ["doubleclick.net", "googlesyndication.com", "googleadservices.com", "adservice.google.com",
                   "amazon-adsystem.com", "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "adnxs.com",
                   "rubiconproject.com", "pubmatic.com", "adsrvr.org", "bat.bing.com", "ads-twitter.com"],
    "Píxeles sociales": ["connect.facebook.net", "facebook.com/tr", "snap.licdn.com", "px.ads.linkedin.com",
                         "analytics.tiktok.com", "static.ads-twitter.com", "ct.pinterest.com", "s.pinimg.com",
                         "sc-static.net", "tr.snapchat.com"],
    "Chat y marketing": ["widget.intercom.io", "js.intercomcdn.com", "js.hs-scripts.com", "js.hs-analytics.net",
                         "js.hsforms.net", "embed.tawk.to", "client.crisp.chat", "static.zdassets.com",
                         "js.driftt.com", "cdn.onesignal.com", "app.getbeamer.com", "static.klaviyo.com"],
}
DEFAULT_BLOCKLIST = sorted({h for hosts in THIRD_PARTY.values() for h in hosts})

# Clicking JavaScript "links" (elements without <a href> that navigate on click). Only elements that look like
# navigation are clicked, never form buttons or anything whose text suggests an action.
_CLICK_CANDIDATES_JS = """
(max) => {
  const risky = /(eliminar|borrar|delete|remove|quitar|comprar|pagar|pago|checkout|carrito|cart|buy|order|pedido|reservar|book|enviar|send|submit|suscrib|subscribe|logout|log out|cerrar sesi|salir|sign out|aceptar|accept|rechazar|reject|cookie|like|me gusta|votar|vote|compartir|share|imprimir|print|descargar|download)/i;
  const sel = '[onclick], [role="link"], [data-href], [data-url], [data-link], [data-to], [data-navigate], [data-route], [routerlink], [ng-click], [v-on\\\\:click], [\\\\@click], button, [role="button"], [role="menuitem"], [role="tab"], li[tabindex], div[tabindex], span[tabindex]';
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= max) break;
    if (el.closest('a[href], form, [contenteditable], dialog[open] [aria-modal]')) continue;
    if (el.matches('input, select, textarea, option, [type="submit"], [type="reset"], [disabled], [aria-disabled="true"]')) continue;
    const text = (el.innerText || el.getAttribute('aria-label') || el.title || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
    if (risky.test(text) || risky.test(el.id || '') || risky.test(el.getAttribute('class') || '')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const hint = el.getAttribute('data-href') || el.getAttribute('data-url') || el.getAttribute('data-link') ||
                 el.getAttribute('data-to') || el.getAttribute('routerlink') || '';
    el.setAttribute('data-lc-click', String(i));
    out.push({idx: i++, text: text, hint: hint});
  }
  return out;
}
"""
_CLICK_HOOKS_JS = """
() => {
  window.__lcNav = [];
  const rec = (u) => { try { window.__lcNav.push(new URL(u, location.href).href); } catch (e) {} };
  const ps = history.pushState, rs = history.replaceState;
  history.pushState = function (s, t, u) { if (u != null) rec(u); };
  history.replaceState = function (s, t, u) { if (u != null && String(u) !== location.pathname + location.search) rec(u); };
  window.open = function (u) { if (u) rec(u); return null; };
  window.__lcRestore = () => { history.pushState = ps; history.replaceState = rs; };
}
"""
_LAUNCH_ORDER =(("chromium", {}), ("msedge", {"channel": "msedge"}), ("chrome", {"channel": "chrome"}))


SANDBOX_STATE = {"sandboxed": None}  # last launch: True with Chromium's own sandbox, False without it


async def _launch(pw, proxy: str | None = None):
    """Headless browser for pages of other sites. Chromium's sandbox is turned on (Playwright leaves it off by
    default), so a hostile page's JavaScript stays locked inside the browser. Only where the system cannot
    run it (some Linux containers) does it fall back to the plain launch."""
    errors = []
    for label, kwargs in _LAUNCH_ORDER:
        if proxy:
            kwargs = {**kwargs, "proxy": {"server": proxy}}
        for sandbox in (True, False):
            try:
                browser = await pw.chromium.launch(headless=True, chromium_sandbox=sandbox, **kwargs)
                SANDBOX_STATE["sandboxed"] = sandbox
                return browser, label
            except Exception as exc:  # noqa: BLE001 - any launch failure means try next
                errors.append(f"{label}{'' if sandbox else ' (sin sandbox)'}: {str(exc).splitlines()[0][:120]}")
    raise RuntimeError("No hay navegador disponible para renderizar (" + "; ".join(errors) + ")")


def _playwright():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise RuntimeError("Playwright no está instalado") from exc
    return async_playwright()


class Renderer:
    def __init__(self, pw: Any, browser: Any, context: Any, engine: str, timeout_s: int,
                 blocklist: list[str] | None = None, max_clicks: int = 0):
        self._pw, self._browser, self._context = pw, browser, context
        self.engine = engine
        self.timeout_ms = timeout_s * 1000
        self.blocklist = [b.lower().strip() for b in (blocklist or []) if b.strip()]
        self.max_clicks = max_clicks

    def blocked(self, url: str) -> str | None:
        """The blocklist entry matching this URL (host suffix, or host + path prefix), if any."""
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        for b in self.blocklist:
            b_host, _, b_path = b.partition("/")
            if (host == b_host or host.endswith("." + b_host)) and (not b_path or parts.path.startswith("/" + b_path)):
                return b
        return None

    @classmethod
    async def start(cls, user_agent: str, timeout_s: int, auth: tuple[str, str] | None = None,
                    auth_origin: str | None = None, extra_headers: dict | None = None, verify_ssl: bool = True,
                    proxy: str | None = None, cookies: list[dict] | None = None,
                    blocklist: list[str] | None = None, max_clicks: int = 0,
                    origins: list[dict] | None = None) -> "Renderer":
        """Try bundled Chromium, then installed Edge, then Chrome. Raises RuntimeError if none works."""
        pw = await _playwright().start()
        try:
            browser, label = await _launch(pw, proxy)
        except RuntimeError:
            await pw.stop()
            raise
        context = await browser.new_context(
            user_agent=user_agent,
            # credentials are only offered to the audited origin
            http_credentials=({"username": auth[0], "password": auth[1], "origin": auth_origin}
                              if auth and auth_origin else None),
            java_script_enabled=True,
            extra_http_headers=extra_headers or None,
            ignore_https_errors=not verify_ssl,
            viewport={"width": 1366, "height": 900},
            # localStorage of a recorded login (only the audited site's origins, filtered when it was saved)
            storage_state={"cookies": [], "origins": origins} if origins else None,
        )
        if cookies:
            await context.add_cookies(cookies)

        async def _route(route):
            if route.request.resource_type in {"media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", _route)
        return cls(pw, browser, context, label, timeout_s, blocklist, max_clicks)

    async def render(self, url: str, screenshot_path: str | None = None, click: bool | None = None) -> dict:
        page = await self._context.new_page()
        nav: dict = {"blocked": {}, "clicked": [], "phase": "load"}

        async def guard(route):
            """The browser must not wander off: later top-level navigations (meta refresh, JS redirects, clicks) are
            recorded and blocked, so the crawler decides (robots.txt, scope) whether to fetch the target.
            Blocklisted third-party requests are aborted; while clicking, so is every non-GET request."""
            req = route.request
            hit = self.blocked(req.url)
            if hit:
                nav["blocked"][hit] = nav["blocked"].get(hit, 0) + 1
                await route.abort()
                return
            if req.is_navigation_request() and req.frame == page.main_frame:
                if nav.get("allow_nav"):  # reloading the original page between clicks
                    await route.fallback()
                    return
                if nav.get("started"):
                    if nav["phase"] == "click":
                        nav["clicked"].append(req.url)
                    else:
                        nav.setdefault("redirect_to", req.url)
                    await route.abort("aborted")  # cancelled navigation: the page stays as it was
                    return
                nav["started"] = True
            if nav["phase"] == "click" and req.method not in ("GET", "HEAD", "OPTIONS"):
                nav["unsafe_blocked"] = nav.get("unsafe_blocked", 0) + 1
                await route.abort()  # no side effects: forms, carts, votes… never reach the server
                return
            await route.fallback()

        await page.route("**/*", guard)
        try:
            await page.goto(url, wait_until="load", timeout=self.timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:  # noqa: BLE001 - long-polling pages never go idle; use what we have
                pass
            data = await page.evaluate(_EXTRACT_JS)
            if nav.get("redirect_to"):
                data["final_url"] = nav["redirect_to"]
            if screenshot_path:
                try:
                    await page.screenshot(path=screenshot_path, type="jpeg", quality=60)
                    data["screenshot"] = True
                except Exception:  # noqa: BLE001
                    data["screenshot"] = False
            if getattr(self, "want_html", False):
                data["html"] = await page.content()
            if (click if click is not None else self.max_clicks > 0) and not nav.get("redirect_to"):
                data["click_links"], data["clicks_tried"] = await self._click_discover(page, nav, url)
            data["blocked_requests"] = dict(nav["blocked"])
            data["unsafe_blocked"] = nav.get("unsafe_blocked", 0)
            return data
        finally:
            await page.close()

    @staticmethod
    async def _alive(page) -> bool:
        """False when the click left us on an error page or a page without our click markers."""
        try:
            return bool(await page.evaluate("() => !!document.querySelector('[data-lc-click]')"))
        except Exception:  # noqa: BLE001
            return False

    async def _click_discover(self, page, nav: dict, url: str) -> tuple[list[dict], int]:
        """Click elements that behave like links but are not <a href> and record where they try to go
        (navigation, history.pushState or window.open). Navigations are aborted and so are POST/PUT/DELETE requests."""
        found: dict[str, str] = {}
        tried = reloads = 0
        limit = max(1, self.max_clicks) * 3
        try:
            candidates = await page.evaluate(_CLICK_CANDIDATES_JS, limit)
        except Exception:  # noqa: BLE001
            return [], 0
        for c in candidates:  # data-href / routerlink already say where they go
            if c.get("hint"):
                found.setdefault(c["hint"], c["text"])
        nav["phase"] = "click"
        try:
            await page.evaluate(_CLICK_HOOKS_JS)
            for c in candidates:
                if tried >= self.max_clicks:
                    break
                if c.get("hint"):
                    continue
                before = len(nav["clicked"])
                try:
                    await page.evaluate("() => { window.__lcNav = []; }")
                    el = page.locator(f'[data-lc-click="{c["idx"]}"]')
                    if not await el.count():
                        continue
                    tried += 1
                    await el.first.click(timeout=1500, no_wait_after=True)
                    await page.wait_for_timeout(250)
                    pushed = await page.evaluate("() => window.__lcNav || []")
                except Exception:  # noqa: BLE001 - detached, covered or slow element: skip it
                    pushed = []
                for target in nav["clicked"][before:] + pushed:
                    if target and not target.startswith(("javascript:", "about:", "data:")):
                        found.setdefault(target, c["text"])
                if page.url.split("#")[0] != url.split("#")[0] or not await self._alive(page):
                    if reloads >= 3:
                        break
                    reloads += 1  # the view changed (hash router, error page): reload the original and go on
                    nav["allow_nav"] = True
                    try:
                        await page.goto(url, wait_until="load", timeout=self.timeout_ms)
                        await page.evaluate(_CLICK_CANDIDATES_JS, limit)
                        await page.evaluate(_CLICK_HOOKS_JS)
                    except Exception:  # noqa: BLE001
                        break
                    finally:
                        nav["allow_nav"] = False
        finally:
            nav["phase"] = "done"
        from urllib.parse import urljoin
        out = []
        url = url.split("#")[0]
        for target, text in found.items():
            href = urljoin(url, target)
            if href.split("#")[0] != url.split("#")[0]:
                out.append({"href": href, "anchor": text})
        return out, tried

    async def close(self) -> None:
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass


async def form_login(login_url: str, user: str, password: str, user_sel: str, pass_sel: str, submit_sel: str,
                     user_agent: str, verify_ssl: bool = True, timeout_s: int = 30) -> list[dict]:
    """Log in through a web form with Playwright and return the session cookies (Playwright format)."""
    async with _playwright() as pw:
        browser, _ = await _launch(pw)
        try:
            context = await browser.new_context(user_agent=user_agent, ignore_https_errors=not verify_ssl)
            page = await context.new_page()
            await page.goto(login_url, wait_until="load", timeout=timeout_s * 1000)
            await page.locator(user_sel).first.fill(user)
            await page.locator(pass_sel).first.fill(password)
            async with page.expect_navigation(wait_until="load", timeout=timeout_s * 1000):
                await page.locator(submit_sel).first.click()
            return await context.cookies()
        finally:
            await browser.close()


async def waterfall(url: str, user_agent: str, timeout_s: int = 30, verify_ssl: bool = True,
                    auth: tuple[str, str] | None = None, auth_origin: str | None = None) -> dict:
    """Load one page and record every request's timing (like the Network tab)."""
    async with _playwright() as pw:
        browser, engine = await _launch(pw)
        try:
            context = await browser.new_context(
                user_agent=user_agent, ignore_https_errors=not verify_ssl,
                http_credentials=({"username": auth[0], "password": auth[1], "origin": auth_origin}
                                  if auth and auth_origin else None))
            page = await context.new_page()
            entries: list[dict] = []

            async def finished(req):
                try:
                    t = req.timing
                    resp = await req.response()
                    size = None
                    try:
                        size = (await req.sizes()).get("responseBodySize")
                    except Exception:  # noqa: BLE001
                        size = None
                    entries.append({"url": req.url, "type": req.resource_type, "method": req.method,
                                    "status": resp.status if resp else None, "start": t.get("startTime"),
                                    "dns": t.get("domainLookupEnd", -1) - t.get("domainLookupStart", -1),
                                    "connect": t.get("connectEnd", -1) - t.get("connectStart", -1),
                                    "ttfb": t.get("responseStart", -1) - t.get("requestStart", -1),
                                    "end": t.get("responseEnd", -1), "size": size})
                except Exception:  # noqa: BLE001
                    pass

            tasks = []
            page.on("requestfinished", lambda r: tasks.append(finished(r)))
            await page.goto(url, wait_until="load", timeout=timeout_s * 1000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            for t in tasks:
                await t
            perf = await page.evaluate("""() => { const n = performance.getEntriesByType('navigation')[0] || {};
                return {dcl: Math.round(n.domContentLoadedEventEnd || 0) || null, load: Math.round(n.loadEventEnd || 0) || null,
                        ttfb: Math.round(n.responseStart || 0) || null}; }""")
        finally:
            await browser.close()
    if not entries:
        return {"engine": engine, "entries": [], "perf": perf}
    t0 = min(e["start"] for e in entries if e["start"] is not None)
    rows = []
    for e in sorted(entries, key=lambda e: e["start"] or 0):
        begin = (e["start"] or t0) - t0
        duration = max(e["end"], 0) if e["end"] and e["end"] > 0 else 0
        rows.append({**e, "begin_ms": round(begin), "duration_ms": round(duration),
                     "end_ms": round(begin + duration)})
    total = max(r["end_ms"] for r in rows) or 1
    return {"engine": engine, "entries": rows, "total_ms": total, "perf": perf,
            "bytes": sum(r["size"] or 0 for r in rows), "requests": len(rows)}
