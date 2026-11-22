"""Pure text-similarity tools: near duplicates (MinHash + LSH), TF-IDF, semantic clusters,
internal-link suggestions and plagiarism comparison against pasted text. No network, no ML deps."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict

STOPWORDS = set("""
a al algo algunas algunos ante antes aqui asi aun aunque bajo bien cada casi como con contra cual cuales cuando de
del desde donde dos el ella ellas ellos en entre era eran es esa esas ese eso esos esta estaba estado estan estar
este esto estos fue fueron ha hace hacen hacer han hasta hay la las le les lo los mas me mi mis mucho muy nada ni
no nos nosotros o otra otras otro otros para pero poco por porque que quien se sea segun ser si sido sin sobre solo
son su sus tambien tanto te tiene tienen todo todos tu tus un una unas uno unos usted ustedes ya yo vez veces cada
mismo misma puede pueden sus tras durante mientras cuyo e u etc
the a an and are as at be by for from has have in is it its of on or that the this to was were will with you your
we our they their not but can all more about into than then also which who what when where how
""".split())
_WORD = re.compile(r"[a-záéíóúüñ0-9]+", re.I)


def fold(text: str) -> str:
    """Lowercase and strip accents (cerrajería -> cerrajeria) for matching."""
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def tokens(text: str, min_len: int = 3) -> list[str]:
    return [w for w in _WORD.findall(fold(text)) if len(w) >= min_len and w not in STOPWORDS and not w.isdigit()]


# --- near duplicates -----------------------------------------------------------------------
_PERMS = 64
_MAX = (1 << 61) - 1
_A = [int(hashlib.sha1(f"a{i}".encode()).hexdigest()[:15], 16) | 1 for i in range(_PERMS)]
_B = [int(hashlib.sha1(f"b{i}".encode()).hexdigest()[:15], 16) for i in range(_PERMS)]


def shingles(text: str, k: int = 5) -> set[int]:
    words = _WORD.findall(fold(text))
    if len(words) < k:
        return {int(hashlib.md5(" ".join(words).encode()).hexdigest()[:15], 16)} if words else set()
    return {int(hashlib.md5(" ".join(words[i:i + k]).encode()).hexdigest()[:15], 16) for i in range(len(words) - k + 1)}


def minhash(sh: set[int]) -> list[int]:
    if not sh:
        return [_MAX] * _PERMS
    return [min((a * x + b) % _MAX for x in sh) for a, b in zip(_A, _B)]


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def near_duplicates(docs: dict[str, str], threshold: float = 0.9, min_words: int = 50) -> dict[str, tuple[str, float, int]]:
    """For each URL with a near-duplicate: (closest URL, similarity 0-1, number of near-duplicates).
    Exact duplicates are included (similarity 1.0). LSH: 16 bands x 4 rows."""
    sh = {u: shingles(t) for u, t in docs.items() if len(_WORD.findall(t)) >= min_words}
    sigs = {u: minhash(s) for u, s in sh.items()}
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for u, sig in sigs.items():
        for band in range(16):
            buckets[(band, tuple(sig[band * 4:(band + 1) * 4]))].append(u)
    best: dict[str, tuple[str, float, int]] = {}
    checked: set[tuple[str, str]] = set()
    counts: Counter = Counter()
    for urls in buckets.values():
        if len(urls) < 2 or len(urls) > 500:
            continue
        for i, a in enumerate(urls):
            for b in urls[i + 1:]:
                pair = (a, b) if a < b else (b, a)
                if pair in checked:
                    continue
                checked.add(pair)
                sim = jaccard(sh[a], sh[b])
                if sim >= threshold:
                    counts[a] += 1
                    counts[b] += 1
                    for x, y in ((a, b), (b, a)):
                        if x not in best or sim > best[x][1]:
                            best[x] = (y, sim, 0)
    return {u: (o, round(s, 4), counts[u]) for u, (o, s, _) in best.items()}


# --- TF-IDF, clusters, link suggestions ------------------------------------------------------------

def tfidf(docs: dict[str, str], max_terms: int = 200) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    tf = {u: Counter(tokens(t)) for u, t in docs.items()}
    df = Counter(w for c in tf.values() for w in c)
    n = max(1, len(docs))
    idf = {w: math.log((1 + n) / (1 + d)) + 1 for w, d in df.items()}
    vecs = {}
    for u, c in tf.items():
        total = sum(c.values()) or 1
        weights = {w: (cnt / total) * idf[w] for w, cnt in c.items()}
        top = dict(sorted(weights.items(), key=lambda kv: -kv[1])[:max_terms])
        norm = math.sqrt(sum(v * v for v in top.values())) or 1
        vecs[u] = {w: v / norm for w, v in top.items()}
    return vecs, idf


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(w, 0.0) for w, v in a.items())


def clusters(docs: dict[str, str], threshold: float = 0.35, max_docs: int = 2000) -> list[dict]:
    """Greedy centroid clustering on TF-IDF vectors. Returns clusters (>=2 pages) with their top terms."""
    vecs, _ = tfidf(dict(list(docs.items())[:max_docs]))
    groups: list[dict] = []
    for u, v in sorted(vecs.items(), key=lambda kv: -len(kv[1])):
        if not v:
            continue
        best, best_sim = None, threshold
        for g in groups:
            s = cosine(v, g["centroid"])
            if s >= best_sim:
                best, best_sim = g, s
        if best is None:
            groups.append({"urls": [u], "centroid": dict(v)})
        else:
            best["urls"].append(u)
            k = len(best["urls"])
            c = best["centroid"]
            for w in set(c) | set(v):
                c[w] = (c.get(w, 0.0) * (k - 1) + v.get(w, 0.0)) / k
    out = []
    for g in groups:
        if len(g["urls"]) < 2:
            continue
        terms = [w for w, _ in sorted(g["centroid"].items(), key=lambda kv: -kv[1])[:6]]
        out.append({"label": ", ".join(terms[:3]), "terms": terms, "urls": sorted(g["urls"]), "size": len(g["urls"])})
    return sorted(out, key=lambda c: -c["size"])


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?¡¿])\s+|\n+", text or "") if len(s.strip()) > 20]


def link_suggestions(docs: dict[str, str], titles: dict[str, str], existing: set[tuple[str, str]],
                     targets: set[str] | None = None, min_sim: float = 0.2, per_source: int = 5,
                     max_docs: int = 1500) -> list[dict]:
    """Suggest A -> B internal links: similar topics, no link yet, and a sentence in A mentions B's key terms."""
    docs = dict(list(docs.items())[:max_docs])
    vecs, idf = tfidf(docs)
    key_terms = {}
    for u in docs:
        words = [w for w in tokens(titles.get(u, ""))]
        key_terms[u] = sorted(set(words), key=lambda w: -idf.get(w, 0))[:4] or list(vecs.get(u, {}))[:3]
    out = []
    targets = targets if targets is not None else set(docs)
    for a, va in vecs.items():
        cands = []
        for b in targets:
            if a == b or b not in vecs or (a, b) in existing:
                continue
            sim = cosine(va, vecs[b])
            if sim >= min_sim:
                cands.append((sim, b))
        found = 0
        for sim, b in sorted(cands, reverse=True):
            terms = [_stem(t) for t in key_terms.get(b) or []]
            for sent in _sentences(docs[a]):
                fs = fold(sent)
                hits = [t for t in terms if re.search(rf"\b{re.escape(t)}\w*", fs)]
                if hits and len(hits) >= min(2, len(terms)):
                    anchor = _anchor_from(sent, hits)
                    out.append({"source": a, "target": b, "similarity": round(sim, 3), "anchor": anchor,
                                "sentence": sent[:300], "target_title": titles.get(b, "")})
                    found += 1
                    break
            if found >= per_source:
                break
    return sorted(out, key=lambda s: -s["similarity"])


