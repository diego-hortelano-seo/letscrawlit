"""Bugs found in the full UX review (November 2026): each test pins one fix."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parent.parent / "app"


def _crawl(urls):
    from app import db
    from app.validation import ACK_TEXT
    db.init_db()
    cid = db.create_crawl(urls[0], {"start_url": urls[0], "max_urls": 10, "delay": 0}, ACK_TEXT)
    for u in urls:
        db.save_page(cid, {"url": u, "depth": 0, "status_code": 200, "content_type": "text/html", "title": "T " + u,
                           "h1_json": "[]", "h2_json": "[]"})
    db.update_crawl(cid, status="completed", finished_at=db.now(), pages_done=len(urls))
    return cid


def test_every_data_click_names_a_real_action():
    """«Archivo › Auditorías guardadas…» used to call openDlg('saved-dlg') as an action name: nothing happened."""
    used = set()
    for f in list(APP.glob("templates/*.html")) + list(APP.glob("*.py")):
        used |= set(re.findall(r'data-(?:click|change|input|submit)="([^"]+)"', f.read_text(encoding="utf-8")))
    used = {u for u in used if "{" not in u}
    js = "\n".join(f.read_text(encoding="utf-8") for f in APP.glob("static/*.js"))
    for name in used:
        assert re.fullmatch(r"[A-Za-z_]+", name), f"data-click must be a bare action name: {name}"
        assert re.search(rf"\b{name}\s*[:=(]|function {name}\b", js), f"no JS action called {name}"


def test_context_menu_script_does_not_touch_removed_items():
    js = (APP / "static" / "crawl.js").read_text(encoding="utf-8")
    html = (APP / "templates" / "crawl.html").read_text(encoding="utf-8")
    for act in re.findall(r"querySelector\('\[data-act=\"(\w+)\"\]'\)", js):
        assert f'data-act="{act}"' in html


def test_repeated_evidence_is_grouped():
    from app.views import evidence_parts
    assert evidence_parts("A; A; A; B") == [("A", 3), ("B", 1)]
    assert evidence_parts("") == []


def test_old_spelling_issues_are_rechecked():
    from app import db
    from app.analysis import Issue  # noqa: F401  (the issue shape used by replace_issues)
    cid = _crawl(["https://orto.es/", "https://orto.es/b"])

    class I:  # minimal stand-in with the attributes replace_issues reads
        def __init__(self, url, evidence):
            self.code, self.severity, self.title, self.url = "spelling_errors", "low", "Posibles errores ortográficos", url
            self.evidence, self.remediation, self.why = evidence, "r", "w"
    db.replace_issues(cid, [I("https://orto.es/", "4 palabra(s) desconocidas: cumpla, quién, mantenga, trabajáis"),
                            I("https://orto.es/b", "3 palabra(s) desconocidas: presupesto, cerradra, trabajr")])
    left = db.load_issues(cid)
    assert [i["url"] for i in left] == ["https://orto.es/b"]  # valid Spanish words are no longer reported


def test_trends_without_finished_audit_explains_instead_of_error():
    from app import db
    from app.main import app
    from app.validation import ACK_TEXT
    db.init_db()
    db.create_crawl("https://pausada.es/", {"start_url": "https://pausada.es/"}, ACK_TEXT)
    with TestClient(app) as c:
        r = c.get("/trends/pausada.es")
        assert r.status_code == 200 and "Aún no hay ninguna auditoría terminada" in r.text


def test_trends_index_opens_the_latest_site():
    from app.main import app
    _crawl(["https://tendencia.es/"])
    with TestClient(app) as c:
        r = c.get("/trends", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/trends/tendencia.es"


def test_status_line_counts_live_issues():
    from app import db
    from app.main import app
    cid = _crawl(["https://cuenta.es/"])
    db.update_crawl(cid, extras={"summary": {"pages": 1, "indexable": 1, "issues": 99}})
    with TestClient(app) as c:
        html = c.get(f"/crawls/{cid}/status").text
    assert "99 incidencias" not in html and f"{len(db.load_issues(cid))} incidencias" in html


def test_new_strings_are_translated():
    from app import i18n
    for text in ("Nuevas", "Eliminadas", "Diferencia", "Avisos del rastreo", "Pulsa el ? de cada opción para ver qué hace."):
        for lang in ("en", "fr", "it", "pt", "de"):
            assert i18n.tr(text, lang) != text, (text, lang)


def test_desktop_build_bundles_every_data_folder():
    """The first .exe shipped without app/locales: French, Italian, Portuguese and German fell back to English."""
    spec = (APP.parent / "letscrawl.spec").read_text(encoding="utf-8")
    for d in APP.iterdir():
        if d.is_dir() and d.name != "__pycache__" and any(f.suffix != ".py" for f in d.rglob("*") if f.is_file()):
            assert f'"app/{d.name}"' in spec, f"app/{d.name} is not copied into the desktop build"


def test_windows_installer_is_wired_up():
    """The release ships one LetsCrawlit-Setup.exe; the .exe's version comes from app/config.py."""
    root = APP.parent
    iss = (root / "packaging" / "letscrawl.iss").read_text(encoding="utf-8")
    for pic in re.findall(r"wizard[\w-]*\.bmp", iss):
        assert (root / "packaging" / pic).exists(), pic
    assert "{#MyVersion}" in iss and "AppId={{6F3C2A9E" in iss
    info = (root / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    assert "(0, 1, 0, 0)" in info and '"0.1.0"' in info  # placeholders letscrawl.spec swaps for app/config.py's VERSION
    wf = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "packaging/letscrawl.iss" in wf and "files: LetsCrawlit-Setup.exe" in wf


def test_colours_panel_saves_only_on_demand():
    """Colours used to be saved on every click, with no way to undo: now preview, then Guardar / Cancelar."""
    html = (APP / "templates" / "_theme_panel.html").read_text(encoding="utf-8")
    assert 'data-save="1" disabled' in html and 'data-cancel="1"' in html and "Cambios sin guardar" in html
    js = (APP / "static" / "theme.js").read_text(encoding="utf-8")
    assert "save(draft)" in js and js.count("save(") == 2  # the definition and the «Guardar» button, nothing else
    assert "addEventListener('close', revert)" in js  # ✕, Esc or clicking outside throws the preview away
    from app import i18n
    for text in ("Cambios sin guardar", "Guardado", "Volver a los colores de LetsCrawlit"):
        for lang in ("en", "fr", "it", "pt", "de"):
            assert i18n.tr(text, lang), (text, lang)  # «Guardado» is also Portuguese, so only check it exists


def test_scheduled_audit_runs_inside_the_installed_program(monkeypatch):
    """Up to 0.2.1 the scheduled task ran «LetsCrawl.exe … run.py crawl …», which the installed program ignored."""
    from app import cli
    monkeypatch.setattr(cli, "FROZEN", True)
    monkeypatch.setattr(cli.sys, "executable", r"C:\Programs\LetsCrawlit\LetsCrawlit.exe")
    cmd = cli.schedule_command("https://misitio.es/", "03:00", False, "MON", None)
    task = cmd[cmd.index("/TR") + 1]
    assert task == r'"C:\Programs\LetsCrawlit\LetsCrawlit.exe" crawl "https://misitio.es/" --i-own-this-site'
    assert "run.py" not in task and cmd[cmd.index("/TN") + 1].startswith("LetsCrawlit-")


def test_installed_program_has_a_command_line():
    """The window program answers to arguments (scheduled audits) and a console program is built for CI."""
    root = APP.parent
    desktop = (APP / "desktop.py").read_text(encoding="utf-8")
    assert "if len(sys.argv) > 1:" in desktop and "cli_main(sys.argv[1:])" in desktop
    spec = (root / "letscrawl.spec").read_text(encoding="utf-8")
    assert 'name="letscrawlit-cli"' in spec and "console=True" in spec and (root / "cli_main.py").exists()


def test_brand_name_is_letscrawlit_everywhere():
    """D-01: the product is LetsCrawlit. «LetsCrawl» alone only survives where changing it would lose data."""
    import re
    root = APP.parent
    allowed = {"secretstore.py"}  # its encryption salt: changing it would make saved keys unreadable
    files = [*APP.glob("*.py"), *APP.glob("templates/*.html"), *APP.glob("static/*.js"), *APP.glob("locales/*.json"),
             root / "packaging" / "letscrawl.iss", root / "letscrawl.spec", root / "README.md"]
    for f in files:
        if f.name in allowed:
            continue
        # lines that move users over from the old name (data folder, old program, old user-agent) may say it
        migration = ("«LetsCrawl»", "LetsCrawl.exe", "LetsCrawl.lnk", '"LetsCrawl"', '"LetsCrawl/"', "{userpf}")
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if not any(m in ln for m in migration)]
        bad = [ln for ln in lines if re.search(r"LetsCrawl(?!it)", ln)]
        assert not bad, (f.name, bad[:3])


