"""CSV, self-contained HTML report and reproducible ZIP bundle."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import db
from .analysis import SEVERITY_LABEL, group_issues
from .config import VERSION, get_settings
from .pagerank import RULES as PAGERANK_RULES
from .urlutil import safe_filename, site_key
from .views import evidence_parts

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
_env.filters["evidence_parts"] = evidence_parts

PAGE_FIELDS = [
    "url", "status_code", "status_text", "indexable_label", "indexability", "content_type", "redirect_url", "redirect_type",
    "redirect_hops", "redirect_final", "in_sitemap",
    "depth", "source", "via", "title", "title_len", "title_px", "meta_description", "desc_len", "desc_px",
    "h1_1", "h1_1_len", "h1_2", "h2_1", "h2_1_len", "h2_2", "canonical", "meta_robots", "x_robots", "lang", "hreflang_count", "hreflang_langs", "hreflang_no_return",
    "word_count", "rendered_word_count", "words_total", "content_area", "content_hash", "last_modified",
    "response_ms", "response_ms_first", "size_bytes",
    "pagerank", "authority_pct", "inlinks", "unique_inlinks", "self_links", "outlinks", "unique_outlinks", "external_outlinks", "unique_external_outlinks",
    "rendered_title", "rendered_robots", "rendered_canonical", "structured_types", "jsonld_invalid", "mixed_count", "hsts",
    "ttfb_ms", "blocked_by_robots", "error", "render_error", "fetched_at", "note_status", "note", "note_updated",
]
COUNTING_RULES = {
    "inlinks": "Enlaces <a> internos que apuntan a la URL (HTML original + los que solo aparecen al renderizar), "
               "contando duplicados. INCLUYEN los enlaces de la página a sí misma (criterio habitual en las herramientas de rastreo); "
               "esos se muestran aparte en self_links. Para recursos (imágenes, CSS…) cuentan sus referencias.",
    "unique_inlinks": "Páginas distintas que enlazan a la URL (incluida ella misma si se autoenlaza).",
    "self_links": "Enlaces de la página hacia su propia URL. Para detectar huérfanas NO cuentan.",
    "outlinks": "Enlaces <a> internos de la página, contando duplicados; unique_outlinks = destinos distintos.",
    "external_outlinks": "Enlaces <a> a otros dominios, contando duplicados; unique_external_outlinks = destinos distintos.",
    "word_count": "Palabras del contenido principal: <main> (o role=main); si no hay, <article>; si no, el <body> sin "
                  "nav, header, footer ni aside. content_area indica qué zona se usó. words_total = página completa.",
    "content_hash": "SHA-1 del texto del contenido principal normalizado (minúsculas, espacios), para duplicados exactos.",
    "response_ms": "Tiempo hasta descargar la respuesta completa. Las que superan 1000 ms se miden otras dos veces en "
                   "solitario al final y se guarda la mediana de las tres; la primera queda en response_ms_first.",
    "ttfb_ms": "Tiempo hasta recibir las cabeceras de la respuesta (primer byte), en la primera petición.",
    "redirect_hops": "Saltos de redirección hasta el destino final (3xx, meta refresh o JavaScript); redirect_final = destino.",
    "target_status": "En links.csv: código de respuesta de la URL de destino (vacío si no se comprobó).",
    "pagerank": PAGERANK_RULES,
    "via": "Dónde se encontró la URL como recurso: img, srcset, og:image, twitter:image, schema, favicon, css, script, "
           "preload, manifest o sitemap (image:loc).",
    "indexability": "«Recurso (no HTML)» es el estado común de imágenes, CSS, JS, PDF… tanto si se rastrearon como "
                    "página como si se comprobaron con HEAD.",
}
ISSUE_FIELDS = ["severity", "code", "title", "url", "evidence", "remediation", "why"]
LINK_FIELDS = ["source_url", "target_url", "anchor", "kind", "internal", "nofollow", "follow", "target_status"]
IMAGE_FIELDS = ["page_url", "src", "alt"]
EXTERNAL_FIELDS = ["url", "status_code", "content_type", "redirect_target", "response_ms", "blocked_by_robots", "error"]
EXPORT_KINDS = ("pages", "issues", "links", "images", "externals")


def _safe_cell(value) -> str:
    """Stringify and neutralise spreadsheet formula injection."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " | ".join(str(v) for v in value)
    if isinstance(value, float):  # Spanish Excel: decimal comma (1,21), never read as a date or as text
        return f"{value:.10g}".replace(".", ",")
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text


