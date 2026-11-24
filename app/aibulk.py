"""Run one AI task over many pages (the rows of the current tab and filter) in the background.

Results are stored like the single-page ones (ai_results table) and appear as columns in the «IA» tab, so they
can be filtered, sorted and exported with the rest of the crawl. One request per page, a few at a time.
"""
from __future__ import annotations

import threading
import time

from . import ai, db

MAX_PAGES = 500
PARALLEL = 3  # requests at once to a cloud provider; on «too many requests» it drops to one
RATE_RETRIES = 4  # waits for a rate-limited page before counting it as an error
RATE_WAIT = 15.0  # seconds; grows with each wait (15, 30, 45, 60): Groq's free plan refills its minute in ~20-30 s
_jobs: dict[int, dict] = {}
_lock = threading.Lock()


def status(crawl_id: int) -> dict | None:
    with _lock:
        job = _jobs.get(crawl_id)
        return dict(job) if job else None


def start(crawl_id: int, task: str, urls: list[str], custom_prompt: str = "", label: str = "",
          client: dict | None = None) -> dict:
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
               "last_error": "", "model": st["model"], "provider": st["provider"], "started": time.time(), "cancel": False,
               "client": (client or {}).get("name", "")}
        _jobs[crawl_id] = job
    threading.Thread(target=_run, args=(crawl_id, task, urls, custom_prompt, client), daemon=True).start()
    return dict(job)


def cancel(crawl_id: int) -> None:
    with _lock:
        if crawl_id in _jobs:
            _jobs[crawl_id]["cancel"] = True


def _run(crawl_id: int, task: str, urls: list[str], custom_prompt: str, client: dict | None = None) -> None:
    """One page at a time until the first good answer (so a bad key or no credit stops after 3 tries, as always),
    then PARALLEL requests at once: a cloud model answers in seconds, but 500 pages one by one take far too long.
    Ollama runs on this computer, so it keeps one at a time."""
    pages = {p["url"]: p for p in db.load_pages(crawl_id)}
    todo, state, gate = iter(urls), {"consecutive": 0, "stop": False, "ok": False, "slow": False}, threading.Lock()

    def next_url(worker_no: int = 0) -> str | None:
        with gate:
            if state["stop"] or (state["slow"] and worker_no > 0):  # rate limited: only one worker carries on
                return None
            with _lock:
                if _jobs[crawl_id]["cancel"]:
                    return None
            return next(todo, None)

    def call(page: dict) -> tuple[str, str]:
        """«Too many requests» is the free plan asking to slow down, not a failure: from then on one page at a time,
        wait and retry the same page (up to RATE_RETRIES times) instead of stopping the job."""
        for attempt in range(RATE_RETRIES + 1):
            try:
                return ai.run_task(task, page, custom_prompt, client) if client else ai.run_task(task, page, custom_prompt)
            except ai.RateLimited:
                if attempt == RATE_RETRIES:
                    raise
                with gate:
                    state["slow"] = True
                with _lock:
                    _jobs[crawl_id]["note"] = "El proveedor pide ir más despacio (límite de su plan): sigue solo, una página cada vez."
                until = time.time() + RATE_WAIT * (attempt + 1)
                while time.time() < until:  # «Detener» works during the wait too
                    with _lock:
                        if _jobs[crawl_id]["cancel"]:
                            raise
                    time.sleep(0.25)
        raise AssertionError("unreachable")

    def one(url: str) -> None:
        page = pages.get(url)
        try:
            if not page:
                raise ai.AIUnavailable("La URL no está en el rastreo.")
            text, model = call(page)
            db.save_ai(crawl_id, url, task, text.strip()[:12000 if task == "seo_pack" else 4000], model)
            with gate:
                state["consecutive"], state["ok"] = 0, True
        except ai.AIUnavailable as exc:
            with gate:
                state["consecutive"] += 1
                stop = state["consecutive"] >= 3  # bad key, no credit, provider down: stop instead of failing 500 times
                state["stop"] = state["stop"] or stop
            with _lock:
                _jobs[crawl_id]["errors"] += 1
                _jobs[crawl_id]["last_error"] = f"Detenido tras 3 errores seguidos: {exc}" if stop else str(exc)
        with _lock:
            _jobs[crawl_id]["done"] += 1

    def worker(worker_no: int) -> None:
        while (u := next_url(worker_no)) is not None:
            one(u)

    try:
        while not state["ok"] and (u := next_url()) is not None:  # warm-up: one by one until an answer works
            one(u)
        with _lock:
            n = 1 if _jobs[crawl_id].get("provider") == "ollama" else PARALLEL
        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        with _lock:
            _jobs[crawl_id]["running"] = False
            _jobs[crawl_id]["finished"] = time.time()
