"""Compare two crawls and build historical trends for a site (no network)."""
from __future__ import annotations

from collections import Counter

from . import db
from .urlutil import site_key


def _index(crawl_id: int) -> dict[str, dict]:
    return {p["url"]: p for p in db.load_pages(crawl_id)}


def compare(a_id: int, b_id: int) -> dict:
    """b is the newer crawl. URLs added / removed, and per-URL changes in key fields."""
    a, b = _index(a_id), _index(b_id)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    fields = [("status_code", "Código"), ("indexability", "Indexabilidad"), ("title", "Título"),
              ("meta_description", "Meta descripción"), ("canonical", "Canonical"), ("meta_robots", "Meta robots"),
              ("word_count", "Palabras")]
    changes = []
    for url in sorted(set(a) & set(b)):
        for key, label in fields:
            va, vb = a[url].get(key), b[url].get(key)
            if key == "word_count":
                if va and vb and abs(va - vb) / max(va, 1) < 0.2:
                    continue
            if (va or "") != (vb or ""):
                changes.append({"url": url, "field": label, "before": va, "after": vb})
        ha, hb = a[url].get("h1") or [], b[url].get("h1") or []
        if ha[:1] != hb[:1]:
            changes.append({"url": url, "field": "H1", "before": (ha or [""])[0], "after": (hb or [""])[0]})
    ia = Counter(i["code"] for i in db.load_issues(a_id))
    ib = Counter(i["code"] for i in db.load_issues(b_id))
    titles = {i["code"]: i["title"] for i in db.load_issues(a_id) + db.load_issues(b_id)}
    issue_delta = sorted(({"code": c, "title": titles[c], "before": ia.get(c, 0), "after": ib.get(c, 0),
                           "delta": ib.get(c, 0) - ia.get(c, 0)} for c in set(ia) | set(ib)),
                         key=lambda d: (-abs(d["delta"]), d["code"]))
    return {"added": added, "removed": removed, "changes": changes, "issues": issue_delta,
            "a_count": len(a), "b_count": len(b)}


def metrics(crawl: dict) -> dict:
    """Per-crawl metrics for trend charts (cached in extras['metrics'] once computed)."""
    cached = (crawl.get("extras") or {}).get("metrics")
    if cached and cached.get("_v") == 4:
        return cached
    pages = db.load_pages(crawl["id"])
    issues = db.load_issues(crawl["id"])
    links = db.load_links(crawl["id"])
    externals = db.load_externals(crawl["id"])
    html = [p for p in pages if p.get("status_code") == 200 and "html" in (p.get("content_type") or "")]
    internal_links = [l for l in links if l["kind"] in ("a", "rendered") and l.get("internal")]
    sev = Counter(i["severity"] for i in issues)
    codes = Counter(i["code"] for i in issues)
    times = [p["response_ms"] for p in html if p.get("response_ms") is not None]
    images = [e for e in externals if e.get("kind") == "resource" and (e.get("content_type") or "").startswith("image/")
              and e.get("size_bytes")]
    words = [p.get("word_count") or 0 for p in html]
    m = {
        "_v": 4, "pages": len(pages), "indexable": sum(1 for p in pages if p.get("indexable")),
        "images_mb": round(sum(e["size_bytes"] for e in images) / 1048576, 2),
        "avg_image_kb": round(sum(e["size_bytes"] for e in images) / len(images) / 1024) if images else 0,
        "html_kb_avg": round(sum(p.get("size_bytes") or 0 for p in html) / len(html) / 1024) if html else 0,
        "broken_links": codes.get("broken_link", 0) + codes.get("broken_resource", 0),
        "thin_pages": codes.get("thin_content", 0), "missing_titles": codes.get("missing_title", 0),
        "missing_descriptions": codes.get("missing_description", 0),
        "avg_words": round(sum(words) / len(words)) if words else 0,
        "issues": len(issues), "critical": sev.get("critical", 0), "high": sev.get("high", 0),
        "medium": sev.get("medium", 0), "low": sev.get("low", 0),
        "errors_4xx": sum(1 for p in pages if 400 <= (p.get("status_code") or 0) < 500),
        "errors_5xx": sum(1 for p in pages if (p.get("status_code") or 0) >= 500),
        "redirects": sum(1 for p in pages if 300 <= (p.get("status_code") or 0) < 400),
        "avg_inlinks": round(len(internal_links) / max(1, len(html)), 1),
        "avg_response_ms": round(sum(times) / len(times)) if times else 0,
    }
    extras = dict(crawl.get("extras") or {})
    extras["metrics"] = m
    if crawl["status"] not in db.ACTIVE_STATUSES:
        db.update_crawl(crawl["id"], extras=extras)
    return m


