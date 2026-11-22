"""«Apuntes»: a notebook per site, linked to URLs or issues, kept across audits and optionally in the client report."""
from fastapi.testclient import TestClient

from app import db
from tests.test_e2e import site, wait_done  # noqa: F401  (pytest fixture)


def test_journal_storage():
    db.init_db()
    a = db.journal_add("misitio.es", "Llamada: priorizar la ficha de Gijón", "pendiente")
    db.journal_add("misitio.es", "El canonical lo pone el plugin", url="https://misitio.es/x", issue="canonical_missing", in_report=True)
    db.journal_add("otro.es", "No debe verse en misitio")
    assert [e["text"] for e in db.journal_list("misitio.es", q="gijón")] == ["Llamada: priorizar la ficha de Gijón"]
    assert db.journal_list("misitio.es", q="100%") == []                     # % is a literal, not a wildcard
    assert len(db.journal_list("misitio.es", report_only=True)) == 1
    c = db.journal_counts("misitio.es")
    assert c["total"] == 2 and c["pending"] == 1 and c["url"]["https://misitio.es/x"] == 1 and c["issue"]["canonical_missing"] == 1
    db.journal_update("misitio.es", a, status="hecho")
    db.journal_update("otro.es", a, text="intento desde otra web")             # another site cannot touch it
    e = next(x for x in db.journal_list("misitio.es") if x["id"] == a)
    assert e["status"] == "hecho" and e["text"].startswith("Llamada")
    for bad in ({"text": "  "}, {"status": "raro"}):
        try:
            db.journal_update("misitio.es", a, **bad)
            raise AssertionError("should refuse")
        except ValueError:
            pass
    db.journal_delete("misitio.es", a)
    assert db.journal_counts("misitio.es")["total"] == 1


def test_journal_in_the_app_and_report(site):  # noqa: F811
    base = site[0]
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/crawls", data={"url": base + "/", "max_urls": "20", "delay": "0", "ack": "yes"}, follow_redirects=False)
        crawl_url = r.headers["location"]
        wait_done(client, crawl_url)
        cid = crawl_url.rsplit("/", 1)[1]
        page = client.get(crawl_url).text
        assert 'id="journal"' in page and 'id="apuntar-dlg"' in page and 'id="download-dlg"' in page
        from app.urlutil import site_key
        nl = chr(10)
        # «📝 Apuntar» with URLs selected: a text box plus their problems, to add with one click
        bad = next(i for i in db.load_issues(int(cid)))
        form = client.post(f"/crawls/{cid}/journal/compose", data={"urls": nl.join([bad["url"], base + "/nada"])}).text
        assert "Nota para 2 URLs" in form and "<b>1/2</b>" in form and "Guardar nota" in form and "Revisar con el cliente" in form
        assert "Marca antes" in client.post(f"/crawls/{cid}/journal/compose", data={"urls": "javascript:x"}).text
        # saving: the note keeps its URLs (bad and repeated ones dropped), escaped
        client.post(f"/crawls/{cid}/journal", data={"text": "Revisar el <script>alert(1)</script> del cliente",
                                                   "urls": nl.join([base + "/about", "javascript:alert(1)", base + "/about", base + "/b"])})
        note = db.journal_list(db.note_scope(int(cid)))[0]
        assert note["url"] == base + "/about" + nl + base + "/b" and note["status"] == "nota"
        assert [e["text"] for e in db.journal_list(db.note_scope(int(cid)), url=base + "/b")] == [note["text"]]
        assert db.journal_counts(db.note_scope(int(cid)))["url"][base + "/about"] == 1
        box = client.get(f"/crawls/{cid}/journal").text
        assert "&lt;script&gt;" in box and "<script>alert" not in box and "2 URLs" in box
        detail = client.get(f"/crawls/{cid}/detail", params={"url": base + "/about"}, headers={"HX-Request": "true"}).text
        assert "1 nota(s) sobre esta URL" in detail
        grid = client.get(f"/crawls/{cid}/grid", params={"tab": "internal"}).text
        assert 'class="row-note"' in grid
        # the client report: the notes and a comment only when asked for at download time
        assert "Notas del consultor" not in client.get(f"/crawls/{cid}/client-report").text
        rep = client.get(f"/crawls/{cid}/client-report", params={"notes": 1, "comment": "Hola Ana <b>", "summary": 1}).text
        assert "Notas del consultor" in rep and "Revisar el &lt;script&gt;" in rep and "about" in rep
        assert "Comentario de tu consultor" in rep and "Hola Ana &lt;b&gt;" in rep
        assert "Escribe algo" in client.post(f"/crawls/{cid}/journal", data={"text": "   "}).text
        client.post(f"/crawls/{cid}/journal/{note['id']}", data={"action": "delete"})
        assert db.journal_counts(db.note_scope(int(cid)))["total"] == 0
        client.cookies.set("lang", "en")
        assert "Note for 1 URL" in client.post(f"/crawls/{cid}/journal/compose", data={"urls": bad["url"]}).text
        assert "Notes for this site" in client.get(crawl_url).text


def test_notes_belong_to_one_audit():
    from app import db
    db.init_db()
    a = db.create_crawl("https://notas.test/", {"start_url": "https://notas.test/", "max_urls": 10, "delay": 0}, "ok")
    b = db.create_crawl("https://notas.test/", {"start_url": "https://notas.test/", "max_urls": 10, "delay": 0}, "ok")
    db.set_note(db.note_scope(a), "https://notas.test/x", "Revisar", "en curso")
    db.journal_add(db.note_scope(a), "Apunte de la primera")
    assert "https://notas.test/x" in db.get_notes(db.note_scope(a))
    assert db.get_notes(db.note_scope(b)) == {}  # a new audit of the same site starts without notes
    assert db.journal_list(db.note_scope(b)) == [] and len(db.journal_list(db.note_scope(a))) == 1