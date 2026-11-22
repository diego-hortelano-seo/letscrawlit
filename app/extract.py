"""Pure HTML extraction: raw HTML in, SEO signals out."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from bs4 import BeautifulSoup

from .checks import a11y_checks, amp_checks, validate_schema
from .urlutil import normalize

_WS = re.compile(r"\s+")
_WORD = re.compile(r"\w+", re.UNICODE)


def _clean(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def robots_tokens(value: str | None) -> set[str]:
    """'noindex, nofollow' / 'googlebot: noindex' -> {'noindex', 'nofollow'}."""
    tokens: set[str] = set()
    for part in re.split(r"[,;]", (value or "").lower()):
        part = part.strip()
        if ":" in part:  # "googlebot: noindex" or "unavailable_after: date"
            part = part.split(":", 1)[1].strip()
        if part:
            tokens.add(part)
    return tokens


def is_noindex(value: str | None) -> bool:
    t = robots_tokens(value)
    return "noindex" in t or "none" in t


def is_nofollow(value: str | None) -> bool:
    t = robots_tokens(value)
    return "nofollow" in t or "none" in t


def text_stats(text: str) -> tuple[int, str]:
    normalized = _clean(text).lower()
    words = len(_WORD.findall(normalized))
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest() if normalized else ""
    return words, digest


def _jsonld_types(node: Any, out: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _jsonld_types(item, out)
    elif isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, list):
            out.extend(str(x) for x in t)
        if "@graph" in node:
            _jsonld_types(node["@graph"], out)


BOILERPLATE = ["nav", "header", "footer", "aside"]
BOILERPLATE_ROLES = ["navigation", "banner", "contentinfo", "complementary"]


# Headings that belong to the site's frame, not to the page: menus, footers, sidebars and widgets such as the cart
# drawer («Tu carrito (artículos: 0)»), pop-ups or cookie banners. Repeated on every page, they are left out of H1/H2.
_WIDGET = re.compile(r"(^|[\s_-])(cart|carrito|minicart|mini-cart|basket|cesta|offcanvas|off-canvas|drawer|modal|popup|"
                     r"cookie|newsletter|widget|sidebar|menu)([\s_-]|$)", re.I)
_FRAME_TAGS = {"nav", "footer", "aside"}
_FRAME_ROLES = {"navigation", "contentinfo", "complementary", "dialog"}


def _in_frame(el) -> bool:
    for anc in el.parents:
        if anc.name in (None, "[document]", "html", "body"):
            continue
        if anc.name in _FRAME_TAGS or (anc.get("role") or "").lower() in _FRAME_ROLES:
            return True
        if _WIDGET.search(" ".join(anc.get("class") or []) + " " + (anc.get("id") or "")):
            return True
    return False


def page_headings(soup: BeautifulSoup, tag: str) -> list[str]:
    """H1/H2 of the page itself, without the ones repeated by the site's frame and widgets."""
    return [_clean(h.get_text(" ")) for h in soup.find_all(tag) if not _in_frame(h)]


# Language of the main text, from very common words. Only answers when the text is long and clearly one language.
_STOPWORDS = {
    "es": set("el la los las de del que y en un una por con para es no se su al lo como más pero sus le ya o este esta son "
              "también fue muy sin sobre entre cuando todo nos hay donde".split()),
    "en": set("the and of to in is that for it with as was on be are this by at from or an have not but they which you "
              "your can will all has more their our we".split()),
    "fr": set("le la les de des du et en un une est que qui pour dans pas sur au avec ce sont par plus ne nous vous "
              "cette leur".split()),
    "de": set("der die das und ist nicht ein eine zu den von mit sich des auf für im dem auch es an werden aus er sie "
              "wir ihr".split()),
    "it": set("il lo la gli le di del della che e è un una per con non sono nel alla come più anche questo sua".split()),
    "pt": set("o a os as de do da que e em um uma para com não é se na no mais por como dos das ao seu sua".split()),
}


def text_language(text: str) -> str | None:
    words = re.findall(r"[a-záéíóúüñàèìòùâêîôûäöüßçãõ]+", (text or "").lower())
    if len(words) < 80:
        return None
    hits = {lang: sum(1 for w in words if w in sw) for lang, sw in _STOPWORDS.items()}
    best = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    (lang, n), (_, second) = best[0], best[1]
    return lang if n >= 0.12 * len(words) and n >= 2 * second else None


