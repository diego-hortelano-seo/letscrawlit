"""Static validators: hreflang codes, Schema.org rules (Google rich-result requirements), AMP basics,
basic WCAG checks and spelling. Pure functions over parsed HTML / data."""
from __future__ import annotations

import functools
import re
import threading
from collections import Counter

# ISO 639-1 language codes and ISO 3166-1 alpha-2 region codes (+ hreflang special value).
LANGS = set("""aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz
ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv
ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn
no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te
tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu""".split())
REGIONS = set("""ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw
by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg eh er es et fi fj fk fm fo fr ga
gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh
ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my
mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh
si sj sk sl sm sn so sr ss st sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug um us uy uz va vc ve vg
vi vn vu wf ws ye yt za zm zw""".split())


def hreflang_code_error(code: str) -> str | None:
    """None if valid ('es', 'es-ES', 'zh-Hant-TW', 'x-default'), else the reason."""
    c = (code or "").strip().lower().replace("_", "-")
    if c == "x-default":
        return None
    if "_" in (code or ""):
        return "usa guion bajo; debe ser guion (es-ES)"
    parts = c.split("-")
    if parts[0] not in LANGS:
        if parts[0] in REGIONS and len(parts) == 1:
            return f"«{code}» parece un país, no un idioma (usa p. ej. es-{parts[0].upper()})"
        return f"«{parts[0]}» no es un código de idioma ISO 639-1"
    rest = parts[1:]
    if rest and len(rest[0]) == 4:  # script subtag (Hant, Latn)
        rest = rest[1:]
    if rest:
        if len(rest) > 1 or rest[0] not in REGIONS:
            return f"«{'-'.join(parts[1:])}» no es un código de región ISO 3166-1"
    return None


# --- Schema.org validation (required / recommended properties used by Google rich results) ------
SCHEMA_RULES: dict[str, tuple[list, list]] = {
    "Organization": (["name"], ["url", "logo", "sameAs"]),
    "LocalBusiness": (["name", "address"], ["telephone", "openingHoursSpecification", "geo", "url", "image", "priceRange"]),
    "Locksmith": (["name", "address"], ["telephone", "openingHoursSpecification", "geo", "url", "image"]),
    "HomeAndConstructionBusiness": (["name", "address"], ["telephone", "openingHoursSpecification", "url", "image"]),
    "Product": (["name"], ["image", "description", "offers|review|aggregateRating", "brand", "sku"]),
    "Offer": (["price|priceSpecification"], ["priceCurrency", "availability", "url"]),
    "AggregateRating": (["ratingValue", "ratingCount|reviewCount"], ["bestRating"]),
    "Review": (["author", "reviewRating"], ["datePublished", "itemReviewed"]),
    "Article": (["headline"], ["image", "datePublished", "author", "dateModified"]),
    "NewsArticle": (["headline"], ["image", "datePublished", "author"]),
    "BlogPosting": (["headline"], ["image", "datePublished", "author"]),
    "BreadcrumbList": (["itemListElement"], []),
    "FAQPage": (["mainEntity"], []),
    "Question": (["name", "acceptedAnswer"], []),
    "HowTo": (["name", "step"], ["image", "totalTime"]),
    "Event": (["name", "startDate", "location"], ["endDate", "eventStatus", "image", "offers", "organizer"]),
    "Recipe": (["name", "image"], ["recipeIngredient", "recipeInstructions", "author", "aggregateRating"]),
    "VideoObject": (["name", "thumbnailUrl", "uploadDate"], ["description", "contentUrl|embedUrl", "duration"]),
    "JobPosting": (["title", "description", "datePosted", "hiringOrganization", "jobLocation"], ["validThrough", "baseSalary"]),
    "WebSite": (["name|url"], ["potentialAction"]),
    "Person": (["name"], ["url", "sameAs"]),
    "Service": (["name|serviceType"], ["provider", "areaServed"]),
}


def _has(node: dict, spec: str) -> bool:
    return any(node.get(k) not in (None, "", [], {}) for k in spec.split("|"))


