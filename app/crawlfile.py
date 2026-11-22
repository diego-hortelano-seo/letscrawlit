"""Portable crawl files (.seocrawl): the whole crawl, including the resume checkpoint, in one gzipped JSON file.
Use it to archive a crawl, move it to another computer or resume it there."""
from __future__ import annotations

import gzip
import json
import zlib

from . import db
from .config import VERSION

FORMAT = "letscrawl-crawl"
OLD_FORMATS = {"seo-spider-local-crawl"}  # files exported before the rename
TABLES = ("pages", "links", "images", "sitemap_urls", "issues", "externals", "insights", "analytics", "ai_results")
MAX_IMPORT_BYTES = 500 * 1024 * 1024
MAX_UNCOMPRESSED = 2 * 1024 * 1024 * 1024  # a gzip «bomb» stops here instead of filling the memory


def export_crawl(crawl_id: int) -> bytes:
    with db.connect() as c:
        crawl = dict(c.execute("SELECT * FROM crawls WHERE id=?", (crawl_id,)).fetchone())
        data = {"format": FORMAT, "version": VERSION, "crawl": crawl}
        for t in TABLES:
            rows = c.execute(f"SELECT * FROM {t} WHERE crawl_id=?", (crawl_id,)).fetchall()
            data[t] = [{k: r[k] for k in r.keys() if k not in ("id", "crawl_id")} for r in rows]
    return gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8"), compresslevel=6)


def import_crawl(blob: bytes) -> int:
    if len(blob) > MAX_IMPORT_BYTES:
        raise ValueError("El archivo supera 500 MB.")
    try:
        from .safehttp import gunzip_capped
        data = json.loads(gunzip_capped(blob, MAX_UNCOMPRESSED).decode("utf-8"))
    except (OSError, ValueError, zlib.error) as exc:
        raise ValueError("No es un archivo .seocrawl válido.") from exc
    if not isinstance(data, dict) or data.get("format") not in {FORMAT, *OLD_FORMATS} or not isinstance(data.get("crawl"), dict):
        raise ValueError("No es un archivo .seocrawl válido.")
    crawl = data["crawl"]
    with db.connect() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(crawls)").fetchall()} - {"id"}
        row = {k: v for k, v in crawl.items() if k in cols}
        if row.get("status") in db.ACTIVE_STATUSES:
            row["status"] = "interrupted"
        if not row.get("start_url") or not row.get("ack_text"):
            raise ValueError("Al archivo le falta la URL o la confirmación de permiso.")
        keys = list(row)
        cur = c.execute(f"INSERT INTO crawls ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})", [row[k] for k in keys])
        new_id = int(cur.lastrowid)
        for t in TABLES:
            tcols = {r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()} - {"id", "crawl_id"}
            for r in data.get(t) or []:
                if not isinstance(r, dict):
                    continue
                vals = {k: v for k, v in r.items() if k in tcols}
                if not vals:
                    continue
                ks = ["crawl_id", *vals]
                c.execute(f"INSERT OR IGNORE INTO {t} ({', '.join(ks)}) VALUES ({', '.join('?' * len(ks))})",
                          [new_id, *vals.values()])
    return new_id