def content_text(soup: BeautifulSoup) -> tuple[str, str]:
    """Main content only (the usual "content area" convention): <main>, else <article>, else the body without
    nav/header/footer/aside (and their ARIA roles). Call after scripts/styles are removed. Mutates nothing it returns."""
    mains = soup.find_all("main") or soup.find_all(attrs={"role": "main"})
    if mains:
        return _clean(" ".join(m.get_text(" ") for m in mains)), "main"
    articles = soup.find_all("article")
    if articles:
        return _clean(" ".join(a.get_text(" ") for a in articles)), "article"
    body = soup.body or soup
    for el in body.find_all(BOILERPLATE) + body.find_all(attrs={"role": BOILERPLATE_ROLES}):
        el.decompose()
    return _clean(body.get_text(" ")), "body sin nav/header/footer/aside"


def _srcset(value: str | None) -> list[str]:
    """'a.jpg 300w, b-1024x768.jpg 1024w' -> ['a.jpg', 'b-1024x768.jpg']"""
    out = []
    for part in (value or "").split(","):
        url = part.strip().split(" ")[0].strip()
        if url and not url.startswith("data:"):
            out.append(url)
    return out


def _schema_media(blocks: list) -> list[str]:
    """Image/logo/media URLs referenced from JSON-LD."""
    found: list[str] = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in ("image", "logo", "thumbnailUrl", "contentUrl", "url") \
                and node.startswith(("http://", "https://", "/")) and key != "url":
            found.append(node)

    for b in blocks:
        walk(b)
    # {"image": {"@type": "ImageObject", "url": "..."}} -> the url of an ImageObject
    def walk_objects(node):
        if isinstance(node, dict):
            if node.get("@type") in ("ImageObject", "VideoObject") and isinstance(node.get("url"), str):
                found.append(node["url"])
            for v in node.values():
                walk_objects(v)
        elif isinstance(node, list):
            for v in node:
                walk_objects(v)

    for b in blocks:
        walk_objects(b)
    return found


_SYL = re.compile(r"[aeiouyáéíóúü]+", re.I)


def readability(text: str, lang: str | None) -> dict | None:
    """Fernández-Huerta (Spanish adaptation of Flesch) or Flesch reading ease. 0-100, higher = easier."""
    words = _WORD.findall(text or "")
    words = [w for w in words if not w.isdigit()]
    if len(words) < 30:
        return None
    sentences = max(1, len(re.findall(r"[.!?¡¿…]+", text)))
    syllables = sum(max(1, len(_SYL.findall(w))) for w in words)
    wps, spw = len(words) / sentences, syllables / len(words)
    if (lang or "es").lower().startswith("es"):
        score, formula = 206.84 - 60 * spw - 1.02 * wps, "Fernández-Huerta"
    else:
        score, formula = 206.835 - 1.015 * wps - 84.6 * spw, "Flesch"
    score = max(0, min(100, round(score)))
    label = ("Muy fácil" if score >= 90 else "Fácil" if score >= 80 else "Bastante fácil" if score >= 70 else
             "Normal" if score >= 60 else "Algo difícil" if score >= 50 else "Difícil" if score >= 30 else "Muy difícil")
    return {"score": score, "label": label, "formula": formula, "words_per_sentence": round(wps, 1)}