def _walk(node, out: list, parent: tuple = ()):
    if isinstance(node, list):
        for n in node:
            _walk(n, out, parent)
    elif isinstance(node, dict):
        here = parent
        if "@type" in node:
            out.append((node, parent))
            here = tuple(node["@type"] if isinstance(node["@type"], list) else [node["@type"]])
        for k, v in node.items():
            if k != "@context":
                _walk(v, out, here)


def validate_schema(blocks: list) -> list[dict]:
    """Validate parsed JSON-LD blocks. Returns [{'type', 'level': 'error'|'warning', 'message'}]."""
    nodes: list = []
    for b in blocks:
        _walk(b, nodes)
    problems = []
    for node, parent in nodes:
        types = node["@type"] if isinstance(node["@type"], list) else [node["@type"]]
        for t in types:
            rule = SCHEMA_RULES.get(str(t))
            if not rule:
                continue
            required, recommended = rule
            if t == "Offer" and "Product" not in parent:  # Google asks for the price only on product offers
                required, recommended = [], required + recommended
            for spec in required:
                if not _has(node, spec):
                    problems.append({"type": t, "level": "error", "message": f"{t}: falta la propiedad obligatoria «{spec.replace('|', '» o «')}»"})
            for spec in recommended:
                if not _has(node, spec):
                    problems.append({"type": t, "level": "warning", "message": f"{t}: falta la propiedad recomendada «{spec.replace('|', '» o «')}»"})
    if any("@context" not in b for b in blocks if isinstance(b, dict)):
        problems.append({"type": "", "level": "error", "message": "Bloque JSON-LD sin @context (debe ser https://schema.org)"})
    return problems


# --- AMP -----------------------------------------------------------------------------------------

def amp_checks(soup, source: str) -> dict:
    html = soup.find("html")
    is_amp = bool(html and (html.has_attr("amp") or html.has_attr("⚡")))
    amphtml = next((l.get("href") for l in soup.find_all("link", href=True)
                    if "amphtml" in [r.lower() for r in (l.get("rel") or [])]), None)
    errors = []
    if is_amp:
        if not soup.find("script", src=re.compile(r"^https://cdn\.ampproject\.org/v0\.js")):
            errors.append("Falta <script async src=\"https://cdn.ampproject.org/v0.js\">")
        if not soup.find("meta", charset=True):
            errors.append("Falta <meta charset>")
        if not soup.find("meta", attrs={"name": "viewport"}):
            errors.append("Falta meta viewport")
        if not any("canonical" in [r.lower() for r in (l.get("rel") or [])] for l in soup.find_all("link")):
            errors.append("Falta link rel=canonical")
        if not soup.find("style", attrs={"amp-boilerplate": True}):
            errors.append("Falta <style amp-boilerplate>")
        bad = [s.get("src") for s in soup.find_all("script", src=True)
               if not s["src"].startswith("https://cdn.ampproject.org/")]
        if bad or soup.find("script", attrs={"type": lambda t: t in (None, "text/javascript")}, src=False):
            errors.append("Contiene JavaScript propio (no permitido en AMP)")
        if soup.find("img"):
            errors.append("Usa <img> en lugar de <amp-img>")
    # separate mobile version: <link rel="alternate" media="only screen and (max-width: 640px)" href="https://m.…">
    mobile_alt = next((l.get("href") for l in soup.find_all("link", href=True)
                       if "alternate" in [r.lower() for r in (l.get("rel") or [])] and not l.get("hreflang")
                       and re.search(r"max-width|handheld", l.get("media") or "", re.I)), None)
    return {"is_amp": is_amp, "amphtml": amphtml, "errors": errors, "mobile_alt": mobile_alt}


# --- basic WCAG checks (automatable subset) ----------------------------------------------------------

