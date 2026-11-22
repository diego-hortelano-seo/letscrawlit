"""CI/CD gate: turn a finished crawl into pass/fail, JUnit XML, Markdown, JSON and an optional webhook.

Exit codes: 0 = every check passed, 1 = SEO checks failed, 2 = the crawl could not run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import db
from .analysis import CATALOG, SEVERITY_LABEL, SEVERITY_ORDER, compute_indexability, group_issues

SEVERITIES = tuple(SEVERITY_ORDER)


def _codes(text: str) -> set[str]:
    return {c.strip() for c in (text or "").split(",") if c.strip()}


def evaluate(crawl_id: int, fail_on: str = "critical,high", fail_on_code: str = "", ignore_code: str = "",
             require_indexable: list[str] | None = None, check_home: bool = True, baseline: dict | None = None) -> dict:
    crawl = db.get_crawl(crawl_id)
    severities = set() if fail_on.strip().lower() == "none" else _codes(fail_on.lower())
    unknown = severities - set(SEVERITIES)
    if unknown:
        raise ValueError(f"Gravedad desconocida: {', '.join(sorted(unknown))}. Usa {', '.join(SEVERITIES)} o none.")
    always, never = _codes(fail_on_code), _codes(ignore_code)
    bad = (always | never) - set(CATALOG)
    if bad:
        raise ValueError(f"Código de incidencia desconocido: {', '.join(sorted(bad))}")
    issues = db.load_issues(crawl_id)
    known = {(i["code"], i["url"]) for i in (baseline or {}).get("issues", [])} if baseline else None
    failing = []
    for i in issues:
        if i["code"] in never:
            continue
        if i["code"] in always or i["severity"] in severities:
            if known is not None and (i["code"], i["url"]) in known:
                continue  # already there in the baseline: only new problems fail the build
            failing.append(i)
    pages = {p["url"]: p for p in db.load_pages(crawl_id)}
    checks = []
    targets = list(dict.fromkeys(([crawl["start_url"]] if check_home else []) + list(require_indexable or [])))
    for url in targets:
        p = pages.get(url)
        ok, reason = compute_indexability(p) if p else (False, "no se rastreó")
        checks.append({"name": f"Indexable: {url}", "ok": ok, "detail": reason})
    ran = crawl["status"] in ("completed", "cancelled") and crawl["pages_done"] > 0
    checks.insert(0, {"name": "Rastreo completado", "ok": ran, "detail": crawl.get("error") or crawl["status"]})
    by_code: dict[str, list[dict]] = {}
    for i in failing:
        by_code.setdefault(i["code"], []).append(i)
    passed = ran and not failing and all(c["ok"] for c in checks)
    return {
        "tool": "letscrawl", "crawl_id": crawl_id, "url": crawl["start_url"], "status": crawl["status"],
        "passed": passed, "pages": crawl["pages_done"], "fail_on": sorted(severities), "fail_on_code": sorted(always),
        "ignored_codes": sorted(never), "baseline": bool(baseline), "checks": checks,
        "failing": [{"code": c, "title": items[0]["title"], "severity": items[0]["severity"], "count": len(items),
                     "remediation": items[0]["remediation"], "urls": [i["url"] for i in items][:50],
                     "evidence": items[0]["evidence"]} for c, items in
                    sorted(by_code.items(), key=lambda kv: (SEVERITY_ORDER[kv[1][0]["severity"]], -len(kv[1])))],
        "summary": {s: sum(1 for i in issues if i["severity"] == s) for s in SEVERITIES},
        "issues": [{"code": i["code"], "url": i["url"], "severity": i["severity"]} for i in issues],
        "groups": [{"code": g["code"], "patterns": g["clusters"][:3]} for g in group_issues(issues) if g["clusters"]],
    }


def junit_xml(r: dict) -> str:
    """One <testcase> per check and per issue type that is gating; JUnit is read natively by every CI."""
    cases = []
    for c in r["checks"]:
        body = "" if c["ok"] else f'<failure message={quoteattr(c["detail"] or "")}/>'
        cases.append(f'  <testcase classname="letscrawl.checks" name={quoteattr(c["name"])}>{body}</testcase>')
    for f in r["failing"]:
        text = escape(f"{f['count']} URL(s). {f['remediation']}\n" + "\n".join(f["urls"]))
        cases.append(f'  <testcase classname="letscrawl.{f["severity"]}" name={quoteattr(f["title"])}>'
                     f'<failure message={quoteattr(f"{f["count"]} URL(s): {f["urls"][0]}")} type={quoteattr(f["code"])}>{text}</failure></testcase>')
    if not r["failing"]:
        cases.append('  <testcase classname="letscrawl.issues" name="Sin incidencias que bloqueen"/>')
    failures = sum(1 for c in r["checks"] if not c["ok"]) + len(r["failing"])
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name={quoteattr("LetsCrawlit SEO · " + r["url"])} '
            f'tests="{len(cases)}" failures="{failures}">\n' + "\n".join(cases) + "\n</testsuite>\n")


def markdown(r: dict) -> str:
    icon = "✅" if r["passed"] else "❌"
    lines = [f"## {icon} LetsCrawlit: {r['url']}", "",
             f"{r['pages']} URLs rastreadas · incidencias: " + ", ".join(f"{SEVERITY_LABEL[s]} {n}" for s, n in r["summary"].items() if n), ""]
    for c in r["checks"]:
        lines.append(f"- {'✅' if c['ok'] else '❌'} {c['name']} — {c['detail']}")
    if r["failing"]:
        lines += ["", "| Gravedad | Problema | URLs | Ejemplo |", "|---|---|---|---|"]
        for f in r["failing"]:
            lines.append(f"| {SEVERITY_LABEL[f['severity']]} | {f['title']} | {f['count']} | {f['urls'][0]} |")
    for g in r["groups"][:5]:
        p = g["patterns"][0]
        lines.append(f"\n> `{g['code']}`: {p['pct']} % en `{p['pattern']}`")
    if r["baseline"]:
        lines.append("\n_Comparado con la línea base: solo suspenden las incidencias nuevas._")
    return "\n".join(lines) + "\n"


def send_webhook(url: str, r: dict) -> str | None:
    """POST the summary. «text» works for Slack, Microsoft Teams (incoming webhook) and Discord («content»)."""
    import httpx
    text = markdown(r)
    payload = {"text": text, "content": text[:1900], "passed": r["passed"], "url": r["url"], "crawl_id": r["crawl_id"],
               "failing": [{k: f[k] for k in ("code", "title", "severity", "count")} for f in r["failing"]]}
    try:
        resp = httpx.post(url, json=payload, timeout=15)
        return None if resp.status_code < 400 else f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return str(exc)


def evaluate_and_report(crawl_id: int, args) -> int:
    baseline = None
    if args.baseline:
        try:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"No se pudo leer la línea base {args.baseline}: {exc}. Se evalúa sin ella.")
    try:
        r = evaluate(crawl_id, args.fail_on, args.fail_on_code, args.ignore_code, args.require_indexable,
                     not args.allow_noindex_home, baseline)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2
    md = markdown(r)
    print(md)
    if args.json:
        Path(args.json).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.junit:
        Path(args.junit).write_text(junit_xml(r), encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:  # GitHub Actions shows this on the run page
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(md)
    if args.webhook:
        err = send_webhook(args.webhook, r)
        print("Webhook enviado." if not err else f"El webhook falló ({err}); el resultado no cambia.")
    if args.out:
        from .cli import _finish
        _finish(crawl_id, args.out)
    if not r["checks"][0]["ok"]:
        return 2
    return 0 if r["passed"] else 1
