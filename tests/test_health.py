"""The health score and the «pages fine» chart must not call every page broken because of one site-wide fix,
and the Spanish spell check must not flag normal verb forms."""
from app import insights
from app.checks import spelling


def _page(i):
    return {"url": f"https://web.test/p{i}", "content_type": "text/html", "status_code": 200}


def test_site_wide_problem_counts_once():
    pages = [_page(i) for i in range(10)]
    issues = [{"code": "missing_hsts", "title": "Falta HSTS", "severity": "low", "remediation": "", "url": p["url"]} for p in pages]
    issues.append({"code": "missing_description", "title": "Falta la meta descripción", "severity": "medium",
                   "remediation": "", "url": pages[0]["url"]})
    h = insights.health(pages, issues, [], pages[0]["url"])
    assert h["clean_urls"] == 9                       # only the page with its own problem
    assert [w["code"] for w in h["site_wide"]] == ["missing_hsts"]
    assert 90 <= h["score"] < 100


def test_spanish_spelling_knows_inflections_and_finds_typos():
    ok = "dónde sabes requiere permita esté cumpla usarse reformarlo puede pide dándole interiorismo edificabilidad"
    assert spelling(ok, "es")["count"] == 0
    # future, conditional, imperfect, irregular stems, prefixed verbs, subjunctive spelling changes, imperatives
    more = ("según debería podrá han información harán usaba estuvo haría dará hubiera huele decía contenga mantiene "
            "tendremos dirías recoja seque guardes muévelo hazlo mímalo atraída dando tenéis elegís darte "
            # valid words missing from the small bundled dictionary, and English words inside the text
            "formato comercial controlar detección planificación grabación the user")
    assert spelling(more, "es")["count"] == 0
    bad = spelling("quiero recivir el presupesto con la medicacion y tambien un vurro", "es")
    assert set(bad["words"]) == {"recivir", "presupesto", "medicacion", "tambien", "vurro"}
