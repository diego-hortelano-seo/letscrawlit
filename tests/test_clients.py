"""Client profiles for the AI, and «IA en bloque» on only the URLs selected in the table."""
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_team import _crawl_with_pages

FICHA = {"name": "Clínica Fisioterapia González", "sector": "Fisioterapia", "audience": "Mujeres de 35 a 50 con dolor de espalda",
         "tone": "Cercano, tutea, frases cortas", "avoid": "barato, revolucionario", "context": "Clínica en Gijón con 20 años."}


def test_profiles_save_edit_delete_and_brief():
    from app import clients
    c = clients.save(FICHA)
    assert clients.get(c["id"])["tone"] == "Cercano, tutea, frases cortas"
    with pytest.raises(ValueError):
        clients.save({"name": "  "})                                   # the name is required
    with pytest.raises(ValueError):
        clients.save({"name": "clínica fisioterapia gonzález"})        # no two clients with the same name
    clients.save({**FICHA, "tone": "Formal"}, c["id"])
    assert clients.get(c["id"])["tone"] == "Formal" and len(clients.load()) == 1
    brief = clients.prompt_block(clients.get(c["id"]))
    assert "Clínica Fisioterapia González" in brief and "NUNCA" in brief and "barato, revolucionario" in brief
    assert clients.prompt_block(None) == ""
    # the last client used on a site comes chosen next time; a site belongs to one client
    other = clients.save({"name": "Otro"})
    clients.remember_site(c["id"], "x.es")
    assert clients.for_site("x.es")["id"] == c["id"]
    clients.remember_site(other["id"], "x.es")
    assert clients.for_site("x.es")["id"] == other["id"] and "x.es" not in clients.get(c["id"])["sites"]
    clients.delete(other["id"])
    assert clients.for_site("x.es") is None


def test_client_brief_reaches_the_prompt(monkeypatch):
    from app import ai, clients
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "openai", "model": "m", "note": ""})
    sent = []
    monkeypatch.setattr(ai, "_openai", lambda prompt, model, **kw: sent.append(prompt) or "ok")
    c = clients.save(FICHA)
    ai.run_task("seo_pack", {"url": "https://x.es/", "title": "T"}, client=c)
    assert "Público objetivo: Mujeres de 35 a 50" in sent[0] and sent[0].index("Contexto del cliente") < sent[0].index("URL: https://x.es/")


def test_bulk_on_selected_urls_with_a_client(monkeypatch):
    from app import ai, aibulk, clients, db
    from app.main import app
    monkeypatch.setattr(ai, "status", lambda: {"enabled": True, "provider": "openai", "model": "m", "note": ""})
    seen = []
    monkeypatch.setattr(ai, "run_task", lambda task, page, prompt="", client=None: (seen.append((page["url"], (client or {}).get("name"))) or ('{"title": "T"}', "m")))
    cid = _crawl_with_pages(["https://x.es/", "https://x.es/a", "https://x.es/b"])
    c = clients.save(FICHA)
    with TestClient(app) as http:
        box = http.get(f"/crawls/{cid}/ai/bulk?nsel=2").text
        assert "Solo las 2 URLs que has seleccionado" in box and "Escribir para" in box
        r = http.post(f"/crawls/{cid}/ai/bulk", data={"task": "seo_pack", "scope": "selected", "client": c["id"],
                                                     "urls": "https://x.es/a\nhttps://x.es/b\nhttps://otra.com/"})
        assert r.status_code == 200
        deadline = time.time() + 10
        while aibulk.status(cid)["running"] and time.time() < deadline:
            time.sleep(0.05)
        assert sorted(seen) == [("https://x.es/a", FICHA["name"]), ("https://x.es/b", FICHA["name"])]  # never the foreign URL
        assert clients.for_site("x.es")["id"] == c["id"]                 # chosen again next time on this site
        assert f'value="{c["id"]}" selected' in http.get(f"/crawls/{cid}/ai/bulk").text
        # «selected» with nothing selected: a clear message, nothing sent
        r = http.post(f"/crawls/{cid}/ai/bulk", data={"task": "seo_pack", "scope": "selected", "urls": ""})
        assert "No hay URLs seleccionadas" in r.text
        # the profile window
        assert "Nuevo cliente" in http.get("/clients?new=1").text
        r = http.post("/clients", data={"name": "Tienda Moda", "tone": "Inspirador"})
        assert "Guardado: Tienda Moda." in r.text and r.headers.get("HX-Trigger") == "clientsChanged"
        assert "Escribe al menos el nombre" in http.post("/clients", data={"name": ""}).text
    assert len(db.load_ai(cid)) == 2
