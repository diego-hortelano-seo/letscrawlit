"""Write a few whitelisted settings into the local .env (never committed: it is in .gitignore).

Used by the «Configurar IA» screen so nobody has to edit .env by hand. Only the keys below can be written,
values are checked, other lines and comments of .env are kept as they are, and the running app picks the new
values up at once (settings are read from the environment on every call).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .config import USER_DIR

ENV_PATH: Path = USER_DIR / ".env"
AI_PROVIDERS = ("anthropic", "openai", "gemini", "groq", "ollama")
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}
WRITABLE = {"AI_PROVIDER", "AI_MODEL", "OLLAMA_URL", *KEY_VARS.values()}
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.:]{8,300}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9_\-.:/]{0,100}$")
_URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(:\d{1,5})?/?$")


def check_ai(provider: str, key: str, model: str, ollama_url: str) -> str:
    """Spanish error message, or '' when the values can be saved."""
    if provider not in AI_PROVIDERS:
        return "Elige un proveedor de IA."
    if key and not _KEY_RE.match(key):
        return "La clave no parece válida: pégala entera, sin espacios ni comillas."
    if provider in KEY_VARS and not key and not os.getenv(KEY_VARS[provider]) and provider != "anthropic":
        return "Pega la clave de la API para activar este proveedor."
    if not _MODEL_RE.match(model):
        return "El nombre del modelo solo puede llevar letras, números y - _ . : /"
    if provider == "ollama" and ollama_url and not _URL_RE.match(ollama_url):
        return "La dirección de Ollama debe ser del tipo http://127.0.0.1:11434"
    return ""


def set_values(values: dict[str, str], path: Path | None = None) -> None:
    """Update (or append) KEY=value lines in .env and in the running process."""
    path = path or ENV_PATH
    bad = set(values) - WRITABLE
    if bad:
        raise ValueError(f"No se puede escribir {sorted(bad)} desde la app")
    for k, v in values.items():
        if "\n" in v or "\r" in v:
            raise ValueError(f"Valor no válido para {k}")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    for i, line in enumerate(lines):
        m = re.match(r"^\s*#?\s*([A-Z0-9_]+)\s*=", line)
        if m and m[1] in pending:
            # an active line, or the commented-out example line (`# OPENAI_API_KEY=`) becomes the real one
            if not line.lstrip().startswith("#") or not any(
                    re.match(rf"^\s*{m[1]}\s*=", other) for other in lines):
                lines[i] = f"{m[1]}={pending.pop(m[1])}"
    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"{k}={v}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in values.items():
        os.environ[k] = v


def masked(var: str) -> str:
    """'••••abcd' for a key that is set, '' otherwise. The key itself never goes back to the page."""
    v = os.getenv(var, "")
    return ("••••" + v[-4:]) if len(v) >= 8 else ("••••" if v else "")
