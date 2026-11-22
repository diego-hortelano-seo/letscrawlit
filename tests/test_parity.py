"""Counting rules: word count, link counting, resources, columns."""
from app.extract import parse_html
from app.views import build_rows, query, tabs_for

BASE = "https://example.com"
NAV = "<nav>" + " ".join(f"menu{i}" for i in range(150)) + "</nav>"
FOOTER = "<footer>" + " ".join(f"pie{i}" for i in range(130)) + "</footer>"


def test_word_count_is_main_content_only():
    html = f"<html><body><header>Cabecera con logo</header>{NAV}<main><h1>Título</h1><p>uno dos tres cuatro cinco</p></main>{FOOTER}</body></html>"
    d = parse_html(html, BASE + "/")
    assert d["word_count"] == 6 and d["extra"]["content_area"] == "main"
    assert d["extra"]["words_total"] > 280
    # no <main>: body minus nav/header/footer/aside
    html2 = f"<html><body><header>Cabecera</header>{NAV}<div><p>solo esto cuenta aquí</p></div><aside>lateral</aside>{FOOTER}</body></html>"
    d2 = parse_html(html2, BASE + "/")
    assert d2["word_count"] == 4 and d2["extra"]["content_area"].startswith("body")
    # links in the nav are still extracted even though its words don't count
    d3 = parse_html(f"<html><body><nav><a href='/menu'>Menú</a></nav><main>texto</main></body></html>", BASE + "/")
    assert [l["href"] for l in d3["links"]] == [BASE + "/menu"]


def test_resources_record_where_they_come_from():
    html = """<html><head>
      <link rel="icon" href="/favicon.ico"><link rel="apple-touch-icon" href="/apple.png">
      <meta property="og:image" content="https://example.com/og-1200x630.jpg"><link rel="stylesheet" href="/s.css">
      <script type="application/ld+json">{"@type":"Organization","logo":"https://example.com/logo.png",
        "image":{"@type":"ImageObject","url":"https://example.com/schema.jpg"}}</script></head>
      <body><img src="/foto.jpg" srcset="/foto-300x200.jpg 300w, /foto-1024x683.jpg 1024w">
      <picture><source srcset="/foto.webp 1x, /foto@2x.webp 2x"></picture></body></html>"""
    via = {r["url"].replace(BASE, ""): r["via"] for r in parse_html(html, BASE + "/")["resources"]}
    assert via["/foto-1024x683.jpg"] == ["srcset"] and via["/foto@2x.webp"] == ["srcset"]
    assert via["/foto.jpg"] == ["img"] and via["/s.css"] == ["css"]
    assert via["/favicon.ico"] == ["favicon"] and via["/apple.png"] == ["favicon"]
    assert via["/og-1200x630.jpg"] == ["og:image"]
    assert via["/logo.png"] == ["schema"] and via["/schema.jpg"] == ["schema"]


def page(path, **kw):
    p = {"url": BASE + path, "status_code": 200, "content_type": "text/html", "title": "t" * 40, "depth": 0,
         "headers": {"last-modified": "Wed, 01 Oct 2025 10:00:00 GMT"}, "content_hash": "abc" + path}
    p.update(kw)
    return p


def link(src, dst, kind="a", internal=True, anchor=""):
    return {"source_url": BASE + src, "target_url": dst if dst.startswith("http") else BASE + dst, "kind": kind,
            "internal": internal, "nofollow": False, "anchor": anchor}


def test_link_counts_self_links_and_resources():
    pages = [page("/", depth=0), page("/a", depth=1), page("/doc.pdf", content_type="application/pdf", depth=2),
             page("/r", status_code=301, content_type=None, redirect_target=BASE + "/a", depth=1)]
    links = [link("/", "/a"), link("/", "/a"), link("/", "/"), link("/a", "/"), link("/a", "/doc.pdf"),
             link("/", "https://otro.com/", internal=False), link("/", "https://otro.com/", internal=False),
             link("/a", "/img-1024x683.jpg", kind="resource", anchor="srcset"),
             link("/", "/img-1024x683.jpg", kind="resource", anchor="og:image"),
             link("/sitemap.xml", "/solo-sitemap.jpg", kind="resource", anchor="sitemap (image:loc)")]
    externals = [{"url": BASE + "/img-1024x683.jpg", "kind": "resource", "status_code": 200, "content_type": "image/jpeg"},
                 {"url": BASE + "/solo-sitemap.jpg", "kind": "resource", "status_code": 200, "content_type": "image/jpeg"}]
    rows = {r["url"].replace(BASE, ""): r for r in build_rows(pages, links, [], externals)["pages"]}
    home = rows["/"]
    assert home["outlinks"] == 3 and home["unique_outlinks"] == 2          # /a twice + self
    assert home["external_outlinks"] == 2 and home["unique_external_outlinks"] == 1
    assert home["inlinks"] == 2 and home["unique_inlinks"] == 2 and home["self_links"] == 1  # self-links count as inlinks
    img = rows["/img-1024x683.jpg"]
    assert img["inlinks"] == 2 and img["depth"] == 1 and img["via"] == "og:image, srcset"   # min(0, 1) + 1
    assert img["indexability"] == rows["/doc.pdf"]["indexability"] == "Recurso (no HTML)"   # one state for all resources
    assert rows["/solo-sitemap.jpg"]["via"] == "sitemap (image:loc)" and rows["/solo-sitemap.jpg"]["depth"] is None
    assert rows["/r"]["redirect_url"] == BASE + "/a" and rows["/r"]["redirect_type"] == "HTTP 301"
    assert home["last_modified"].startswith("Wed")
    internal = next(t for t in tabs_for(None) if t.key == "internal")
    keys = {k for k, _ in internal.columns}
    assert {"canonical", "meta_robots", "x_robots", "h2_1", "h2_2", "desc_len", "h1_1_len", "lang", "last_modified",
            "content_hash", "redirect_url", "redirect_type", "outlinks", "unique_outlinks", "self_links", "via",
            "words_content", "words_total"} <= keys
    # navigation found only by clicking: reported, but not counted as a link (Google doesn't click)
    from app.analysis import analyze
    click = [link("/", "/solo-clic", kind="rendered", anchor="Ver servicio") | {"rel": "js-click"}]
    pages2 = pages + [page("/solo-clic", depth=1)]
    rows2 = {r["url"].replace(BASE, ""): r for r in build_rows(pages2, links + click, [], externals)["pages"]}
    assert rows2["/solo-clic"]["inlinks"] == 0 and rows2["/solo-clic"]["js_click_in"] == 1 and rows2["/"]["js_click_out"] == 1
    issues = analyze(pages2, links + click, sitemap_urls=[BASE + "/solo-clic"], start_url=BASE + "/")
    assert any(i.code == "js_click_navigation" and i.url == BASE + "/" for i in issues)
    assert any(i.code == "orphan_candidate" and i.url == BASE + "/solo-clic" for i in issues)
    links_tab = next(t for t in tabs_for(None) if t.key == "links")
    assert [r["url"] for r in query({"pages": list(rows.values())}, links_tab, "self")] == [BASE + "/"]
