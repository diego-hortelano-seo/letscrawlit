"""Settings loaded from environment / .env. Read on each call so tests can override."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION = "0.3.0"

# Inside the Windows program (LetsCrawlit.exe) the program folder can be replaced by a new version, so the
# user's things — .env with their keys, crawls, notes — live in %APPDATA%\LetsCrawlit instead.
FROZEN = bool(getattr(sys, "frozen", False))
# Installed from the Microsoft Store (MSIX): the program sits in a protected WindowsApps folder that other programs
# (Task Scheduler) cannot start by path; they use the «letscrawlit.exe» alias Windows creates instead.
PACKAGED = FROZEN and "\\windowsapps\\" in str(Path(sys.executable)).lower()
_APPDATA = Path(os.getenv("APPDATA") or Path.home())
USER_DIR = _APPDATA / "LetsCrawlit" if FROZEN else BASE_DIR


def _adopt_old_data(old: Path, new: Path) -> Path:
    """The program was called «LetsCrawl» up to 0.2.x: bring the user's audits, notes and keys to the new folder.

    If both folders exist (an even older build already used the new name), the one with the most recent database
    wins and the other is kept next to it as «…-anterior-<fecha>», so nothing is ever deleted."""
    def last_used(folder: Path) -> float:
        db_file = folder / "data" / "letscrawl.db"
        return db_file.stat().st_mtime if db_file.exists() else 0.0
    if not old.is_dir() or old == new:
        return new
    try:
        if new.exists():
            if last_used(new) >= last_used(old):
                return new
            from datetime import date
            new.rename(new.with_name(f"{new.name}-anterior-{date.today():%Y-%m-%d}"))
        old.rename(new)
        return new
    except OSError:  # a folder still in use (an old window open): keep working where the newest data is
        return old if last_used(old) > last_used(new) else new


if FROZEN:
    USER_DIR = _adopt_old_data(_APPDATA / "LetsCrawl", USER_DIR)
    USER_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(USER_DIR / ".env", override=False)


APP_NAME = "LetsCrawlit"


def _env(key: str, default: str | None = None) -> str | None:
    """LETSCRAWL_<key>, falling back to the old SEOSPIDER_<key> so existing .env files keep working."""
    value = os.getenv(f"LETSCRAWL_{key}")
    return value if value is not None else os.getenv(f"SEOSPIDER_{key}", default)


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(_env(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(_env(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    open_browser: bool
    data_dir: Path
    user_agent: str
    default_max_urls: int
    max_urls_limit: int
    default_delay: float
    min_delay: float
    render_timeout: int
    auth_user: str
    auth_password: str
    pagespeed_key: str
    cookies: str = ""
    proxy: str = ""
    login_url: str = ""
    login_user: str = ""
    login_password: str = ""
    login_user_selector: str = ""
    login_password_selector: str = ""
    login_submit_selector: str = ""
    ai_provider: str = ""
    ai_model: str = ""
    ollama_url: str = ""
    copyscape_user: str = ""
    copyscape_key: str = ""

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def db_path(self) -> Path:
        new, old = self.data_dir / "letscrawl.db", self.data_dir / "seospider.db"
        return old if old.exists() and not new.exists() else new  # keep using a database created before the rename

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


def _default_max(limit: int, default: int) -> int:
    """0 = no limit on the crawl. A safety cap set in .env (MAX_URLS_LIMIT) always wins."""
    if limit:
        return min(limit, default) if default else limit
    return default


def get_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = USER_DIR / data_dir
    limit = _int("MAX_URLS_LIMIT", 0, 0, 10_000_000)  # 0 = no safety cap
    ua = _env("USER_AGENT", f"LetsCrawlit/{VERSION}")
    if ua.startswith(("SEOSpiderLocal/", "LetsCrawl/")):  # old names in existing .env files
        ua = f"LetsCrawlit/{VERSION}" + (" " + ua.split(" ", 1)[1] if " " in ua else "")
    return Settings(
        host=_env("HOST", "127.0.0.1"),
        port=_int("PORT", 8765, 1, 65535),
        open_browser=_env("OPEN_BROWSER", "1") == "1",
        data_dir=data_dir,
        user_agent=ua,
        default_max_urls=_default_max(limit, _int("DEFAULT_MAX_URLS", 0, 0, 10_000_000)),
        max_urls_limit=limit,
        default_delay=_float("DEFAULT_DELAY", 1.0, 0.0, 60.0),
        min_delay=_float("MIN_DELAY", 0.2, 0.0, 60.0),
        render_timeout=_int("RENDER_TIMEOUT", 20, 3, 120),
        auth_user=os.getenv("CRAWL_AUTH_USER", ""),
        auth_password=os.getenv("CRAWL_AUTH_PASSWORD", ""),
        pagespeed_key=os.getenv("PAGESPEED_API_KEY", ""),
        cookies=os.getenv("CRAWL_COOKIES", ""),
        proxy=os.getenv("CRAWL_PROXY", ""),
        login_url=os.getenv("LOGIN_URL", ""),
        login_user=os.getenv("LOGIN_USER", ""),
        login_password=os.getenv("LOGIN_PASSWORD", ""),
        login_user_selector=os.getenv("LOGIN_USER_SELECTOR", "input[type=email], input[name*=user], input[name*=log]"),
        login_password_selector=os.getenv("LOGIN_PASSWORD_SELECTOR", "input[type=password]"),
        login_submit_selector=os.getenv("LOGIN_SUBMIT_SELECTOR", "button[type=submit], input[type=submit]"),
        ai_provider=os.getenv("AI_PROVIDER", "").lower(),
        ai_model=os.getenv("AI_MODEL", ""),
        ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        copyscape_user=os.getenv("COPYSCAPE_USER", ""),
        copyscape_key=os.getenv("COPYSCAPE_KEY", ""),
    )
