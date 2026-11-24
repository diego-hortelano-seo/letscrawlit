"""«IA en bloque» must not crawl along: several pages at once, and new answers without rebuilding the audit."""
import threading
import time

from tests.test_team import _crawl_with_pages


def _wait(cid):
    from app import aibulk
    deadline = time.time() + 15
    while aibulk.status(cid)["running"] and time.time() < deadline:
        time.sleep(0.02)


def test_bulk_runs_several_pages_at_once(monkeypatch):
    from app import ai, aibulk, db
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "groq", "model": "m", "note": ""})
    live, peak, lock = [0], [0], threading.Lock()

    def slow(task, page, prompt=""):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.15)
        with lock:
            live[0] -= 1
        return "ok", "m"
    monkeypatch.setattr(ai, "run_task", slow)
    urls = [f"https://rapido.es/{i}" for i in range(10)]
    cid = _crawl_with_pages(urls)
    aibulk.start(cid, "intent", urls)
    _wait(cid)
    assert aibulk.status(cid)["done"] == 10 and len(db.load_ai(cid)) == 10
    assert peak[0] == aibulk.PARALLEL                                  # after the first good answer, in parallel
    # Ollama runs on this computer: one at a time
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "ollama", "model": "m", "note": ""})
    peak[0] = 0
    aibulk.start(cid, "topic", urls[:4])
    _wait(cid)
    assert peak[0] == 1


def test_rate_limit_slows_down_instead_of_stopping(monkeypatch):
    from app import ai, aibulk, db
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "groq", "model": "m", "note": ""})
    monkeypatch.setattr(aibulk, "RATE_WAIT", 0.01)
    calls = [0]

    def limited(task, page, prompt=""):
        calls[0] += 1
        if calls[0] in (2, 3, 4, 6):                    # the free plan says «slow down» several times in a row
            raise ai.RateLimited("Límite de uso de Groq alcanzado; prueba en un minuto.")
        return "ok", "m"
    monkeypatch.setattr(ai, "run_task", limited)
    urls = [f"https://limite.es/{i}" for i in range(6)]
    cid = _crawl_with_pages(urls)
    aibulk.start(cid, "intent", urls)
    _wait(cid)
    st = aibulk.status(cid)
    assert st["done"] == 6 and st["errors"] == 0 and len(db.load_ai(cid)) == 6   # nothing lost, job not stopped
    assert "más despacio" in st["note"]


def test_cut_off_answer_keeps_title_and_description():
    from app.ai import parse_seo_pack
    cut = '{"title":"Política de cookies de LetsCrawlit","description":"Qué cookies usa y cómo rechazarlas.","schema_type":"WebPage","jsonld":{"@context":"https://schema.org","@type":"WebPage","url":"https://'
    sp = parse_seo_pack(cut)
    assert sp["title"] == "Política de cookies de LetsCrawlit" and sp["description"].startswith("Qué cookies")
    assert sp["jsonld"] == "" and "vuelve a generarla" in sp["notes"]


def test_groq_gpt_oss_thinks_little():
    from app.ai import _groq_extra
    assert _groq_extra("openai/gpt-oss-20b") == {"reasoning_effort": "low"} and _groq_extra("llama-3.3-70b-versatile") == {}


def test_new_ai_answers_do_not_rebuild_the_audit():
    from app import db, views
    cid = _crawl_with_pages(["https://cache.es/", "https://cache.es/a"])
    first = views.workbench(db.get_crawl(cid))
    db.save_ai(cid, "https://cache.es/a", "seo_pack", '{"title": "Nuevo title", "description": "d", "jsonld": {"@type": "WebPage"}}', "m")
    again = views.workbench(db.get_crawl(cid))
    assert again is first                                               # same cached audit, AI columns laid on top
    row = next(r for r in again["pages"] if r["url"] == "https://cache.es/a")
    assert row["ai_seo_title"] == "Nuevo title" and '"@type": "WebPage"' in row["ai_seo_schema"]
    db.save_ai(cid, "https://cache.es/a", "seo_pack", '{"title": "Otro"}', "m")
    assert next(r for r in views.workbench(db.get_crawl(cid))["pages"] if r["url"] == "https://cache.es/a")["ai_seo_title"] == "Otro"