TREND_SERIES = [("pages", "URLs rastreadas"), ("indexable", "URLs indexables"),
                ("issues", "Incidencias totales"), ("critical", "Críticas"), ("high", "Altas"),
                ("errors_4xx", "Errores 4xx"), ("errors_5xx", "Errores 5xx"), ("broken_links", "Enlaces y recursos rotos"),
                ("redirects", "Redirecciones"), ("images_mb", "Peso total de imágenes (MB)"),
                ("avg_image_kb", "Peso medio por imagen (KB)"), ("html_kb_avg", "Peso medio del HTML (KB)"),
                ("thin_pages", "Páginas con poco contenido"), ("missing_titles", "Páginas sin título"),
                ("missing_descriptions", "Páginas sin meta description"), ("avg_words", "Palabras medias por página"),
                ("avg_inlinks", "Enlaces internos por página"), ("avg_response_ms", "Tiempo de respuesta medio (ms)")]
LOWER_IS_BETTER = {"issues", "critical", "high", "errors_4xx", "errors_5xx", "broken_links", "redirects", "images_mb",
                   "avg_image_kb", "html_kb_avg", "thin_pages", "missing_titles", "missing_descriptions", "avg_response_ms"}


def trends_csv(site: str) -> bytes:
    """One row per crawl and one column per metric: ready for Looker Studio, Sheets or Excel."""
    import csv
    import io
    t = trends(site)
    keys = [k for k, _ in TREND_SERIES]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["fecha", "auditoria", "sitio"] + keys)
    for p in t["points"]:
        from .report import _safe_cell
        w.writerow([_safe_cell(v) for v in [p["date"], p["id"], site] + [p.get(k, "") for k in keys]])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def chart(points: list[dict], key: str, width: int = 900, height: int = 260) -> dict:
    """Coordinates for a dated line chart with axes (drawn in the template as inline SVG, no JavaScript)."""
    vals = [p.get(key) or 0 for p in points]
    if not vals:
        return {}
    lo, hi = min(0, min(vals)), max(vals) or 1
    left, right, top, bottom = 52, 16, 16, 34
    w, h = width - left - right, height - top - bottom
    n = len(vals)
    xs = [left + (w * i / (n - 1) if n > 1 else w / 2) for i in range(n)]
    ys = [top + h - h * (v - lo) / ((hi - lo) or 1) for v in vals]
    ticks = [round(lo + (hi - lo) * f, 2) for f in (0, .25, .5, .75, 1)]
    step = max(1, n // 8)
    return {"w": width, "h": height, "left": left, "bottom": height - bottom, "right": width - right,
            "path": " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys))),
            "area": f"M{xs[0]:.1f},{top + h:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + f" L{xs[-1]:.1f},{top + h:.1f} Z",
            "dots": [{"x": round(x, 1), "y": round(y, 1), "v": v, "date": p["date"], "id": p["id"],
                      "label": i % step == 0 or i == n - 1} for i, (x, y, v, p) in enumerate(zip(xs, ys, vals, points))],
            "ticks": [{"v": t, "y": round(top + h - h * (t - lo) / ((hi - lo) or 1), 1)} for t in ticks]}


def sites() -> list[dict]:
    out: dict[str, dict] = {}
    for c in db.list_crawls(1000):
        if c["status"] not in ("completed", "cancelled"):
            continue
        k = site_key(c["start_url"])
        s = out.setdefault(k, {"site": k, "crawls": 0, "last": None})
        s["crawls"] += 1
        s["last"] = max(s["last"] or "", c["created_at"])
    return sorted(out.values(), key=lambda s: s["last"] or "", reverse=True)


def trends(site: str) -> dict:
    crawls = [c for c in db.crawls_for_site(site) if c["status"] in ("completed", "cancelled")]
    points = [{"id": c["id"], "date": c["created_at"][:10], **metrics(c)} for c in crawls]
    series = []
    for key, label in TREND_SERIES:
        values = [p[key] for p in points]
        series.append({"key": key, "label": label, "data": values,
                       "first": values[0] if values else None, "last": values[-1] if values else None})
    return {"site": site, "points": points, "series": series}


def svg_line(values: list[float], width: int = 320, height: int = 80) -> str:
    """Tiny inline SVG line chart (no JS, works in the HTML report too)."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values)
    pts = [(8 + (width - 16) * (i / max(1, n - 1)), height - 8 - (height - 16) * ((v - lo) / span)) for i, v in enumerate(values)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3"><title>{v}</title></circle>' for (x, y), v in zip(pts, values))
    return (f'<svg viewBox="0 0 {width} {height}" class="spark" role="img" aria-label="Tendencia">'
            f'<path d="{path}" fill="none" stroke="currentColor" stroke-width="2"/>{dots}</svg>')
