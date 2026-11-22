"""LetsCrawlit as a Windows program: its own window, no browser tab, no console.

The app still runs as a small local server (only on 127.0.0.1, so nothing is reachable from the network) and
the window shows it with WebView2, the web engine that comes with Windows 10 and 11. Closing the window
stops everything. Used by LetsCrawlit.exe (see letscrawl.spec); `python desktop.py` does the same from the code.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
FIRST_PORT = 8765


def _quiet_streams(log_dir: Path) -> None:
    """A windowed program has no console: give Python somewhere to write so logging never crashes."""
    if sys.stdout is None or sys.stderr is None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "letscrawl.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives with the app
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _running_letscrawl(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=1.5) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except Exception:  # noqa: BLE001 - nothing (or something else) on that port
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))  # only free if we could take it ourselves
            return True
        except OSError:
            return False


def _pick_port() -> tuple[int, bool]:
    """(port, already_running). The same port every time keeps the window's preferences (theme, tabs);
    if LetsCrawlit is already open there, the new window just shows it."""
    for port in range(FIRST_PORT, FIRST_PORT + 20):
        if _running_letscrawl(port):
            return port, True
        if _port_free(port):
            return port, False
    raise SystemExit("No hay ningún puerto libre entre 8765 y 8784.")


def _start_server(port: int):
    import uvicorn

    from .main import app
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_config=None, log_level="warning"))
    thread = threading.Thread(target=server.run, name="letscrawl-server", daemon=True)
    thread.start()
    for _ in range(200):  # up to 20 s for the first start (the database is created then)
        if server.started:
            break
        time.sleep(0.1)
    return server, thread


def main() -> None:
    os.environ["LETSCRAWL_HOST"] = HOST          # the window is the only client: never listen on the network
    os.environ["LETSCRAWL_OPEN_BROWSER"] = "0"
    from .config import USER_DIR
    _quiet_streams(USER_DIR)
    if len(sys.argv) > 1:  # a scheduled audit («LetsCrawlit.exe crawl <url> --i-own-this-site»): no window at all
        from .cli import main as cli_main
        raise SystemExit(cli_main(sys.argv[1:]))
    port, running = _pick_port()
    os.environ["LETSCRAWL_PORT"] = str(port)
    server = thread = None
    if not running:
        server, thread = _start_server(port)
    url = f"http://{HOST}:{port}/"
    try:
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True                   # CSV, PDF and Excel exports
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True    # «Abrir ↗» opens the audited page outside
        webview.create_window("LetsCrawlit", url, width=1440, height=900, min_size=(960, 600), text_select=True)
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(USER_DIR / "window"))
    except Exception:  # noqa: BLE001 - no WebView2 on this computer: use the normal browser instead
        import webbrowser
        webbrowser.open(url)
        if thread:
            thread.join()  # keep serving until the process is closed
        return
    if server:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
