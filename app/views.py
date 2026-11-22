"""Workbench data: one enriched row per URL, plus tab/filter/column definitions,
the side-panel summaries (overview tree, site structure, response times).

Everything here is a pure transformation of what is stored in SQLite, cached per crawl state.
"""
from __future__ import annotations

import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Callable
from urllib.parse import urlsplit

import re

from . import db, dictionary
from .analysis import compute_indexability, is_html, page_noindex, url_problems
from .extract import NON_DESCRIPTIVE, is_nofollow, is_noindex, robots_tokens
from .urlutil import site_key

# --- pixel widths (Arial, units per 1000 em; approximation of what Google truncates) ---------
_W = {**{c: 556 for c in "abdeghnopqu0123456789"}, **{c: 500 for c in "ckszvxyJ"}, **{c: 222 for c in "ijl"},
      **{c: 278 for c in "ftI .,:;!/\\[]"}, "r": 333, "m": 833, "w": 722, "-": 333, "|": 260, "'": 191, '"': 355,
      **{c: 667 for c in "ABEKPSVXY&"}, **{c: 722 for c in "CDHNRU"}, **{c: 778 for c in "GOQ"},
      "M": 833, "W": 944, **{c: 611 for c in "FTZ"}, "L": 556, "(": 333, ")": 333, "?": 556, "%": 889, "@": 1015}


def pixel_width(text: str | None, font_px: int) -> int:
    total = 0
    for ch in text or "":
        base = unicodedata.normalize("NFD", ch)[0]
        total += _W.get(ch, _W.get(base, 556))
    return round(total * font_px / 1000)


def title_px(text: str | None) -> int:
    return pixel_width(text, 20)


def desc_px(text: str | None) -> int:
    return pixel_width(text, 14)


def status_text(code: int | None) -> str:
    if code is None:
        return ""
    try:
        return HTTPStatus(code).phrase
    except ValueError:
        return ""


TYPE_LABELS = {"html": "HTML", "js": "JavaScript", "css": "CSS", "image": "Imágenes", "media": "Multimedia",
               "font": "Fuentes", "xml": "XML", "pdf": "PDF", "other": "Otros", "unknown": "Desconocido"}
_EXT = {"js": "js", "mjs": "js", "css": "css", "jpg": "image", "jpeg": "image", "png": "image", "gif": "image",
        "webp": "image", "avif": "image", "svg": "image", "ico": "image", "mp4": "media", "webm": "media",
        "mp3": "media", "ogg": "media", "woff": "font", "woff2": "font", "ttf": "font", "otf": "font", "eot": "font",
        "xml": "xml", "pdf": "pdf"}


def type_group(ctype: str | None, url: str = "") -> str:
    c = (ctype or "").lower()
    if "html" in c:
        return "html"
    if "javascript" in c or "ecmascript" in c:
        return "js"
    if "css" in c:
        return "css"
    if c.startswith("image/"):
        return "image"
    if c.startswith(("video/", "audio/")):
        return "media"
    if "font" in c:
        return "font"
    if "xml" in c:
        return "xml"
    if "pdf" in c:
        return "pdf"
    if c:
        return "other"
    ext = urlsplit(url).path.rsplit(".", 1)[-1].lower() if "." in urlsplit(url).path else ""
    return _EXT.get(ext, "unknown")


RESOURCE_STATE = "Recurso (no HTML)"


AI_COLUMNS = ("intent", "topic", "meta_rewrite", "custom", "summary", "translate_en")


def _note_fields(n: dict | None) -> dict:
    n = n or {}
    return {"note": n.get("note", ""), "note_status": n.get("status", "") if n else "",
            "note_updated": (n.get("updated_at") or "")[:16].replace("T", " ")}


def _empty_row(url: str) -> dict:
    """Defaults so every predicate can read any key on resource rows too."""
    return {"url": url, "address": url, "html200": False, "title": "", "meta_description": "", "h1_1": "", "h2_1": "",
            "title_len": 0, "title_px": 0, "desc_len": 0, "desc_px": 0, "h1_count": 0, "h2_count": 0, "h1_1_len": 0,
            "h2_1_len": 0, "url_flags": set(), "inlinks": 0, "unique_inlinks": 0, "outlinks": 0, "external_outlinks": 0,
            "words": 0, "custom": {}, "extra": {}, "headers": {}, "hreflang": [], "mixed": [], "structured": {},
            "in_sitemap": False, "type": "unknown", "indexable_label": "No indexable", "indexability": "",
            "redirect_type": "", "has_metrics": False, "self_links": 0, "unique_outlinks": 0,
            "unique_external_outlinks": 0, "via": "", "redirect_url": "", "words_content": None, "words_total": None,
            "has_local": False, "ttfb_ms": None, "redirect_hops": None, "redirect_final": "", "note": "", "note_status": "", "note_updated": "", "pagerank": None, "authority_pct": None,
            **{k: False for k in ("th_url_long", "th_title_long", "th_title_short", "th_title_wide", "th_title_narrow",
                                  "th_desc_long", "th_desc_short", "th_desc_wide", "th_desc_narrow", "th_h1_long",
                                  "th_h2_long", "th_thin", "th_many_internal", "th_many_external", "th_deep", "th_big_html",
                                  "has_pdf_props")},
            "readability": None, "readability_label": "", "text_ratio": None, "forms_count": None, "forms_insecure": 0,
            "cookies_count": None, "cookies_names": "", "cookies_insecure": 0, "pdf_title": "", "pdf_author": "",
            "pdf_pages": None, "pdf_created": "", "pdf_modified": "", "co2_g": None, "page_weight_kb": None,
            "words_per_sentence": None, "url_len": len(url)}


# --- row building -----------------------------------------------------------------------------

