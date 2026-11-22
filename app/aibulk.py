"""Run one AI task over many pages (the rows of the current tab and filter) in the background.

Results are stored like the single-page ones (ai_results table) and appear as columns in the «IA» tab, so they
can be filtered, sorted and exported with the rest of the crawl. One request per page, one at a time.
"""
from __future__ import annotations

import threading
import time

from . import ai, db

MAX_PAGES = 500
_jobs: dict[int, dict] = {}
_lock = threading.Lock()


def status(crawl_id: int) -> dict | None:
    with _lock:
        job = _jobs.get(crawl_id)
        return dict(job) if job else None


def start(crawl_id: int, task: str, urls: list[str], custom_prompt: str = "", label: str = "") -> dict:
    if task not in ai.BULK_TASKS:
        raise ai.AIUnavailable("Esa tarea no se puede aplicar en bloque.")
    st = ai.status()
    if not st["enabled"]:
        raise ai.AIUnavailable(st["reason"])
    if task == "custom" and not custom_prompt.strip():
        raise ai.AIUnavailable("Escribe la instrucción que quieres aplicar a cada página.")
    urls = list(dict.fromkeys(urls))[:MAX_PAGES]
    if not urls:
        raise ai.AIUnavailable("No hay páginas HTML en la vista actual.")
    with _lock:
        if _jobs.get(crawl_id, {}).get("running"):
            raise ai.AIUnavailable("Ya hay una tarea de IA en marcha en esta auditoría.")
        job = {"running": True, "task": task, "label": label or ai.TASKS[task][0], "total": len(urls), "done": 0, "errors": 0,
               "last_error": "", "model": st["model"], "provider": st["provider"], "started": time.time(), "cancel": False}
        _jobs[crawl_id] = job
    threading.Thread(target=_run, args=(crawl_id, task, urls, custom_prompt), daemon=True).start()
    return dict(job)


def cancel(crawl_id: int) -> None:
    with _lock:
        if crawl_id in _jobs:
            _jobs[crawl_id]["cancel"] = True


def _run(crawl_id: int, task: str, urls: list[str], custom_prompt: str) -> None:
    pages = {p["url"]: p for p in db.load_pages(crawl_id)}
    consecutive = 0
    for url in urls:
        with _lock:
            if _jobs[crawl_id]["cancel"]:
                break
        page = pages.get(url)
        try:
            if not page:
                raise ai.AIUnavailable("La URL no está en el rastreo.")
            text, model = ai.run_task(task, page, custom_prompt)
            db.save_ai(crawl_id, url, task, text.strip()[:4000], model)
            consecutive = 0
        except ai.AIUnavailable as exc:
            consecutive += 1
            with _lock:
                _jobs[crawl_id]["errors"] += 1
                _jobs[crawl_id]["last_error"] = str(exc)
            if consecutive >= 3:  # bad key, no credit, provider down: stop instead of failing 500 times
                with _lock:
                    _jobs[crawl_id]["last_error"] = f"Detenido tras 3 errores seguidos: {exc}"
                    _jobs[crawl_id]["done"] += 1
                break
        with _lock:
            _jobs[crawl_id]["done"] += 1
    with _lock:
        _jobs[crawl_id]["running"] = False
        _jobs[crawl_id]["finished"] = time.time()