def a11y_checks(soup) -> dict:
    labels_for = {l.get("for") for l in soup.find_all("label") if l.get("for")}
    inputs = [i for i in soup.find_all(["input", "select", "textarea"])
              if (i.get("type") or "").lower() not in ("hidden", "submit", "button", "reset", "image")]
    unlabeled = [i for i in inputs if not (i.get("id") in labels_for or i.get("aria-label") or i.get("aria-labelledby")
                                           or i.find_parent("label") or i.get("title"))]
    buttons = [b for b in soup.find_all("button")
               if not (b.get_text(strip=True) or b.get("aria-label") or b.get("title")
                       or any((img.get("alt") or "").strip() for img in b.find_all("img")))]
    links = [a for a in soup.find_all("a", href=True)
             if not (a.get_text(strip=True) or a.get("aria-label") or a.get("title")
                     or any((img.get("alt") or "").strip() for img in a.find_all("img")))]
    empty_headings = [h.name for h in soup.find_all(re.compile(r"^h[1-6]$"))
                      if not h.get_text(strip=True) and not h.find("img", alt=True)]
    ids = Counter(el.get("id") for el in soup.find_all(id=True))
    dup_ids = sorted(i for i, n in ids.items() if n > 1)
    iframes = [f for f in soup.find_all("iframe") if not (f.get("title") or "").strip()]
    tabindex = [el for el in soup.find_all(attrs={"tabindex": True})
                if str(el.get("tabindex")).strip().lstrip("-").isdigit() and int(el.get("tabindex")) > 0]
    autoplay = soup.find_all(["video", "audio"], autoplay=True)
    images_no_alt = [i for i in soup.find_all("img") if i.get("alt") is None and i.get("role") != "presentation"]
    return {
        "unlabeled_inputs": len(unlabeled), "unnamed_buttons": len(buttons), "unnamed_links": len(links),
        "empty_headings": len(empty_headings), "duplicate_ids": dup_ids[:20], "iframes_no_title": len(iframes),
        "positive_tabindex": len(tabindex), "autoplay_media": len(autoplay), "images_no_alt": len(images_no_alt),
    }


A11Y_LABELS = {
    "images_no_alt": ("Imágenes sin texto alternativo", "1.1.1"),
    "unlabeled_inputs": ("Campos de formulario sin etiqueta", "1.3.1 / 3.3.2"),
    "unnamed_buttons": ("Botones sin nombre accesible", "4.1.2"),
    "unnamed_links": ("Enlaces sin texto accesible", "2.4.4"),
    "empty_headings": ("Encabezados vacíos", "1.3.1"),
    "duplicate_ids": ("IDs duplicados", "4.1.1"),
    "iframes_no_title": ("Iframes sin title", "4.1.2"),
    "positive_tabindex": ("tabindex positivo", "2.4.3"),
    "autoplay_media": ("Audio/vídeo con reproducción automática", "1.4.2"),
}


# --- spelling ------------------------------------------------------------------------------------------
_spell_cache: dict[str, object] = {}
_spell_lock = threading.Lock()
_SPELL_LANGS = {"es", "en", "fr", "pt", "de", "it", "nl", "ru", "ar", "lv", "eu", "fa"}
_SPELL_WORD = re.compile(r"\b[a-záéíóúüñàèìòùâêîôûçäëïöß]{3,}\b")


def _checker(lang: str):
    with _spell_lock:
        if lang not in _spell_cache:
            try:
                from spellchecker import SpellChecker
                _spell_cache[lang] = SpellChecker(language=lang)
            except Exception:  # noqa: BLE001 - dictionary not available
                _spell_cache[lang] = None
        return _spell_cache[lang]