def build_rows(pages: list[dict], links: list[dict], images: list[dict], externals: list[dict],
               sitemap_urls: set[str] | None = None, analytics: list[dict] | None = None,
               near_dups: dict | None = None, spelling_vocab: set[str] | None = None,
               local_rows: dict | None = None, notes: dict | None = None, options: dict | None = None,
               ai_results: list[dict] | None = None) -> dict:
    from .checks import effective_spelling
    from .options import thresholds
    th = thresholds(options)
    options = options or {}
    local_rows = local_rows or {}
    notes = notes or {}
    # external <a> links are only kept when «Almacenar enlaces externos» is on: otherwise the count is unknown, not 0
    ext_stored = bool(options.get("store_external", True) or options.get("check_external"))
    ai_by_url: dict[str, dict] = defaultdict(dict)
    for a in ai_results or []:
        ai_by_url[a["url"]]["ai_" + a["task"]] = (a["result"] or "").strip()
    vocab = set(spelling_vocab or ())
    sitemap_urls = sitemap_urls or set()
    near_dups = near_dups or {}
    metrics: dict[str, dict] = defaultdict(dict)
    for a in analytics or []:
        m = metrics[a["url"]]
        for k in ("clicks", "impressions", "ctr", "position", "sessions", "conversions"):
            if a.get(k) is not None:
                m[k] = a[k]
    sponsored_out, ugc_out = Counter(), Counter()
    by_url = {p["url"]: p for p in pages}
    # Counting rules (also written to the export manifest):
    #  - inlinks / outlinks = every internal <a> (raw + found only after rendering), duplicates included;
    #  - "únicos" = distinct source / target URLs;
    #  - links from a page to itself DO count as inlinks (the common crawler convention) and are shown as "Autoenlaces".
    inl, inl_src, outl, outl_ext = Counter(), defaultdict(set), defaultdict(set), defaultdict(set)
    outl_total, outl_ext_total, self_links = Counter(), Counter(), Counter()
    ext_src: dict[str, set] = defaultdict(set)
    res_src: dict[str, set] = defaultdict(set)
    res_count: Counter = Counter()
    res_via: dict[str, set] = defaultdict(set)
    res_by_page: dict[str, set] = defaultdict(set)
    nofollow_out, weak_out, empty_out, rendered_only = Counter(), Counter(), Counter(), Counter()
    js_click_out, js_click_in = Counter(), Counter()
    out_targets: dict[str, set] = defaultdict(set)
    for l in links:
        kind = l["kind"]
        if kind == "resource":
            res_src[l["target_url"]].add(l["source_url"])
            res_by_page[l["source_url"]].add(l["target_url"])
            res_count[l["target_url"]] += 1
            res_via[l["target_url"]].update(v.strip() for v in (l.get("anchor") or "").split(",") if v.strip())
            continue
        if kind not in ("a", "rendered"):
            if kind == "redirect" and not l.get("internal"):
                ext_src[l["target_url"]].add(l["source_url"])
            continue
        src = l["source_url"]
        rel = l.get("rel") or ""
        if rel == "js-click":  # found by clicking: not a link for Google, so it doesn't count as inlink/outlink
            js_click_out[src] += 1
            js_click_in[l["target_url"]] += 1
            continue
        if kind == "rendered":
            rendered_only[src] += 1
        if "sponsored" in rel:
            sponsored_out[src] += 1
        if "ugc" in rel:
            ugc_out[src] += 1
        if l.get("internal"):
            inl[l["target_url"]] += 1
            inl_src[l["target_url"]].add(src)
            if src == l["target_url"]:
                self_links[src] += 1
            outl_total[src] += 1
            outl[src].add(l["target_url"])
            out_targets[src].add(l["target_url"])
            if l.get("nofollow"):
                nofollow_out[src] += 1
            anchor = (l.get("anchor") or "").strip()
            if not anchor:
                empty_out[src] += 1
            elif anchor.lower().strip(".:»›→ ") in NON_DESCRIPTIVE:
                weak_out[src] += 1
        else:
            outl_ext_total[src] += 1
            outl_ext[src].add(l["target_url"])
            ext_src[l["target_url"]].add(src)
    page_depth = {p["url"]: p.get("depth") for p in pages}

    rows = []
    for p in pages:
        ok, reason = compute_indexability(p)
        h1 = [h for h in (p.get("h1") or []) if h]
        h2 = [h for h in (p.get("h2") or []) if h]
        sd = p.get("structured") or {}
        headers = p.get("headers") or {}
        extra = p.get("extra") or {}
        val = extra.get("validation") or {}
        canonical = p.get("canonical")
        ctarget = by_url.get(canonical) if canonical else None
        title = (p.get("title") or "").strip()
        desc = (p.get("meta_description") or "").strip()
        levels = extra.get("headings") or []
        viewport = (extra.get("viewport") or "").lower()
        next_t, prev_t = extra.get("next"), extra.get("prev")
        row = {
            **_empty_row(p["url"]), **p,
            "type": type_group(p.get("content_type"), p["url"]),
            "html200": p.get("status_code") == 200 and is_html(p) and not p.get("blocked_by_robots"),
            "status_text": "Bloqueada por robots.txt" if p.get("blocked_by_robots") else (
                status_text(p.get("status_code")) or (p.get("error") or "")[:80]),
            "indexable_label": "Indexable" if ok else "No indexable",
            "indexability": reason,
            "title": title, "title_len": len(title), "title_px": title_px(title),
            "title_count": val.get("title_count", 1 if title else 0),
            "title_outside_head": bool(val.get("title_outside_head")),
            "meta_description": desc, "desc_len": len(desc), "desc_px": desc_px(desc),
            "desc_count": extra.get("desc_count", 1 if desc else 0),
            "meta_keywords": extra.get("meta_keywords") or "", "kw_len": len(extra.get("meta_keywords") or ""),
            "h1_1": h1[0] if h1 else "", "h1_1_len": len(h1[0]) if h1 else 0,
            "h1_2": h1[1] if len(h1) > 1 else "", "h1_count": len(h1), "h1_alt": bool(extra.get("h1_alt")),
            "h2_1": h2[0] if h2 else "", "h2_1_len": len(h2[0]) if h2 else 0,
            "h2_2": h2[1] if len(h2) > 1 else "", "h2_count": len(h2),
            "heading_seq": "-".join(f"H{n}" for n in levels[:12]),
            "non_sequential": bool(levels) and (levels[0] != 1 or any(b > a + 1 for a, b in zip(levels, levels[1:]))),
            "h2_non_sequential": any(b > a + 1 for a, b in zip(levels, levels[1:]) if a >= 2),
            "url_len": len(p["url"]), "url_flags": url_problems(p["url"]),
            "inlinks": inl[p["url"]], "unique_inlinks": len(inl_src[p["url"]]), "self_links": self_links[p["url"]],
            "outlinks": outl_total[p["url"]], "unique_outlinks": len(outl[p["url"]]),
            "external_outlinks": outl_ext_total[p["url"]] if ext_stored else None,
            "unique_external_outlinks": len(outl_ext[p["url"]]) if ext_stored else None,
            "words_content": p.get("word_count"), "words_total": extra.get("words_total"),
            "content_area": extra.get("content_area") or "",
            "last_modified": headers.get("last-modified", ""),
            "response_ms_first": extra.get("response_ms_first"), "ttfb_ms": extra.get("ttfb_ms"),
            "via": ", ".join(sorted(res_via.get(p["url"], ()))),
            "nofollow_outlinks": nofollow_out[p["url"]], "weak_anchors": weak_out[p["url"]],
            "empty_anchors": empty_out[p["url"]], "rendered_only_links": rendered_only[p["url"]],
            "js_click_out": js_click_out[p["url"]], "js_click_in": js_click_in[p["url"]],
            "blocked_requests": sum((extra.get("blocked_requests") or {}).values()),
            "blocked_hosts": ", ".join(sorted(extra.get("blocked_requests") or {})),
            "links_to_non200": sum(1 for t in out_targets[p["url"]]
                                   if t in by_url and (by_url[t].get("status_code") or 0) != 200),
            "canonical_status": (ctarget.get("status_code") if ctarget else None),
            "jsonld_types": ", ".join(sd.get("jsonld_types") or []),
            "microdata": ", ".join(t.rsplit("/", 1)[-1] for t in sd.get("microdata") or []),
            "jsonld_count": sd.get("jsonld_count") or 0,
            "hreflang_count": len(p.get("hreflang") or []),
            "hreflang_langs": ", ".join(sorted({a["lang"] for a in p.get("hreflang") or []})),
            "scheme": urlsplit(p["url"]).scheme,
            "mixed_count": len(p.get("mixed") or []),
            "hsts": "sí" if "strict-transport-security" in headers else ("no" if headers else ""),
            "xcto": headers.get("x-content-type-options", ""),
            "xfo": headers.get("x-frame-options", ""),
            "csp": "sí" if "content-security-policy" in headers else ("no" if headers else ""),
            "hash_short": (p.get("content_hash") or "")[:10],
            "words": max(p.get("word_count") or 0, p.get("rendered_word_count") or 0),
            "in_sitemap": p["url"] in sitemap_urls,
            "viewport": extra.get("viewport") or "",
            "next": next_t or "", "prev": prev_t or "",
            "next_status": (by_url.get(next_t) or {}).get("status_code") if next_t else None,
            "prev_status": (by_url.get(prev_t) or {}).get("status_code") if prev_t else None,
            "doctype": "sí" if val.get("doctype") else ("no" if val else ""),
            "head_count": val.get("head_count"), "body_count": val.get("body_count"),
            "invalid_in_head": ", ".join(val.get("invalid_in_head") or []),
            "has_extra": bool(extra),
            "viewport_missing": bool(extra) and not viewport,
            "viewport_fixed": bool(viewport) and "width=device-width" not in viewport.replace(" ", ""),
            "zoom_blocked": "user-scalable=no" in viewport.replace(" ", "") or "maximum-scale=1" in viewport.replace(" ", "").replace(".0", ""),
        }
        a11y = extra.get("a11y") or {}
        amp = extra.get("amp") or {}
        spell = effective_spelling(extra.get("spelling"), vocab, recheck=True) or {}
        probs = extra.get("schema_problems") or []
        nd = near_dups.get(p["url"])
        refresh = extra.get("meta_refresh") or {}
        from .checks import hreflang_code_error
        row.update({
            "sponsored_outlinks": sponsored_out[p["url"]], "ugc_outlinks": ugc_out[p["url"]],
            "meta_refresh": refresh.get("target") or "", "js_redirect": extra.get("js_redirect") or "",
            "redirect_type": (f"HTTP {p['status_code']}" if 300 <= (p.get("status_code") or 0) < 400 else
                              "Meta refresh" if refresh.get("target") else "JavaScript" if extra.get("js_redirect") else ""),
            "redirect_url": p.get("redirect_target") or refresh.get("target") or extra.get("js_redirect") or "",
            "a11y_unlabeled": a11y.get("unlabeled_inputs", 0), "a11y_buttons": a11y.get("unnamed_buttons", 0),
            "a11y_links": a11y.get("unnamed_links", 0), "a11y_headings": a11y.get("empty_headings", 0),
            "a11y_dup_ids": len(a11y.get("duplicate_ids") or []), "a11y_iframes": a11y.get("iframes_no_title", 0),
            "a11y_tabindex": a11y.get("positive_tabindex", 0), "a11y_autoplay": a11y.get("autoplay_media", 0),
            "a11y_img_alt": a11y.get("images_no_alt", 0),
            "a11y_total": sum(v if isinstance(v, int) else len(v or []) for v in a11y.values()) if a11y else 0,
            "is_amp": "sí" if amp.get("is_amp") else "no", "amphtml": amp.get("amphtml") or "",
            "amp_errors": "; ".join(amp.get("errors") or []), "amp_error_count": len(amp.get("errors") or []),
            "spelling_count": spell.get("count", 0) if spell else None,
            "spelling_words": ", ".join(spell.get("words") or []) if spell else "",
            "schema_errors": sum(1 for x in probs if x["level"] == "error"),
            "schema_warnings": sum(1 for x in probs if x["level"] == "warning"),
            "schema_messages": "; ".join(x["message"] for x in probs[:5]),
            "rdfa": ", ".join(sd.get("rdfa") or []),
            "hreflang_bad_codes": ", ".join(a["lang"] for a in p.get("hreflang") or [] if hreflang_code_error(a["lang"])),
            "near_dup_url": nd[0] if nd else "", "near_dup_pct": round(nd[1] * 100) if nd else None,
            "near_dup_count": nd[2] if nd else 0,
            "screenshot": extra.get("screenshot") or "",
            **{f"m_{k}": v for k, v in metrics.get(p["url"], {}).items()},
            "has_metrics": p["url"] in metrics,
            "has_local": p["url"] in local_rows,
            **local_rows.get(p["url"], {}),
            **_note_fields(notes.get(p["url"])),
            **{f"ai_{k}": "" for k in AI_COLUMNS}, **ai_by_url.get(p["url"], {}),
        })
        custom = p.get("custom") or {}
        for name, count in (custom.get("search") or {}).items():
            row[f"search::{name}"] = count
        for name, values in (custom.get("extract") or {}).items():
            row[f"extract::{name}"] = " | ".join(values)
            row[f"extract_n::{name}"] = len(values)
        rows.append(row)

    html = [r for r in rows if r["html200"]]
    for key, flag in (("title", "dup_title"), ("meta_description", "dup_desc"), ("h1_1", "dup_h1"), ("h2_1", "dup_h2"),
                      ("meta_keywords", "dup_kw")):
        counts = Counter(r[key].lower() for r in html if r[key])
        for r in rows:
            r[flag] = bool(r[key]) and r["html200"] and counts[r[key].lower()] > 1
    hashes = Counter(r["content_hash"] for r in html if r.get("content_hash") and (r.get("word_count") or 0) >= 20)
    for r in rows:
        r["dup_content"] = r["html200"] and bool(r.get("content_hash")) and hashes[r["content_hash"]] > 1 \
            and (r.get("word_count") or 0) >= 20
        alts = r.get("hreflang") or []
        r["hreflang_bad_target"] = any((by_url.get(a["href"]) or {}).get("status_code") not in (200, None)
                                       for a in alts if a["href"] in by_url)
        r["hreflang_no_return"] = any(
            a["href"] in by_url and a["href"] != r["url"]
            and r["url"] not in {b["href"] for b in by_url[a["href"]].get("hreflang") or []} for a in alts)
        r["hreflang_no_self"] = bool(alts) and r["url"] not in {a["href"] for a in alts}
        r["hreflang_no_xdefault"] = bool(alts) and "x-default" not in {a["lang"] for a in alts}

    from .pagerank import compute as pagerank
    pr = pagerank(pages, links)
    for r in rows:
        r.update(pr.get(r["url"]) or {"pagerank": None, "authority_pct": None})

    # thresholds from this crawl's configuration (editable in Configuración › Umbrales)
    res_size = {e["url"]: e.get("size_bytes") or 0 for e in externals if e.get("kind") == "resource"}
    for r in rows:
        h = r["html200"]
        extra = r.get("extra") or {}
        rd, forms, cookies, pdf = extra.get("readability") or {}, extra.get("forms") or {}, extra.get("cookies"), extra.get("pdf") or {}
        r.update({
            "th_url_long": r["url_len"] > th["url_max"],
            "th_title_long": h and r["title_len"] > th["title_max"],
            "th_title_short": h and 0 < r["title_len"] < th["title_min"],
            "th_title_wide": h and r["title_px"] > th["title_px_max"],
            "th_title_narrow": h and 0 < r["title_px"] < th["title_px_min"],
            "th_desc_long": h and r["desc_len"] > th["desc_max"],
            "th_desc_short": h and 0 < r["desc_len"] < th["desc_min"],
            "th_desc_wide": h and r["desc_px"] > th["desc_px_max"],
            "th_desc_narrow": h and 0 < r["desc_px"] < th["desc_px_min"],
            "th_h1_long": h and r["h1_1_len"] > th["h1_max"],
            "th_h2_long": h and any(len(x) > th["h2_max"] for x in (r.get("h2") or [])),
            "th_thin": h and r["words"] < th["low_words"],
            "th_many_internal": r["unique_outlinks"] > th["high_internal_outlinks"],
            "th_many_external": (r["unique_external_outlinks"] or 0) > th["high_external_outlinks"],
            "th_deep": (r.get("depth") or 0) > th["high_depth"],
            "th_big_html": h and (r.get("size_bytes") or 0) > th["doc_max_mb"] * 1048576,
            "readability": rd.get("score"), "readability_label": rd.get("label", ""),
            "words_per_sentence": rd.get("words_per_sentence"),
            "text_ratio": extra.get("text_ratio"),
            "forms_count": forms.get("count") if forms else None,
            "forms_insecure": len(forms.get("insecure") or []) + (1 if forms.get("password_on_http") else 0) if forms else 0,
            "cookies_count": len(cookies) if cookies is not None else None,
            "cookies_names": ", ".join(c["name"] for c in cookies or [])[:300],
            "cookies_insecure": sum(1 for c in cookies or [] if not c["secure"] or not c["httponly"]),
            "pdf_title": pdf.get("title") or "", "pdf_author": pdf.get("author") or "", "pdf_pages": pdf.get("pages"),
            "pdf_created": pdf.get("created") or "", "pdf_modified": pdf.get("modified") or "", "has_pdf_props": bool(pdf),
        })
        if options.get("carbon") and r.get("size_bytes"):
            total = (r.get("size_bytes") or 0) + sum(res_size.get(t, 0) for t in res_by_page.get(r["url"], ()))
            r["page_weight_kb"] = round(total / 1024)
            r["co2_g"] = round(total / 1e9 * 0.81 * 494, 3)  # Sustainable Web Design: 0.81 kWh/GB, 494 g CO2e/kWh

    # «respetar» options: those URLs are left out of the tables (they are still in the database and the issues)
    hide = set()
    if options.get("respect_noindex"):
        hide |= {r["url"] for r in rows if r["html200"] and page_noindex(r)}
    if options.get("respect_canonicals"):
        hide |= {r["url"] for r in rows if r.get("canonical") and r["canonical"] != r["url"]}
    if options.get("show_blocked_internal") is False:
        hide |= {r["url"] for r in rows if r.get("blocked_by_robots")}
    if options.get("respect_next_prev"):
        hide |= {r["url"] for r in rows if r["html200"] and r.get("prev")}
    if hide:
        rows = [r for r in rows if r["url"] not in hide]

    # internal resources checked with HEAD become internal rows too (CSS, JS, images, fonts…)
    page_urls = {r["url"] for r in rows}
    checked = {e["url"]: e for e in externals if e.get("kind", "external") == "external"}
    resources = {e["url"]: e for e in externals if e.get("kind") == "resource"}
    def resource_depth(url: str):
        """One click deeper than the shallowest page that references it (sitemaps don't give depth)."""
        depths = [page_depth[s] for s in res_src.get(url, ()) if page_depth.get(s) is not None]
        return min(depths) + 1 if depths else None

    for r in rows:  # non-HTML URLs reached as pages (e.g. a PDF or image linked with <a>) share the resource status
        if r.get("status_code") == 200 and not is_html(r) and not r.get("blocked_by_robots"):
            r["indexability"] = RESOURCE_STATE
            if r.get("depth") is None:
                r["depth"] = resource_depth(r["url"])
            r["inlinks"] += res_count[r["url"]]
            r["unique_inlinks"] = len(inl_src[r["url"]] | res_src.get(r["url"], set()))
    for url, e in resources.items():
        if url in page_urls:
            continue
        code = e.get("status_code")
        state = RESOURCE_STATE if code == 200 else ("Bloqueada por robots.txt" if e.get("blocked_by_robots")
                                                   else f"HTTP {code}" if code else "Sin respuesta")
        row = {**_empty_row(url), "status_code": code, "content_type": e.get("content_type"),
               # error pages are HTML; classify broken resources by their extension instead
               "type": type_group(e.get("content_type") if code == 200 else None, url), "size_bytes": e.get("size_bytes"),
               "response_ms": e.get("response_ms"), "blocked_by_robots": e.get("blocked_by_robots", 0),
               "error": e.get("error"), "redirect_target": e.get("redirect_target"), "source": "recurso",
               "status_text": "Bloqueada por robots.txt" if e.get("blocked_by_robots") else (status_text(code) or (e.get("error") or "")[:80]),
               "indexable_label": "No indexable", "indexability": state, "url_flags": url_problems(url),
               "depth": resource_depth(url), "via": ", ".join(sorted(res_via.get(url, ()))),
               "inlinks": res_count[url], "unique_inlinks": len(res_src.get(url, ()))}
        rows.append(row)

    image_rows: dict[str, dict] = {}
    for img in images:
        src = img.get("src") or "(sin src)"
        row = image_rows.setdefault(src, {"address": src, "url": src, "alt": img.get("alt"), "pages": set(), "missing_alt": 0})
        row["pages"].add(img["page_url"])
        if img.get("alt") is None:
            row["missing_alt"] += 1
        elif row["alt"] is None:
            row["alt"] = img.get("alt")
    for row in image_rows.values():
        res = resources.get(row["address"], {})
        row["page_count"] = len(row["pages"])
        row["first_page"] = sorted(row["pages"])[0]
        row["alt_len"] = len(row["alt"] or "")
        row["alt_label"] = "(falta)" if row["alt"] is None else (row["alt"] or "(vacío · decorativa)")
        row["status_code"] = res.get("status_code")
        row["size_kb"] = round(res["size_bytes"] / 1024, 1) if res.get("size_bytes") else None
        row["th_heavy"] = (row["size_kb"] or 0) > th["image_max_kb"]
        row["th_long_alt"] = row["alt_len"] > th["alt_max"]
        row["checked"] = bool(res)

    ext_rows = []
    for url in sorted(set(ext_src) | set(checked)):
        e = checked.get(url, {})
        code = e.get("status_code")
        ext_rows.append({
            "address": url, "url": url, "status_code": code, "checked": url in checked,
            "status_text": ("Bloqueada por robots.txt" if e.get("blocked_by_robots") else
                            status_text(code) or (e.get("error") or "")[:80]) if url in checked else "Sin comprobar",
            "blocked_by_robots": e.get("blocked_by_robots", 0), "error": e.get("error"),
            "content_type": e.get("content_type"), "type": type_group(e.get("content_type"), url),
            "redirect_target": e.get("redirect_target"),
            "response_ms": e.get("response_ms"), "inlinks": len(ext_src.get(url, ())), "host": urlsplit(url).hostname,
        })
    if options.get("show_blocked_external") is False:
        ext_rows = [r for r in ext_rows if not r["blocked_by_robots"]]
    for row in image_rows.values():
        ctype = (resources.get(row["address"]) or {}).get("content_type") or ""
        ext_ = urlsplit(row["address"]).path.rsplit(".", 1)[-1].lower() if "." in urlsplit(row["address"]).path else ""
        row["format"] = (ctype.split("/")[-1] if ctype.startswith("image/") else ext_).replace("jpeg", "jpg")[:8]

    # Redirect chains: number of hops and final destination, following the stored redirects (3xx, meta refresh, JS)
    rows_by_url = {r["url"]: r for r in rows}
    for r in rows:
        hops, final, cur, seen = 0, "", r, {r["url"]}
        while cur.get("redirect_url") and hops < 20:
            final, hops = cur["redirect_url"], hops + 1
            if final in seen:
                final = "bucle: " + final
                break
            seen.add(final)
            cur = rows_by_url.get(final) or {}
        r["redirect_hops"], r["redirect_final"] = (hops or None), final

    by_row = rows_by_url
    analytics_rows = []
    for url, m in metrics.items():
        r = by_row.get(url)
        analytics_rows.append({"address": url, "url": url, "crawled": r is not None, "status_code": (r or {}).get("status_code"),
                               "inlinks": (r or {}).get("inlinks", 0), "indexable_label": (r or {}).get("indexable_label", "No rastreada"),
                               "title": (r or {}).get("title", ""), **{f"m_{k}": v for k, v in m.items()}})
    return {"pages": rows, "images": list(image_rows.values()), "externals": ext_rows,
            "analytics": analytics_rows, "sitemap_count": len(sitemap_urls)}