def _stem(term: str) -> str:
    """Crude stem so 'bombin' matches 'bombines' and 'cambio' matches 'cambiamos'."""
    return term[:max(4, len(term) - 2)] if len(term) > 4 else term


def _anchor_from(sentence: str, hits: list[str]) -> str:
    """Shortest window of original words that contains all matched stems (max 6 words)."""
    words = sentence.split()
    folded = [fold(w).strip(".,;:!?¡¿()\"'") for w in words]
    best = None
    for i in range(len(words)):
        for j in range(i, min(len(words), i + 6)):
            window = folded[i:j + 1]
            if all(any(w.startswith(h) for w in window) for h in hits):
                if best is None or (j - i) < (best[1] - best[0]):
                    best = (i, j)
                break
    if not best:
        return " ".join(words[:5])
    return " ".join(words[best[0]:best[1] + 1]).strip(".,;:!?¡¿()\"'")


def plagiarism(text: str, docs: dict[str, str], min_score: float = 0.05) -> list[dict]:
    """Compare a pasted external text with every crawled page: containment of 5-word shingles + shared passages."""
    ext = shingles(text)
    if not ext:
        return []
    ext_words = _WORD.findall(fold(text))
    out = []
    for u, t in docs.items():
        sh = shingles(t)
        if not sh:
            continue
        shared = ext & sh
        containment = len(shared) / len(sh)  # % of the page found in the external text
        if containment >= min_score:
            out.append({"url": u, "page_copied": round(containment * 100, 1),
                        "external_from_page": round(len(shared) / len(ext) * 100, 1),
                        "passage": _longest_passage(ext_words, _WORD.findall(fold(t)))})
    return sorted(out, key=lambda r: -r["page_copied"])


def _longest_passage(a: list[str], b: list[str], k: int = 5) -> str:
    grams = {" ".join(b[i:i + k]) for i in range(len(b) - k + 1)}
    best, run, start = (0, 0), 0, 0
    for i in range(len(a) - k + 1):
        if " ".join(a[i:i + k]) in grams:
            if run == 0:
                start = i
            run += 1
            if run > best[1] - best[0]:
                best = (start, start + run)
        else:
            run = 0
    return " ".join(a[best[0]:best[1] + k - 1])[:300] if best[1] > best[0] else ""