def test_user_data_moves_to_the_new_folder(tmp_path, monkeypatch):
    """Updating from 0.2.x must keep the audits: the folder LetsCrawl in APPDATA becomes LetsCrawlit."""
    import importlib
    import sys as _sys
    (tmp_path / "LetsCrawl" / "data").mkdir(parents=True)
    (tmp_path / "LetsCrawl" / "data" / "letscrawl.db").write_text("x")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    from app import config
    try:
        importlib.reload(config)
        assert config.USER_DIR == tmp_path / "LetsCrawlit"
        assert (tmp_path / "LetsCrawlit" / "data" / "letscrawl.db").exists() and not (tmp_path / "LetsCrawl").exists()
    finally:
        monkeypatch.delattr(_sys, "frozen")
        importlib.reload(config)


def test_newest_data_wins_when_both_folders_exist(tmp_path):
    """A stale LetsCrawlit folder from an older build must not hide the user's current audits (kept as a backup)."""
    import os
    import time
    from app.config import _adopt_old_data
    old, new = tmp_path / "LetsCrawl", tmp_path / "LetsCrawlit"
    for folder, age in ((new, 3600), (old, 0)):
        (folder / "data").mkdir(parents=True)
        db_file = folder / "data" / "letscrawl.db"
        db_file.write_text(folder.name)
        os.utime(db_file, (time.time() - age, time.time() - age))
    assert _adopt_old_data(old, new) == new
    assert (new / "data" / "letscrawl.db").read_text() == "LetsCrawl"  # the current audits
    backups = list(tmp_path.glob("LetsCrawlit-anterior-*"))
    assert len(backups) == 1 and (backups[0] / "data" / "letscrawl.db").read_text() == "LetsCrawlit"