# --- tabs ---------------------------------------------------------------------------------

Pred = Callable[[dict], bool]


@dataclass
class Tab:
    key: str
    label: str
    source: str  # pages | images | externals | issues
    columns: list[tuple[str, str]]
    filters: list[tuple[str, str, Pred]] = field(default_factory=list)
    base: Pred = lambda r: True


def _code(lo: int, hi: int) -> Pred:
    return lambda r: not r.get("blocked_by_robots") and r.get("status_code") is not None and lo <= r["status_code"] <= hi


def _type_filters() -> list[tuple[str, str, Pred]]:
    return [(k, label, (lambda k: lambda r: r.get("type") == k)(k)) for k, label in TYPE_LABELS.items()]


_html: Pred = lambda r: r["html200"]
ADDR = ("address", "URL")
IDX = ("indexable_label", "Indexabilidad")
IDXR = ("indexability", "Estado de indexabilidad")
_vp = lambda r: r["viewport"].lower().replace(" ", "")

STATIC_TABS: list[Tab] = [
    Tab("internal", "Internos", "pages",
        [ADDR, ("content_type", "Tipo de contenido"), ("status_code", "Código de respuesta"), ("status_text", "Estado"), IDX, IDXR,
         ("title", "Título 1"), ("title_len", "Long. título 1"), ("title_px", "Píxeles título 1"),
         ("meta_description", "Meta description 1"), ("desc_len", "Long. meta description 1"), ("desc_px", "Píxeles meta description 1"),
         ("h1_1", "H1-1"), ("h1_1_len", "Long. H1-1"), ("h1_2", "H1-2"), ("h2_1", "H2-1"), ("h2_1_len", "Long. H2-1"), ("h2_2", "H2-2"),
         ("canonical", "Elemento de enlace canónico 1"), ("meta_robots", "Meta robots 1"), ("x_robots", "X-Robots-Tag 1"),
         ("lang", "Idioma (lang)"), ("size_bytes", "Tamaño (bytes)"),
         ("words_content", "Palabras (contenido principal)"), ("words_total", "Palabras (página completa)"),
         ("content_hash", "Hash del contenido"), ("response_ms", "Tiempo de respuesta (ms)"), ("ttfb_ms", "TTFB (ms)"), ("last_modified", "Last-Modified"),
         ("redirect_url", "URL de redirección"), ("redirect_type", "Tipo de redirección"), ("depth", "Profundidad"),
         ("pagerank", "PageRank interno (0-100)"), ("authority_pct", "% de autoridad"),
         ("inlinks", "Inlinks (incl. autoenlaces)"), ("unique_inlinks", "Inlinks únicos"), ("self_links", "Autoenlaces"),
         ("outlinks", "Outlinks"), ("unique_outlinks", "Outlinks únicos"),
         ("external_outlinks", "Outlinks externos"), ("unique_external_outlinks", "Outlinks externos únicos"),
         ("source", "Descubierta por"), ("via", "Referenciada en (img, srcset, og:image…)"),
         ("page_weight_kb", "Peso con recursos (KB)"), ("co2_g", "CO₂ por visita (g)"),
         ("note_status", "Estado de la nota"), ("note", "Anotación Consultor")],
        _type_filters()),
    Tab("external", "Externo", "externals",
        [ADDR, ("host", "Host"), ("status_code", "Código de respuesta"), ("status_text", "Estado"), ("content_type", "Tipo de contenido"),
         ("redirect_target", "Redirige a"), ("response_ms", "Tiempo de respuesta (ms)"), ("ttfb_ms", "TTFB (ms)"), ("inlinks", "Inlinks")],
        _type_filters() + [
         ("unchecked", "Sin comprobar", lambda r: not r["checked"]),
         ("blocked", "Bloqueadas por robots.txt", lambda r: bool(r.get("blocked_by_robots"))),
         ("noresponse", "Sin respuesta", lambda r: r["checked"] and not r.get("blocked_by_robots") and r.get("status_code") is None),
         ("2xx", "Correcto (2xx)", _code(200, 299)), ("3xx", "Redirección (3xx)", _code(300, 399)),
         ("4xx", "Error de cliente (4xx)", _code(400, 499)), ("5xx", "Error de servidor (5xx)", _code(500, 599))]),
    Tab("security", "Seguridad", "pages",
        [ADDR, ("scheme", "Esquema"), ("mixed_count", "Recursos HTTP"), ("hsts", "HSTS"), ("xcto", "X-Content-Type-Options"),
         ("xfo", "X-Frame-Options"), ("csp", "CSP"), ("forms_count", "Formularios"), ("forms_insecure", "Formularios sin HTTPS"),
         ("cookies_count", "Cookies"), ("cookies_insecure", "Cookies sin Secure/HttpOnly"), ("cookies_names", "Nombres de cookies"), IDX],
        [("http", "URLs HTTP", lambda r: r["scheme"] == "http"),
         ("https", "URLs HTTPS", lambda r: r["scheme"] == "https"),
         ("mixed", "Contenido mixto", lambda r: r["mixed_count"] > 0),
         ("no_hsts", "Falta el header HSTS", lambda r: r["scheme"] == "https" and r["hsts"] == "no"),
         ("no_csp", "Falta el header Content-Security-Policy", lambda r: r["csp"] == "no"),
         ("no_xcto", "Falta el header X-Content-Type-Options", lambda r: bool(r.get("headers")) and not r["xcto"]),
         ("no_xfo", "Falta el header X-Frame-Options", lambda r: bool(r.get("headers")) and not r["xfo"]),
         ("insecure_forms", "Formularios que envían sin HTTPS", lambda r: r.get("forms_insecure", 0) > 0),
         ("cookies", "Ponen cookies", lambda r: (r.get("cookies_count") or 0) > 0),
         ("insecure_cookies", "Cookies sin Secure o HttpOnly", lambda r: r.get("cookies_insecure", 0) > 0)],
        base=_html),
    Tab("codes", "Códigos de respuesta", "pages",
        [ADDR, ("status_code", "Código de respuesta"), ("status_text", "Estado"), IDX, IDXR, ("redirect_type", "Tipo de redirección"),
         ("redirect_target", "Redirige a"), ("meta_refresh", "Meta refresh a"), ("js_redirect", "Redirección JS a"),
         ("redirect_hops", "Saltos de redirección"), ("redirect_final", "Destino final"),
         ("inlinks", "Inlinks"), ("response_ms", "Tiempo de respuesta (ms)"), ("ttfb_ms", "TTFB (ms)"), ("source", "Descubierta por")],
        [("blocked", "Bloqueado por robots.txt", lambda r: bool(r.get("blocked_by_robots"))),
         ("noresponse", "Sin respuesta", lambda r: not r.get("blocked_by_robots") and r.get("status_code") is None),
         ("ssl", "Error de certificado SSL", lambda r: "CERTIFICATE" in (r.get("error") or "").upper()),
         ("2xx", "Correcto (2xx)", _code(200, 299)), ("3xx", "Redirección (3xx)", _code(300, 399)),
         ("301", "Redirección 301", _code(301, 301)), ("302", "Redirección 302", _code(302, 302)),
         ("307", "Redirección 307", _code(307, 307)), ("308", "Redirección 308", _code(308, 308)),
         ("meta_refresh", "Redirección (meta refresh)", lambda r: bool(r.get("meta_refresh"))),
         ("js_redirect", "Redirección (JavaScript)", lambda r: bool(r.get("js_redirect"))),
         ("4xx", "Error de cliente (4xx)", _code(400, 499)), ("5xx", "Error de servidor (5xx)", _code(500, 599))]),
    Tab("url", "URL", "pages",
        [ADDR, ("url_len", "Longitud"), ("status_code", "Código de respuesta"), IDX],
        [("non_ascii", "Caracteres no ASCII", lambda r: "url_non_ascii" in r["url_flags"]),
         ("underscore", "Guiones bajos", lambda r: "url_underscore" in r["url_flags"]),
         ("uppercase", "Mayúsculas", lambda r: "url_uppercase" in r["url_flags"]),
         ("params", "Parámetros", lambda r: "url_parameters" in r["url_flags"]),
         ("slashes", "Barras múltiples", lambda r: "url_multiple_slashes" in r["url_flags"]),
         ("long", "Más larga que el máximo", lambda r: r["th_url_long"])]),
    Tab("titles", "Títulos de página", "pages",
        [ADDR, ("title", "Título 1"), ("title_len", "Longitud título 1"), ("title_px", "Píxeles título 1"), ("h1_1", "H1-1"),
         ("title_count", "Nº de títulos"), IDX],
        [("missing", "Falta", lambda r: not r["title"]),
         ("duplicate", "Duplicado", lambda r: r["dup_title"]),
         ("over_chars", "Por encima del máximo de caracteres", lambda r: r["th_title_long"]),
         ("under_chars", "Por debajo del mínimo de caracteres", lambda r: r["th_title_short"]),
         ("over_px", "Por encima del máximo de píxeles", lambda r: r["th_title_wide"]),
         ("under_px", "Por debajo del mínimo de píxeles", lambda r: r["th_title_narrow"]),
         ("same_h1", "Igual que H1", lambda r: bool(r["title"]) and r["title"].lower() == r["h1_1"].lower()),
         ("multiple", "Múltiple", lambda r: r["title_count"] > 1),
         ("outside_head", "Fuera de <head>", lambda r: r["title_outside_head"])],
        base=_html),
    Tab("descriptions", "Meta description", "pages",
        [ADDR, ("meta_description", "Meta description 1"), ("desc_len", "Longitud"), ("desc_px", "Píxeles"), ("desc_count", "Nº"), IDX],
        [("missing", "Falta", lambda r: not r["meta_description"]),
         ("duplicate", "Duplicado", lambda r: r["dup_desc"]),
         ("over_chars", "Por encima del máximo de caracteres", lambda r: r["th_desc_long"]),
         ("under_chars", "Por debajo del mínimo de caracteres", lambda r: r["th_desc_short"]),
         ("over_px", "Por encima del máximo de píxeles", lambda r: r["th_desc_wide"]),
         ("under_px", "Por debajo del mínimo de píxeles", lambda r: r["th_desc_narrow"]),
         ("multiple", "Múltiple", lambda r: r["desc_count"] > 1)],
        base=_html),
    Tab("keywords", "Meta keywords", "pages",
        [ADDR, ("meta_keywords", "Meta keywords 1"), ("kw_len", "Longitud"), IDX],
        [("missing", "Falta", lambda r: not r["meta_keywords"]),
         ("present", "Con meta keywords", lambda r: bool(r["meta_keywords"])),
         ("duplicate", "Duplicado", lambda r: r["dup_kw"])],
        base=_html),
    Tab("h1", "H1", "pages",
        [ADDR, ("h1_1", "H1-1"), ("h1_1_len", "Longitud H1-1"), ("h1_2", "H1-2"), ("h1_count", "Nº de H1"), ("heading_seq", "Orden de encabezados"), IDX],
        [("missing", "Falta", lambda r: r["h1_count"] == 0 and not r["h1_alt"]),
         ("duplicate", "Duplicado", lambda r: r["dup_h1"]),
         ("over_chars", "Por encima del máximo de caracteres", lambda r: r["th_h1_long"]),
         ("multiple", "Múltiple", lambda r: r["h1_count"] > 1),
         ("alt_text", "Texto ALT en H1", lambda r: r["h1_alt"]),
         ("non_sequential", "No secuencial", lambda r: r["non_sequential"])],
        base=_html),
    Tab("h2", "H2", "pages",
        [ADDR, ("h2_1", "H2-1"), ("h2_1_len", "Longitud H2-1"), ("h2_2", "H2-2"), ("h2_count", "Nº de H2"), ("heading_seq", "Orden de encabezados"), IDX],
        [("missing", "Falta", lambda r: r["h2_count"] == 0),
         ("duplicate", "Duplicado", lambda r: r["dup_h2"]),
         ("over_chars", "Por encima del máximo de caracteres", lambda r: r["th_h2_long"]),
         ("multiple", "Múltiple", lambda r: r["h2_count"] > 1),
         ("non_sequential", "No secuencial", lambda r: r["h2_non_sequential"])],
        base=_html),
    Tab("content", "Contenido", "pages",
        [ADDR, ("word_count", "Palabras contenido (HTML)"), ("rendered_word_count", "Palabras contenido (renderizado)"),
         ("words_total", "Palabras página completa"), ("content_area", "Zona contada"), ("near_dup_pct", "Similitud más alta (%)"),
         ("near_dup_url", "URL más parecida"), ("near_dup_count", "Nº casi duplicados"), ("spelling_count", "Errores ortográficos"),
         ("spelling_words", "Palabras desconocidas"), ("hash_short", "Huella"), ("lang", "Idioma"),
         ("readability", "Legibilidad (0-100)"), ("readability_label", "Lectura"), ("words_per_sentence", "Palabras por frase"),
         ("text_ratio", "Texto / código (%)"), IDX],
        [("duplicate", "Duplicados exactos", lambda r: r["dup_content"]),
         ("near_duplicate", "Casi duplicados", lambda r: bool(r.get("near_dup_url"))),
         ("spelling", "Errores ortográficos", lambda r: (r.get("spelling_count") or 0) > 0),
         ("thin", "Contenido escaso (por debajo del mínimo de palabras)", lambda r: r["th_thin"]),
         ("js", "Depende de JavaScript", lambda r: bool(r.get("rendered")) and (r.get("rendered_word_count") or 0) >= 2 * (r.get("word_count") or 0) + 50),
         ("hard", "Lectura difícil (legibilidad < 50)", lambda r: r.get("readability") is not None and r["readability"] < 50),
         ("low_ratio", "Poco texto frente al código (< 10 %)", lambda r: r.get("text_ratio") is not None and r["text_ratio"] < 10),
         ("lorem", "Contiene «lorem ipsum»", lambda r: "lorem ipsum" in ((r.get("text_excerpt") or "") + (r.get("rendered_text") or "")).lower())],
        base=_html),
    Tab("images", "Imágenes", "images",
        [ADDR, ("alt_label", "Texto alt"), ("alt_len", "Longitud alt"), ("format", "Formato"), ("size_kb", "Tamaño (KB)"),
         ("status_code", "Código de respuesta"), ("page_count", "Páginas que la usan"), ("first_page", "Primera página")],
        [("over_kb", "Por encima del peso máximo", lambda r: r.get("th_heavy", False)),
         ("legacy", "Formato antiguo (JPG/PNG/GIF)", lambda r: r.get("format") in ("jpg", "png", "gif")),
         ("nextgen", "Formato moderno (WebP/AVIF)", lambda r: r.get("format") in ("webp", "avif")),
         ("missing_alt", "Falta texto alternativo", lambda r: r["missing_alt"] > 0),
         ("empty_alt", "Atributo alt vacío (decorativa)", lambda r: r["alt"] == ""),
         ("long_alt", "Texto alt por encima del máximo", lambda r: r.get("th_long_alt", False)),
         ("broken", "Imagen rota (4xx/5xx)", lambda r: (r.get("status_code") or 0) >= 400)]),
    Tab("canonicals", "Canonicals", "pages",
        [ADDR, ("canonical", "Elemento de enlace canónico 1"), ("rendered_canonical", "Canonical renderizado"),
         ("canonical_status", "Código del canonical"), IDX, IDXR],
        [("present", "Contiene canonical", lambda r: bool(r.get("canonical"))),
         ("self", "Autorreferenciado", lambda r: r.get("canonical") == r["url"]),
         ("canonicalised", "Canonicalizado", lambda r: bool(r.get("canonical")) and r["canonical"] != r["url"]),
         ("missing", "Falta", lambda r: not r.get("canonical")),
         ("multiple", "Múltiple", lambda r: (r.get("canonical_count") or 0) > 1),
         ("non200", "URL canónica no indexable", lambda r: r["canonical_status"] not in (None, 200)),
         ("js_changed", "Cambiado por JavaScript", lambda r: bool(r.get("rendered_canonical")) and r["rendered_canonical"] != r.get("canonical"))],
        base=_html),
    Tab("pagination", "Paginación", "pages",
        [ADDR, ("next", "rel=next"), ("prev", "rel=prev"), ("next_status", "Código next"), ("prev_status", "Código prev"), IDX],
        [("contains", "Contiene paginación", lambda r: bool(r["next"] or r["prev"])),
         ("first", "Primera página", lambda r: bool(r["next"]) and not r["prev"]),
         ("paginated", "Paginada 2+", lambda r: bool(r["prev"])),
         ("non200", "URL de paginación no 200", lambda r: r["next_status"] not in (None, 200) or r["prev_status"] not in (None, 200)),
         ("noindex", "Paginación con noindex", lambda r: bool(r["next"] or r["prev"]) and page_noindex(r))],
        base=_html),
    Tab("directives", "Directivas", "pages",
        [ADDR, ("meta_robots", "Meta robots 1"), ("x_robots", "X-Robots-Tag 1"), ("rendered_robots", "Robots renderizado"), IDX, IDXR],
        [("index", "Index", lambda r: r["html200"] and not page_noindex(r)),
         ("noindex", "Noindex", lambda r: page_noindex(r)),
         ("follow", "Follow", lambda r: r["html200"] and not (is_nofollow(r.get("meta_robots")) or is_nofollow(r.get("x_robots")))),
         ("nofollow", "Nofollow", lambda r: is_nofollow(r.get("meta_robots")) or is_nofollow(r.get("x_robots"))),
         ("none", "None (noindex + nofollow)", lambda r: "none" in robots_tokens(r.get("meta_robots")) | robots_tokens(r.get("x_robots"))),
         ("noarchive", "NoArchive", lambda r: "noarchive" in robots_tokens(r.get("meta_robots")) | robots_tokens(r.get("x_robots"))),
         ("nosnippet", "NoSnippet", lambda r: "nosnippet" in robots_tokens(r.get("meta_robots")) | robots_tokens(r.get("x_robots"))),
         ("meta", "Contiene meta robots", lambda r: bool(r.get("meta_robots"))),
         ("xrobots", "Contiene X-Robots-Tag", lambda r: bool(r.get("x_robots"))),
         ("js_changed", "Cambiado por JavaScript", lambda r: bool(r.get("rendered")) and is_noindex(r.get("meta_robots")) != is_noindex(r.get("rendered_robots"))),
         ("blocked", "Bloqueado por robots.txt", lambda r: bool(r.get("blocked_by_robots")))]),
    Tab("hreflang", "Hreflang", "pages",
        [ADDR, ("hreflang_count", "Nº hreflang"), ("hreflang_langs", "Idiomas"), ("hreflang_bad_codes", "Códigos no válidos"),
         ("lang", "lang del HTML"), IDX],
        [("with", "Contiene hreflang", lambda r: r["hreflang_count"] > 0),
         ("bad_code", "Código de idioma/región no válido", lambda r: bool(r.get("hreflang_bad_codes"))),
         ("without", "Falta", lambda r: r["hreflang_count"] == 0),
         ("non200", "URLs hreflang no 200", lambda r: r["hreflang_bad_target"]),
         ("no_return", "Faltan enlaces de retorno", lambda r: r["hreflang_no_return"]),
         ("no_self", "Falta autorreferencia", lambda r: r["hreflang_no_self"]),
         ("no_xdefault", "Falta x-default", lambda r: r["hreflang_no_xdefault"])],
        base=_html),
    Tab("javascript", "JavaScript", "pages",
        [ADDR, ("word_count", "Palabras HTML"), ("rendered_word_count", "Palabras renderizado"), ("rendered_only_links", "Enlaces solo en renderizado"),
         ("js_click_out", "Destinos solo con clic"), ("js_click_in", "Llega solo por clic desde"),
         ("blocked_requests", "Peticiones de terceros bloqueadas"), ("blocked_hosts", "Dominios bloqueados"),
         ("title", "Título HTML"), ("rendered_title", "Título renderizado"), ("render_error", "Error de renderizado"), IDX],
        [("content", "Contenido que depende de JS", lambda r: bool(r.get("rendered")) and (r.get("rendered_word_count") or 0) >= 2 * (r.get("word_count") or 0) + 50),
         ("links", "Enlaces solo en el HTML renderizado", lambda r: r["rendered_only_links"] > 0),
         ("js_click", "Navegación solo con clic (sin <a href>)", lambda r: r.get("js_click_out", 0) > 0),
         ("js_click_target", "Páginas descubiertas solo por clic", lambda r: r.get("js_click_in", 0) > 0 and r["inlinks"] == 0),
         ("blocked", "Con scripts de terceros bloqueados", lambda r: r.get("blocked_requests", 0) > 0),
         ("title", "Título cambiado por JS", lambda r: bool(r.get("rendered_title")) and r["title"] and r["rendered_title"].strip() != r["title"]),
         ("canonical", "Canonical cambiado por JS", lambda r: bool(r.get("rendered_canonical")) and r["rendered_canonical"] != r.get("canonical")),
         ("robots", "Noindex cambiado por JS", lambda r: bool(r.get("rendered")) and is_noindex(r.get("meta_robots")) != is_noindex(r.get("rendered_robots"))),
         ("error", "Error de renderizado", lambda r: bool(r.get("render_error"))),
         ("not_rendered", "Sin renderizar", lambda r: not r.get("rendered"))],
        base=_html),
    Tab("links", "Enlaces", "pages",
        [ADDR, ("outlinks", "Outlinks internos"), ("unique_outlinks", "Outlinks internos únicos"), ("external_outlinks", "Outlinks externos"),
         ("unique_external_outlinks", "Outlinks externos únicos"), ("self_links", "Autoenlaces"), ("nofollow_outlinks", "Internos nofollow"),
         ("sponsored_outlinks", "rel=sponsored"), ("ugc_outlinks", "rel=ugc"),
         ("weak_anchors", "Anclas no descriptivas"), ("empty_anchors", "Sin texto ancla"), ("links_to_non200", "Hacia URLs no 200"),
         ("inlinks", "Inlinks"), ("pagerank", "PageRank interno (0-100)"), ("authority_pct", "% de autoridad"),
         ("depth", "Profundidad"), IDX],
        [("low_pr", "Indexables con poca autoridad (PageRank < 20)",
          lambda r: r["indexable_label"] == "Indexable" and r.get("pagerank") is not None and r["pagerank"] < 20),
         ("pr_on_nonindexable", "Autoridad gastada en URLs no indexables (PageRank ≥ 40)",
          lambda r: r["indexable_label"] != "Indexable" and (r.get("pagerank") or 0) >= 40),
         ("no_internal_out", "Páginas sin outlinks internos", lambda r: r["unique_outlinks"] - (1 if r.get("self_links") else 0) <= 0),
         ("many_out", "Demasiados outlinks internos", lambda r: r["th_many_internal"]),
         ("many_ext", "Demasiados outlinks externos", lambda r: r["th_many_external"]),
         ("self", "Páginas que se enlazan a sí mismas", lambda r: r.get("self_links", 0) > 0),
         ("nofollow", "Outlinks internos nofollow", lambda r: r["nofollow_outlinks"] > 0),
         ("sponsored", "Enlaces rel=sponsored", lambda r: r.get("sponsored_outlinks", 0) > 0),
         ("ugc", "Enlaces rel=ugc", lambda r: r.get("ugc_outlinks", 0) > 0),
         ("weak", "Texto ancla no descriptivo", lambda r: r["weak_anchors"] > 0),
         ("empty", "Outlinks internos sin texto ancla", lambda r: r["empty_anchors"] > 0),
         ("non200", "Outlinks hacia URLs no 200", lambda r: r["links_to_non200"] > 0),
         ("deep", "Más profunda que el máximo", lambda r: r["th_deep"]),
         ("one_inlink", "Solo un inlink", lambda r: r["unique_inlinks"] == 1)],
        base=_html),
    Tab("structured", "Datos estructurados", "pages",
        [ADDR, ("jsonld_types", "Tipos JSON-LD"), ("microdata", "Microdatos"), ("rdfa", "RDFa"), ("jsonld_count", "Bloques JSON-LD"),
         ("jsonld_invalid", "JSON-LD no válidos"), ("schema_errors", "Errores de validación"), ("schema_warnings", "Advertencias"),
         ("schema_messages", "Detalle"), IDX],
        [("with", "Contiene datos estructurados", lambda r: bool(r["jsonld_count"] or r["microdata"] or r.get("rdfa"))),
         ("without", "Falta", lambda r: not (r["jsonld_count"] or r["microdata"] or r.get("rdfa"))),
         ("jsonld", "JSON-LD", lambda r: r["jsonld_count"] > 0),
         ("microdata", "Microdatos", lambda r: bool(r["microdata"])),
         ("rdfa", "RDFa", lambda r: bool(r.get("rdfa"))),
         ("invalid", "Errores de análisis (JSON-LD)", lambda r: (r.get("jsonld_invalid") or 0) > 0),
         ("validation_errors", "Errores de validación (Google)", lambda r: r.get("schema_errors", 0) > 0),
         ("validation_warnings", "Advertencias de validación", lambda r: r.get("schema_warnings", 0) > 0)],
        base=_html),
    Tab("local", "SEO local", "pages",
        [ADDR, ("local_phones", "Teléfonos visibles"), ("local_tel", "Enlaces tel:"), ("local_clickable", "Teléfono clicable"),
         ("local_whatsapp", "WhatsApp"), ("local_map", "Mapa"), ("local_schema", "Schema de negocio local"),
         ("local_service", "Página de servicio"), ("local_city_title", "Ciudad en título"), ("local_city_h1", "Ciudad en H1"),
         ("local_city_url", "Ciudad en URL"), ("title", "Título 1"), ("h1_1", "H1-1"), IDX],
        [("no_phone", "Sin teléfono visible", lambda r: r["has_local"] and not r.get("local_phones")),
         ("not_clickable", "Teléfono no clicable", lambda r: r.get("local_clickable") == "no"),
         ("whatsapp", "Con WhatsApp", lambda r: r.get("local_whatsapp") == "sí"),
         ("map", "Con mapa", lambda r: r.get("local_map") in ("incrustado", "enlace")),
         ("schema", "Con schema de negocio local", lambda r: bool(r.get("local_schema"))),
         ("service", "Páginas de servicio", lambda r: r.get("local_service") == "sí"),
         ("city_title", "Ciudad ausente del título", lambda r: r.get("local_city_title") == "no"),
         ("city_h1", "Ciudad ausente del H1", lambda r: r.get("local_city_h1") == "no"),
         ("city_url", "Ciudad ausente de la URL", lambda r: r.get("local_city_url") == "no"),
         ("contact", "Página de contacto", lambda r: r.get("local_contact_page") == "sí")],
        base=_html),
    Tab("accessibility", "Accesibilidad", "pages",
        [ADDR, ("a11y_total", "Total"), ("a11y_img_alt", "Img sin alt"), ("a11y_unlabeled", "Campos sin etiqueta"),
         ("a11y_buttons", "Botones sin nombre"), ("a11y_links", "Enlaces sin texto"), ("a11y_headings", "Encabezados vacíos"),
         ("a11y_dup_ids", "IDs duplicados"), ("a11y_iframes", "Iframes sin title"), ("a11y_tabindex", "tabindex > 0"), IDX],
        [("any", "Con barreras", lambda r: r.get("a11y_total", 0) > 0),
         ("img_alt", "Imágenes sin texto alternativo (1.1.1)", lambda r: r.get("a11y_img_alt", 0) > 0),
         ("labels", "Campos sin etiqueta (1.3.1, 3.3.2)", lambda r: r.get("a11y_unlabeled", 0) > 0),
         ("buttons", "Botones sin nombre accesible (4.1.2)", lambda r: r.get("a11y_buttons", 0) > 0),
         ("links", "Enlaces sin texto (2.4.4)", lambda r: r.get("a11y_links", 0) > 0),
         ("headings", "Encabezados vacíos (1.3.1)", lambda r: r.get("a11y_headings", 0) > 0),
         ("dup_ids", "IDs duplicados (4.1.1)", lambda r: r.get("a11y_dup_ids", 0) > 0),
         ("iframes", "Iframes sin title (4.1.2)", lambda r: r.get("a11y_iframes", 0) > 0),
         ("tabindex", "tabindex positivo (2.4.3)", lambda r: r.get("a11y_tabindex", 0) > 0),
         ("autoplay", "Reproducción automática (1.4.2)", lambda r: r.get("a11y_autoplay", 0) > 0),
         ("lang", "Falta idioma de la página (3.1.1)", lambda r: r["has_extra"] and not r.get("lang")),
         ("zoom", "Zoom bloqueado (1.4.4)", lambda r: r["zoom_blocked"])],
        base=_html),
    Tab("amp", "AMP", "pages",
        [ADDR, ("is_amp", "Es AMP"), ("amphtml", "Versión AMP (amphtml)"), ("amp_error_count", "Errores"), ("amp_errors", "Detalle"), IDX],
        [("amp", "Páginas AMP", lambda r: r.get("is_amp") == "sí"),
         ("has_amphtml", "Con versión AMP enlazada", lambda r: bool(r.get("amphtml"))),
         ("errors", "AMP con errores", lambda r: r.get("amp_error_count", 0) > 0)],
        base=_html),
    Tab("analytics", "Search Console / Analytics", "analytics",
        [ADDR, ("m_clicks", "Clics"), ("m_impressions", "Impresiones"), ("m_ctr", "CTR"), ("m_position", "Posición"),
         ("m_sessions", "Sesiones"), ("m_conversions", "Conversiones"), ("crawled", "Rastreada"), ("inlinks", "Inlinks"),
         ("status_code", "Código"), ("indexable_label", "Indexabilidad")],
        [("orphan", "Con tráfico y sin enlaces internos", lambda r: r["inlinks"] == 0 and ((r.get("m_clicks") or 0) + (r.get("m_sessions") or 0)) > 0),
         ("not_crawled", "Con datos pero no encontradas en el rastreo", lambda r: not r["crawled"]),
         ("nonindexable", "Con tráfico y no indexables", lambda r: r["crawled"] and r["indexable_label"] != "Indexable"),
         ("low_ctr", "CTR bajo (<1 %) con impresiones", lambda r: (r.get("m_impressions") or 0) >= 100 and (r.get("m_ctr") or 0) < 0.01),
         ("striking", "Posición 4-20 (oportunidad)", lambda r: 4 <= (r.get("m_position") or 0) <= 20),
         ("no_clicks", "Sin clics", lambda r: r.get("m_clicks") == 0)]),
    Tab("sitemaps", "Sitemaps", "pages",
        [ADDR, ("in_sitemap", "En sitemap"), ("status_code", "Código de respuesta"), ("inlinks", "Inlinks"), IDX, IDXR],
        [("in", "URLs en el sitemap", lambda r: r["in_sitemap"]),
         ("not_in", "URLs indexables fuera del sitemap", lambda r: not r["in_sitemap"] and r["html200"] and r["indexable_label"] == "Indexable"),
         ("orphan", "URLs huérfanas", lambda r: r["in_sitemap"] and r["inlinks"] == 0 and r.get("depth") != 0),
         ("nonindexable", "URLs no indexables en el sitemap", lambda r: r["in_sitemap"] and r["indexable_label"] != "Indexable")]),
    Tab("mobile", "Móvil", "pages",
        [ADDR, ("viewport", "Meta viewport"), IDX],
        [("missing", "Falta viewport", lambda r: r["viewport_missing"]),
         ("fixed", "Viewport no adaptable", lambda r: r["viewport_fixed"]),
         ("zoom", "Zoom bloqueado", lambda r: r["zoom_blocked"])],
        base=_html),
    Tab("validation", "Validación", "pages",
        [ADDR, ("doctype", "Doctype"), ("head_count", "Nº <head>"), ("body_count", "Nº <body>"), ("title_count", "Nº <title>"),
         ("invalid_in_head", "Elementos no válidos en <head>"), ("lang", "lang"), IDX],
        [("no_doctype", "Falta doctype", lambda r: r["doctype"] == "no"),
         ("no_head", "Falta <head>", lambda r: r["has_extra"] and r.get("head_count") == 0),
         ("multi_head", "Varios <head>", lambda r: (r.get("head_count") or 0) > 1),
         ("no_body", "Falta <body>", lambda r: r["has_extra"] and r.get("body_count") == 0),
         ("multi_body", "Varios <body>", lambda r: (r.get("body_count") or 0) > 1),
         ("invalid_head", "Elementos no válidos en <head>", lambda r: bool(r["invalid_in_head"])),
         ("no_lang", "Falta atributo lang", lambda r: r["has_extra"] and not r.get("lang")),
         ("big_html", "HTML por encima del tamaño máximo", lambda r: r["th_big_html"])],
        base=_html),
    Tab("pdf", "PDF", "pages",
        [ADDR, ("status_code", "Código de respuesta"), ("pdf_title", "Título"), ("pdf_author", "Autor"), ("pdf_pages", "Páginas"),
         ("pdf_created", "Creado"), ("pdf_modified", "Modificado"), ("size_bytes", "Tamaño (bytes)"), ("inlinks", "Inlinks")],
        [("no_title", "Sin título en las propiedades", lambda r: r["has_pdf_props"] and not r["pdf_title"]),
         ("no_author", "Sin autor", lambda r: r["has_pdf_props"] and not r["pdf_author"])],
        base=lambda r: "pdf" in (r.get("content_type") or "") or r["url"].lower().split("?")[0].endswith(".pdf")),
    Tab("ai", "IA", "pages",
        [ADDR, ("title", "Título 1"), ("ai_intent", "Intención de búsqueda"), ("ai_topic", "Palabra clave | grupo"),
         ("meta_description", "Meta description actual"), ("ai_meta_rewrite", "Meta description propuesta"),
         ("ai_custom", "Mi instrucción"), ("ai_summary", "Resumen"), ("ai_translate_en", "Traducción (inglés)"), IDX],
        [("done", "Con algún resultado de IA", lambda r: any(r.get(f"ai_{k}") for k in AI_COLUMNS)),
         ("pending", "Sin analizar", lambda r: not any(r.get(f"ai_{k}") for k in AI_COLUMNS))]
        + [(f"intent_{v}", f"Intención: {v}", (lambda v: lambda r: (r.get("ai_intent") or "").lower().strip(" .") == v)(v))
           for v in ("informacional", "comercial", "transaccional", "navegacional", "local")],
        base=_html),
    Tab("notes", "Notas", "pages",
        [ADDR, ("note_status", "Estado"), ("note", "Anotación Consultor"), ("note_updated", "Actualizada"), ("status_code", "Código de respuesta"),
         IDX, ("title", "Título 1"), ("h1_1", "H1-1")],
        [(s.replace(" ", "_"), s.capitalize(), (lambda s: lambda r: r["note_status"] == s)(s)) for s in db.NOTE_STATUSES],
        base=lambda r: bool(r.get("note_status"))),
]