PAGE_HEADERS = {"note": "Anotación Consultor"}  # the consultant's note on the URL, under the name clients see


def to_csv(rows: list[dict], fields: list[str], headers: list[str] | None = None, drop_empty: bool = False) -> bytes:
    """CSV for a Spanish Excel: ";" between columns and decimal comma. With drop_empty, columns that came out
    empty in every row (data not collected in this crawl) are left out instead of exported blank."""
    headers = headers or fields
    cells = [[_safe_cell(row.get(f)) for f in fields] for row in rows]
    if drop_empty and cells:
        keep = [i for i in range(len(fields)) if i == 0 or any(r[i] != "" for r in cells)]
        headers, cells = [headers[i] for i in keep], [[r[i] for i in keep] for r in cells]
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(headers)
    for row in cells:
        writer.writerow(row)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens UTF-8 correctly


def _page_rows(crawl_id: int) -> list[dict]:
    """The same enriched rows as the Internos tab (pages + checked resources), so exports match the screen."""
    from . import views
    crawl = db.get_crawl(crawl_id)
    rows = []
    for r in views.workbench(crawl)["pages"]:
        sd = r.get("structured") or {}
        rows.append({**r, "structured_types": (sd.get("jsonld_types") or []) + (sd.get("microdata") or []) + (sd.get("rdfa") or []),
                     "h1": r.get("h1") or [], "h2": r.get("h2") or []})
    return rows


def csv_export(crawl_id: int, kind: str) -> bytes:
    if kind == "pages":
        return to_csv(_page_rows(crawl_id), PAGE_FIELDS, [PAGE_HEADERS.get(f, f) for f in PAGE_FIELDS], drop_empty=True)
    if kind == "issues":
        return to_csv(db.load_issues(crawl_id), ISSUE_FIELDS)
    if kind == "links":  # with the response code of the target: links to redirects and to 404 at a glance
        status = {e["url"]: e.get("status_code") for e in db.load_externals(crawl_id)}
        status.update({p["url"]: p.get("status_code") for p in db.load_pages(crawl_id)})
        rows = [{**l, "follow": "nofollow" if l.get("nofollow") else "follow", "target_status": status.get(l["target_url"])}
                for l in db.load_links(crawl_id)]
        return to_csv(rows, LINK_FIELDS)
    if kind == "images":
        return to_csv(db.load_images(crawl_id), IMAGE_FIELDS)
    if kind == "externals":
        return to_csv(db.load_externals(crawl_id), EXTERNAL_FIELDS)
    raise ValueError(f"Unknown export kind: {kind}")


