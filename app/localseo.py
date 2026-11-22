"""Local SEO: business data (name, address, phone), contact signals and "service + city" checks.

`extract_local` runs on each page while parsing; `audit` runs once per crawl on the stored signals and returns
issues, per-URL columns (tab «SEO local») and a site checklist (client report). Pure functions, no network.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from urllib.parse import unquote, urlsplit

# schema.org types that describe a local business (plus anything ending in Business/Store/Contractor/Shop)
LOCAL_TYPES = {
    "LocalBusiness", "ProfessionalService", "HomeAndConstructionBusiness", "Locksmith", "Plumber", "Electrician",
    "GeneralContractor", "RoofingContractor", "HVACBusiness", "HousePainter", "MovingCompany", "Dentist", "Physician",
    "MedicalClinic", "LegalService", "Attorney", "Notary", "AccountingService", "RealEstateAgent", "Restaurant",
    "CafeOrCoffeeShop", "BarOrPub", "Bakery", "Hotel", "BeautySalon", "HairSalon", "DaySpa", "AutoRepair", "AutoDealer",
    "AutomotiveBusiness", "Store", "ExerciseGym", "SportsActivityLocation", "ChildCare", "Optician", "Pharmacy",
    "VeterinaryCare", "TravelAgency", "InsuranceAgency", "FinancialService", "EmergencyService", "Florist",
    "FoodEstablishment", "LodgingBusiness", "HealthAndBeautyBusiness", "EntertainmentBusiness", "DryCleaningOrLaundry",
    "SelfStorage", "AnimalShelter", "EmploymentAgency", "GovernmentOffice", "Library", "RecyclingCenter",
    "ShoppingCenter", "TouristInformationCenter", "InternetCafe", "ArchiveOrganization",
}
WHATSAPP = re.compile(r"(?:^|//)(?:wa\.me|api\.whatsapp\.com|web\.whatsapp\.com|chat\.whatsapp\.com)/|^whatsapp:", re.I)
MAP_EMBED = re.compile(r"google\.[a-z.]+/maps|maps\.google\.|openstreetmap\.org|bing\.com/maps|maps\.apple\.com|"
                       r"mapbox\.com|here\.com/", re.I)
MAP_LINK = re.compile(r"google\.[a-z.]+/maps|maps\.google\.|maps\.app\.goo\.gl|goo\.gl/maps|g\.page/|maps\.apple\.com|"
                      r"openstreetmap\.org", re.I)
# Spanish numbers: 9 digits starting 6-9, optional +34/0034, any grouping with spaces, dots or dashes
PHONE_ES = re.compile(r"(?<![\d/.,])(?:(?:\+|00)\s?34[\s.\-]?)?[6789](?:[\s.\-]?\d){8}(?![\d])")
CONTACT = re.compile(r"contact|contactar|contacto|presupuesto|donde-estamos|ubicacion|localizacion", re.I)
NON_SERVICE = (r"aviso-legal|legal|privacidad|privacy|cookies|politica|terminos|condiciones|contact|presupuesto|"
               r"/blog(?:/|$)|/noticias(?:/|$)|/novedades(?:/|$)|/tag/|/etiqueta/|/category/|/categoria/|/author/|/autor/|/page/\d|/feed|sitemap|"
               r"gracias|thank|carrito|cart|checkout|mi-cuenta|login|/wp-")


def norm_phone(raw: str) -> str:
    """'+34 985 12 34 56' / 'tel:0034985123456' -> '985123456'. Other countries keep all their digits."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0034") and len(digits) == 13:
        digits = digits[4:]
    elif digits.startswith("34") and len(digits) == 11:
        digits = digits[2:]
    return digits if len(digits) >= 6 else ""


def fmt_phone(digits: str) -> str:
    return f"{digits[:3]} {digits[3:6]} {digits[6:]}" if len(digits) == 9 else digits