def tabs_for(crawl: dict | None) -> list[Tab]:
    """Static tabs + custom search/extraction tabs defined in this crawl's configuration + issues."""
    cfg = (crawl or {}).get("settings") or {}
    tabs = list(STATIC_TABS)
    searches = cfg.get("searches") or []
    tabs.append(Tab("search", "Búsqueda personalizada", "pages",
        [ADDR, *((f"search::{s['name']}", f"{s['name']} ({'contiene' if s['mode'] == 'contains' else 'no contiene'})") for s in searches), IDX],
        [(f"s{i}", f"{s['name']}: {'contiene' if s['mode'] == 'contains' else 'no contiene'} «{s['text']}»",
          (lambda key, mode: (lambda r: (r.get(key) or 0) > 0) if mode == "contains" else (lambda r: (r.get(key) or 0) == 0))(f"search::{s['name']}", s["mode"]))
         for i, s in enumerate(searches)], base=_html))
    extractions = cfg.get("extractions") or []
    tabs.append(Tab("extract", "Extracción personalizada", "pages",
        [ADDR, *((f"extract::{e['name']}", e["name"]) for e in extractions), IDX],
        [(f"e{i}", f"{e['name']}: con resultados", (lambda key: lambda r: (r.get(key) or 0) > 0)(f"extract_n::{e['name']}"))
         for i, e in enumerate(extractions)], base=_html))
    tabs.append(Tab("issues", "Errores", "issues", []))
    return tabs


