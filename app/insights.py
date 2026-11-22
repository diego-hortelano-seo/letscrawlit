"""Crawl-level insights computed at the end of a crawl and stored in the `insights` table:
near duplicates, semantic clusters, internal-link suggestions, health score and prioritised actions."""
from __future__ import annotations

from collections import defaultdict

from . import db
from .analysis import SEVERITY_LABEL, SEVERITY_ORDER, compute_indexability, is_html
from .textsim import clusters, link_suggestions, near_duplicates

SEV_WEIGHT = {"critical": 1.0, "high": 0.6, "medium": 0.3, "low": 0.1, "info": 0.0}


def page_text(p: dict) -> str:
    return (p.get("rendered_text") or "") if (p.get("rendered_word_count") or 0) > (p.get("word_count") or 0) \
        else (p.get("text_excerpt") or "")


def compute_insights(crawl_id: int, cfg: dict, pages: list[dict]) -> None:
    html = [p for p in pages if p.get("status_code") == 200 and is_html(p) and not p.get("blocked_by_robots")]
    indexable = [p for p in html if compute_indexability(p)[0]]
    docs = {p["url"]: page_text(p) for p in indexable}
    threshold = int(cfg.get("near_dup_threshold") or 90) / 100
    db.save_insight(crawl_id, "near_dups", {u: list(v) for u, v in near_duplicates(docs, threshold).items()})
    db.save_insight(crawl_id, "clusters", clusters(docs))
    existing = {(l["source_url"], l["target_url"]) for l in db.load_links(crawl_id) if l["kind"] in ("a", "rendered")}
    titles = {p["url"]: p.get("title") or ((p.get("h1") or [""])[0]) for p in indexable}
    db.save_insight(crawl_id, "link_suggestions", link_suggestions(docs, titles, existing)[:500])
    db.save_insight(crawl_id, "spelling_vocab", sorted(spelling_vocab(html)))


def spelling_vocab(pages: list[dict]) -> set[str]:
    """Unknown words used on many pages are site vocabulary (brands, services, places), not typos."""
    df: dict[str, int] = defaultdict(int)
    with_sp = 0
    for p in pages:
        sp = (p.get("extra") or {}).get("spelling")
        if sp:
            with_sp += 1
            for w in set(sp.get("words") or []):
                df[w] += 1
    threshold = max(3, int(0.2 * with_sp))
    return {w for w, n in df.items() if n >= threshold}


def health(pages: list[dict], issues: list[dict], links: list[dict], start_url: str) -> dict:
    """Health score 0-100 (share of URLs free of problems, weighted by severity) and a prioritised action list.
    Priority = severity weight x (affected URLs, boosted by how important those URLs are)."""
    page_urls = {p["url"] for p in pages}
    n = max(1, len(page_urls))
    inlinks = defaultdict(int)
    for l in links:
        if l["kind"] in ("a", "rendered") and l.get("internal") and l["source_url"] != l["target_url"]:
            inlinks[l["target_url"]] += 1
    max_in = max(inlinks.values(), default=1) or 1
    page_penalty: dict[str, float] = defaultdict(float)
    by_code: dict[str, dict] = {}
    # A problem on (almost) every page is one fix in the template or the server (no phone in the footer, a
    # missing header…): it counts once for the whole site instead of making every page look broken.
    html = {p["url"] for p in pages if str(p.get("content_type") or "").startswith("text/html")} or page_urls
    cover: dict[str, set] = defaultdict(set)
    for i in issues:
        if i["url"] in html:
            cover[i["code"]].add(i["url"])
    site_wide = {c for c, us in cover.items() if len(html) >= 8 and len(us) >= 0.7 * len(html)}
    for i in issues:
        w = SEV_WEIGHT[i["severity"]]
        if i["url"] in page_urls and i["code"] not in site_wide:  # resources / external links don't count
            page_penalty[i["url"]] = max(page_penalty[i["url"]], w)
        g = by_code.setdefault(i["code"], {"code": i["code"], "title": i["title"], "severity": i["severity"],
                                           "severity_label": SEVERITY_LABEL[i["severity"]], "remediation": i["remediation"],
                                           "urls": [], "importance": 0.0, "home": False})
        g["urls"].append(i["url"])
        g["importance"] += 1 + 3 * (inlinks.get(i["url"], 0) / max_in)
        if i["url"] == start_url:
            g["home"] = True
    wide = [g for c, g in by_code.items() if c in site_wide and SEV_WEIGHT[g["severity"]] > 0]
    score = round(100 * (1 - sum(page_penalty.values()) / n) - sum(10 * SEV_WEIGHT[g["severity"]] for g in wide))
    actions = []
    for g in by_code.values():
        w = SEV_WEIGHT[g["severity"]]
        if w == 0:
            continue
        priority = w * (g["importance"] + (10 if g["home"] else 0))
        reasons = []
        if g["home"]:
            reasons.append("afecta a la página de inicio")
        if g["severity"] in ("critical", "high"):
            reasons.append("gravedad " + g["severity_label"].lower())
        top = sorted(g["urls"], key=lambda u: -inlinks.get(u, 0))[:3]
        if top and inlinks.get(top[0], 0) >= 3:
            reasons.append(f"incluye URLs muy enlazadas ({inlinks[top[0]]} inlinks)")
        reasons.append(f"{len(g['urls'])} URL(s) afectada(s)")
        actions.append({**g, "count": len(g["urls"]), "priority": round(priority, 2), "reasons": reasons,
                        "gain": round(100 * w * len(g["urls"]) / n, 1), "top_urls": top})
    # Plain, checkable order (no weighted score): severity, then the home page, then most URLs, then most linked.
    actions.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], not a["home"], -a["count"], -a["priority"]))
    grade = "Excelente" if score >= 90 else "Buena" if score >= 75 else "Mejorable" if score >= 50 else "Mala"
    return {"score": max(0, min(100, score)), "grade": grade, "actions": actions, "urls": n,
            "clean_urls": n - sum(1 for v in page_penalty.values() if v > 0),
            "site_wide": [{"code": g["code"], "title": g["title"], "severity": g["severity"], "count": len(set(g["urls"]))}
                          for g in sorted(wide, key=lambda g: SEVERITY_ORDER[g["severity"]])]}
