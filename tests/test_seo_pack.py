"""AI «title, meta description and structured data»: reading the answer and showing it as columns."""
from app.ai import parse_seo_pack
from app.views import build_rows

ANSWER = """```json
{"title": "Cerrajero urgente en Madrid 24 h | Cerrajería López",
 "description": "Cerrajeros en Madrid las 24 horas: aperturas sin romper la puerta, cambio de cerraduras y bombines. Llegamos en 20 minutos. Llama y pide presupuesto.",
 "schema_type": "Service",
 "jsonld": {"@context": "https://schema.org", "@type": "Service", "name": "Cerrajero urgente", "url": "https://example.com/"},
 "notes": "Añade el teléfono si quieres usar LocalBusiness."}
```"""


def test_parse_seo_pack_reads_wrapped_json():
    sp = parse_seo_pack(ANSWER)
    assert sp["title"].startswith("Cerrajero urgente") and sp["title_ok"]
    assert sp["description_len"] == len(sp["description"]) and sp["description_ok"]
    assert sp["schema_type"] == "Service" and '"@type": "Service"' in sp["jsonld"]
    assert parse_seo_pack("Lo siento, no puedo.") is None and parse_seo_pack("{roto") is None


def test_seo_pack_fills_three_columns():
    page = {"url": "https://example.com/", "status_code": 200, "content_type": "text/html", "title": "t", "depth": 0}
    ai = [{"url": page["url"], "task": "seo_pack", "result": ANSWER}]
    row = build_rows([page], [], [], [], ai_results=ai)["pages"][0]
    assert row["ai_seo_title"].startswith("Cerrajero") and row["ai_seo_description"] and '"@context"' in row["ai_seo_schema"]
