"""python -m app  -> start the local server."""
import threading
import webbrowser

import uvicorn

from .config import get_settings


def main() -> None:
    s = get_settings()
    from .access import password
    if s.host not in ("127.0.0.1", "localhost", "::1") and not password():
        print(f"\n  LETSCRAWL_HOST={s.host} abre LetsCrawlit a otros equipos de la red, y la app no tiene usuarios.\n"
              "  Pon también LETSCRAWL_PASSWORD=<una contraseña larga> en .env para hacerlo.\n")
        raise SystemExit(2)
    url = f"http://{s.host}:{s.port}/"
    print(f"\n  LetsCrawlit -> {url}\n  Datos en: {s.data_dir}\n  Ctrl+C para salir.\n")
    if s.open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
