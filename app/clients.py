"""Client profiles: who the client is and how they write, so the AI writes titles, descriptions and the rest in
their voice. Reused in any audit; the last client used on a site is picked again for that site.

Stored in data/clients.json next to the database (data/ is never committed).
"""
from __future__ import annotations

import json
import re
import secrets
import threading

from .config import get_settings

MAX_CLIENTS = 200
# field: (label shown in the form, max length)
FIELDS = {
    "name": ("Nombre del cliente", 120),
    "sector": ("Sector", 200),
    "audience": ("Público objetivo", 400),
    "tone": ("Tono y estilo de escritura", 1500),
    "avoid": ("Términos a evitar", 600),
    "context": ("Contexto del negocio", 4000),
}
_lock = threading.Lock()


def _path():
    return get_settings().data_dir / "clients.json"


def load() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = [c for c in data if isinstance(c, dict) and c.get("id") and c.get("name")] if isinstance(data, list) else []
    return sorted(items, key=lambda c: c["name"].lower())


def get(cid: str | None) -> dict | None:
    return next((c for c in load() if c["id"] == cid), None) if cid else None


def _write(items: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def clean(form: dict) -> dict:
    out = {}
    for k, (_, limit) in FIELDS.items():
        v = str(form.get(k) or "").replace("\r\n", "\n").strip()[:limit]
        out[k] = re.sub(r"[ \t]+", " ", v) if k == "name" else v
    return out


def save(form: dict, cid: str | None = None) -> dict:
    """Create or update a profile. Raises ValueError with a Spanish message."""
    data = clean(form)
    if not data["name"]:
        raise ValueError("Escribe al menos el nombre del cliente.")
    with _lock:
        items = load()
        old = next((c for c in items if c["id"] == cid), None) if cid else None
        if any(c["name"].lower() == data["name"].lower() and c is not old for c in items):
            raise ValueError(f"Ya hay un cliente llamado «{data['name']}».")
        if old:
            old.update(data)
            item = old
        else:
            if len(items) >= MAX_CLIENTS:
                raise ValueError(f"Como mucho {MAX_CLIENTS} clientes: borra alguno antes.")
            item = {"id": secrets.token_hex(4), "sites": [], **data}
            items.append(item)
        _write(items)
    return item


def delete(cid: str) -> None:
    with _lock:
        _write([c for c in load() if c["id"] != cid])


def for_site(site: str) -> dict | None:
    """The client last used on this site, so it comes chosen next time."""
    return next((c for c in load() if site and site in (c.get("sites") or [])), None)


def remember_site(cid: str | None, site: str) -> None:
    """Link the site to this client only (a site belongs to one client)."""
    if not site:
        return
    with _lock:
        items = load()
        changed = False
        for c in items:
            sites = [s for s in c.get("sites") or [] if s != site]
            if c["id"] == cid:
                sites.append(site)
            if sites != (c.get("sites") or []):
                c["sites"], changed = sites[-50:], True
        if changed:
            _write(items)


def prompt_block(client: dict | None) -> str:
    """The client's brief, in the words the AI reads before the page."""
    if not client:
        return ""
    lines = ["Contexto del cliente para el que escribes. Respétalo en todo lo que escribas:",
             f"- Cliente: {client.get('name')}"]
    for k, label in (("sector", "Sector"), ("audience", "Público objetivo"), ("tone", "Tono y estilo de escritura"),
                     ("context", "Negocio, servicios y qué le diferencia")):
        if client.get(k):
            lines.append(f"- {label}: {client[k]}")
    if client.get("avoid"):
        lines.append(f"- Términos que NUNCA debes usar: {client['avoid']}")
    lines.append("Usa este contexto para el enfoque y el tono, pero no afirmes nada que no aparezca en la página o en este contexto.")
    return "\n".join(lines)