def pdf_properties(data: bytes) -> dict:
    """Title, author, dates and page count read from the PDF's Info dictionary / XMP (no extra library)."""
    head = data[:2_000_000]
    out: dict = {"bytes": len(data), "valid": data[:5] == b"%PDF-"}
    if not out["valid"]:
        return out
    out["version"] = data[5:8].decode("latin-1", "replace")
    def field(name: str) -> str | None:
        m = re.search(rb"/" + name.encode() + rb"\s*\((.*?)(?<!\\)\)", head, re.S)
        if m:
            raw = m.group(1)
            if raw.startswith(b"\xfe\xff"):
                return raw[2:].decode("utf-16-be", "replace").strip() or None
            return raw.decode("latin-1", "replace").replace("\\(", "(").replace("\\)", ")").strip() or None
        m = re.search(rb"<(?:dc:)?" + name.lower().encode() + rb"[^>]*>(?:\s*<rdf:\w+>\s*<rdf:li[^>]*>)?([^<]+)", head, re.I)
        return m.group(1).decode("utf-8", "replace").strip() if m else None
    for key, name in (("title", "Title"), ("author", "Author"), ("subject", "Subject"), ("creator", "Creator"),
                      ("producer", "Producer"), ("created", "CreationDate"), ("modified", "ModDate")):
        out[key] = field(name)
    for key in ("created", "modified"):
        m = re.match(r"D:(\d{4})(\d{2})(\d{2})", out.get(key) or "")
        if m:
            out[key] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    counts = [int(x) for x in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data[:50_000_000])]
    out["pages"] = max(counts) if counts else len(re.findall(rb"/Type\s*/Page\b", data)) or None
    return out


NON_DESCRIPTIVE = {"aquí", "aqui", "clic aquí", "clic aqui", "haz clic aquí", "haga clic aquí", "pincha aquí", "pulsa aquí",
                   "leer más", "leer mas", "ver más", "ver mas", "más", "mas", "más info", "más información", "saber más",
                   "click here", "here", "read more", "more", "learn more", "link", "enlace", "este enlace", "web", "info"}


def custom_data(soup: BeautifulSoup, source: str, searches: list[dict], extractions: list[dict]) -> dict:
    """Custom search (occurrences in the HTML source) and custom extraction (CSS selector or regex)."""
    out: dict = {"search": {}, "extract": {}}
    low = source.lower()
    for s in searches or []:
        out["search"][s["name"]] = low.count(s["text"].lower())
    for e in extractions or []:
        try:
            if e["type"] == "xpath":
                from lxml import html as lxml_html
                parser = lxml_html.HTMLParser(encoding="utf-8")
                result = lxml_html.fromstring(source.encode("utf-8", "replace"), parser=parser).xpath(e["expr"])
                result = result if isinstance(result, list) else [result]
                values = [_clean(r.text_content() if hasattr(r, "text_content") else str(r)) for r in result[:20]]
            elif e["type"] == "regex":
                values = []
                for m in re.finditer(e["expr"], source):
                    values.append(m.group(1) if m.groups() else m.group(0))
                    if len(values) >= 20:
                        break
            else:
                values = [_clean(el.get_text(" ")) or _clean(el.get("content") or el.get("href") or el.get("src") or "")
                          for el in soup.select(e["expr"], limit=20)]
        except Exception:  # noqa: BLE001 - bad expressions are validated earlier; never break a crawl
            values = []
        out["extract"][e["name"]] = [v[:500] for v in values if v]
    return out