def fold(text: str) -> str:
    """Lowercase without accents, so «Gijón», «gijon» and «GIJÓN» match."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def is_local_type(t: str) -> bool:
    t = (t or "").rsplit("/", 1)[-1]
    return t in LOCAL_TYPES or t.endswith(("Business", "Store", "Contractor", "Shop"))


def _addr(node) -> dict:
    if isinstance(node, list):
        node = node[0] if node else {}
    if isinstance(node, str):
        return {"street": node.strip()}
    if not isinstance(node, dict):
        return {}
    return {k: str(node.get(src) or "").strip() for k, src in (("street", "streetAddress"), ("locality", "addressLocality"),
                                                               ("postal", "postalCode"), ("region", "addressRegion"))
            if node.get(src)}


def _businesses(blocks: list) -> list[dict]:
    found = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            local = [str(t) for t in types if t and is_local_type(str(t))]
            if local:
                phone = node.get("telephone")
                phone = phone[0] if isinstance(phone, list) and phone else phone
                found.append({
                    "type": ", ".join(local), "name": str(node.get("name") or "").strip(),
                    "telephone": norm_phone(str(phone or "")), "address": _addr(node.get("address")),
                    "hours": bool(node.get("openingHours") or node.get("openingHoursSpecification")),
                    "geo": bool(node.get("geo")), "area": bool(node.get("areaServed")), "url": bool(node.get("url")),
                    "image": bool(node.get("image") or node.get("logo")), "price": bool(node.get("priceRange")),
                    "reviews": bool(node.get("aggregateRating") or node.get("review")),
                    "same_as": bool(node.get("sameAs")), "has_map": bool(node.get("hasMap")),
                })
            for k, v in node.items():
                if k != "@type":
                    walk(v)

    walk(blocks)
    return found


def extract_local(soup, full_text: str, jsonld_blocks: list, microdata: list[str]) -> dict:
    """Per-page local signals. Call with scripts removed but before nav/header/footer are stripped."""
    tel, wa, mail, map_links = [], [], [], False
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("tel:") or low.startswith("callto:"):
            n = norm_phone(unquote(href.split(":", 1)[1]))
            if n:
                tel.append(n)
        elif WHATSAPP.search(href):
            wa.append(href[:200])
        elif low.startswith("mailto:"):
            mail.append(href[7:].split("?")[0][:120])
        elif MAP_LINK.search(href):
            map_links = True
    embeds = any(MAP_EMBED.search(f.get("src") or f.get("data-src") or "") for f in soup.find_all("iframe"))
    phones = sorted({n for m in PHONE_ES.finditer(full_text or "") if (n := norm_phone(m.group(0)))})
    return {
        "tel": sorted(set(tel)), "phones": phones, "whatsapp": sorted(set(wa))[:5], "email": sorted(set(mail))[:5],
        "map_embed": embeds, "map_link": map_links,
        "biz": _businesses(jsonld_blocks)[:5],
        "biz_microdata": [t.rsplit("/", 1)[-1] for t in microdata if is_local_type(t)],
    }


# --- crawl-level audit ----------------------------------------------------------------------------------

def _cities(cfg: dict, bizs: list[dict]) -> list[str]:
    cities = [c.strip() for c in re.split(r"[,;\n]", cfg.get("local_city") or "") if c.strip()]
    if not cities:
        loc = Counter(b["address"].get("locality") for b in bizs if b["address"].get("locality"))
        cities = [loc.most_common(1)[0][0]] if loc else []
    return cities


def _city_in(text: str, cities: list[str], url: bool = False) -> bool:
    t = fold(unquote(text or ""))
    if url:
        t = re.sub(r"[-_/.]+", " ", t)
    return any(re.search(r"(?<!\w)" + re.escape(fold(c)) + r"(?!\w)", t) for c in cities)


def _nap_key(b: dict) -> tuple:
    a = b["address"]
    return (fold(b["name"]).strip(), b["telephone"], fold(a.get("street", "")).replace(" ", ""), a.get("postal", ""))


def audit(pages: list[dict], links: list[dict], start_url: str | None, cfg: dict | None = None) -> dict:
    """-> {"available", "issues": [(code, url, evidence)], "pages": {url: row fields}, "site": {...}, "checklist": [...]}"""
    cfg = cfg or {}
    html = [p for p in pages if p.get("status_code") == 200 and "html" in (p.get("content_type") or "")
            and not p.get("blocked_by_robots")]
    data = {p["url"]: (p.get("extra") or {}).get("local") for p in html}
    data = {u: d for u, d in data.items() if d is not None}
    result = {"available": bool(data), "issues": [], "pages": {}, "site": {}, "checklist": []}
    if not data or cfg.get("local_enabled") is False:
        return result
    by_url = {p["url"]: p for p in html}
    issues = result["issues"]
    home_url = start_url if start_url in data else next(iter(data))
    from .analysis import compute_indexability
    indexable = [u for u in data if compute_indexability(by_url[u])[0]]

    # --- phones ---------------------------------------------------------------------------------------
    shown = {u: set(d["phones"]) | set(d["tel"]) for u, d in data.items()}
    freq = Counter(n for s in shown.values() for n in s)
    main_phone = norm_phone(cfg.get("local_phone") or "") or (freq.most_common(1)[0][0] if freq else "")
    if indexable and not any(shown[u] for u in data):
        issues.append(("local_no_phone", home_url, "Ninguna página muestra un teléfono (ni en el texto ni en enlaces tel:). "
                                                    "Ponlo en la cabecera o el pie y saldrá en todas."))
    for u in indexable:
        d = data[u]
        not_clickable = sorted(set(d["phones"]) - set(d["tel"]))
        if not_clickable:
            issues.append(("local_phone_not_clickable", u, "Teléfono en texto sin enlace tel: " + ", ".join(fmt_phone(n) for n in not_clickable[:3])))
    if main_phone and freq.get(main_phone, 0) >= 3:
        rare = {n for n, c in freq.items() if n != main_phone and c <= max(1, len(data) // 5) and len(n) == 9}
        for u in sorted(data):
            odd = sorted(shown[u] & rare)
            if odd:
                issues.append(("local_phone_variant", u, f"Aparece {', '.join(fmt_phone(n) for n in odd)}; el teléfono habitual "
                                                         f"es {fmt_phone(main_phone)} ({freq[main_phone]} páginas)"))

    # --- business data (schema) -----------------------------------------------------------------------
    bizs = [(u, b) for u, d in data.items() for b in d.get("biz") or []]
    home_has_local = bool(data[home_url].get("biz") or data[home_url].get("biz_microdata"))
    if not home_has_local:
        issues.append(("local_no_schema", home_url, "La página de inicio no tiene datos estructurados LocalBusiness "
                                                     "(ni ninguno de sus subtipos: Locksmith, GeneralContractor, Plumber…)"))
    seen = set()
    for u, b in sorted(bizs, key=lambda x: x[0] != home_url):
        key = _nap_key(b)
        if key in seen:
            continue
        seen.add(key)
        missing = [label for ok, label in ((b["name"], "name"), (b["telephone"], "telephone"),
                                           (b["address"].get("street"), "address.streetAddress"),
                                           (b["address"].get("locality"), "address.addressLocality"),
                                           (b["address"].get("postal"), "address.postalCode"),
                                           (b["hours"], "openingHours"), (b["geo"], "geo"), (b["url"], "url"),
                                           (b["image"], "image/logo"), (b["area"], "areaServed")) if not ok]
        if missing:
            issues.append(("local_schema_incomplete", u, f"{b['type']} «{b['name'] or 'sin nombre'}»: falta " + ", ".join(missing)))
    variants = {_nap_key(b): (u, b) for u, b in bizs}
    if len(variants) > 1:
        desc = [f"{b['name'] or '¿nombre?'} · {fmt_phone(b['telephone']) or '¿tel?'} · "
                f"{b['address'].get('street') or '¿dirección?'} {b['address'].get('postal', '')} (en {u})" for u, b in variants.values()]
        issues.append(("local_nap_inconsistent", home_url, f"{len(variants)} versiones distintas del negocio: " + " / ".join(desc[:4])))
    want_name = (cfg.get("local_name") or "").strip()
    if want_name:
        wrong = sorted({b["name"] for _, b in bizs if b["name"] and fold(want_name) not in fold(b["name"])})
        if wrong:
            issues.append(("local_nap_inconsistent", home_url, f"El nombre configurado es «{want_name}» y el schema dice: "
                                                               + ", ".join(f"«{n}»" for n in wrong[:3])))
    schema_phones = {b["telephone"] for _, b in bizs if b["telephone"]}
    if main_phone and cfg.get("local_phone") and schema_phones and main_phone not in schema_phones:
        issues.append(("local_nap_inconsistent", home_url, f"El teléfono configurado es {fmt_phone(main_phone)} y el schema dice: "
                                                           + ", ".join(fmt_phone(n) for n in sorted(schema_phones))))
    for n in sorted(schema_phones):
        if not freq.get(n):
            issues.append(("local_schema_phone_hidden", home_url, f"El schema declara {fmt_phone(n)} pero no aparece en ninguna página"))

    # --- contact page, WhatsApp, map --------------------------------------------------------------------
    contact = [u for u in data if CONTACT.search(urlsplit(u).path) or CONTACT.search(fold(by_url[u].get("title") or "")[:30])]
    contact = sorted(contact, key=lambda u: (0 if "contact" in u.lower() else 1, len(u)))
    contact_url = contact[0] if contact else None
    if not contact_url:
        issues.append(("local_no_contact_page", home_url, "Ninguna URL rastreada parece una página de contacto (/contacto, «Contacto» en el título…)"))
    else:
        linking = {l["source_url"] for l in links if l["kind"] in ("a", "rendered") and l.get("internal") and l["target_url"] == contact_url}
        missing = sorted(u for u in indexable if u != contact_url and u not in linking)
        if missing:
            more = f" (+{len(missing) - 5} más)" if len(missing) > 5 else ""
            issues.append(("local_contact_not_linked", contact_url,
                           f"No la enlazan {len(missing)} de {len(indexable) - (contact_url in indexable)} páginas indexables: "
                           + ", ".join(missing[:5]) + more))
    wa_pages = [u for u, d in data.items() if d["whatsapp"]]
    if not wa_pages:
        issues.append(("local_no_whatsapp", home_url, "Ninguna página enlaza a WhatsApp (wa.me o api.whatsapp.com)"))
    map_pages = [u for u, d in data.items() if d["map_embed"] or d["map_link"]]
    if not map_pages:
        issues.append(("local_no_map", contact_url or home_url, "No hay mapa incrustado ni enlace a Google Maps en ninguna página"))

    # --- service + city -----------------------------------------------------------------------------------
    cities = _cities(cfg, [b for _, b in bizs])
    exclude = [p for p in (cfg.get("local_exclude") or "").splitlines() if p.strip()]
    non_service = re.compile(NON_SERVICE, re.I)
    city_stats = {"checked": 0, "title": 0, "h1": 0, "url": 0}
    for u in data:
        p = by_url[u]
        path = urlsplit(u).path
        service = u in indexable and u != home_url and not non_service.search(path) and not urlsplit(u).query and \
            not any(re.search(x, u) for x in exclude if _valid(x))
        row = {"local_phones": ", ".join(fmt_phone(n) for n in sorted(shown[u])),
               "local_tel": len(data[u]["tel"]),
               "local_clickable": ("sí" if not (set(data[u]["phones"]) - set(data[u]["tel"])) else "no") if shown[u] else "",
               "local_whatsapp": "sí" if data[u]["whatsapp"] else "no",
               "local_map": "incrustado" if data[u]["map_embed"] else "enlace" if data[u]["map_link"] else "no",
               "local_schema": ", ".join(sorted({b["type"] for b in data[u].get("biz") or []} | set(data[u].get("biz_microdata") or []))),
               "local_service": "sí" if service else "no", "local_city_title": "", "local_city_h1": "", "local_city_url": "",
               "local_contact_page": "sí" if u == contact_url else ""}
        if cities and (service or u == home_url):
            in_title = _city_in(p.get("title") or "", cities)
            h1 = " ".join(p.get("h1") or [])
            in_h1 = _city_in(h1, cities)
            in_url = _city_in(path, cities, url=True)
            row.update(local_city_title="sí" if in_title else "no", local_city_h1="sí" if in_h1 else "no",
                       local_city_url="sí" if in_url else "no")
            if service:
                city_stats["checked"] += 1
                city_stats["title"] += in_title
                city_stats["h1"] += in_h1
                city_stats["url"] += in_url
            where = [n for ok, n in ((in_title, "el título"), (in_h1, "el H1")) if not ok]
            if where:
                issues.append(("local_city_missing", u, f"«{' / '.join(cities[:3])}» no aparece en {' ni en '.join(where)}"
                               + ("" if in_url else " (tampoco en la URL)")
                               + (f". Título: «{(p.get('title') or '')[:70]}»" if not in_title else f". H1: «{h1[:70]}»")))
        result["pages"][u] = row

    result["site"] = {
        "home": home_url, "main_phone": fmt_phone(main_phone), "cities": cities, "contact_url": contact_url,
        "whatsapp_pages": len(wa_pages), "map_pages": len(map_pages), "pages": len(data), "indexable": len(indexable),
        "schema_types": sorted({b["type"] for _, b in bizs}), "business": next((b for u, b in bizs if u == home_url), bizs[0][1] if bizs else None),
        "city": city_stats, "phone_pages": sum(1 for u in indexable if shown[u]),
        "clickable_pages": sum(1 for u in indexable if shown[u] and not (set(data[u]["phones"]) - set(data[u]["tel"]))),
    }
    result["checklist"] = checklist(result)
    return result


def _valid(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def _in_pages(n: int) -> str:
    return "No hay" if not n else ("En 1 página" if n == 1 else f"En {n} páginas")


def checklist(result: dict) -> list[dict]:
    """Plain-language checklist for the client report: label, ok (True/False/None = not applicable), detail."""
    s = result["site"]
    codes = Counter(c for c, _, _ in result["issues"])
    city = s["city"]
    items = [
        ("Ficha del negocio para Google (datos estructurados LocalBusiness)", not codes["local_no_schema"],
         ", ".join(s["schema_types"]) if s["schema_types"] else "No la tiene: Google no recibe nombre, dirección y horario en un formato que entienda"),
        ("Datos del negocio completos (dirección, horario, coordenadas…)",
         None if codes["local_no_schema"] and not s["schema_types"] else not codes["local_schema_incomplete"],
         "Completa" if not codes["local_schema_incomplete"] else "Faltan datos en la ficha"),
        ("Nombre, dirección y teléfono iguales en toda la web", not codes["local_nap_inconsistent"],
         "Coinciden" if not codes["local_nap_inconsistent"] else "Hay versiones distintas"),
        ("Teléfono visible en todas las páginas", s["phone_pages"] == s["indexable"],
         f"{s['phone_pages']} de {s['indexable']} páginas" + (f" · {s['main_phone']}" if s["main_phone"] else "")),
        ("Teléfono que se puede pulsar para llamar desde el móvil", s["clickable_pages"] == s["phone_pages"] and s["phone_pages"] > 0,
         f"{s['clickable_pages']} de {s['phone_pages']} páginas con teléfono"),
        ("Botón de WhatsApp", s["whatsapp_pages"] > 0, _in_pages(s["whatsapp_pages"])),
        ("Mapa de ubicación", s["map_pages"] > 0, _in_pages(s["map_pages"])),
        ("Página de contacto enlazada desde toda la web", bool(s["contact_url"]) and not codes["local_contact_not_linked"],
         "No existe" if not s["contact_url"] else ("Enlazada desde todas" if not codes["local_contact_not_linked"] else "Algunas páginas no la enlazan")),
    ]
    if s["cities"] and city["checked"]:
        items.append((f"La ciudad ({', '.join(s['cities'][:3])}) en el título de cada servicio", city["title"] == city["checked"],
                      f"{city['title']} de {city['checked']} páginas de servicio"))
        items.append(("La ciudad en el encabezado principal (H1) de cada servicio", city["h1"] == city["checked"],
                      f"{city['h1']} de {city['checked']} páginas de servicio"))
    elif not s["cities"]:
        items.append(("La ciudad en títulos y encabezados", None, "Indica la ciudad en la configuración (SEO local) para comprobarlo"))
    return [{"label": l, "ok": ok, "detail": d} for l, ok, d in items]
