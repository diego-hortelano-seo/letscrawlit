"""Files that hold credentials (saved login sessions, the Google permission).

On Windows they are encrypted with DPAPI, tied to your Windows user: copying the file to another computer or
reading it as another user gives nothing usable. On macOS/Linux they are written readable only by you (0600).
Older plain files are still read, and encrypted the next time they are saved.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from pathlib import Path

PREFIX = b"LCDPAPI1:"
_ENTROPY = b"LetsCrawl secret store"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, ent, out = _blob(data), _blob(_ENTROPY), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))  # 0x1: no UI
    if not ok:
        raise OSError("DPAPI no pudo " + ("cifrar" if protect else "descifrar") + " el archivo")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def available() -> bool:
    return sys.platform == "win32"


def write_json(path: Path, obj) -> None:
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if available():
        tmp.write_bytes(PREFIX + base64.b64encode(_dpapi(raw, True)))
    else:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    tmp.replace(path)


def read_json(path: Path):
    """Raises OSError/ValueError like reading a normal JSON file would."""
    data = path.read_bytes()
    if data.startswith(PREFIX):
        if not available():
            raise ValueError("Archivo cifrado con la cuenta de Windows: no se puede leer en este sistema.")
        data = _dpapi(base64.b64decode(data[len(PREFIX):]), False)
    return json.loads(data.decode("utf-8"))


def encrypt_existing(paths) -> int:
    """Encrypt credential files saved by older versions in plain text. Returns how many were converted."""
    if not available():
        return 0
    n = 0
    for p in paths:
        try:
            if p.is_file() and not p.read_bytes().startswith(PREFIX):
                write_json(p, read_json(p))
                n += 1
        except (OSError, ValueError):
            continue
    return n
