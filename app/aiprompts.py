"""Your own AI instructions, saved with a name so they can be reused in any audit.

Stored in data/ai_prompts.json next to the database (data/ is never committed).
"""
from __future__ import annotations

import json
import re
import secrets
import threading

from .config import get_settings

MAX_PROMPTS = 50
MAX_NAME = 60
MAX_PROMPT = 2000
_lock = threading.Lock()


def _path():
    return get_settings().data_dir / "ai_prompts.json"


def load() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, dict) and {"id", "name", "prompt"} <= p.keys()] if isinstance(data, list) else []


def get(pid: str) -> dict | None:
    return next((p for p in load() if p["id"] == pid), None)


def _write(items: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def add(name: str, prompt: str) -> dict:
    """Save (or replace, same name) an instruction. Raises ValueError with a Spanish message."""
    name = re.sub(r"\s+", " ", name or "").strip()[:MAX_NAME]
    prompt = (prompt or "").strip()[:MAX_PROMPT]
    if not prompt:
        raise ValueError("Escribe primero la instrucción que quieres guardar.")
    if not name:
        name = prompt.split("\n")[0][:MAX_NAME]
    with _lock:
        items = [p for p in load() if p["name"].lower() != name.lower()]
        if len(items) >= MAX_PROMPTS:
            raise ValueError(f"Como mucho {MAX_PROMPTS} instrucciones guardadas: borra alguna antes.")
        item = {"id": secrets.token_hex(4), "name": name, "prompt": prompt}
        _write(items + [item])
    return item


def delete(pid: str) -> None:
    with _lock:
        _write([p for p in load() if p["id"] != pid])
