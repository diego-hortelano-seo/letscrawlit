"""Crawl configuration: defaults, user-agent presets, validation and saved defaults (data/config.json)."""
from __future__ import annotations

import json
import re

from .config import Settings, get_settings
from .robots import parse_robots

UA_PRESETS = {
    "default": ("LetsCrawlit (predeterminado)", None, None),
    "googlebot_mobile": ("Googlebot para smartphones",
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "googlebot"),
    "googlebot_desktop": ("Googlebot para ordenador",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "googlebot"),
    "bingbot": ("Bingbot", "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "bingbot"),
    "chrome": ("Chrome (ordenador)",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "*"),
    "chrome_mobile": ("Chrome (móvil)",
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36", "*"),
    "chatgpt_user": ("ChatGPT-User (IA)",
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot", "chatgpt-user"),
    "gptbot": ("GPTBot (IA)", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot", "gptbot"),
    "claudebot": ("ClaudeBot (IA)", "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "claudebot"),
    "perplexitybot": ("PerplexityBot (IA)",
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "perplexitybot"),
    "custom": ("Personalizado", None, None),
}
FORBIDDEN_HEADERS = {"host", "content-length", "authorization", "cookie", "proxy-authorization", "connection",
                     "transfer-encoding", "user-agent"}
MAX_CUSTOM = 5

DEFAULTS = {
    "max_urls": None,  # filled from settings
    "delay": None,
    "render": True,
    "render_timeout": None,
    "check_external": False,
    "check_resources": True,
    "crawl_canonicals": True,
    "crawl_hreflang": True,
    "crawl_sitemaps": True,
    "max_depth": 0,
    "max_query_params": 0,
    "timeout": 20,
    "retries_5xx": 0,
    "ignore_nonindexable": True,
    "custom_robots": "",
    "include": "",
    "exclude": "",
    "remove_params": "",
    "strip_all_params": False,
    "ua_preset": "default",
    "ua_custom": "",
    "headers": "",
    "searches": [],
    "extractions": [],
    "pagespeed": False,
    "psi_max_urls": 1,
    "threads": 1,
    "verify_ssl": True,
    "dns_map": "",
    "crawl_fragments": False,
    "screenshots": False,
    "spelling": True,
    "custom_dictionary": "",
    "near_dup_threshold": 90,
    "form_login": False,
    "use_proxy": False,
    "use_saved_session": True,
    "block_third_party": True,
    "block_hosts": "",
    "js_click": False,
    "js_click_max": 20,
    "local_enabled": True,
    "local_name": "",
    "local_city": "",
    "local_phone": "",
    "local_exclude": "",
}
from . import options as _options

DEFAULTS.update(_options.DEFAULTS)
BOOL_KEYS = ("render", "check_external", "check_resources", "crawl_canonicals", "crawl_hreflang", "crawl_sitemaps",
             "ignore_nonindexable", "strip_all_params", "pagespeed", "verify_ssl", "crawl_fragments", "screenshots",
             "spelling", "form_login", "use_proxy", "local_enabled", "block_third_party", "js_click",
             "use_saved_session") + _options.BOOL_KEYS


def _config_path(settings: Settings | None = None):
    return (settings or get_settings()).data_dir / "config.json"


def base_defaults(settings: Settings | None = None) -> dict:
    s = settings or get_settings()
    d = dict(DEFAULTS)
    d.update(max_urls=s.default_max_urls, delay=s.default_delay, render_timeout=s.render_timeout)
    return d


def load_defaults(settings: Settings | None = None) -> dict:
    d = base_defaults(settings)
    try:
        saved = json.loads(_config_path(settings).read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            d.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return d


def save_defaults(cfg: dict, settings: Settings | None = None) -> None:
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: cfg[k] for k in DEFAULTS if k in cfg}, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_defaults(settings: Settings | None = None) -> None:
    try:
        _config_path(settings).unlink()
    except FileNotFoundError:
        pass


def _flag(form: dict, key: str) -> bool:
    return form.get(key) in ("yes", "on", "true", "1")


def _int(form: dict, key: str, lo: int, hi: int, label: str, errors: dict, default: int) -> int:
    raw = str(form.get(key, "")).strip()
    if raw == "":
        return default
    try:
        v = int(raw)
        if not lo <= v <= hi:
            raise ValueError
        return v
    except ValueError:
        errors[key] = f"{label}: número entre {lo} y {hi}."
        return default


def _lines(text: str) -> list[str]:
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def parse_config(form: dict, settings: Settings, errors: dict) -> dict:
    """Validate every configuration field from the form. Adds messages to `errors`."""
    cfg = load_defaults(settings)
    # Checkboxes are only sent when ticked. With the config panel present (cfg_panel=1) an absent box means "off";
    # without it (scripted posts) the saved defaults apply and only explicit "yes" values switch options on.
    panel = bool(form.get("cfg_panel"))
    for key in BOOL_KEYS:
        cfg[key] = _flag(form, key) if panel else (_flag(form, key) or bool(cfg.get(key)))
    if not panel:  # fill every other field from the saved defaults unless the post sets it
        base = {k: str(v) for k, v in cfg.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
        for i, s in enumerate(cfg.get("searches") or []):
            base.update({f"search_name_{i}": s["name"], f"search_mode_{i}": s["mode"], f"search_text_{i}": s["text"]})
        for i, e in enumerate(cfg.get("extractions") or []):
            base.update({f"extract_name_{i}": e["name"], f"extract_type_{i}": e["type"], f"extract_expr_{i}": e["expr"]})
        form = {**base, **form}

    cfg["max_depth"] = _int(form, "max_depth", 0, 100, "Profundidad máxima", errors, 0)
    cfg["max_query_params"] = _int(form, "max_query_params", 0, 50, "Máximo de parámetros", errors, 0)
    cfg["timeout"] = _int(form, "timeout", 3, 120, "Tiempo de espera", errors, 20)
    cfg["retries_5xx"] = _int(form, "retries_5xx", 0, 3, "Reintentos 5xx", errors, 0)
    cfg["render_timeout"] = _int(form, "render_timeout", 3, 120, "Tiempo de renderizado", errors, settings.render_timeout)
    cfg["threads"] = _int(form, "threads", 1, 5, "Hilos", errors, 1)
    cfg["psi_max_urls"] = _int(form, "psi_max_urls", 1, 500, "URLs para PageSpeed", errors, 1)
    cfg["near_dup_threshold"] = _int(form, "near_dup_threshold", 50, 100, "Umbral de casi duplicados", errors, 90)
    cfg["custom_dictionary"] = (form.get("custom_dictionary") or "")[:20000]
    dns_text = (form.get("dns_map") or "")[:5000]
    for line in _lines(dns_text):
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9.-]+", parts[0]) or not (
                re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", parts[1]) or re.fullmatch(r"[0-9a-fA-F:]+", parts[1])):
            errors["dns_map"] = f"DNS: «{line[:60]}» debe ser «dominio IP», p. ej. misitio.es 203.0.113.10"
            break
    cfg["dns_map"] = dns_text

    _options.parse_into(cfg, form, errors, panel)
    cfg["js_click_max"] = _int(form, "js_click_max", 1, 100, "Clics por página", errors, 20)
    block_text = (form.get("block_hosts") or "")[:5000]
    for line in _lines(block_text):
        host = line.lower().removeprefix("https://").removeprefix("http://")
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}(/[\w./-]*)?", host):
            errors["block_hosts"] = f"Bloquear también: «{line[:60]}» debe ser un dominio, p. ej. widget.ejemplo.com"
            break
    cfg["block_hosts"] = block_text
    cfg["local_name"] =(form.get("local_name") or "").strip()[:120]
    cfg["local_city"] = (form.get("local_city") or "").strip()[:300]
    phone = (form.get("local_phone") or "").strip()[:40]
    if phone and (not re.fullmatch(r"[+\d\s().\-]+", phone) or len(re.sub(r"\D", "", phone)) < 6):
        errors["local_phone"] = "Teléfono principal: solo números, espacios y +, p. ej. 985 123 456."
    cfg["local_phone"] = phone
    local_exclude = (form.get("local_exclude") or "")[:3000]
    for pattern in _lines(local_exclude):
        try:
            re.compile(pattern)
        except re.error as exc:
            errors["local_exclude"] = f"Páginas que no son de servicio: la expresión «{pattern[:60]}» no es válida ({exc})."
            break
    cfg["local_exclude"] = local_exclude

    robots = (form.get("custom_robots") or "")[:20000]
    if robots.strip():
        groups, _ = parse_robots(robots)
        if not groups:
            errors["custom_robots"] = "El robots.txt personalizado no tiene ningún grupo «User-agent:»."
    cfg["custom_robots"] = robots

    for key, label in (("include", "Incluir"), ("exclude", "Excluir")):
        text = (form.get(key) or "")[:5000]
        for pattern in _lines(text):
            try:
                re.compile(pattern)
            except re.error as exc:
                errors[key] = f"{label}: la expresión «{pattern[:60]}» no es válida ({exc})."
                break
        cfg[key] = text

    params = (form.get("remove_params") or "")[:2000]
    if any(not re.fullmatch(r"[\w.\-\[\]]+", p) for p in _lines(params)):
        errors["remove_params"] = "Parámetros a eliminar: un nombre por línea (letras, números, _ . - [ ])."
    cfg["remove_params"] = params

    preset = form.get("ua_preset") or "default"
    cfg["ua_preset"] = preset if preset in UA_PRESETS else "default"
    cfg["ua_custom"] = (form.get("ua_custom") or "").strip()[:500]
    if cfg["ua_preset"] == "custom" and not cfg["ua_custom"]:
        errors["ua_custom"] = "Escribe el User-Agent personalizado."
    if any(ord(c) < 32 for c in cfg["ua_custom"]):
        errors["ua_custom"] = "El User-Agent no puede contener saltos de línea."

    headers_text = (form.get("headers") or "")[:5000]
    for line in _lines(headers_text):
        if ":" not in line:
            errors["headers"] = f"Cabecera «{line[:60]}»: usa el formato Nombre: valor."
            break
        name = line.split(":", 1)[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9-]+", name):
            errors["headers"] = f"Nombre de cabecera no válido: «{name[:40]}»."
            break
        if name.lower() in FORBIDDEN_HEADERS:
            errors["headers"] = (f"La cabecera «{name}» no se permite aquí"
                                 + (" (las credenciales van en .env)." if name.lower() in ("authorization", "cookie") else "."))
            break
    cfg["headers"] = headers_text

    searches, extractions = [], []
    for i in range(MAX_CUSTOM):
        text = (form.get(f"search_text_{i}") or "").strip()[:200]
        if text:
            mode = form.get(f"search_mode_{i}") if form.get(f"search_mode_{i}") in ("contains", "not_contains") else "contains"
            name = (form.get(f"search_name_{i}") or "").strip()[:40] or f"Búsqueda {i + 1}"
            searches.append({"name": name, "mode": mode, "text": text})
        expr = (form.get(f"extract_expr_{i}") or "").strip()[:300]
        if expr:
            kind = form.get(f"extract_type_{i}") if form.get(f"extract_type_{i}") in ("css", "regex", "xpath") else "css"
            name = (form.get(f"extract_name_{i}") or "").strip()[:40] or f"Extracción {i + 1}"
            if kind == "xpath":
                try:
                    from lxml import etree
                    etree.XPath(expr)
                except Exception as exc:  # noqa: BLE001
                    errors[f"extract_expr_{i}"] = f"Extracción «{name}»: XPath no válido ({str(exc)[:80]})."
            elif kind == "regex":
                try:
                    re.compile(expr)
                except re.error as exc:
                    errors[f"extract_expr_{i}"] = f"Extracción «{name}»: regex no válida ({exc})."
            else:
                try:
                    import soupsieve
                    soupsieve.compile(expr)
                except Exception as exc:  # noqa: BLE001
                    errors[f"extract_expr_{i}"] = f"Extracción «{name}»: selector CSS no válido ({str(exc)[:80]})."
            extractions.append({"name": name, "type": kind, "expr": expr})
    cfg["searches"], cfg["extractions"] = searches, extractions
    return cfg


def user_agent(cfg: dict, settings: Settings) -> tuple[str, str]:
    """(User-Agent header, robots.txt token)."""
    preset = cfg.get("ua_preset", "default")
    if preset == "custom" and cfg.get("ua_custom"):
        ua = cfg["ua_custom"]
        token = ua.split("/")[0].split()[0].lower() if ua else "*"
        for known in ("googlebot", "bingbot"):
            if known in ua.lower():
                token = known
        return ua, token
    label, ua, token = UA_PRESETS.get(preset, UA_PRESETS["default"])
    if ua:
        return ua, token
    return settings.user_agent, settings.user_agent.split("/")[0].split()[0].lower()


def header_dict(cfg: dict) -> dict:
    out = {}
    for line in _lines(cfg.get("headers") or ""):
        name, value = line.split(":", 1)
        out[name.strip()] = value.strip()
    return out