def html_report(crawl_id: int) -> str:
    crawl = db.get_crawl(crawl_id)
    if crawl is None:
        raise KeyError(crawl_id)
    issues = db.load_issues(crawl_id)
    pages = _page_rows(crawl_id)
    static = TEMPLATES.parent / "static"
    css = (static / "style.css").read_text(encoding="utf-8")
    import base64
    for w in (400, 500, 600, 700):  # embed the font so the report stays a single self-contained file
        font = static / f"plex-sans-{w}.woff2"
        if font.exists():
            data_uri = "data:font/woff2;base64," + base64.b64encode(font.read_bytes()).decode("ascii")
            css = css.replace(f'url("/static/plex-sans-{w}.woff2")', f'url("{data_uri}")')
    return _env.get_template("report.html").render(
        crawl=crawl, groups=group_issues(issues), pages=pages, css=css, version=VERSION, counting_rules=COUNTING_RULES,
        severity_label=SEVERITY_LABEL, generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def base_name(crawl: dict) -> str:
    stamp = (crawl.get("created_at") or "")[:16].replace("T", "-").replace(":", "")  # no audit number, just site and time
    return safe_filename(f"auditoria-{site_key(crawl['start_url'])}-{stamp}")


def manifest(crawl: dict, files: dict[str, bytes]) -> dict:
    return {
        "tool": "letscrawl", "version": VERSION,
        "crawl_id": crawl["id"], "start_url": crawl["start_url"], "status": crawl["status"],
        "settings": crawl["settings"],
        "permission_acknowledgement": {"text": crawl["ack_text"], "at": crawl["ack_at"]},
        "created_at": crawl["created_at"], "started_at": crawl["started_at"], "finished_at": crawl["finished_at"],
        "pages_done": crawl["pages_done"], "notes": crawl["notes"],
        "robots_txt": crawl["robots"], "extras": crawl["extras"], "counting_rules": COUNTING_RULES,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }


def sitemap_xml(crawl_id: int, images: bool = False) -> list[tuple[str, bytes]]:
    """Standard XML sitemap(s) of indexable HTML 200 pages (optionally with <image:image>). Split at 50,000 URLs."""
    from email.utils import parsedate_to_datetime
    from xml.sax.saxutils import escape
    pages = [p for p in db.load_pages(crawl_id) if p.get("indexable")]
    imgs: dict[str, list[str]] = {}
    if images:
        for i in db.load_images(crawl_id):
            if i.get("src", "").startswith("http"):
                imgs.setdefault(i["page_url"], []).append(i["src"])
    files = []
    for n in range(0, max(1, len(pages)), 50000):
        chunk = pages[n:n + 50000]
        ns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"' + (
            ' xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"' if images else "")
        out = ['<?xml version="1.0" encoding="UTF-8"?>', f"<urlset {ns}>"]
        for p in chunk:
            out.append(f"  <url><loc>{escape(p['url'])}</loc>")
            lm = (p.get("headers") or {}).get("last-modified")
            if lm:
                try:
                    out.append(f"    <lastmod>{parsedate_to_datetime(lm).date().isoformat()}</lastmod>")
                except (TypeError, ValueError):
                    pass
            for src in (list(dict.fromkeys(imgs.get(p["url"], [])))[:1000] if images else []):
                out.append(f"    <image:image><image:loc>{escape(src)}</image:loc></image:image>")
            out.append("  </url>")
        out.append("</urlset>")
        suffix = "" if n == 0 else f"-{n // 50000 + 1}"
        files.append((f"sitemap{'-imagenes' if images else ''}{suffix}.xml", "\n".join(out).encode("utf-8")))
    return files


BULK_EXPORTS = {
    "inlinks_4xx": "Todos los inlinks hacia URLs 4xx",
    "inlinks_5xx": "Todos los inlinks hacia URLs 5xx o sin respuesta",
    "inlinks_3xx": "Todos los inlinks hacia redirecciones",
    "inlinks_noindex": "Todos los inlinks hacia URLs noindex",
    "external_broken": "Todos los enlaces hacia externos rotos",
    "images_missing_alt": "Todas las imágenes sin alt (con su página)",
    "images_large": "Todas las imágenes de más de 100 KB",
    "resources_broken": "Todos los recursos internos rotos (con su página)",
    "redirect_chains": "Todas las cadenas de redirección",
    "issues_by_pattern": "Incidencias agrupadas por patrón de URL",
    "canonical_issues": "Todos los problemas de canonical",
    "mixed_content": "Todo el contenido mixto (página + recurso HTTP)",
    "all_inlinks": "Todos los enlaces internos (origen, destino, ancla, código de destino)",
    "all_outlinks_external": "Todos los enlaces externos (origen, destino, ancla, código)",
}


