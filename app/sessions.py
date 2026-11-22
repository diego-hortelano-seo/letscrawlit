"""Record a login by hand (SSO, 2FA, CAPTCHA…) in a visible browser window and reuse it for later crawls.

You log in yourself in a real window opened by the app; when you press «Guardar sesión» the cookies and
localStorage of YOUR site are saved to data/sessions/<site>.json (never in the database or the exports) and the
crawler sends them only to that site. Nothing is typed or solved automatically.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import secretstore
from .config import get_settings
from .urlutil import site_key


def _dir() -> Path:
    return get_settings().data_dir / "sessions"


def _path(site: str) -> Path:
    from .urlutil import safe_filename
    return _dir() / (safe_filename(site) + ".json")


def _belongs(domain: str, site: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return d == site or d.endswith("." + site) or site.endswith("." + d) and d.count(".") >= 1


def filter_state(state: dict, site: str) -> dict:
    """Keep only cookies and localStorage of the audited site (SSO providers' cookies are dropped)."""
    cookies = [c for c in state.get("cookies") or [] if _belongs(c.get("domain", ""), site)]
    origins = [o for o in state.get("origins") or [] if _belongs(urlsplit(o.get("origin", "")).hostname or "", site)]
    return {"cookies": cookies, "origins": origins}


def save(site: str, state: dict) -> dict:
    state = filter_state(state, site)
    _dir().mkdir(parents=True, exist_ok=True)
    data = {"site": site, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **state}
    secretstore.write_json(_path(site), data)
    return info(data)


def load(site: str) -> dict | None:
    try:
        data = secretstore.read_json(_path(site))
    except (OSError, ValueError):
        return None
    now = datetime.now(timezone.utc).timestamp()
    data["cookies"] = [c for c in data.get("cookies") or [] if not c.get("expires") or c["expires"] < 0 or c["expires"] > now]
    return data


def info(data: dict) -> dict:
    exp = [c["expires"] for c in data.get("cookies") or [] if c.get("expires") and c["expires"] > 0]
    return {"site": data.get("site"), "saved_at": (data.get("saved_at") or "")[:16].replace("T", " "),
            "cookies": len(data.get("cookies") or []), "storage": len(data.get("origins") or []),
            "expires": datetime.fromtimestamp(min(exp), timezone.utc).strftime("%Y-%m-%d %H:%M") if exp else ""}


def list_saved() -> list[dict]:
    out = []
    for p in sorted(_dir().glob("*.json")) if _dir().exists() else []:
        try:
            out.append(info(secretstore.read_json(p)))
        except (OSError, ValueError):
            continue
    return out


def delete(site: str) -> None:
    try:
        _path(site).unlink()
    except FileNotFoundError:
        pass


# --- recorder (one visible browser at a time) ---------------------------------------------------------
class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = {"status": "idle", "message": "", "site": "", "url": ""}
        self._save = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _set(self, **kw) -> None:
        with self.lock:
            self.state.update(kw)

    def start(self, url: str, user_agent: str | None = None) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Ya hay una grabación en curso. Guárdala o cancélala primero.")
        self._save.clear()
        self._cancel.clear()
        site = site_key(url)
        self._set(status="starting", message="Abriendo el navegador…", site=site, url=url)
        self._thread = threading.Thread(target=self._run, args=(url, site, user_agent), daemon=True)
        self._thread.start()

    def save(self) -> None:
        self._save.set()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, url: str, site: str, user_agent: str | None) -> None:
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._record(url, site, user_agent))
        except Exception as exc:  # noqa: BLE001
            self._set(status="error", message=f"No se pudo abrir el navegador: {str(exc).splitlines()[0][:200]}")
        finally:
            loop.close()

    async def _record(self, url: str, site: str, user_agent: str | None) -> None:
        from .render import _playwright
        async with _playwright() as pw:
            browser = None
            for kwargs in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
                for sandbox in (True, False):  # Chromium's sandbox on whenever the system allows it
                    try:
                        browser = await pw.chromium.launch(headless=False, chromium_sandbox=sandbox, **kwargs)
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if browser is not None:
                    break
            if browser is None:
                raise RuntimeError("no hay Chromium, Edge ni Chrome disponibles")
            closed = asyncio.Event()
            browser.on("disconnected", lambda _b: closed.set())
            context = await browser.new_context(user_agent=user_agent) if user_agent else await browser.new_context()
            page = await context.new_page()
            page.on("close", lambda _p: closed.set())
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:  # noqa: BLE001 - the user can still navigate by hand
                pass
            self._set(status="recording", message="Inicia sesión en la ventana que se ha abierto (con tu 2FA, SSO o "
                                                  "CAPTCHA) y, cuando veas tu zona privada, pulsa «Guardar sesión».")
            while not (self._save.is_set() or self._cancel.is_set() or closed.is_set()):
                await asyncio.sleep(0.3)
            if self._save.is_set() and not closed.is_set():
                saved = save(site, await context.storage_state())
                self._set(status="saved", message=f"Sesión guardada: {saved['cookies']} cookies de {site}"
                                                  + (f" (caducan el {saved['expires']} UTC)" if saved["expires"] else "") + ".")
            elif closed.is_set():
                self._set(status="idle", message="Se cerró la ventana sin guardar la sesión.")
            else:
                self._set(status="idle", message="Grabación cancelada.")
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass


recorder = Recorder()
