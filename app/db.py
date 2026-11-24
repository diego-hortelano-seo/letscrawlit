"""SQLite storage. One short-lived connection per operation (safe across threads)."""
from __future__ import annotations

import json
from collections import Counter
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS crawls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_url TEXT NOT NULL,
    status TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    ack_text TEXT NOT NULL,
    ack_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    pages_done INTEGER NOT NULL DEFAULT 0,
    urls_queued INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    notes_json TEXT NOT NULL DEFAULT '[]',
    robots_json TEXT NOT NULL DEFAULT '{}',
    extras_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    depth INTEGER,
    source TEXT,
    status_code INTEGER,
    content_type TEXT,
    redirect_target TEXT,
    response_ms INTEGER,
    size_bytes INTEGER,
    blocked_by_robots INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    title TEXT,
    meta_description TEXT,
    h1_json TEXT NOT NULL DEFAULT '[]',
    h2_json TEXT NOT NULL DEFAULT '[]',
    canonical TEXT,
    canonical_count INTEGER NOT NULL DEFAULT 0,
    meta_robots TEXT,
    x_robots TEXT,
    lang TEXT,
    word_count INTEGER,
    content_hash TEXT,
    text_excerpt TEXT,
    structured_json TEXT NOT NULL DEFAULT '{}',
    jsonld_invalid INTEGER NOT NULL DEFAULT 0,
    rendered INTEGER NOT NULL DEFAULT 0,
    rendered_title TEXT,
    rendered_robots TEXT,
    rendered_canonical TEXT,
    rendered_word_count INTEGER,
    rendered_text TEXT,
    render_error TEXT,
    indexable INTEGER,
    indexability TEXT,
    fetched_at TEXT,
    UNIQUE(crawl_id, url)
);
CREATE TABLE IF NOT EXISTS links (
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    anchor TEXT,
    nofollow INTEGER NOT NULL DEFAULT 0,
    internal INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_crawl_target ON links(crawl_id, target_url);
CREATE INDEX IF NOT EXISTS idx_links_crawl_source ON links(crawl_id, source_url);
CREATE TABLE IF NOT EXISTS images (
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    page_url TEXT NOT NULL,
    src TEXT,
    alt TEXT
);
CREATE INDEX IF NOT EXISTS idx_images_crawl ON images(crawl_id);
CREATE TABLE IF NOT EXISTS sitemap_urls (
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    UNIQUE(crawl_id, url)
);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    evidence TEXT NOT NULL,
    remediation TEXT NOT NULL,
    why TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issues_crawl ON issues(crawl_id);
CREATE TABLE IF NOT EXISTS externals (
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    status_code INTEGER,
    content_type TEXT,
    redirect_target TEXT,
    response_ms INTEGER,
    blocked_by_robots INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE(crawl_id, url)
);
"""

# Columns added after v0.1 (databases created earlier are migrated in place).
MIGRATIONS = [
    ("pages", "headers_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("pages", "hreflang_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("pages", "mixed_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("crawls", "phase", "TEXT"),
    ("crawls", "ext_done", "INTEGER NOT NULL DEFAULT 0"),
    ("crawls", "ext_total", "INTEGER NOT NULL DEFAULT 0"),
    ("pages", "extra_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("pages", "custom_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("externals", "kind", "TEXT NOT NULL DEFAULT 'external'"),
    ("externals", "size_bytes", "INTEGER"),
    ("links", "rel", "TEXT"),
    ("crawls", "state_json", "TEXT"),
]
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS insights (
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    UNIQUE(crawl_id, kind)
);
CREATE TABLE IF NOT EXISTS analytics (
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    clicks REAL, impressions REAL, ctr REAL, position REAL, sessions REAL, conversions REAL,
    UNIQUE(crawl_id, source, url)
);
CREATE TABLE IF NOT EXISTS ai_results (
    crawl_id INTEGER NOT NULL REFERENCES crawls(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    task TEXT NOT NULL,
    result TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(crawl_id, url, task)
);
-- Notes per URL. site holds the audit they belong to ("crawl:<id>"): a new audit starts without notes.
CREATE TABLE IF NOT EXISTS url_notes (
    site TEXT NOT NULL,
    url TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pendiente',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (site, url)
);
-- «Apuntes»: a notebook per audit (site = "crawl:<id>"; free notes, optionally tied to a URL or an issue code).
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'nota',
    url TEXT,
    issue TEXT,
    in_report INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_site ON journal(site, created_at);
"""

NOTE_STATUSES = ("pendiente", "en curso", "revisado", "corregido")


def get_notes(site: str) -> dict[str, dict]:
    with connect() as c:
        rows = c.execute("SELECT url, note, status, updated_at FROM url_notes WHERE site=?", (site,)).fetchall()
    return {r[0]: {"note": r[1], "status": r[2], "updated_at": r[3]} for r in rows}


def notes_version(site: str) -> tuple:
    with connect() as c:
        return tuple(c.execute("SELECT COUNT(*), MAX(updated_at) FROM url_notes WHERE site=?", (site,)).fetchone())


def set_note(site: str, url: str, note: str, status: str) -> dict | None:
    """Save a note (empty text and status 'pendiente' deletes it). Returns the stored note or None."""
    note = (note or "").strip()[:2000]
    status = status if status in NOTE_STATUSES else "pendiente"
    with connect() as c:
        if not note and status == "pendiente":
            c.execute("DELETE FROM url_notes WHERE site=? AND url=?", (site, url))
            return None
        stamp = now()
        c.execute("INSERT INTO url_notes (site, url, note, status, updated_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(site, url) DO UPDATE SET note=excluded.note, status=excluded.status, updated_at=excluded.updated_at",
                  (site, url, note, status, stamp))
    return {"note": note, "status": status, "updated_at": stamp}

JOURNAL_STATUSES = ("nota", "pendiente", "hecho")
JOURNAL_MAX_TEXT = 4000


def journal_list(site: str, q: str = "", status: str = "", url: str = "", issue: str = "", report_only: bool = False) -> list[dict]:
    sql, args = "SELECT * FROM journal WHERE site=?", [site]
    if q:
        sql += " AND (text LIKE ? ESCAPE '!' OR IFNULL(url,'') LIKE ? ESCAPE '!' OR IFNULL(issue,'') LIKE ? ESCAPE '!')"
        like = "%" + q.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        args += [like, like, like]
    if status in JOURNAL_STATUSES:
        sql += " AND status=?"
        args.append(status)
    if url:  # the note's URLs are stored one per line
        sql += " AND instr(char(10) || IFNULL(url,'') || char(10), char(10) || ? || char(10)) > 0"
        args.append(url)
    if issue:
        sql += " AND issue=?"
        args.append(issue)
    if report_only:
        sql += " AND in_report=1"
    with connect() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT 500", args).fetchall()]


def journal_counts(site: str) -> dict:
    """How many notes point at each URL and each issue code, and how many are still pending."""
    with connect() as c:
        rows = c.execute("SELECT url, issue, status FROM journal WHERE site=?", (site,)).fetchall()
    by_url = Counter(u for r in rows if r[0] for u in r[0].split("\n") if u)
    by_issue = Counter(r[1] for r in rows if r[1])
    return {"total": len(rows), "pending": sum(1 for r in rows if r[2] == "pendiente"), "url": by_url, "issue": by_issue}


def journal_add(site: str, text: str, status: str = "nota", url: str | None = None, issue: str | None = None,
                in_report: bool = False) -> int:
    text = (text or "").strip()[:JOURNAL_MAX_TEXT]
    if not text:
        raise ValueError("Escribe algo en el apunte.")
    status = status if status in JOURNAL_STATUSES else "nota"
    stamp = now()
    with connect() as c:
        cur = c.execute("INSERT INTO journal (site, text, status, url, issue, in_report, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (site, text, status, url or None, issue or None, 1 if in_report else 0, stamp, stamp))
        return int(cur.lastrowid)


def journal_update(site: str, entry_id: int, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in ("text", "status", "in_report")}
    if "text" in allowed:
        allowed["text"] = (allowed["text"] or "").strip()[:JOURNAL_MAX_TEXT]
        if not allowed["text"]:
            raise ValueError("El apunte no puede quedar vacío.")
    if "status" in allowed and allowed["status"] not in JOURNAL_STATUSES:
        raise ValueError("Estado no válido.")
    if "in_report" in allowed:
        allowed["in_report"] = 1 if allowed["in_report"] else 0
    if not allowed:
        return
    sets = ", ".join(f"{k}=?" for k in allowed)  # keys come from the fixed list above
    with connect() as c:
        c.execute(f"UPDATE journal SET {sets}, updated_at=? WHERE site=? AND id=?", (*allowed.values(), now(), site, entry_id))


def journal_delete(site: str, entry_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM journal WHERE site=? AND id=?", (site, entry_id))


PAGE_COLUMNS = [
    "url", "depth", "source", "status_code", "content_type", "redirect_target", "response_ms",
    "size_bytes", "blocked_by_robots", "error", "title", "meta_description", "h1_json", "h2_json",
    "canonical", "canonical_count", "meta_robots", "x_robots", "lang", "word_count", "content_hash",
    "text_excerpt", "structured_json", "jsonld_invalid", "rendered", "rendered_title",
    "rendered_robots", "rendered_canonical", "rendered_word_count", "rendered_text", "render_error",
    "indexable", "indexability", "fetched_at", "headers_json", "hreflang_json", "mixed_json",
    "extra_json", "custom_json",
]
_JSON_DEFAULTS = {"h1_json": "[]", "h2_json": "[]", "structured_json": "{}", "headers_json": "{}",
                  "hreflang_json": "[]", "mixed_json": "[]", "extra_json": "{}", "custom_json": "{}"}
ACTIVE_STATUSES = ("queued", "running")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            existing = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        c.executescript(EXTRA_SCHEMA)
        _notes_per_crawl(c)


def note_scope(crawl_id: int) -> str:
    """Key under which an audit's notes and «Apuntes» are stored: they never carry over to another audit."""
    return f"crawl:{int(crawl_id)}"


def _notes_per_crawl(c: sqlite3.Connection) -> None:
    """Notes used to be shared by every crawl of a site. Old ones now belong to the latest audit of their site."""
    old = {r[0] for t in ("url_notes", "journal") for r in c.execute(f"SELECT DISTINCT site FROM {t} WHERE site NOT LIKE 'crawl:%'")}
    if not old:
        return
    from .urlutil import site_key
    latest = {site_key(r[1]): r[0] for r in c.execute("SELECT id, start_url FROM crawls ORDER BY id")}
    for site in old & set(latest):
        for t in ("url_notes", "journal"):
            c.execute(f"UPDATE OR IGNORE {t} SET site=? WHERE site=?", (note_scope(latest[site]), site))


def mark_interrupted() -> int:
    """Crawls left running by a previous process become 'interrupted' (recoverable)."""
    with connect() as c:
        cur = c.execute(
            "UPDATE crawls SET status='interrupted', finished_at=?, error=? WHERE status IN ('queued','running')",
            (now(), "La aplicación se cerró durante el rastreo. Puedes reanudarlo desde el último punto guardado "
                    "o relanzarlo con la misma configuración."),
        )
        return cur.rowcount


# --- crawls -----------------------------------------------------------------

def create_crawl(start_url: str, settings: dict, ack_text: str) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO crawls (start_url, status, settings_json, ack_text, ack_at, created_at) VALUES (?,?,?,?,?,?)",
            (start_url, "queued", json.dumps(settings), ack_text, now(), now()),
        )
        return int(cur.lastrowid)


def get_crawl(crawl_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM crawls WHERE id=?", (crawl_id,)).fetchone()
    return _crawl_dict(row) if row else None


def list_crawls(limit: int = 100) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM crawls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_crawl_dict(r) for r in rows]


def _crawl_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["settings"] = json.loads(d.pop("settings_json") or "{}")
    d["notes"] = json.loads(d.pop("notes_json") or "[]")
    d["robots"] = json.loads(d.pop("robots_json") or "{}")
    d["extras"] = json.loads(d.pop("extras_json") or "{}")
    return d


_CRAWL_UPDATABLE = {"status", "started_at", "finished_at", "pages_done", "urls_queued", "error",
                    "phase", "ext_done", "ext_total", "state_json"}


def update_crawl(crawl_id: int, **fields: Any) -> None:
    sets, values = [], []
    for key, value in fields.items():
        if key in ("notes", "robots", "extras"):
            sets.append(f"{key}_json=?")
            values.append(json.dumps(value))
        elif key in _CRAWL_UPDATABLE:
            sets.append(f"{key}=?")
            values.append(value)
        else:
            raise ValueError(f"Unknown crawl field {key}")
    if not sets:
        return
    with connect() as c:
        c.execute(f"UPDATE crawls SET {', '.join(sets)} WHERE id=?", (*values, crawl_id))


def delete_crawl(crawl_id: int) -> None:
    import shutil
    with connect() as c:
        c.execute("DELETE FROM crawls WHERE id=?", (crawl_id,))
    s = get_settings()
    for folder in (s.data_dir / "html" / str(int(crawl_id)), s.screenshots_dir / str(int(crawl_id))):
        shutil.rmtree(folder, ignore_errors=True)  # stored HTML and screenshots of that crawl


# --- crawl data -------------------------------------------------------------

def save_page(crawl_id: int, page: dict) -> None:
    cols = ["crawl_id", *PAGE_COLUMNS]
    vals = [crawl_id, *(page.get(col) for col in PAGE_COLUMNS)]
    for i, col in enumerate(cols):
        if col in ("blocked_by_robots", "canonical_count", "jsonld_invalid", "rendered") and vals[i] is None:
            vals[i] = 0
        if col in _JSON_DEFAULTS and vals[i] is None:
            vals[i] = _JSON_DEFAULTS[col]
    with connect() as c:
        c.execute(
            f"INSERT OR REPLACE INTO pages ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            vals,
        )


def save_links(crawl_id: int, links: Iterable[dict]) -> None:
    rows = [
        (crawl_id, l["source_url"], l["target_url"], (l.get("anchor") or "")[:300],
         int(bool(l.get("nofollow"))), int(bool(l.get("internal", True))), l["kind"], l.get("rel") or None)
        for l in links
    ]
    if rows:
        with connect() as c:
            c.executemany(
                "INSERT INTO links (crawl_id, source_url, target_url, anchor, nofollow, internal, kind, rel) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )


def delete_links_from(crawl_id: int, source_urls: Iterable[str]) -> None:
    """Used on resume: a page that was in flight is fetched again, so drop its partial links."""
    with connect() as c:
        c.executemany("DELETE FROM links WHERE crawl_id=? AND source_url=?", [(crawl_id, u) for u in source_urls])


def update_timing(crawl_id: int, url: str, kind: str, ms: int, more: list[int] | None = None) -> bool:
    """Pages: the median of the first measurement and the new ones (3 in total); all are kept in extra_json.
    Resources: the lower of two. True if the stored time went down."""
    with connect() as c:
        if kind == "page":
            row = c.execute("SELECT response_ms, extra_json FROM pages WHERE crawl_id=? AND url=?", (crawl_id, url)).fetchone()
            if not row:
                return False
            extra = json.loads(row["extra_json"] or "{}")
            samples = ([row["response_ms"]] if row["response_ms"] is not None else []) + [ms] + list(more or [])
            extra["response_ms_first"], extra["response_ms_samples"] = row["response_ms"], samples
            median = sorted(samples)[len(samples) // 2]
            better = row["response_ms"] is None or median < row["response_ms"]
            c.execute("UPDATE pages SET response_ms=?, extra_json=? WHERE crawl_id=? AND url=?",
                      (median, json.dumps(extra, ensure_ascii=False), crawl_id, url))
            return better
        row = c.execute("SELECT response_ms FROM externals WHERE crawl_id=? AND url=?", (crawl_id, url)).fetchone()
        if row and (row["response_ms"] is None or ms < row["response_ms"]):
            c.execute("UPDATE externals SET response_ms=? WHERE crawl_id=? AND url=?", (ms, crawl_id, url))
            return True
        return False


def save_insight(crawl_id: int, kind: str, data) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO insights (crawl_id, kind, data_json) VALUES (?,?,?)",
                  (crawl_id, kind, json.dumps(data, ensure_ascii=False)))


def load_insight(crawl_id: int, kind: str, default=None):
    with connect() as c:
        row = c.execute("SELECT data_json FROM insights WHERE crawl_id=? AND kind=?", (crawl_id, kind)).fetchone()
    return json.loads(row[0]) if row else default


_analytics_ver: dict[int, int] = {}  # bumped on every import, so cached tables show the new figures


def analytics_version(crawl_id: int) -> int:
    return _analytics_ver.get(crawl_id, 0)


def save_analytics(crawl_id: int, source: str, rows: list[dict]) -> int:
    with connect() as c:
        c.execute("DELETE FROM analytics WHERE crawl_id=? AND source=?", (crawl_id, source))
        c.executemany(
            "INSERT OR REPLACE INTO analytics (crawl_id, source, url, clicks, impressions, ctr, position, sessions, conversions)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(crawl_id, source, r["url"], r.get("clicks"), r.get("impressions"), r.get("ctr"), r.get("position"),
              r.get("sessions"), r.get("conversions")) for r in rows])
    _analytics_ver[crawl_id] = _analytics_ver.get(crawl_id, 0) + 1
    return len(rows)


def load_analytics(crawl_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM analytics WHERE crawl_id=?", (crawl_id,)).fetchall()]


def save_ai(crawl_id: int, url: str, task: str, result: str, model: str) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO ai_results (crawl_id, url, task, result, model, created_at) VALUES (?,?,?,?,?,?)",
                  (crawl_id, url, task, result, model, now()))


def ai_version(crawl_id: int) -> tuple:
    with connect() as c:
        # MAX(rowid): INSERT OR REPLACE gives the row a new rowid, so a page redone within the same second still counts
        return tuple(c.execute("SELECT COUNT(*), MAX(created_at), MAX(rowid) FROM ai_results WHERE crawl_id=?", (crawl_id,)).fetchone())


def load_ai(crawl_id: int, url: str | None = None) -> list[dict]:
    with connect() as c:
        if url:
            rows = c.execute("SELECT * FROM ai_results WHERE crawl_id=? AND url=?", (crawl_id, url)).fetchall()
        else:
            rows = c.execute("SELECT * FROM ai_results WHERE crawl_id=?", (crawl_id,)).fetchall()
    return [dict(r) for r in rows]


def crawls_for_site(site: str) -> list[dict]:
    from .urlutil import site_key
    return [c for c in reversed(list_crawls(1000)) if site_key(c["start_url"]) == site]


def save_images(crawl_id: int, page_url: str, images: Iterable[dict]) -> None:
    rows = [(crawl_id, page_url, (i.get("src") or "")[:2000], i.get("alt")) for i in images]
    if rows:
        with connect() as c:
            c.executemany("INSERT INTO images (crawl_id, page_url, src, alt) VALUES (?,?,?,?)", rows)


def save_sitemap_urls(crawl_id: int, urls: Iterable[str]) -> None:
    with connect() as c:
        c.executemany("INSERT OR IGNORE INTO sitemap_urls (crawl_id, url) VALUES (?,?)", [(crawl_id, u) for u in urls])


def load_pages(crawl_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM pages WHERE crawl_id=? ORDER BY id", (crawl_id,)).fetchall()
    return [decode_page(r) for r in rows]


def decode_page(row: sqlite3.Row | dict) -> dict:
    d = dict(row)
    d["h1"] = json.loads(d.get("h1_json") or "[]")
    d["h2"] = json.loads(d.get("h2_json") or "[]")
    d["structured"] = json.loads(d.get("structured_json") or "{}")
    d["headers"] = json.loads(d.get("headers_json") or "{}")
    d["hreflang"] = json.loads(d.get("hreflang_json") or "[]")
    d["mixed"] = json.loads(d.get("mixed_json") or "[]")
    d["extra"] = json.loads(d.get("extra_json") or "{}")
    d["custom"] = json.loads(d.get("custom_json") or "{}")
    return d


def save_external(crawl_id: int, row: dict) -> None:
    """External links (kind='external') and checked internal resources (kind='resource')."""
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO externals (crawl_id, url, status_code, content_type, redirect_target, response_ms,"
            " blocked_by_robots, error, kind, size_bytes) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (crawl_id, row["url"], row.get("status_code"), row.get("content_type"), row.get("redirect_target"),
             row.get("response_ms"), int(bool(row.get("blocked_by_robots"))), row.get("error"),
             row.get("kind", "external"), row.get("size_bytes")),
        )


def load_externals(crawl_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM externals WHERE crawl_id=?", (crawl_id,)).fetchall()]


def get_page(crawl_id: int, url: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM pages WHERE crawl_id=? AND url=?", (crawl_id, url)).fetchone()
    return decode_page(row) if row else None


def query_pages(crawl_id: int, q: str = "", flt: str = "all", offset: int = 0, limit: int = 100) -> tuple[list[dict], int]:
    where, params = ["crawl_id=?"], [crawl_id]
    if q:
        where.append("(url LIKE ? OR title LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    filters = {
        "html": "content_type LIKE '%html%' AND status_code=200",
        "indexable": "indexable=1",
        "nonindexable": "COALESCE(indexable,0)=0",
        "errors": "(status_code>=400 OR error IS NOT NULL)",
        "redirects": "status_code BETWEEN 300 AND 399",
        "blocked": "blocked_by_robots=1",
    }
    if flt in filters:
        where.append(filters[flt])
    clause = " AND ".join(where)
    with connect() as c:
        total = c.execute(f"SELECT COUNT(*) FROM pages WHERE {clause}", params).fetchone()[0]
        rows = c.execute(
            f"SELECT * FROM pages WHERE {clause} ORDER BY id LIMIT ? OFFSET ?", (*params, limit, offset)
        ).fetchall()
    return [decode_page(r) for r in rows], int(total)


def load_links(crawl_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM links WHERE crawl_id=?", (crawl_id,)).fetchall()]


def links_for(crawl_id: int, url: str) -> tuple[list[dict], list[dict]]:
    with connect() as c:
        out = [dict(r) for r in c.execute(
            "SELECT * FROM links WHERE crawl_id=? AND source_url=? LIMIT 500", (crawl_id, url)).fetchall()]
        inl = [dict(r) for r in c.execute(
            "SELECT * FROM links WHERE crawl_id=? AND target_url=? LIMIT 500", (crawl_id, url)).fetchall()]
    return out, inl


def load_images(crawl_id: int, page_url: str | None = None) -> list[dict]:
    with connect() as c:
        if page_url:
            rows = c.execute("SELECT * FROM images WHERE crawl_id=? AND page_url=?", (crawl_id, page_url)).fetchall()
        else:
            rows = c.execute("SELECT * FROM images WHERE crawl_id=?", (crawl_id,)).fetchall()
    return [dict(r) for r in rows]


def load_sitemap_urls(crawl_id: int) -> set[str]:
    with connect() as c:
        return {r[0] for r in c.execute("SELECT url FROM sitemap_urls WHERE crawl_id=?", (crawl_id,)).fetchall()}


def replace_issues(crawl_id: int, issues: Iterable[Any]) -> None:
    rows = [(crawl_id, i.code, i.severity, i.title, i.url, i.evidence, i.remediation, i.why) for i in issues]
    with connect() as c:
        c.execute("DELETE FROM issues WHERE crawl_id=?", (crawl_id,))
        c.executemany(
            "INSERT INTO issues (crawl_id, code, severity, title, url, evidence, remediation, why) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def load_issues(crawl_id: int) -> list[dict]:
    with connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM issues WHERE crawl_id=? ORDER BY id", (crawl_id,)).fetchall()]
    return [r for r in map(_fresh_spelling, rows) if r is not None]


_SPELL_EVIDENCE = re.compile(r"^(\d+) palabra\(s\) desconocidas: (.*)$")


def _fresh_spelling(issue: dict) -> dict | None:
    """Audits saved by an older, stricter spell checker listed valid words («coincida», «interiorista»).
    Re-check the listed words with the current rules; drop the issue when fewer than 3 remain."""
    if issue.get("code") != "spelling_errors":
        return issue
    m = _SPELL_EVIDENCE.match(issue.get("evidence") or "")
    if not m:
        return issue
    from .checks import _still_typo
    words = [w for w in m.group(2).split(", ") if w]
    keep = [w for w in words if _still_typo(w, "es")]
    if len(keep) == len(words):
        return issue
    count = round(int(m.group(1)) * len(keep) / len(words))  # only some words are listed: scale the total
    if count < 3:
        return None
    return {**issue, "evidence": f"{count} palabra(s) desconocidas: {', '.join(keep)}"}