def test_command_line_survives_a_legacy_windows_console(monkeypatch):
    """letscrawlit-cli printed ❌ to a cp1252 console and crashed before writing the JUnit report."""
    import io
    import sys as _sys
    from app import cli
    raw = io.BytesIO()
    monkeypatch.setattr(_sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    cli._utf8_console()
    print("❌ Auditoría")
    _sys.stdout.flush()
    assert "❌ Auditoría".encode("utf-8") in raw.getvalue()


def test_rare_screens_are_translated_too():
    """Opening every screen in English found ~110 texts still in Spanish (Google connected, saving a session, crawl phases,
    the robots.txt tester, the full HTML report…). Each language must translate them, numbers and names included."""
    from app import i18n
    samples = ["Guardar sesión", "Desconectar Google", "Importar clics e impresiones", "Buscar copias", "Mi propia instrucción",
               "El idioma del texto no coincide con el atributo lang", "Leyendo robots.txt y sitemap…", "Analizando incidencias…",
               "Comprobando enlaces externos 3 / 10", "Detenido por ti · se analizaron 11 URL(s)", "Ninguna regla coincide (permitido)",
               "Disallow: /x (línea 2)", "42 caracteres (mínimo 70): «Hola»", "1 palabras visibles (mínimo 200)",
               "URLs rastreadas a lo largo del tiempo", "Tope de URLs: sin límite · pausa: 1.0 s · JavaScript: sí · robots.txt respetado: sí",
               "Conectado el 2026-11-20 10:00 UTC.", "Sin imágenes.", "No se registró ninguna petición."]
    for text in samples:
        for lang in ("en", "fr", "it", "pt", "de"):
            out = i18n.tr(text, lang)
            assert out and (out != text or lang == "pt"), (text, lang)  # Portuguese sometimes says it the same way
    assert i18n.tr("Disallow: /x (línea 2)", "en") == "Disallow: /x (line 2)"
    assert i18n.tr("Comprobando enlaces externos 3 / 10", "de") == "Externe Links werden geprüft 3 / 10"
