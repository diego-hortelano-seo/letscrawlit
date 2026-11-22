"""Validation of the new-crawl form."""
from __future__ import annotations

from urllib.parse import urlsplit

from .config import Settings
from .crawlconfig import parse_config, user_agent
from .urlutil import normalize

ACK_TEXT = ("Confirmo que soy el propietario de este sitio web o que cuento con permiso explícito "
            "de su propietario para rastrearlo y auditarlo.")


def check_url(raw_url: str) -> tuple[str | None, str | None]:
    """Return (normalized_url, error_message)."""
    raw_url = (raw_url or "").strip()
    if raw_url and "://" not in raw_url:
        raw_url = "https://" + raw_url
    if not raw_url:
        return None, "Introduce la URL de inicio."
    if len(raw_url) > 2000 or any(c.isspace() for c in raw_url):
        return None, "La URL no es válida (contiene espacios o es demasiado larga)."
    try:
        parts = urlsplit(raw_url)
    except ValueError:
        parts = None
    if parts is None or parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None, "Usa una URL http:// o https:// con dominio, p. ej. https://misitio.es/"
    if parts.username or parts.password:
        return None, "No pongas usuario/contraseña en la URL; usa CRAWL_AUTH_USER/PASSWORD en .env."
    url = normalize(raw_url)
    return (url, None) if url else (None, "La URL no es válida.")


def validate_crawl_form(form: dict, settings: Settings) -> tuple[dict | None, dict[str, str]]:
    errors: dict[str, str] = {}
    mode = form.get("mode") if form.get("mode") in ("spider", "list") else "spider"
    urls: list[str] = []
    start_url = None
    if mode == "spider":
        start_url, err = check_url(form.get("url") or "")
        if err:
            errors["url"] = err
    else:
        lines = [l.strip() for l in (form.get("urls") or "").splitlines() if l.strip()]
        if not lines:
            errors["urls"] = "Pega al menos una URL (una por línea). Puedes incluir un sitemap .xml."
        elif settings.max_urls_limit and len(lines) > settings.max_urls_limit:
            errors["urls"] = f"Como máximo {settings.max_urls_limit} URLs por lista."
        else:
            for n, line in enumerate(lines, 1):
                url, err = check_url(line)
                if err:
                    errors["urls"] = f"Línea {n} («{line[:60]}»): {err}"
                    break
                urls.append(url)
            urls = list(dict.fromkeys(urls))
            start_url = urls[0] if urls else None

    max_urls = parse_max_urls(form, settings, errors)

    try:
        delay = float(str(form.get("delay") or settings.default_delay).replace(",", "."))
        if not settings.min_delay <= delay <= 60:
            raise ValueError
    except (TypeError, ValueError):
        errors["delay"] = f"La pausa entre peticiones debe estar entre {settings.min_delay} y 60 segundos."
        delay = None

    if form.get("ack") not in ("yes", "on", "true", "1"):
        errors["ack"] = "Debes confirmar que el sitio es tuyo o que tienes permiso para rastrearlo."

    cfg = parse_config(form, settings, errors)
    if errors:
        return None, errors
    ua, token = user_agent(cfg, settings)
    cfg.update({
        "mode": mode, "start_url": start_url, "urls": urls, "max_urls": max_urls, "delay": delay,
        "respect_robots": True, "user_agent": ua, "robots_token": token,
        # one-click audit from the start page: only decides which tab the results open on
        "preset": form.get("preset") if form.get("preset") in ("broken", "images", "sitemap") else "",
    })
    return cfg, {}


def parse_max_urls(form: dict, settings: Settings, errors: dict[str, str]) -> int | None:
    """Total URL cap. Empty, 0 or an unticked box means no limit (0), unless .env sets a safety cap."""
    limit = settings.max_urls_limit
    raw = str(form.get("max_urls") or "").strip()
    try:
        n = int(raw) if raw else 0
        if n < 0 or (limit and n > limit):
            raise ValueError
    except (TypeError, ValueError):
        errors["max_urls"] = (f"El tope de URLs debe ser un número entre 1 y {limit}." if limit
                              else "El tope de URLs debe ser un número positivo, o desmarca la casilla para no limitar.")
        return None
    return n or limit  # 0 = no limit; with a safety cap, the cap


def config_only(form: dict, settings: Settings) -> tuple[dict, dict[str, str]]:
    """Validate just the configuration panel (used by 'Guardar como predeterminada')."""
    errors: dict[str, str] = {}
    cfg = parse_config(form, settings, errors)
    max_urls = parse_max_urls(form, settings, errors)
    if max_urls is not None:
        cfg["max_urls"] = max_urls
    for key, lo, hi, cast in (("delay", settings.min_delay, 60, float),):
        try:
            v = cast(str(form.get(key) or cfg[key]).replace(",", "."))
            if not lo <= v <= hi:
                raise ValueError
            cfg[key] = v
        except (TypeError, ValueError):
            errors[key] = f"Valor fuera de rango ({lo}–{hi})."
    return cfg, errors
