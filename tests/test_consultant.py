"""Consultor SEO con IA: the chat box, the conversation and what is sent to the model."""
from fastapi.testclient import TestClient


def test_answer_rendering_is_safe():
    from app.consultant import to_html
    out = to_html("**Hola** <script>x</script>\n- uno\n- dos\n1. paso\nVer https://example.com/a")
    assert "<strong>Hola</strong>" in out and "&lt;script&gt;" in out and "<script>" not in out
    assert "<ul><li>uno</li><li>dos</li></ul>" in out and "<ol><li>paso</li></ol>" in out
    assert 'href="https://example.com/a"' in out


def test_without_ai_the_box_explains_how_to_turn_it_on():
    from app.main import app
    with TestClient(app) as client:
        r = client.get("/consultor")
        assert r.status_code == 200 and "Configurar la IA" in r.text and 'id="consult-form"' not in r.text
        r = client.post("/consultor", data={"q": "hola"})
        assert r.status_code == 200 and "Configurar la IA" in r.text


def test_conversation_is_kept_and_sent_whole(monkeypatch):
    from app import ai, consultant
    from app.main import app
    seen = []
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "ollama", "model": "fake"})

    def fake(prompt, model, system="", messages=None):
        seen.append((system, list(messages)))
        return f"Respuesta {len(seen)}: **vale**"
    monkeypatch.setattr(ai, "_ollama", fake)
    consultant.clear(0)
    with TestClient(app) as client:
        assert 'id="consult-form"' in client.get("/consultor").text
        client.post("/consultor", data={"q": "¿Qué es un canonical?"})
        r = client.post("/consultor", data={"q": "¿Y un hreflang?"})
        assert "Respuesta 2: <strong>vale</strong>" in r.text and "¿Qué es un canonical?" in r.text
        system, msgs = seen[-1]
        assert "consultor SEO" in system and "no hay ninguna auditoría abierta" in system
        assert [m["role"] for m in msgs] == ["user", "assistant", "user"] and msgs[-1]["content"] == "¿Y un hreflang?"
        r = client.post("/consultor/clear")
        assert "Respuesta" not in r.text and "Hola, soy tu consultor SEO" in r.text


def test_groq_uses_its_key_and_replaces_a_retired_model(monkeypatch):
    import httpx
    from app import ai
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "")
    from app.config import get_settings
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    assert not ai.status()["enabled"] and "console.groq.com/keys" in ai.status()["reason"]
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    calls = []

    def post(url, **kw):
        calls.append(kw["json"]["model"])
        assert url == ai.GROQ_BASE + "/chat/completions" and kw["headers"]["Authorization"] == "Bearer gsk_test"
        if kw["json"]["model"] == "viejo":
            return httpx.Response(404, json={"error": {"message": "The model `viejo` has been decommissioned"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hola"}}]})
    monkeypatch.setattr(ai.httpx, "post", post)
    monkeypatch.setattr(ai.httpx, "get", lambda url, **kw: httpx.Response(200, json={"data": [
        {"id": "whisper-large-v3"}, {"id": "llama-3.3-70b-versatile"}]}))
    assert ai._groq("", "viejo", messages=[{"role": "user", "content": "hola"}]) == "hola"
    assert calls == ["viejo", "llama-3.3-70b-versatile"]

def test_consultant_follows_the_manual(monkeypatch):
    from app import ai
    seen = {}
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "ollama", "model": "fake"})
    monkeypatch.setattr(ai, "_ollama", lambda prompt, model, system="", messages=None: seen.setdefault("s", system) or "ok")
    ai.chat([{"role": "user", "content": "hola"}])
    s = seen["s"]
    assert "Manual del consultor SEO" in s and "[NO VE]" in s and "120-160 caracteres" in s
    assert "pide que lo revisen" in s

def test_question_about_one_url_sends_that_page(monkeypatch):
    from app import ai, consultant, db
    from app.main import app
    seen = {}
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "ollama", "model": "fake"})
    monkeypatch.setattr(ai, "_ollama", lambda prompt, model, system="", messages=None: seen.update(s=system, m=messages) or "ok")
    monkeypatch.setattr(db, "get_crawl", lambda cid: {"id": cid, "start_url": "https://example.com/", "status": "completed",
                                                       "pages_done": 1} if cid == 7 else None)
    monkeypatch.setattr(db, "get_page", lambda cid, url: {"url": url} if url == "https://example.com/a" else None)
    monkeypatch.setattr(consultant, "context", lambda cid: "RESUMEN")
    monkeypatch.setattr(consultant, "page_context", lambda cid, url: "DATOS DE " + url)
    consultant.clear(7)
    with TestClient(app) as client:
        r = client.get("/consultor", params={"crawl": 7, "url": "https://example.com/a"})
        assert "Hablando de esta página" in r.text and "¿Qué le pasa a esta página?" in r.text
        r = client.post("/consultor?crawl=7", data={"q": "¿Qué le pasa?", "url": "https://example.com/a"})
        assert "DATOS DE https://example.com/a" in seen["s"] and "RESUMEN" in seen["s"]
        assert seen["m"][-1]["content"].startswith("[Pregunta sobre la URL https://example.com/a]")
        assert 'class="cmsg-url"' in r.text
        # a URL that is not in the audit is ignored
        client.post("/consultor?crawl=7", data={"q": "hola", "url": "https://otra.com/"})
        assert "DATOS DE" not in seen["s"]
    consultant.clear(7)