"""Internal PageRank: how link authority flows through the site's own links. Pure function, no network.

Rules (also written to the export manifest):
- nodes = crawled URLs; edges = distinct internal <a> links (raw HTML or rendered), without nofollow, without
  self-links and without links found only by clicking (Google does not click);
- a redirect passes all its authority to its target (3xx, meta refresh or JavaScript redirect);
- pages with no outgoing links (errors, PDFs…) spread their authority evenly, as in the original algorithm;
- damping factor 0.85, iterated until it converges.
Score 0-100 on a logarithmic scale (100 = the strongest page), plus the share of total authority in %.
"""
from __future__ import annotations

import math
from collections import defaultdict

DAMPING = 0.85
RULES = ("Enlaces <a> internos únicos (HTML o renderizado), sin nofollow, sin autoenlaces y sin los que solo funcionan con "
         "clic; las redirecciones pasan toda su autoridad al destino; factor de amortiguación 0,85. «PageRank interno» "
         "es una escala logarítmica 0-100 (100 = la página más fuerte); «% de autoridad» es su parte del total.")


def compute(pages: list[dict], links: list[dict], iterations: int = 100, tol: float = 1e-9) -> dict[str, dict]:
    nodes = [p["url"] for p in pages]
    if not nodes:
        return {}
    index = {u: i for i, u in enumerate(nodes)}
    out: dict[int, set[int]] = defaultdict(set)
    for l in links:
        if l["kind"] not in ("a", "rendered") or not l.get("internal") or l.get("nofollow") or l.get("rel") == "js-click":
            continue
        s, t = index.get(l["source_url"]), index.get(l["target_url"])
        if s is not None and t is not None and s != t:
            out[s].add(t)
    for p in pages:  # redirects hand everything to their target
        extra = p.get("extra") or {}
        target = p.get("redirect_target") or (extra.get("meta_refresh") or {}).get("target") or extra.get("js_redirect")
        if target in index and target != p["url"]:
            out[index[p["url"]]] = {index[target]}
    n = len(nodes)
    rank = [1.0 / n] * n
    for _ in range(iterations):
        dangling = sum(rank[i] for i in range(n) if not out.get(i))
        new = [(1 - DAMPING) / n + DAMPING * dangling / n] * n
        for s, targets in out.items():
            share = DAMPING * rank[s] / len(targets)
            for t in targets:
                new[t] += share
        delta = sum(abs(a - b) for a, b in zip(new, rank))
        rank = new
        if delta < tol:
            break
    lo, hi = min(rank), max(rank)
    span = math.log(hi / lo) if hi > lo else 0
    return {u: {"pagerank": round(100 * math.log(rank[i] / lo) / span) if span else 100,
                "authority_pct": round(100 * rank[i], 2),
                "unique_outlinks_pr": len(out.get(i, ()))}
            for u, i in index.items()}