# The bundled Spanish dictionary lists mostly base forms, so inflections are checked against their lemma.
_ES_IRREGULAR = set("""tiene tienen tienes tengo tenemos tenga tengan hay haya hace hacen hacemos hago haga puede pueden puedes
puedo podemos pueda quiere quieren quieres quiero queremos dice dicen digo decimos va van vamos voy vas da dan damos doy
es son somos eres soy está están estamos estás estoy fue fueron era eran sea sean será serán sería tuvo hizo pudo dijo
viene vienen vengo venimos sabe saben sé sabemos pone ponen pongo sale salen salgo sigue siguen sigo pide piden ofrece
ofrecen ofrezco conoce conocen conozco todos todas cuál cuáles días meses años veces esté estés estén estemos sepa sepan
sabes hayan vaya vayan vayas dé den web online email blog wifi según periodo élite
he has ha han hemos habéis había habías habían habíamos hubo hubiera hubieras hubieran hubiese hubiesen habría habrían
estuvo estuvieron estuviera estuvieran tuvo tuvieron tuviera tuvieran hizo hicieron hiciera hicieran pudo pudieron pudiera
pudieran dijo dijeron dijera dijeran fuera fueran fuese fuesen fui fuimos iba iban íbamos ibas puso pusieron pusiera quiso
quisiera supo supiera vino vinieron viniera trajo trajeron trajera condujo produjo produjera tenía tenían ponía ponían
huele huelen huelo veía veían vio vieron vea vean veo ves oye oyen oigo oiga caiga caigan traiga traigan valga valgan
salga salgan ponga pongan venga vengan diga digan haga hagan tenga tengan quepa dará darán daría darían
haz pon ten sal ven di ve oye ríe ríen huye huyen etc vais sois estáis dais""".split())
# Prefixed forms of irregular verbs: «contenga» (contener), «mantiene», «obtuvo», «propone», «deshace», «previene».
_ES_PREFIXES = ("contra", "inter", "sobre", "super", "entre", "man", "con", "com", "obs", "ob", "det", "de", "re", "pro",
                "dis", "sup", "des", "pre", "ex", "im", "in", "su", "ab")
# Future and conditional of verbs whose stem changes: «podrá», «habría», «harán», «dirías», «tendremos».
_ES_FUTURE_STEMS = {"podr": "poder", "habr": "haber", "har": "hacer", "dir": "decir", "tendr": "tener", "pondr": "poner",
                    "saldr": "salir", "vendr": "venir", "sabr": "saber", "querr": "querer", "cabr": "caber", "valdr": "valer"}
_ES_FUTURE_ENDINGS = ("íamos", "íais", "emos", "éis", "ían", "ías", "ía", "án", "ás", "á", "é")
_CLITICS = ("selos", "selas", "selo", "sela", "noslo", "melo", "telo", "los", "las", "les", "nos", "se", "lo", "la", "le", "me", "te")
_PLAIN = str.maketrans("áéíóú", "aeiou")
_ES_SUFFIXES = [("áis", ["ar", "er"]), ("éis", ["er", "ar"]), ("ís", ["ir"]), ("aríamos", ["ar"]), ("eríamos", ["er"]), ("iríamos", ["ir"]), ("arían", ["ar"]), ("erían", ["er"]),
                ("irían", ["ir"]), ("arías", ["ar"]), ("erías", ["er"]), ("irías", ["ir"]), ("aría", ["ar"]), ("ería", ["er"]),
                ("iría", ["ir"]), ("arás", ["ar"]), ("erás", ["er"]), ("irás", ["ir"]), ("aré", ["ar"]), ("eré", ["er"]),
                ("iré", ["ir"]), ("abas", ["ar"]), ("aba", ["ar"]), ("ías", ["er", "ir"]), ("ía", ["er", "ir"]),
                ("aran", ["ar"]), ("ieran", ["er", "ir"]), ("ábamos", ["ar"]), ("íamos", ["er", "ir"]), ("aremos", ["ar"]), ("eremos", ["er"]), ("iremos", ["ir"]),
                ("amos", ["ar"]), ("emos", ["er"]), ("imos", ["ir"]), ("aban", ["ar"]), ("ían", ["er", "ir"]),
                ("aron", ["ar"]), ("ieron", ["er", "ir"]), ("ando", ["ar"]), ("iendo", ["er", "ir"]),
                ("ados", ["ar", "ado"]), ("adas", ["ar", "ada"]), ("idos", ["er", "ir", "ido"]), ("idas", ["er", "ir", "ida"]),
                ("ado", ["ar"]), ("ada", ["ar"]), ("ido", ["er", "ir"]), ("ida", ["er", "ir"]),
                ("arán", ["ar"]), ("erán", ["er"]), ("irán", ["ir"]), ("ará", ["ar"]), ("erá", ["er"]), ("irá", ["ir"]),
                ("mente", ["", "o", "e"]), ("ces", ["z"]), ("quen", ["car"]), ("ques", ["car"]), ("que", ["car"]), ("guen", ["gar"]), ("gues", ["gar"]),
                ("gue", ["gar"]), ("cen", ["zar"]), ("ce", ["zar"]), ("jan", ["ger", "gir"]), ("jas", ["ger", "gir"]),
                ("ja", ["ger", "gir"]), ("jo", ["ger", "gir"]), ("ídas", ["er", "ir"]), ("ídos", ["er", "ir"]), ("ída", ["er", "ir"]),
                ("ído", ["er", "ir"]), ("iones", ["ión"]), ("es", ["", "e"]), ("s", [""]),
                ("an", ["ar", "a"]), ("en", ["er", "ir", "e"]), ("as", ["ar", "a"]), ("a", ["ar", "o"]), ("e", ["er", "ir", "ar"]),
                ("o", ["ar", "er", "ir", "a"])]


