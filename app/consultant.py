"""AI SEO consultant: a chat that uses the AI provider the user configured, and the open audit as context.

Only a short summary of the audit is sent (site, totals and the issue list with a few example URLs), never the
page contents. The conversation lives in memory, one per audit, until the app restarts or the user clears it.
"""
from __future__ import annotations

import html
import re

from . import db
from .analysis import SEVERITY_LABEL as SEVERITY
from .insights import health

MAX_ISSUES = 30
_conversations: dict[int, list[dict]] = {}   # crawl id (0 = no audit open) -> messages
_context_cache: dict[int, tuple[int, str, str]] = {}


def history(crawl_id: int) -> list[dict]:
    return _conversations.setdefault(crawl_id, [])


def clear(crawl_id: int) -> None:
    _conversations.pop(crawl_id, None)


def context(crawl_id: int) -> str:
    """Plain-text summary of an audit for the consultant (cached while the audit does not change)."""
    crawl = db.get_crawl(crawl_id) if crawl_id else None
    if not crawl:
        return ""
    key = (crawl["pages_done"], crawl["status"])
    hit = _context_cache.get(crawl_id)
    if hit and hit[:2] == key:
        return hit[2]
    pages = db.load_pages(crawl_id)
    h = health(pages, db.load_issues(crawl_id), db.load_links(crawl_id), crawl["start_url"])
    html_pages = [p for p in pages if str(p.get("content_type") or "").startswith("text/html")]
    codes: dict[str, int] = {}
    for p in pages:
        s = p.get("status_code")
        k = "sin respuesta" if not s else f"{str(s)[0]}xx"
        codes[k] = codes.get(k, 0) + 1
    lines = [f"Sitio: {crawl['start_url']}",
             "Estado de la auditoría: " + {"queued": "en cola", "running": "rastreando", "completed": "completada",
                                           "cancelled": "detenida", "failed": "con error", "interrupted": "interrumpida",
                                           "paused": "en pausa"}.get(crawl["status"], crawl["status"]) + (" (aún rastreando: los datos están incompletos)"
                                                              if crawl["status"] in ("queued", "running") else ""),
             f"URLs internas rastreadas: {len(pages)} · páginas HTML: {len(html_pages)} · "
             f"indexables: {sum(1 for p in html_pages if p.get('indexable'))}",
             "Códigos de respuesta: " + ", ".join(f"{k}: {v}" for k, v in sorted(codes.items())),
             f"URLs sin ningún problema: {h['clean_urls']} de {h['urls']}"]
    if h["site_wide"]:
        lines.append("Problemas en (casi) todas las páginas, normalmente de la plantilla: " +
                     "; ".join(f"{g['title']} ({g['count']})" for g in h["site_wide"]))
    lines.append(f"\nProblemas, en orden de qué arreglar primero (máx. {MAX_ISSUES}):")
    for a in h["actions"][:MAX_ISSUES]:
        ex = ", ".join(a["top_urls"][:3])
        lines.append(f"- [{a['severity_label']}] {a['title']}: {a['count']} URL(s)"
                     + (" · afecta a la página de inicio" if a["home"] else "")
                     + (f" · p. ej. {ex}" if ex else "")
                     + (f" · cómo se arregla: {a['remediation']}" if a.get("remediation") else ""))
    if len(h["actions"]) > MAX_ISSUES:
        lines.append(f"(y {len(h['actions']) - MAX_ISSUES} tipos de problema más, de menor prioridad)")
    if not h["actions"]:
        lines.append("- No se han encontrado problemas.")
    text = "\n".join(lines)
    _context_cache[crawl_id] = (*key, text)
    return text


PAGE_TEXT_CHARS = 9000   # about 1,500 words of visible text: enough to judge the content and propose a title


def page_context(crawl_id: int, url: str) -> str:
    """What the consultant knows about one URL of the audit: crawl data, its issues, inlinks and an excerpt of its text."""
    p = db.get_page(crawl_id, url) if crawl_id and url else None
    if not p:
        return ""
    with db.connect() as c:
        inlinks = c.execute("SELECT COUNT(*), COUNT(DISTINCT source_url) FROM links WHERE crawl_id=? AND target_url=? "
                            "AND internal=1 AND kind IN ('a','rendered') AND source_url<>target_url", (crawl_id, url)).fetchone()
        issues = c.execute("SELECT severity, title, remediation FROM issues WHERE crawl_id=? AND url=? ORDER BY id",
                           (crawl_id, url)).fetchall()
    def val(v, empty="(no tiene)"):
        return v if v not in (None, "", []) else empty
    title, desc = p.get("title") or "", p.get("meta_description") or ""
    lines = [f"URL: {url}",
             f"Código de respuesta: {val(p.get('status_code'), 'sin respuesta')}"
             + (f" → redirige a {p['redirect_target']}" if p.get("redirect_target") else ""),
             f"Indexable: {'sí' if p.get('indexable') else 'no'}" + (f" ({p['indexability']})" if p.get("indexability") else ""),
             f"Title ({len(title)} caracteres): {val(title)}",
             f"Meta description ({len(desc)} caracteres): {val(desc)}",
             f"H1: {' | '.join(p.get('h1') or []) or '(no tiene)'}",
             f"H2: {' | '.join((p.get('h2') or [])[:12]) or '(no tiene)'}",
             f"Canonical: {val(p.get('canonical'))} · meta robots: {val(p.get('meta_robots'), 'ninguna')}",
             f"Profundidad: {val(p.get('depth'), '?')} clics desde la inicial · enlaces internos recibidos: {inlinks[0]} "
             f"(desde {inlinks[1]} páginas)",
             f"Palabras: {val(p.get('word_count'), '?')} · peso: {round((p.get('size_bytes') or 0) / 1024)} KB · "
             f"respuesta del servidor: {val(p.get('response_ms'), '?')} ms",
             f"Datos estructurados: {', '.join(sorted(p.get('structured') or {})) or 'ninguno'}"]
    lines.append("Problemas detectados en esta URL:" if issues else "Problemas detectados en esta URL: ninguno.")
    for sev, t, fix in issues[:40]:
        lines.append(f"- [{SEVERITY.get(sev, sev)}] {t}" + (f" · cómo se arregla: {fix}" if fix else ""))
    text = (p.get("rendered_text") or p.get("text_excerpt") or "").strip()
    if text:
        lines.append(f"\nTexto visible de la página (extracto):\n{text[:PAGE_TEXT_CHARS]}")
    return "\n".join(lines)


def to_html(text: str) -> str:
    """Safe, minimal rendering of the model's Markdown: paragraphs, lists, **bold**, `code` and links."""
    out, items, kind = [], [], None

    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return re.sub(r"(https?://[^\s<)]+)", r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', s)

    def flush():
        nonlocal items, kind
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{i}</li>" for i in items) + f"</{kind}>")
        items, kind = [], None

    for line in text.splitlines():
        s = line.strip()
        m_ul, m_ol, m_h = re.match(r"^[-*•]\s+(.*)", s), re.match(r"^\d+[.)]\s+(.*)", s), re.match(r"^#{1,6}\s+(.*)", s)
        if m_ul or m_ol:
            k = "ul" if m_ul else "ol"
            if kind and kind != k:
                flush()
            kind = k
            items.append(inline((m_ul or m_ol).group(1)))
        else:
            flush()
            if m_h:
                out.append(f"<p><strong>{inline(m_h.group(1))}</strong></p>")
            elif s:
                out.append(f"<p>{inline(s)}</p>")
    flush()
    return "".join(out)