def bulk_export(crawl_id: int, kind: str) -> bytes:
    pages = {p["url"]: p for p in db.load_pages(crawl_id)}
    links = db.load_links(crawl_id)
    ext = {e["url"]: e for e in db.load_externals(crawl_id)}
    status = lambda u: (pages.get(u) or ext.get(u) or {}).get("status_code")
    link_fields = ["source_url", "target_url", "anchor", "target_status", "kind", "rel", "nofollow"]

    def link_rows(pred, kinds=("a", "rendered", "redirect")):
        return [{**l, "target_status": status(l["target_url"])} for l in links if l["kind"] in kinds and pred(l)]

    if kind == "inlinks_4xx":
        return to_csv(link_rows(lambda l: l.get("internal") and 400 <= (status(l["target_url"]) or 0) < 500), link_fields)
    if kind == "inlinks_5xx":
        return to_csv(link_rows(lambda l: l.get("internal") and l["target_url"] in pages and not pages[l["target_url"]].get("blocked_by_robots")
                                and ((status(l["target_url"]) or 0) >= 500 or status(l["target_url"]) is None)), link_fields)
    if kind == "inlinks_3xx":
        return to_csv(link_rows(lambda l: l.get("internal") and 300 <= (status(l["target_url"]) or 0) < 400, ("a", "rendered")), link_fields)
    if kind == "inlinks_noindex":
        from .extract import is_noindex
        return to_csv(link_rows(lambda l: l["target_url"] in pages and is_noindex(pages[l["target_url"]].get("meta_robots")), ("a", "rendered")), link_fields)
    if kind == "external_broken":
        return to_csv(link_rows(lambda l: not l.get("internal") and ((status(l["target_url"]) or 0) >= 400
                                or (l["target_url"] in ext and status(l["target_url"]) is None and not ext[l["target_url"]].get("blocked_by_robots")))), link_fields)
    if kind == "images_missing_alt":
        return to_csv([i for i in db.load_images(crawl_id) if i.get("alt") is None], ["page_url", "src", "alt"])
    if kind in ("images_large", "resources_broken"):
        res_links = [l for l in links if l["kind"] == "resource"]
        if kind == "images_large":
            bad = {u for u, e in ext.items() if e.get("kind") == "resource" and (e.get("size_bytes") or 0) > 102400
                   and (e.get("content_type") or "").startswith("image/")}
        else:
            bad = {u for u, e in ext.items() if e.get("kind") == "resource" and ((e.get("status_code") or 0) >= 400 or
                                                                                (e.get("status_code") is None and not e.get("blocked_by_robots")))}
        rows = [{"page_url": l["source_url"], "resource_url": l["target_url"], "status_code": ext[l["target_url"]].get("status_code"),
                 "size_kb": round((ext[l["target_url"]].get("size_bytes") or 0) / 1024, 1)} for l in res_links if l["target_url"] in bad]
        return to_csv(rows, ["page_url", "resource_url", "status_code", "size_kb"])
    if kind == "issues_by_pattern":
        rows = [{"severity": g["severity"], "code": g["code"], "title": g["title"], "pattern": c["pattern"], "urls": c["count"],
                 "pct": c["pct"], "examples": " | ".join(c["examples"])}
                for g in group_issues(db.load_issues(crawl_id)) for c in g["clusters"]]
        return to_csv(rows, ["severity", "code", "title", "pattern", "urls", "pct", "examples"])
    if kind == "redirect_chains":
        rows = [{"url": i["url"], "chain": i["evidence"]} for i in db.load_issues(crawl_id) if i["code"] in ("redirect_chain", "redirect_loop")]
        return to_csv(rows, ["url", "chain"])
    if kind == "canonical_issues":
        rows = [i for i in db.load_issues(crawl_id) if "canonical" in i["code"]]
        return to_csv(rows, ["code", "title", "url", "evidence"])
    if kind == "mixed_content":
        rows = [{"page_url": u, "resource_url": r} for u, p in pages.items() for r in (p.get("mixed") or [])]
        return to_csv(rows, ["page_url", "resource_url"])
    if kind == "all_inlinks":
        return to_csv(link_rows(lambda l: l.get("internal"), ("a", "rendered")), link_fields)
    if kind == "all_outlinks_external":
        return to_csv(link_rows(lambda l: not l.get("internal"), ("a", "rendered")), link_fields)
    raise ValueError(kind)


def bundle(crawl_id: int, write_to_disk: bool = True) -> tuple[str, bytes, Path | None]:
    """Build a ZIP with every CSV, the HTML report and a manifest. Also saved under data/exports/."""
    crawl = db.get_crawl(crawl_id)
    if crawl is None:
        raise KeyError(crawl_id)
    files = {f"{kind}.csv": csv_export(crawl_id, kind) for kind in EXPORT_KINDS}
    files["report.html"] = html_report(crawl_id).encode("utf-8")
    from .clientreport import render as client_render
    files["informe-cliente.html"] = client_render(crawl_id, for_download=True).encode("utf-8")
    files["manifest.json"] = json.dumps(manifest(crawl, files), ensure_ascii=False, indent=2).encode("utf-8")
    name = base_name(crawl)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, data in files.items():
            zf.writestr(f"{name}/{fname}", data)
    data = buf.getvalue()
    saved = None
    if write_to_disk:
        out_dir = get_settings().exports_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = out_dir / f"{name}.zip"
        saved.write_bytes(data)
    return f"{name}.zip", data, saved