def _es_known(word: str, sc, _depth: int = 0) -> bool:
    """Spanish inflections against the dictionary of base forms: plurals, verb forms (also with the stem
    changes of «requiere», «puede», «pide»), accents of «dónde» or «está», and pronouns glued to verbs
    («usarse», «reformarlo», «dándole»)."""
    if word in _ES_IRREGULAR or word in sc or word.translate(_PLAIN) in sc:
        return True
    for end in _ES_FUTURE_ENDINGS:
        if word.endswith(end):
            stem = word[: -len(end)]
            for p in ("",) + _ES_PREFIXES:
                if stem.startswith(p) and stem[len(p):] in _ES_FUTURE_STEMS:
                    return True
    if _depth == 0:
        for p in _ES_PREFIXES:
            if word.startswith(p) and len(word) - len(p) >= 3 and word[len(p):] in _ES_IRREGULAR:
                return True
    if _depth == 0:
        for c in _CLITICS:
            if word.endswith(c) and len(word) - len(c) >= 2:
                base = word[: -len(c)].translate(_PLAIN)
                if base in _ES_IRREGULAR or (base.endswith(("ar", "er", "ir")) and base in sc) or (len(base) >= 4 and (base in sc or _es_known(base, sc, 1))):
                    return True
    if _verb_known(word, sc):
        return True
    for stem_from, stem_to in (("ie", "e"), ("ue", "o"), ("i", "e")):  # requiere → requer-ir, puede → pod-er
        k = word.rfind(stem_from)
        if 0 < k < len(word) - len(stem_from):
            if _verb_known(word[:k] + stem_to + word[k + len(stem_from):], sc):
                return True
    return False


_ES_VERB_ENDINGS = [("a", ["ir", "er"]), ("as", ["er", "ir"]), ("an", ["er", "ir"]), ("es", ["er", "ir", "ar"]), ("e", ["er", "ir", "ar"]),
                    ("en", ["ar"]), ("emos", ["ar"]), ("amos", ["er", "ir"]), ("é", ["ar"]), ("ó", ["ar"]), ("í", ["er", "ir"]),
                    ("ió", ["er", "ir"]), ("aste", ["ar"]), ("iste", ["er", "ir"]), ("ara", ["ar"]), ("iera", ["er", "ir"]),
                    ("ase", ["ar"]), ("iese", ["er", "ir"])]


def _verb_known(word: str, sc) -> bool:
    for suffix, endings in _ES_VERB_ENDINGS + _ES_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 1:
            stem = word[: -len(suffix)]
            for base in {stem + e for e in endings} | {stem.translate(_PLAIN) + e for e in endings}:
                if base in sc:
                    return True
    return False


_SLIP_GROUPS = [set("bv"), set("csz kq".replace(" ", "")), set("gj"), set("yl"), set("aá"), set("eé"), set("ií"), set("oó"),
                set("uúü"), set("nñ"), set("mn")]


