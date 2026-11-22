"""One-command launcher.

    py -3.12 run.py          # first run: creates .venv, installs deps, creates .env, starts the app
    py -3.12 run.py test     # run the test suite inside the same .venv

Uses only the standard library so it works before anything is installed.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQS = ROOT / "requirements.txt"

BROWSER_CHECK = """
import asyncio, sys
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        for kw in ({}, {"channel": "msedge"}, {"channel": "chrome"}):
            try:
                b = await p.chromium.launch(**kw); await b.close(); return 0
            except Exception:
                pass
    return 1
sys.exit(asyncio.run(main()))
"""


def step(msg: str) -> None:
    print(f"[letscrawl] {msg}", flush=True)


def ensure_venv() -> None:
    if not PY.exists():
        step("Creando entorno virtual en .venv ...")
        venv.create(VENV, with_pip=True)
    digest = hashlib.sha256(REQS.read_bytes()).hexdigest()
    stamp = VENV / ".requirements.sha256"
    if not stamp.exists() or stamp.read_text().strip() != digest:
        step("Instalando dependencias (solo la primera vez o si cambia requirements.txt) ...")
        subprocess.check_call([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQS)])
        stamp.write_text(digest)


def ensure_env_file() -> None:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        step("Creado .env a partir de .env.example (edítalo para claves opcionales).")


def ensure_browser() -> None:
    """Rendering needs a Chromium-family browser. Reuse Edge/Chrome if present, else install Chromium."""
    stamp = VENV / ".browser-ok"
    if stamp.exists():
        return
    if subprocess.call([str(PY), "-c", BROWSER_CHECK]) == 0:
        stamp.write_text("ok")
        return
    step("Instalando Chromium para Playwright (renderizado JavaScript) ...")
    if subprocess.call([str(PY), "-m", "playwright", "install", "chromium"]) == 0:
        stamp.write_text("ok")
    else:
        step("AVISO: no se pudo instalar Chromium. La app funciona igual; el renderizado JS quedará desactivado.")


def main() -> int:
    if sys.version_info[:2] != (3, 12):
        print(f"Se requiere Python 3.12 (estás usando {sys.version.split()[0]}). Ejecuta: py -3.12 run.py")
        return 1
    os.chdir(ROOT)
    ensure_venv()
    ensure_env_file()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        return subprocess.call([str(PY), "-m", "pytest", *sys.argv[2:]])
    if len(sys.argv) > 1 and sys.argv[1] in ("crawl", "ci", "resume", "schedule", "list"):
        return subprocess.call([str(PY), "-m", "app.cli", *sys.argv[1:]])
    ensure_browser()
    try:
        return subprocess.call([str(PY), "-m", "app"])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
