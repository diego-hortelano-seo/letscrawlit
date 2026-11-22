"""Spelling dictionary shared by a team: one plain-text file (one word per line) that can live in a synced folder
(Dropbox, Google Drive, OneDrive…). Set LETSCRAWL_SHARED_DICTIONARY in .env to its path; without it the file is
data/dictionary.txt. Words added by anyone apply to everyone's crawls, including crawls already made (the
spelling results are filtered when they are shown, not only when crawling).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .config import get_settings

_WORD = re.compile(r"^[^\s,;]{1,60}$")


def path() -> Path:
    custom = os.getenv("LETSCRAWL_SHARED_DICTIONARY") or os.getenv("SEOSPIDER_SHARED_DICTIONARY")
    return Path(os.path.expandvars(os.path.expanduser(custom))) if custom else get_settings().data_dir / "dictionary.txt"


def shared() -> bool:
    return bool(os.getenv("LETSCRAWL_SHARED_DICTIONARY") or os.getenv("SEOSPIDER_SHARED_DICTIONARY"))


def version() -> float:
    try:
        return path().stat().st_mtime
    except OSError:
        return 0.0


def words() -> set[str]:
    try:
        text = path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return {w.strip().lower() for w in text.splitlines() if w.strip() and not w.lstrip().startswith("#")}


def text() -> str:
    try:
        return path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def clean(raw: str) -> list[str]:
    out = []
    for w in re.split(r"[\s,;]+", raw or ""):
        w = w.strip().lower()
        if w and _WORD.match(w) and w not in out:
            out.append(w)
    return out


def add(new: list[str] | str) -> int:
    """Append words that are not there yet. Appending (not rewriting) keeps concurrent edits from a synced
    folder from overwriting each other. Returns how many were added."""
    items = clean(new if isinstance(new, str) else " ".join(new))
    missing = [w for w in items if w not in words()]
    if not missing:
        return 0
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not p.exists() or p.read_bytes().endswith(b"\n") or p.stat().st_size == 0 else "\n"
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(prefix + "\n".join(missing) + "\n")
    return len(missing)


def replace(raw: str) -> int:
    """Save the whole list from the editor (sorted, unique, comments kept at the top)."""
    comments = [l for l in (raw or "").splitlines() if l.lstrip().startswith("#")]
    items = sorted(set(clean("\n".join(l for l in (raw or "").splitlines() if not l.lstrip().startswith("#")))))
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(comments + items) + "\n", encoding="utf-8")
    return len(items)