def tab_by_key(crawl: dict | None, key: str) -> Tab | None:
    return next((t for t in tabs_for(crawl) if t.key == key), None)


# --- cache & queries ---------------------------------------------------------------------------
_cache: dict[int, tuple[tuple, dict]] = {}
_cache_lock = threading.Lock()


def workbench(crawl: dict) -> dict:
    site = db.note_scope(crawl["id"])  # notes belong to this audit only
    sig = (crawl["status"], crawl["pages_done"], crawl.get("ext_done"), crawl.get("phase"), crawl.get("finished_at"),
           db.analytics_version(crawl["id"]), db.notes_version(site),
           dictionary.version(), db.ai_version(crawl["id"]))
    with _cache_lock:
        hit = _cache.get(crawl["id"])
        if hit and hit[0] == sig:
            return hit[1]
    cid = crawl["id"]
    data = build_rows(db.load_pages(cid), db.load_links(cid), db.load_images(cid), db.load_externals(cid),
                      db.load_sitemap_urls(cid), db.load_analytics(cid), db.load_insight(cid, "near_dups", {}),
                      set(db.load_insight(cid, "spelling_vocab", [])) | dictionary.words()
                      | set(re.findall(r"[^\s,;]+", ((crawl.get("settings") or {}).get("custom_dictionary") or "").lower())),
                      (db.load_insight(cid, "local", {}) or {}).get("pages") or {}, db.get_notes(site),
                      crawl.get("settings") or {}, db.load_ai(cid))
    with _cache_lock:
        _cache[cid] = (sig, data)
        if len(_cache) > 5:
            _cache.pop(next(iter(_cache)))
    return data


