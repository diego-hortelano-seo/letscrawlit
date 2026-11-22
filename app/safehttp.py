"""Size-capped reads for anything downloaded from crawled sites.

A hostile or broken server can answer with an endless body or a gzip «bomb» (a few MB that expand to many GB).
Everything the crawler keeps in memory goes through these helpers, so one bad URL cannot exhaust the computer.
"""
from __future__ import annotations

import zlib

import httpx

ROBOTS_MAX = 512 * 1024              # Google reads the first 500 KiB of robots.txt
SITEMAP_MAX = 50 * 1024 * 1024       # sitemaps.org limit (uncompressed)


class TooLarge(ValueError):
    pass


async def get_capped(client: httpx.AsyncClient, url: str, limit: int, **kw) -> tuple[httpx.Response, bytes]:
    """GET that stops reading after `limit` decoded bytes. Returns (response, body[:limit])."""
    body = bytearray()
    async with client.stream("GET", url, **kw) as resp:
        async for chunk in resp.aiter_bytes():
            body += chunk
            if len(body) >= limit:
                del body[limit:]
                break
    return resp, bytes(body)


def gunzip_capped(data: bytes, limit: int) -> bytes:
    """Decompress gzip data, refusing to produce more than `limit` bytes."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(data, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise TooLarge(f"descomprimido supera {limit // (1024 * 1024)} MB")
    return out


def no_doctype(xml: bytes) -> bool:
    """Sitemaps and API answers never need a DTD; refusing it rules out entity tricks (XXE, «billion laughs»)."""
    head = xml[:4096].lower()
    return b"<!doctype" not in head and b"<!entity" not in xml.lower()


def text_of(resp: httpx.Response, body: bytes) -> str:
    try:
        text = body.decode(resp.charset_encoding or "utf-8", "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    return text.lstrip("﻿")


async def is_private_host(host: str) -> bool:
    """True when the name is (or resolves to) this computer or the local network: router, NAS, printers…"""
    import asyncio
    import ipaddress
    import socket
    host = (host or "").strip("[]").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return False  # unresolvable: the request will simply fail
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    return any(a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified
               for a in addrs)
