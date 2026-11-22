"""Office exports for the client: a short PowerPoint and one Excel file per error type.

Following the project's principle, both contain only faults that are ALWAYS errors (analysis.ALWAYS_BAD):
no health score, no opinions, no filler slides. A type of error that did not happen gets no slide and no file.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime

from . import db
from .analysis import ALWAYS_BAD, SEVERITY_ORDER, group_issues
from .urlutil import safe_filename, site_key

MAX_ERROR_SLIDES = 8  # cover + summary + 8 = at most 10 slides
MAX_EXAMPLES = 5
BRAND = "331942"
ACCENT = "C77DFF"


def error_groups(crawl_id: int) -> list[dict]:
    """Groups of always-bad issues, most serious and most frequent first."""
    groups = [g for g in group_issues(db.load_issues(crawl_id)) if g["code"] in ALWAYS_BAD and g["items"]]
    rank = {s: n for n, s in enumerate(SEVERITY_ORDER)}
    return sorted(groups, key=lambda g: (rank.get(g["severity"], 99), -len(g["items"])))


def _date(crawl: dict) -> str:
    raw = crawl.get("finished_at") or crawl.get("created_at") or ""
    try:
        return datetime.fromisoformat(raw[:19]).strftime("%d/%m/%Y")
    except ValueError:
        return raw[:10]


# --- PowerPoint -------------------------------------------------------------------------------------------
def presentation(crawl_id: int) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt

    crawl = db.get_crawl(crawl_id)
    groups = error_groups(crawl_id)
    site = site_key(crawl["start_url"])
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    main, accent, white = RGBColor.from_string(BRAND), RGBColor.from_string(ACCENT), RGBColor(255, 255, 255)

    def text(slide, x, y, w, h, value, size=18, bold=False, color=None):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.word_wrap = True
        for n, line in enumerate(value if isinstance(value, list) else [value]):
            p = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
            p.text = line
            p.font.size, p.font.bold = Pt(size), bold
            if color is not None:
                p.font.color.rgb = color
        return box

    def band(slide, title):
        bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(1.1))  # 1 = rectangle
        bar.fill.solid()
        bar.fill.fore_color.rgb = main
        bar.line.fill.background()
        text(slide, 0.5, 0.22, 12.3, 0.8, title, 28, True, white)

    # 1. cover
    s = prs.slides.add_slide(blank)
    bg = s.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = main
    bg.line.fill.background()
    text(s, 0.8, 2.4, 11.7, 1.2, "Auditoría SEO técnica", 44, True, white)
    text(s, 0.8, 3.6, 11.7, 0.8, site, 28, False, accent)
    text(s, 0.8, 4.4, 11.7, 0.6, _date(crawl), 18, False, white)

    # 2. summary: facts only
    s = prs.slides.add_slide(blank)
    band(s, "Resumen")
    affected = len({i["url"] for g in groups for i in g["items"]})
    facts = [f"URLs rastreadas: {crawl.get('pages_done') or 0}",
             f"Tipos de error encontrados: {len(groups)}",
             f"URLs con algún error: {affected}"]
    if groups:
        facts.append("Solo se incluyen fallos que siempre son un error; los avisos que dependen de cada caso quedan fuera.")
    else:
        facts.append("No se encontró ningún fallo de los que siempre son un error.")
    text(s, 0.8, 1.6, 11.7, 4.5, facts, 24)

    # 3…10. one slide per type of error
    for g in groups[:MAX_ERROR_SLIDES]:
        s = prs.slides.add_slide(blank)
        n = len(g["items"])
        band(s, f"{g['title']} · {n} URL{'s' if n != 1 else ''}")
        text(s, 0.8, 1.4, 11.7, 1.1, ["Por qué importa", g["why"]], 16)
        text(s, 0.8, 2.6, 11.7, 1.1, ["Cómo se arregla", g["remediation"]], 16)
        examples = [i["url"] for i in g["items"][:MAX_EXAMPLES]]
        more = [f"… y {n - len(examples)} más (ver el Excel)"] if n > len(examples) else []
        text(s, 0.8, 3.9, 11.7, 3.2, ["URLs afectadas"] + examples + more, 14)
        for shape in s.shapes:  # headings of each block in bold
            if shape.has_text_frame and shape.text_frame.paragraphs[0].text in ("Por qué importa", "Cómo se arregla", "URLs afectadas"):
                shape.text_frame.paragraphs[0].font.bold = True
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


# --- Excel: one file per type of error --------------------------------------------------------------------
def _workbook(group: dict, notes: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Errores"
    with_notes = any((notes.get(i["url"]) or {}).get("note") for i in group["items"])  # no empty columns
    headers = ["URL", "Evidencia", "Por qué importa", "Cómo se arregla"] + (["Anotación Consultor"] if with_notes else [])
    ws.append(headers)
    for item in group["items"]:
        row = [_cell(item["url"]), _cell(item.get("evidence")), _cell(group["why"]), _cell(group["remediation"])]
        ws.append(row + ([_cell((notes.get(item["url"]) or {}).get("note", ""))] if with_notes else []))
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=BRAND)
        cell.alignment = Alignment(vertical="center")
    for col, width in zip("ABCDE"[:len(headers)], (70, 50, 45, 45, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _cell(value) -> str:
    """Plain text, never a formula (same protection as the CSV exports)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def excel_zip(crawl_id: int) -> bytes:
    groups = error_groups(crawl_id)
    notes = db.get_notes(db.note_scope(crawl_id))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, g in enumerate(groups, 1):
            zf.writestr(f"{n:02d}-{safe_filename(g['title'])[:60]}.xlsx", _workbook(g, notes))
        if not groups:
            zf.writestr("LEEME.txt", "No se encontró ningún fallo de los que siempre son un error.\n")
    return buf.getvalue()