def tab_rows(data: dict, tab: Tab, flt: str) -> list[dict]:
    rows = [r for r in data[tab.source] if tab.base(r)]
    for key, _, pred in tab.filters:
        if key == flt:
            return [r for r in rows if pred(r)]
    return rows


def query(data: dict, tab: Tab, flt: str, q: str = "", sort: str = "", desc: bool = False) -> list[dict]:
    rows = tab_rows(data, tab, flt)
    if q:
        ql = q.lower()
        rows = [r for r in rows if any(ql in str(r.get(k) or "").lower() for k, _ in tab.columns[:3])]
    if sort in {k for k, _ in tab.columns}:
        nums = [r.get(sort) for r in rows if r.get(sort) not in (None, "")]
        numeric = all(isinstance(v, (int, float)) for v in nums)
        missing = [r for r in rows if r.get(sort) in (None, "")]
        present = sorted((r for r in rows if r.get(sort) not in (None, "")),
                         key=(lambda r: r[sort]) if numeric else (lambda r: str(r[sort]).lower()), reverse=desc)
        rows = present + missing
    return rows


def _pct(n: int, total: int) -> float:
    return round(100 * n / total, 2) if total else 0


def overview(data: dict, tabs: list[Tab] | None = None) -> list[dict]:
    """Per tab: 'Todo' plus each filter with its count and % of the tab total."""
    out = []
    for tab in tabs or tabs_for(None):
        if tab.source == "issues":
            continue
        base = [r for r in data[tab.source] if tab.base(r)]
        total = len(base)
        out.append({
            "key": tab.key, "label": tab.label, "total": total,
            "filters": [{"key": k, "label": label, "count": (n := sum(1 for r in base if pred(r))), "pct": _pct(n, total),
                         "help": any_filter_help(tab.key, k, label)} for k, label, pred in tab.filters],
            "todo_help": any_filter_help(tab.key, "", "Todo"),
        })
    return out


