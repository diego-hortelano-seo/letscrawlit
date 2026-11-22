"""«Configurar la IA»: provider and key saved into .env from the app, without editing files by hand."""
import os

from fastapi.testclient import TestClient

from app import envfile

AI_VARS = ("AI_PROVIDER", "AI_MODEL", "OLLAMA_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")


def _clean_ai_env(monkeypatch):
    """Start without AI settings and put everything back afterwards (set_values writes os.environ)."""
    for k in AI_VARS:
        monkeypatch.setenv(k, "x")   # registers the original value (or its absence) for undo
        monkeypatch.delenv(k)


def test_set_values_keeps_other_lines(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comentario\nDATA_DIR=data\nAI_PROVIDER=\n# OPENAI_API_KEY=\n", encoding="utf-8")
    _clean_ai_env(monkeypatch)
    envfile.set_values({"AI_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test-12345678", "AI_MODEL": ""}, env)
    text = env.read_text(encoding="utf-8")
    assert "# comentario\nDATA_DIR=data\nAI_PROVIDER=openai\nOPENAI_API_KEY=sk-test-12345678\n" in text
    assert "AI_MODEL=" in text and os.environ["AI_PROVIDER"] == "openai"
    assert envfile.masked("OPENAI_API_KEY") == "••••5678"


def test_only_whitelisted_and_clean_values(tmp_path):
    env = tmp_path / ".env"
    for bad in ({"DATA_DIR": "x"}, {"AI_MODEL": "a\nDATA_DIR=y"}):
        try:
            envfile.set_values(bad, env)
            raise AssertionError("should refuse")
        except ValueError:
            pass
    assert envfile.check_ai("evil", "", "", "") == "Elige un proveedor de IA."
    assert "clave" in envfile.check_ai("openai", "sk test with spaces", "", "")
    assert envfile.check_ai("ollama", "", "llama3.1", "http://127.0.0.1:11434") == ""


def test_setup_screen_saves_and_switches_on(tmp_path, monkeypatch):
    from app.main import app
    monkeypatch.setattr(envfile, "ENV_PATH", tmp_path / ".env")
    _clean_ai_env(monkeypatch)
    with TestClient(app) as client:
        box = client.get("/ai/setup").text
        assert "Guardar y activar" in box and 'name="key_gemini"' in box
        r = client.post("/ai/setup", data={"provider": "gemini", "key_gemini": "AIzaTest1234567890", "model_gemini": ""})
        assert "IA activada con Gemini" in r.text and r.headers.get("HX-Trigger") == "aiChanged"
        assert "AIzaTest1234567890" not in r.text and "••••7890" in r.text   # the key never goes back to the page
        assert "GEMINI_API_KEY=AIzaTest1234567890" in (tmp_path / ".env").read_text(encoding="utf-8")
        bad = client.post("/ai/setup", data={"provider": "openai", "key_openai": ""})
        assert "Pega la clave" in bad.text


def test_task_cards_and_saved_instructions(tmp_path, monkeypatch):
    """The tasks are visible before AI is set up, and your own instructions can be saved and reused."""
    from app import aiprompts
    _clean_ai_env(monkeypatch)
    monkeypatch.setattr(aiprompts, "_path", lambda: tmp_path / "ai_prompts.json")
    item = aiprompts.add("¿Tiene precios?", "¿La página menciona precios? Responde sí o no.")
    assert aiprompts.get(item["id"])["prompt"].startswith("¿La página")
    aiprompts.add("¿tiene PRECIOS?", "Otra versión")           # same name replaces instead of duplicating
    assert len(aiprompts.load()) == 1 and aiprompts.load()[0]["prompt"] == "Otra versión"
    try:
        aiprompts.add("vacía", "  ")
        raise AssertionError("should refuse")
    except ValueError:
        pass
    aiprompts.delete(aiprompts.load()[0]["id"])
    assert aiprompts.load() == []
