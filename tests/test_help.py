"""«?» help: every option of the configuration and every results tab explains what it does, in both languages."""
import re

from fastapi.testclient import TestClient

from app import help as H
from app.main import app

NOT_OPTIONS = {"url", "urls", "file", "cfg_panel", "preset", "pick_url", "urls_file", "words", "robots_test_urls",
               "accent", "main", "second", "back"}  # free text inputs, theme colours and internal fields


def test_every_option_and_tab_has_help():
    from app import views
    with TestClient(app) as c:
        html = c.get("/").text
    fields = {n for n in re.findall(r'name="([a-z_0-9]+)"', html) if not re.search(r"_\d$", n)} - NOT_OPTIONS
    assert sorted(fields - set(H.OPTION_HELP)) == []
    crawl_tabs = {k for k in re.findall(r'Tab\("([a-z_0-9]+)"', open(views.__file__, encoding="utf-8").read())}
    assert sorted(crawl_tabs - set(H.TAB_HELP)) == []
    for what, affects, en_what, en_affects in (*H.OPTION_HELP.values(), *H.TAB_HELP.values()):
        assert what and affects and en_what and en_affects


def test_help_is_rendered_and_translated():
    with TestClient(app) as c:
        es = c.get("/").text
        assert 'id="opt-help"' in es and 'data-h1="Qué hace"' in es and "Cuántas páginas se piden a la vez." in es
        c.cookies.set("lang", "en")
        en = c.get("/").text
        assert 'data-h1="What it does"' in en and "How many pages are requested at the same time." in en
        assert "Cuántas páginas se piden a la vez." not in en


def test_overview_summary_rows_have_help():
    from app import views
    data = {"pages": [{"type": "html", "html200": True, "indexable_label": "Indexable", "status_code": 200}],
            "externals": [], "sitemap_count": 3}
    rows = views.summary(data)
    assert rows and all(r["help"] and r["help"]["why"] for r in rows)
