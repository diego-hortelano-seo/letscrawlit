"""Search Console / Analytics data via CSV export (no OAuth, nothing leaves your computer) and the optional
Copyscape API for finding copies of your pages on other websites."""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET

import httpx

from .config import get_settings
from .urlutil import normalize

URL_COLS = ("url", "page", "página", "pagina", "páginas principales", "top pages", "landing page", "página de destino",
            "address", "dirección", "ruta de la página y clase de pantalla", "page path and screen class", "page path")
METRIC_COLS = {
    "clicks": ("clicks", "clics"),
    "impressions": ("impressions", "impresiones"),
    "ctr": ("ctr",),
    "position": ("position", "posición", "posicion", "average position", "posición media"),
    "sessions": ("sessions", "sesiones", "views", "vistas", "usuarios", "users"),
    "conversions": ("conversions", "conversiones", "key events", "eventos clave", "transactions", "transacciones"),
}


def _num(value: str) -> float | None:
    v = (value or "").strip().replace("%", "").replace(" ", "")
    if not v:
        return None
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".") if v.rfind(",") > v.rfind(".") else v.replace(",", "")
    else:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def parse_metrics_csv(blob: bytes, base_url: str) -> tuple[str, list[dict]]:
    """Parse a Search Console ('Páginas') or GA4 ('Páginas y pantallas' / landing pages) CSV export.
    Returns (source, rows). Relative paths (GA4) are resolved against the crawl's start URL."""
    text = blob.decode("utf-8-sig", errors="replace")
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    if not lines:
        raise ValueError("El CSV está vacío.")
    dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t") if any(d in lines[0] for d in ",;\t") else csv.excel
    reader = csv.reader(io.StringIO("\n".join(lines)), dialect)
    header = [h.strip().lower() for h in next(reader)]
    try:
        ui = next(i for i, h in enumerate(header) if h in URL_COLS)
    except StopIteration:
        raise ValueError("No encuentro la columna de URL/página (p. ej. «Páginas principales» o «Página»).")
    idx = {k: next((i for i, h in enumerate(header) if h in names), None) for k, names in METRIC_COLS.items()}
    if all(v is None for v in idx.values()):
        raise ValueError("No encuentro columnas de métricas (Clics, Impresiones, Sesiones…).")
    source = "gsc" if idx["clicks"] is not None or idx["impressions"] is not None else "ga"
    rows = []
    for rec in reader:
        if len(rec) <= ui:
            continue
        url = normalize(rec[ui].strip(), base_url)
        if not url:
            continue
        row = {"url": url}
        for k, i in idx.items():
            row[k] = _num(rec[i]) if i is not None and i < len(rec) else None
        if row.get("ctr") is not None and row["ctr"] > 1:
            row["ctr"] = row["ctr"] / 100
        rows.append(row)
    if not rows:
        raise ValueError("El CSV no contiene filas con URLs válidas.")
    return source, rows[:100000]


def copyscape_search(url: str) -> list[dict]:
    """Search the web for copies of one of your URLs with the Copyscape Premium API (your own paid account)."""
    s = get_settings()
    if not (s.copyscape_user and s.copyscape_key):
        raise RuntimeError("Configura COPYSCAPE_USER y COPYSCAPE_KEY en .env para buscar copias en otras webs.")
    try:
        r = httpx.get("https://www.copyscape.com/api/", timeout=60,
                      params={"u": s.copyscape_user, "k": s.copyscape_key, "o": "csearch", "q": url})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Copyscape no responde ({type(exc).__name__}).")
    if r.status_code != 200:
        raise RuntimeError(f"Copyscape devolvió HTTP {r.status_code}.")
    from .safehttp import no_doctype
    if not no_doctype(r.content):
        raise RuntimeError("Respuesta de Copyscape no válida.")
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        raise RuntimeError("Respuesta de Copyscape no válida.")
    err = root.findtext("error")
    if err:
        raise RuntimeError(f"Copyscape: {err}")
    out = []
    for res in root.findall("result"):
        out.append({"url": res.findtext("url"), "title": res.findtext("title"),
                    "words": res.findtext("minwordsmatched"), "snippet": re.sub(r"\s+", " ", res.findtext("textsnippet") or "")[:300]})
    return out