def _typical_slip(word: str, cand: str) -> bool:
    """True when `cand` differs from `word` by one inserted/deleted letter, two swapped letters, or one letter
    swapped for its usual mix-up (b/v, c/s/z/k/q, g/j, y/l, m/n, a missing accent, n/ñ)."""
    if len(word) != len(cand):
        return True  # one letter more or less: «presupesto», «trabajr», «aver»
    diff = [i for i, (a, b) in enumerate(zip(word, cand)) if a != b]
    if len(diff) == 2 and diff[1] == diff[0] + 1 and word[diff[0]] == cand[diff[1]] and word[diff[1]] == cand[diff[0]]:
        return True  # swapped letters
    return len(diff) == 1 and any(word[diff[0]] in g and cand[diff[0]] in g for g in _SLIP_GROUPS)


def _near_checker():
    with _spell_lock:
        if "es-near" not in _spell_cache:
            try:
                from spellchecker import SpellChecker
                _spell_cache["es-near"] = SpellChecker(language="es", distance=1)
            except Exception:  # noqa: BLE001
                _spell_cache["es-near"] = None
        return _spell_cache["es-near"]


def spelling(text: str, lang: str | None, custom_words: set[str] | None = None, limit: int = 60) -> dict | None:
    """Words not found in the dictionary for the page language. Skips capitalised words (proper nouns),
    words with digits and the user's custom dictionary. Returns None if no dictionary for the language.
    Site-wide vocabulary (words repeated across many pages) is filtered later, at the end of the crawl."""
    code = (lang or "es").split("-")[0].lower()
    if code not in _SPELL_LANGS:
        return None
    sc = _checker(code)
    if sc is None:
        return None
    words = []
    for raw in re.findall(r"\S+", text or ""):
        w = raw.strip(".,;:!?¡¿()[]{}\"'«»“”…")
        if not w or not w.islower() or not _SPELL_WORD.fullmatch(w):
            continue
        words.append(w)
    custom = {c.lower() for c in (custom_words or set())}
    unknown = {w for w in sc.unknown(set(words)) if w not in custom}
    if code == "es":
        unknown = {w for w in unknown if not _es_known(w, sc)}
        # The bundled Spanish dictionary is small (it lacks «formato», «comercial», «detección»…), so a word missing
        # from it is often just a valid word. Report only real-looking typos: one letter away from a common word,
        # by a missing/extra/swapped letter or a typical Spanish slip (b/v, c/s/z/k, g/j, y/l, m/n, accents, ñ).
        en = _checker("en")  # English words inside Spanish text («the», «user», «body») are not typos
        if en is not None:
            unknown = {w for w in unknown if w not in en}
        near = _near_checker()
        if near is not None:
            unknown = {w for w in unknown if any(_typical_slip(w, c) and sc.word_usage_frequency(c) >= 3e-7
                                                 for c in (near.candidates(w) or set()) - {w})}
    counts = Counter(w for w in words if w in unknown)
    top = [w for w, _ in counts.most_common(limit)]
    return {"lang": code, "count": len(unknown), "words": top}


@functools.lru_cache(maxsize=20000)
def _still_typo(word: str, lang: str) -> bool:
    """Re-check a stored word with the current rules, so audits saved with an older, stricter checker
    stop flagging valid words («cumpla», «quién», «trabajáis»)."""
    res = spelling(word, lang)
    return res is None or word in res["words"]


def effective_spelling(sp: dict | None, vocab: set[str], recheck: bool = False) -> dict | None:
    """Drop words that the site uses on many pages (vocabulary, brands, local terms) and words that the
    current checker no longer considers typos (`recheck`, for audits saved by an older version)."""
    if not sp:
        return sp
    lang = sp.get("lang") or "es"
    words = [w for w in sp.get("words") or [] if w not in vocab and (not recheck or _still_typo(w, lang))]
    return {**sp, "words": words, "count": len(words)}
