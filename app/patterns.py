"""Group URLs by path pattern: «3.500 de estos 404 están en /blog/{slug}/page/{n}». Pure functions."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, unquote, urlsplit

_NUM = re.compile(r"^\d+$")
_ID = re.compile(r"^(?=.*\d)[0-9a-f-]{8,}$", re.I)            # hashes, uuids
_DATE = re.compile(r"^(19|20)\d\d(-\d\d){0,2}$")
_FILE_NUM = re.compile(r"^(.*?)[-_]?\d+(\.[a-z0-9]{2,5})$", re.I)  # foto-123.jpg
_PAGE_WORDS = {"page", "pagina", "página", "p", "pag"}


def _segment(seg: str, prev: str) -> str:
    s = unquote(seg).lower()
    if re.fullmatch(r"(19|20)\d\d", s):
        return "{año}"
    if _NUM.match(s):
        return "{n}" if prev not in _PAGE_WORDS else "{página}"
    if _DATE.match(s):
        return "{fecha}"
    if _ID.match(s):
        return "{id}"
    m = _FILE_NUM.match(s)
    if m and m.group(1):
        return f"{m.group(1)}-{{n}}{m.group(2)}"
    return s


def template(url: str) -> list[str]:
    """Normalised path segments plus a query signature as the last element (or '')."""
    parts = urlsplit(url)
    segs, prev = [], ""
    for seg in [s for s in parts.path.split("/") if s]:
        norm = _segment(seg, prev)
        segs.append(norm)
        prev = norm if not norm.startswith("{") else prev
    keys = sorted({k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    query = ("?" + "&".join(f"{k}=*" for k in keys)) if keys else ""
    return segs + [query]


def _pattern(t: list[str], depth: int) -> str:
    segs, query = t[:-1], t[-1]
    if depth >= len(segs):
        return "/" + "/".join(segs) + query if segs else "/" + query
    return "/" + "/".join(segs[:depth]) + "/*" + (query if query else "")


def cluster(urls: list[str], min_size: int = 2, max_groups: int = 8) -> list[dict]:
    """Groups that explain most URLs, as specific as possible: start at the first folder and drill down while one
    child pattern still holds 80 % of its parent. Each group: pattern, count, pct, examples."""
    urls = list(dict.fromkeys(urls))
    if len(urls) < 3:
        return []
    temps = {u: template(u) for u in urls}
    total = len(urls)
    out: list[dict] = []

    def drill(members: list[str], depth: int) -> str:
        while True:
            children = Counter(_pattern(temps[u], depth + 1) for u in members)
            pattern, n = children.most_common(1)[0]
            if n < 0.8 * len(members) or _pattern(temps[members[0]], depth) == pattern:
                return _pattern(temps[members[0]], depth) if len({_pattern(temps[u], depth) for u in members}) == 1 \
                    else pattern
            members = [u for u in members if _pattern(temps[u], depth + 1) == pattern]
            depth += 1
            if depth > 6:
                return pattern

    def positional(members: list[str]) -> tuple[str, list[str]] | None:
        """Same shape, varying parts as *: /blog/*/page/{página}. Needs 80 % of the members to share the shape."""
        shapes: dict[tuple, list[str]] = defaultdict(list)
        for u in members:
            shapes[(len(temps[u]), temps[u][-1])].append(u)
        shape, same = max(shapes.items(), key=lambda kv: len(kv[1]))
        if len(same) < 0.8 * len(members) or shape[0] < 3:
            return None
        cols = list(zip(*(temps[u][:-1] for u in same)))
        segs = [c[0] if len(set(c)) == 1 else "*" for c in cols]
        if segs.count("*") == len(segs) or segs[-1] != "*" and "*" not in segs:
            return None
        return "/" + "/".join(segs) + shape[1], same

    by_first: dict[str, list[str]] = defaultdict(list)
    for u in urls:
        by_first[_pattern(temps[u], 1)].append(u)
    groups = []
    for first, members in by_first.items():
        if len(members) < min_size:
            continue
        pattern = drill(members, 1)
        covered = [u for u in members if _matches(temps[u], pattern)] or members
        pos = positional(members)
        if pos and pos[0].rstrip("*/").count("/") > pattern.rstrip("*/").count("/") and len(pos[1]) >= 0.8 * len(covered):
            pattern, covered = pos
        groups.append((pattern, covered))
    groups.sort(key=lambda g: -len(g[1]))
    for pattern, members in groups[:max_groups]:
        out.append({"pattern": pattern, "count": len(members), "pct": round(100 * len(members) / total),
                    "examples": members[:3]})
    if not out or (len(out) == 1 and out[0]["count"] == total and out[0]["pattern"] in ("/*", "/")):
        return []
    rest = total - sum(g["count"] for g in out)
    if rest > 0:
        out.append({"pattern": "otras", "count": rest, "pct": round(100 * rest / total), "examples": []})
    return out


def _matches(t: list[str], pattern: str) -> bool:
    for depth in range(0, len(t) + 1):
        if _pattern(t, depth) == pattern:
            return True
    return False
