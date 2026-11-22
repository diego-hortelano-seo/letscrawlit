"""Command line: unattended audits and scheduling.

    py -3.12 run.py crawl https://misitio.es/ --i-own-this-site [--max-urls 500] [--delay 1] [--out carpeta]
    py -3.12 run.py schedule https://misitio.es/ --i-own-this-site --daily 03:00 [--install]
    py -3.12 run.py ci https://staging.misitio.es/ --i-own-this-site --fail-on critical,high --junit seo.xml
    py -3.12 run.py resume 12
    py -3.12 run.py list

`crawl` uses the saved default configuration (data/config.json) plus the flags, runs to the end,
and writes the ZIP (CSV + HTML report + manifest) to --out (default data/exports).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import os
import sys
from pathlib import Path

from . import crawlconfig, crawler, db, report
from .config import BASE_DIR, FROZEN, PACKAGED, get_settings
from .validation import ACK_TEXT, validate_crawl_form


def _crawl(args) -> int:
    crawl_id = _start_crawl(args)
    if crawl_id < 0:
        return 2
    return _finish(crawl_id, args.out)


def _start_crawl(args) -> int:
    """Validate, create and run a crawl. Returns the crawl id, or -1 on a usage/validation error."""
    if not args.i_own_this_site:
        print("Falta --i-own-this-site: confirma que el sitio es tuyo o que tienes permiso para rastrearlo.")
        return -1
    s = get_settings()
    db.init_db()
    defaults = crawlconfig.load_defaults(s)
    form = {"url": args.url, "ack": "yes", "max_urls": str(args.max_urls or defaults["max_urls"]),
            "delay": str(args.delay if args.delay is not None else defaults["delay"])}
    if args.no_render:
        form["cfg_panel"], defaults["render"] = "1", False
        for k in crawlconfig.BOOL_KEYS:
            if defaults.get(k) and k != "render":
                form[k] = "yes"
    cfg, errors = validate_crawl_form(form, s)
    if errors:
        for k, v in errors.items():
            print(f"Error en {k}: {v}")
        return -1
    crawl_id = db.create_crawl(cfg["start_url"], cfg, ACK_TEXT)
    print(f"Rastreando {cfg['start_url']} (auditoría #{crawl_id})…")
    crawler.run_sync(crawl_id)
    return crawl_id


def _ci(args) -> int:
    """Crawl, then pass/fail like a test suite: exit 0 = OK, 1 = SEO checks failed, 2 = could not run."""
    from . import ci
    crawl_id = _start_crawl(args)
    if crawl_id < 0:
        return 2
    return ci.evaluate_and_report(crawl_id, args)


def _finish(crawl_id: int, out: str | None) -> int:
    c = db.get_crawl(crawl_id)
    print(f"Estado: {c['status']} · {c['pages_done']} URLs · {(c['extras'].get('summary') or {}).get('issues', 0)} incidencias")
    if c["error"]:
        print("Error:", c["error"])
    name, _, saved = report.bundle(crawl_id)
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        dest = Path(out) / name
        shutil.copyfile(saved, dest)
        saved = dest
    print(f"Exportado: {saved}")
    return 0 if c["status"] in ("completed", "cancelled") else 1


def _resume(args) -> int:
    db.init_db()
    c = db.get_crawl(args.crawl_id)
    if not c or not c.get("state_json"):
        print("Ese rastreo no existe o no tiene un punto de reanudación.")
        return 2
    crawler.run_sync(args.crawl_id, resume=True)
    return _finish(args.crawl_id, args.out)


def schedule_command(url: str, when: str, daily: bool, weekly: str | None, monthly: int | None) -> list[str]:
    """Windows Task Scheduler command (schtasks) that runs an unattended audit."""
    py = Path(sys.executable)
    if PACKAGED:  # Microsoft Store version: start it through its alias, never from the WindowsApps folder
        py = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "Microsoft" / "WindowsApps" / "letscrawlit.exe"
    safe_url = url.replace('"', "%22")  # URLs have no spaces (validated); quoted so nothing else becomes an argument
    if FROZEN:  # the installed program: LetsCrawlit.exe runs the audit itself, without opening a window
        runner = f'"{py}" crawl "{safe_url}" --i-own-this-site'
    else:
        runner = f'"{py}" "{BASE_DIR / "run.py"}" crawl "{safe_url}" --i-own-this-site'
    name = "LetsCrawlit-" +"".join(ch if ch.isalnum() else "-" for ch in url.split("://")[-1])[:60]
    cmd = ["schtasks", "/Create", "/F", "/TN", name, "/TR", runner, "/ST", when]
    if weekly:
        cmd += ["/SC", "WEEKLY", "/D", weekly]
    elif monthly:
        cmd += ["/SC", "MONTHLY", "/D", str(monthly)]
    else:
        cmd += ["/SC", "DAILY"]
    return cmd


def _schedule(args) -> int:
    if not args.i_own_this_site:
        print("Falta --i-own-this-site.")
        return 2
    if sys.platform != "win32":
        print(f"En macOS/Linux añade a tu crontab (crontab -e), p. ej. a las {args.time}:")
        h, m = args.time.split(":")
        import shlex
        print(f"{int(m)} {int(h)} * * * cd {shlex.quote(str(BASE_DIR))} && {shlex.quote(sys.executable)} run.py crawl "
              f"{shlex.quote(args.url)} --i-own-this-site")
        return 0
    cmd = schedule_command(args.url, args.time, True, args.weekly, args.monthly)
    print("Comando de tarea programada de Windows:")
    print(subprocess.list2cmdline(cmd))
    if args.install:
        return subprocess.call(cmd)
    print("Añade --install para crearla ahora (o ejecuta el comando tú mismo). Para borrarla: schtasks /Delete /TN <nombre>")
    return 0


def _list(_args) -> int:
    db.init_db()
    for c in db.list_crawls(50):
        print(f"#{c['id']:>4}  {c['status']:<12} {c['pages_done']:>6} URLs  {c['created_at'][:16]}  {c['start_url']}")
    return 0


def _utf8_console() -> None:
    """Windows consoles default to an old code page: accents and ✅/❌ would crash the program mid-audit."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:  # noqa: BLE001 - no console attached (CI log, scheduled task): UTF-8 below is enough
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    p = argparse.ArgumentParser(prog="letscrawlit-cli" if FROZEN else "run.py", description="LetsCrawlit - línea de comandos")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="Rastrear y exportar sin interfaz")
    c.add_argument("url")
    c.add_argument("--i-own-this-site", action="store_true", help="Confirmo que el sitio es mío o tengo permiso")
    c.add_argument("--max-urls", type=int)
    c.add_argument("--delay", type=float)
    c.add_argument("--no-render", action="store_true")
    c.add_argument("--out")
    c.set_defaults(func=_crawl)
    ci = sub.add_parser("ci", help="Rastrear y aprobar o suspender como un test (GitHub Actions, GitLab, Jenkins…)")
    ci.add_argument("url")
    ci.add_argument("--i-own-this-site", action="store_true", help="Confirmo que el sitio es mío o tengo permiso")
    ci.add_argument("--max-urls", type=int)
    ci.add_argument("--delay", type=float)
    ci.add_argument("--no-render", action="store_true")
    ci.add_argument("--fail-on", default="critical,high", metavar="GRAVEDADES",
                    help="Gravedades que suspenden (critical,high,medium,low o none). Por defecto: critical,high")
    ci.add_argument("--fail-on-code", default="", metavar="CÓDIGOS",
                    help="Códigos de incidencia que suspenden siempre, separados por comas (p. ej. noindex_in_sitemap,broken_link)")
    ci.add_argument("--ignore-code", default="", metavar="CÓDIGOS", help="Códigos que nunca suspenden")
    ci.add_argument("--require-indexable", action="append", default=[], metavar="URL",
                    help="URL que debe ser indexable (repetible). La URL inicial se comprueba siempre salvo --allow-noindex-home")
    ci.add_argument("--allow-noindex-home", action="store_true")
    ci.add_argument("--baseline", metavar="JSON", help="Resultado JSON de una ejecución anterior: solo suspenden las incidencias nuevas")
    ci.add_argument("--json", metavar="ARCHIVO", help="Guardar el resultado en JSON (sirve de --baseline la próxima vez)")
    ci.add_argument("--junit", metavar="ARCHIVO", help="Guardar un informe JUnit XML (GitHub, GitLab y Jenkins lo muestran)")
    ci.add_argument("--markdown", metavar="ARCHIVO", help="Guardar un resumen en Markdown")
    ci.add_argument("--webhook", metavar="URL", help="Enviar el resumen por POST (JSON con «text», válido para Slack/Teams/Discord)")
    ci.add_argument("--out", help="Carpeta donde dejar también el ZIP de la auditoría")
    ci.set_defaults(func=_ci)
    r = sub.add_parser("resume", help="Reanudar un rastreo en pausa o interrumpido")
    r.add_argument("crawl_id", type=int)
    r.add_argument("--out")
    r.set_defaults(func=_resume)
    s = sub.add_parser("schedule", help="Programar una auditoría periódica")
    s.add_argument("url")
    s.add_argument("--i-own-this-site", action="store_true")
    s.add_argument("--daily", dest="time", default="03:00", metavar="HH:MM")
    s.add_argument("--weekly", metavar="DIA", help="MON, TUE, WED, THU, FRI, SAT o SUN")
    s.add_argument("--monthly", type=int, metavar="N", help="Día del mes")
    s.add_argument("--install", action="store_true", help="Crear la tarea en el Programador de tareas")
    s.set_defaults(func=_schedule)
    sub.add_parser("list", help="Ver auditorías guardadas").set_defaults(func=_list)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