def summary(data: dict) -> list[dict]:
    """'Resumen' block of the overview, same rows as the desktop tool."""
    pages, ext = data["pages"], data["externals"]
    internal_blocked = sum(1 for r in pages if r.get("blocked_by_robots"))
    external_blocked = sum(1 for r in ext if r.get("blocked_by_robots"))
    found = len(pages) + len(ext)
    crawled = sum(1 for r in pages if r.get("status_code") is not None) + sum(1 for r in ext if r.get("status_code") is not None)
    html = [r for r in pages if r.get("type") == "html" or r.get("html200")]
    indexable = sum(1 for r in pages if r.get("indexable_label") == "Indexable")
    rows = [("URLs encontradas en total", found), ("Internas bloqueadas por robots.txt en total", internal_blocked),
            ("Externas bloqueadas por robots.txt en total", external_blocked), ("URLs rastreadas en total", crawled),
            ("URLs internas en total", len(pages)), ("URLs externas en total", len(ext)),
            ("Total de URLs internas indexables", indexable), ("Total de URLs internas no indexables", len(pages) - indexable)]
    from .help import summary as summary_help
    return [{"label": label, "count": n, "pct": _pct(n, found), "help": summary_help(label)} for label, n in rows] + \
        ([{"label": "URLs en sitemaps XML", "count": data.get("sitemap_count", 0), "pct": "", "help": summary_help("URLs en sitemaps XML")}] if data.get("sitemap_count") else []) + \
        ([{"label": "Páginas HTML", "count": len(html), "pct": _pct(len(html), len(pages)), "help": summary_help("Páginas HTML")}] if pages else [])


def site_structure(data: dict, max_depth: int = 3) -> dict:
    """Directory tree (first path segments) and crawl-depth histogram for internal HTML pages."""
    pages = [r for r in data["pages"] if r.get("type") == "html" or r.get("html200")]
    tree: dict = {}
    for r in pages:
        segs = [s for s in urlsplit(r["url"]).path.split("/") if s][:max_depth]
        node = tree
        path = ""
        for seg in [""] + segs:
            path = path + seg + "/" if seg else "/"
            node = node.setdefault(path, {"_n": 0, "_idx": 0, "_children": {}})
            node["_n"] += 1
            node["_idx"] += 1 if r.get("indexable_label") == "Indexable" else 0
            node = node["_children"]

    def flatten(nodes: dict, level: int) -> list[dict]:
        out = []
        for path, info in sorted(nodes.items(), key=lambda kv: (-kv[1]["_n"], kv[0])):
            out.append({"path": path, "level": level, "count": info["_n"], "indexable": info["_idx"]})
            out.extend(flatten(info["_children"], level + 1))
        return out

    depths = Counter()
    for r in pages:
        d = r.get("depth")
        depths["sin enlazar" if d is None else (str(d) if d < 10 else "10+")] += 1
    order = [str(i) for i in range(10)] + ["10+", "sin enlazar"]
    total = len(pages)
    return {"tree": flatten(tree, 0)[:200], "total": total,
            "depths": [{"label": k, "count": depths[k], "pct": _pct(depths[k], total)} for k in order if depths[k]]}


def response_times(data: dict) -> list[dict]:
    pages = [r for r in data["pages"] if r.get("response_ms") is not None and (r.get("type") == "html" or r.get("html200"))]
    buckets = [(0, 1000, "0 s – 1 s"), (1000, 2000, "1 s – 2 s"), (2000, 3000, "2 s – 3 s"), (3000, 4000, "3 s – 4 s"),
               (4000, 5000, "4 s – 5 s"), (5000, 6000, "5 s – 6 s"), (6000, 7000, "6 s – 7 s"), (7000, 8000, "7 s – 8 s"),
               (8000, 9000, "8 s – 9 s"), (9000, 10000, "9 s – 10 s"), (10000, 10 ** 9, "10 s o más")]
    total = len(pages)
    return [{"label": label, "count": (n := sum(1 for r in pages if lo <= r["response_ms"] < hi)), "pct": _pct(n, total)}
            for lo, hi, label in buckets]