def parse_html(html: str | bytes, url: str, encoding: str | None = None,
               searches: list[dict] | None = None, extractions: list[dict] | None = None) -> dict:
    """Extract title, meta, headings, canonical, robots, links, images, structured data and text."""
    if isinstance(html, bytes):
        soup = BeautifulSoup(html, "html.parser", from_encoding=encoding)
        source = html.decode(soup.original_encoding or encoding or "utf-8", errors="replace")
    else:
        soup = BeautifulSoup(html, "html.parser")
        source = html
    custom = custom_data(soup, source, searches or [], extractions or []) if (searches or extractions) else {}
    a11y = a11y_checks(soup)
    amp = amp_checks(soup, source)
    refresh = None
    for m in soup.find_all("meta", attrs={"http-equiv": True}):
        if m["http-equiv"].strip().lower() == "refresh" and m.get("content"):
            mt = re.match(r"\s*(\d+)?\s*[;,]?\s*(?:url\s*=\s*)?['\"]?([^'\"]*)", m["content"], re.I)
            if mt:
                target = normalize(mt.group(2).strip(), url) if mt.group(2).strip() else None
                refresh = {"delay": int(mt.group(1) or 0), "target": target}
            break
    rdfa = sorted({_clean(t.get("typeof")) for t in soup.find_all(attrs={"typeof": True})})

    # validation & structure signals (html.parser keeps the document as written)
    heads = soup.find_all("head")
    titles = soup.find_all("title")
    head = heads[0] if heads else None
    invalid_in_head = sorted({el.name for el in head.find_all(recursive=False)
                              if el.name not in ("title", "meta", "link", "script", "style", "base", "noscript", "template")}) if head else []
    validation = {
        "doctype": source.lstrip()[:9].lower() == "<!doctype",
        "head_count": len(heads),
        "body_count": len(soup.find_all("body")),
        "title_count": len(titles),
        "title_outside_head": any(t.find_parent("head") is None and t.find_parent("svg") is None for t in titles),
        "invalid_in_head": invalid_in_head,
    }
    headings = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
    h1_alt = any(not _clean(h.get_text()) and any((img.get("alt") or "").strip() for img in h.find_all("img"))
                 for h in soup.find_all("h1"))
    rel_link = lambda rel: next((normalize(l["href"], url) for l in soup.find_all("link", href=True)
                                 if rel in [r.lower() for r in (l.get("rel") or [])]), None)

    base_url = url
    base_tag = soup.find("base", href=True)
    if base_tag:
        base_url = normalize(base_tag["href"], url) or url

    title_tag = soup.find("title")
    title = _clean(title_tag.get_text()) if title_tag else None

    def meta(name: str) -> str | None:
        values = [
            _clean(m.get("content"))
            for m in soup.find_all("meta", attrs={"name": True})
            if m["name"].strip().lower() == name and m.get("content") is not None
        ]
        return ", ".join(v for v in values if v) or (None if not values else "")

    description = meta("description")
    robots = ", ".join(v for v in (meta("robots"), meta("googlebot")) if v) or None
    desc_count = sum(1 for m in soup.find_all("meta", attrs={"name": True}) if m["name"].strip().lower() == "description")
    extra = {
        "meta_keywords": meta("keywords") or "",
        "viewport": meta("viewport"),
        "next": rel_link("next"),
        "prev": rel_link("prev"),
        "headings": headings[:200],
        "h1_alt": h1_alt,
        "desc_count": desc_count,
        "validation": validation,
        "a11y": a11y,
        "amp": amp,
        "meta_refresh": refresh,
    }

    canonicals = [
        normalize(l.get("href"), base_url)
        for l in soup.find_all("link", href=True)
        if "canonical" in [r.lower() for r in (l.get("rel") or [])]
    ]
    canonicals = [c for c in canonicals if c]

    h1 = page_headings(soup, "h1")
    h2 = page_headings(soup, "h2")

    links = []
    for a in soup.find_all("a", href=True):
        href = normalize(a["href"], base_url)
        if not href:
            continue
        rel = [r.lower() for r in (a.get("rel") or [])]
        links.append({
            "href": href,
            "anchor": _clean(a.get_text(" ")) or _clean(a.get("title") or "") or (
                "[img] " + _clean(a.find("img").get("alt") or "") if a.find("img") else ""),
            "nofollow": "nofollow" in rel,
            "rel": " ".join(r for r in rel if r in ("nofollow", "sponsored", "ugc", "noopener", "noreferrer")),
            "fragment": a["href"].split("#", 1)[1].strip() if "#" in a["href"] else "",
        })

    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        images.append({"src": normalize(src, base_url) or src, "alt": img.get("alt")})

    # Sub-resources with where they were found (img, srcset, og:image, schema, favicon, css, script…).
    res_via: dict[str, set[str]] = {}

    def add_res(raw: str | None, via: str) -> None:
        u = normalize((raw or "").strip(), base_url) if raw and not raw.strip().startswith("data:") else None
        if u:
            res_via.setdefault(u, set()).add(via)

    for img in soup.find_all("img"):
        add_res(img.get("src"), "img")
        add_res(img.get("data-src"), "img (lazy)")
        for u in _srcset(img.get("srcset")) + _srcset(img.get("data-srcset")):
            add_res(u, "srcset")
    for el in soup.find_all("source"):
        add_res(el.get("src"), "source")
        for u in _srcset(el.get("srcset")) + _srcset(el.get("data-srcset")):
            add_res(u, "srcset")
    for tag in ("script", "iframe", "video", "audio", "embed"):
        for el in soup.find_all(tag, src=True):
            add_res(el["src"], tag)
    for el in soup.find_all("video", poster=True):
        add_res(el["poster"], "poster")
    for l in soup.find_all("link"):
        rels = {r.lower() for r in (l.get("rel") or [])}
        href = l.get("href")
        if "stylesheet" in rels:
            add_res(href, "css")
        if rels & {"icon", "apple-touch-icon", "apple-touch-icon-precomposed", "mask-icon"}:
            add_res(href, "favicon")
        if "manifest" in rels:
            add_res(href, "manifest")
        if "preload" in rels or "prefetch" in rels:
            add_res(href, "preload")
            for u in _srcset(l.get("imagesrcset")):
                add_res(u, "srcset")
    for m in soup.find_all("meta", content=True):
        key = (m.get("property") or m.get("name") or "").strip().lower()
        if key in ("og:image", "og:image:url", "og:image:secure_url"):
            add_res(m["content"], "og:image")
        elif key in ("twitter:image", "twitter:image:src"):
            add_res(m["content"], "twitter:image")
        elif key in ("msapplication-tileimage",):
            add_res(m["content"], "favicon")

    jsonld_types: list[str] = []
    jsonld_invalid = 0
    jsonld_count = 0
    jsonld_blocks = []
    for script in soup.find_all("script", attrs={"type": True}):
        if script["type"].strip().lower() != "application/ld+json":
            continue
        jsonld_count += 1
        try:
            block = json.loads(script.string or script.get_text() or "")
            jsonld_blocks.extend(block if isinstance(block, list) else [block])
            _jsonld_types(block, jsonld_types)
        except (ValueError, TypeError):
            jsonld_invalid += 1
    extra["schema_problems"] = validate_schema(jsonld_blocks)[:50]
    for u in _schema_media(jsonld_blocks):
        add_res(u, "schema")
    microdata = sorted({_clean(t.get("itemtype")) for t in soup.find_all(attrs={"itemtype": True})})

    hreflang = []
    for l in soup.find_all("link", href=True, hreflang=True):
        if "alternate" in [r.lower() for r in (l.get("rel") or [])]:
            href = normalize(l["href"], base_url)
            if href:
                hreflang.append({"lang": l["hreflang"].strip().lower(), "href": href})

    resources = [{"url": u, "via": sorted(v)} for u, v in res_via.items()]

    html_tag = soup.find("html")
    lang = html_tag.get("lang") if html_tag else None

    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    body = soup.body or soup
    full_text = _clean(body.get_text(" "))
    from .localseo import extract_local
    extra["local"] = extract_local(soup, full_text, jsonld_blocks, microdata)  # before nav/footer are stripped
    forms = soup.find_all("form")
    extra["forms"] = {
        "count": len(forms),
        "insecure": [normalize(f.get("action") or "", url) or "" for f in forms
                     if url.startswith("https://") and (f.get("action") or "").strip().lower().startswith("http://")][:10],
        "password_on_http": url.startswith("http://") and any(f.find("input", attrs={"type": "password"}) for f in forms),
    }
    extra["text_ratio"] = round(100 * len(full_text) / max(1, len(source)), 1)
    text, area = content_text(soup)
    word_count, content_hash = text_stats(text)
    extra["words_total"] = text_stats(full_text)[0]
    extra["content_area"] = area
    extra["readability"] = readability(text, lang)
    extra["text_lang"] = text_language(text)

    return {
        "title": title,
        "meta_description": description,
        "meta_robots": robots,
        "canonical": canonicals[0] if canonicals else None,
        "canonical_count": len(set(canonicals)),
        "h1": h1,
        "h2": h2,
        "links": links,
        "images": images,
        "structured": {"jsonld_count": jsonld_count, "jsonld_types": sorted(set(jsonld_types)), "microdata": microdata,
                       "rdfa": rdfa},
        "jsonld_invalid": jsonld_invalid,
        "lang": lang,
        "extra": extra,
        "custom": custom,
        "hreflang": hreflang,
        "resources": resources,
        "word_count": word_count,
        "content_hash": content_hash,
        "text_excerpt": text[:30000],
    }