def crawl_path(links: list[dict], start_url: str, url: str) -> list[dict] | None:
    """Shortest click path from the start URL (BFS over links, redirects, canonicals…)."""
    kinds = ("a", "rendered", "redirect", "meta_refresh", "js_redirect", "canonical", "hreflang", "pagination", "amphtml", "mobile_alt")
    adj: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for l in links:
        if l["kind"] in kinds:
            adj[l["source_url"]].append((l["target_url"], l["kind"], l.get("anchor") or ""))
    if url == start_url:
        return [{"url": url, "kind": "inicio", "anchor": ""}]
    prev: dict[str, tuple[str, str, str]] = {}
    seen = {start_url}
    frontier = [start_url]
    while frontier:
        nxt = []
        for u in frontier:
            for v, kind, anchor in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    prev[v] = (u, kind, anchor)
                    if v == url:
                        steps, cur = [], url
                        while cur != start_url:
                            p, k, a = prev[cur]  # each step records how it was reached
                            steps.append({"url": cur, "kind": k, "anchor": a})
                            cur = p
                        steps.append({"url": start_url, "kind": "inicio", "anchor": ""})
                        return list(reversed(steps))
                    nxt.append(v)
        frontier = nxt
    return None


def graph(data: dict, links: list[dict], start_url: str, limit: int = 1500) -> dict:
    """Nodes (internal HTML pages) and edges for the force-directed diagram and the crawl tree."""
    pages = [r for r in data["pages"] if r.get("type") == "html" or r.get("html200")]
    pages.sort(key=lambda r: (r.get("depth") if r.get("depth") is not None else 99, -r["inlinks"]))
    pages = pages[:limit]
    ids = {r["url"]: i for i, r in enumerate(pages)}
    edges, seen = [], set()
    for l in links:
        if l["kind"] in ("a", "rendered", "redirect") and l["source_url"] in ids and l["target_url"] in ids \
                and l["source_url"] != l["target_url"]:
            key = (ids[l["source_url"]], ids[l["target_url"]])
            if key not in seen:
                seen.add(key)
                edges.append(key)
    nodes = []
    for r in pages:
        status = r.get("status_code")
        group = ("blocked" if r.get("blocked_by_robots") else "error" if status is None or status >= 400 else
                 "redirect" if 300 <= status < 400 or r.get("redirect_type") else
                 "indexable" if r.get("indexable_label") == "Indexable" else "nonindexable")
        nodes.append({"url": r["url"], "path": urlsplit(r["url"]).path or "/", "inlinks": r["inlinks"],
                      "depth": r.get("depth"), "group": group, "start": r["url"] == start_url, "status": status})
    # crawl tree: parent = first discoverer on a shortest path (BFS)
    adj = defaultdict(list)
    for s, t in edges:
        adj[s].append(t)
    parent = {}
    root = ids.get(start_url)
    if root is not None:
        seen_n, frontier = {root}, [root]
        while frontier:
            nxt = []
            for u in frontier:
                for v in adj.get(u, ()):
                    if v not in seen_n:
                        seen_n.add(v)
                        parent[v] = u
                        nxt.append(v)
            frontier = nxt
    return {"nodes": nodes, "edges": edges, "parent": parent, "root": root, "truncated": len(data["pages"]) > limit}


def cell(row: dict, key: str):
    value = row.get(key)
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, (set, list, dict)):
        return ""
    return "" if value is None else value


# --- [?] help next to each filter: why it matters for Google and how to fix it, in plain words ---------------------
FILTER_HELP_CODES = {
    "codes": {"4xx": "broken_link", "5xx": "server_error", "3xx": "link_to_redirect", "301": "link_to_redirect",
              "302": "link_to_redirect", "307": "link_to_redirect", "308": "link_to_redirect",
              "meta_refresh": "meta_refresh_redirect", "js_redirect": "js_redirect", "ssl": "ssl_error", "blocked": "blocked_but_linked"},
    "external": {"4xx": "broken_external", "5xx": "broken_external", "noresponse": "broken_external"},
    "security": {"http": "http_url", "mixed": "mixed_content", "no_hsts": "missing_hsts", "insecure_forms": "insecure_form"},
    "url": {"non_ascii": "url_non_ascii", "underscore": "url_underscore", "uppercase": "url_uppercase", "params": "url_parameters",
            "slashes": "url_multiple_slashes", "long": "url_too_long"},
    "titles": {"missing": "missing_title", "duplicate": "duplicate_title", "over_chars": "title_too_long",
               "under_chars": "title_too_short", "over_px": "title_too_wide", "multiple": "multiple_titles"},
    "descriptions": {"missing": "missing_description", "duplicate": "duplicate_description",
                     "over_chars": "description_too_long", "under_chars": "description_too_short"},
    "h1": {"missing": "missing_h1", "multiple": "multiple_h1", "over_chars": "h1_too_long", "non_sequential": "heading_non_sequential"},
    "h2": {"over_chars": "h2_too_long", "non_sequential": "heading_non_sequential"},
    "content": {"duplicate": "duplicate_content", "near_duplicate": "near_duplicate", "spelling": "spelling_errors",
                "thin": "thin_content", "js": "js_dependent_content"},
    "images": {"over_kb": "large_image", "legacy": "legacy_image_format", "missing_alt": "images_missing_alt", "broken": "broken_resource"},
    "canonicals": {"missing": "missing_canonical", "multiple": "multiple_canonicals", "non200": "canonical_to_non200"},
    "directives": {"nofollow": "nofollow_internal", "blocked": "blocked_but_linked"},
    "hreflang": {"bad_code": "hreflang_invalid_code", "non200": "hreflang_non200", "no_return": "hreflang_no_return",
                 "no_self": "hreflang_no_self"},
    "javascript": {"content": "js_dependent_content", "js_click": "js_click_navigation", "title": "rendered_title_changed",
                   "canonical": "rendered_canonical_conflict", "robots": "rendered_robots_conflict"},
}
FILTER_HELP_TEXT = {
    ("canonicals", "canonicalised"): (
        "La página dice que su versión «buena» es otra URL, así que Google mostrará la otra y esta no aparecerá en los resultados.",
        "Si es a propósito (filtros, parámetros, versiones de impresión) está bien. Si no, haz que el canonical apunte a la propia "
        "página y evita enlazar internamente a URLs canonicalizadas."),
    ("directives", "noindex"): (
        "Le pide a Google que no muestre la página en los resultados de búsqueda.",
        "Correcto para carritos, áreas privadas o páginas de gracias. Si la página debe posicionar, quita «noindex» de la meta robots "
        "o del header X-Robots-Tag."),
    ("hreflang", "no_xdefault"): (
        "Sin x-default, Google no sabe qué versión enseñar a quien no encaja en ningún idioma o país declarado.",
        "Añade <link rel=\"alternate\" hreflang=\"x-default\"> apuntando a la versión general (normalmente la portada o el selector de idioma)."),
    ("keywords", "present"): (
        "Google ignora la meta keywords desde 2009; solo sirve para enseñar tus palabras clave a la competencia.",
        "Puedes quitarla sin ningún riesgo."),
    ("pagination", "non200"): (
        "Una página de la serie paginada da error o redirige, así que Google no llega al contenido que hay detrás.",
        "Corrige los enlaces rel=next/prev y los de la paginación para que apunten a páginas que respondan 200."),
}


# Every row of the overview and every filter has its «?». Filters that only describe a group (file types…) get these
# texts; the rest come from FILTER_HELP_TEXT or from the issue catalogue.
DESCRIPTIVE_HELP = {
    "Todo": ("Todas las URLs de esta pestaña, sin filtrar.", "Es el punto de partida: elige un filtro para ver solo un grupo."),
    "HTML": ("Páginas web: documentos HTML que el rastreador ha abierto.", "Son las que pueden posicionar; casi todas las pestañas analizan estas."),
    "JavaScript": ("Archivos .js que cargan las páginas.", "Si fallan o pesan mucho, la página puede verse mal o ir lenta."),
    "CSS": ("Hojas de estilo que cargan las páginas.", "Si fallan, la página se ve sin diseño; si pesan mucho, tarda más en cargar."),
    "Imágenes": ("Imágenes usadas en las páginas (img, srcset, og:image…).", "Revisa las rotas y las más pesadas en la pestaña Imágenes."),
    "Multimedia": ("Vídeo y audio enlazados o incrustados en las páginas.", "Comprueba que cargan: suelen ser los archivos más pesados."),
    "Fuentes": ("Tipografías web (woff, woff2, ttf…).", "Muchas fuentes o muy pesadas retrasan que se vea el texto."),
    "XML": ("Archivos XML: normalmente sitemaps o feeds.", "Sirve para comprobar que el sitemap responde bien."),
    "PDF": ("Documentos PDF enlazados desde la web.", "Google también los indexa: comprueba que no estén rotos."),
    "Otros": ("URLs de otros tipos (zip, json, txt…).", "Normalmente no necesitan nada; mira solo si alguna da error."),
}
GENERIC_FILTER_HELP = ("Grupo de URLs de esta pestaña que cumplen este filtro.",
                       "Haz clic para verlas en la tabla. Es un dato descriptivo, no un error por sí mismo.")


def any_filter_help(tab: str, key: str, label: str) -> dict:
    """The «?» of a filter: its own explanation, or a plain description when the filter only groups URLs."""
    h = filter_help(tab, key)
    if h:
        return h
    why, fix = DESCRIPTIVE_HELP.get(label, GENERIC_FILTER_HELP)
    return {"why": why, "fix": fix}


def filter_help(tab: str, key: str) -> dict | None:
    """Plain-language explanation for a tab filter, or None when the filter is purely descriptive."""
    if (tab, key) in FILTER_HELP_TEXT:
        why, fix = FILTER_HELP_TEXT[(tab, key)]
        return {"why": why, "fix": fix}
    code = FILTER_HELP_CODES.get(tab, {}).get(key)
    if code:
        from .analysis import CATALOG
        if code in CATALOG:
            _sev, _title, why, fix = CATALOG[code]
            return {"why": why, "fix": fix}
    return None


# --- Beginner mode: the tabs that stay visible -------------------------------------------------------------------
SIMPLE_TABS = ("internal", "codes", "titles", "h1", "issues")


def evidence_parts(text) -> list[tuple[str, int]]:
    """«A; A; A; B» → [(A, 3), (B, 1)]: the same message repeated on one page is shown once with a count."""
    counts: dict[str, int] = {}
    for part in str(text or "").split("; "):
        if part:
            counts[part] = counts.get(part, 0) + 1
    return list(counts.items())
