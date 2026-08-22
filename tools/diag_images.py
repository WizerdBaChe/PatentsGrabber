"""Diagnose which patentimages URL shapes are actually fetchable.

Google Patents markup exposes more than one image URL shape and they do not all
resolve; the page renders because the browser is fed a working one. This probes
each shape per document so the adapter can pick a URL that exists rather than one
that merely appears in the HTML.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "var" / "raw"

DOCS = ["US20250383260A1", "US6285999B1", "US8046721B2", "US20230123456A1", "US20200000001A1"]

UA = {"User-Agent": "PatentsGrabber/0.1 (personal research reader)"}
UA_REF = dict(UA, Referer="https://patents.google.com/")


def probe(url: str, headers: dict) -> str:
    try:
        r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        kind = r.headers.get("content-type", "")
        return f"{r.status_code} {kind.split(';')[0]} {len(r.content)}B"
    except Exception as exc:
        return f"ERR {type(exc).__name__}"


for number in DOCS:
    path = RAW / f"{number}.html"
    if not path.exists():
        continue
    html = path.read_text(encoding="utf-8")
    full = re.findall(r'<meta[^>]*itemprop="full"[^>]*content="([^"]+)"', html)
    thumb = re.findall(r'(https://patentimages\.storage\.googleapis\.com/thumbnails/[^"\s]+\.png)', html)
    # the hashed shape Google actually serves from, e.g. /80/84/55/<hash>/US...png
    hashed = re.findall(
        r'(https://patentimages\.storage\.googleapis\.com/(?:[0-9a-f]{2}/){3}[0-9a-f]+/[^"\s]+\.png)', html
    )
    print(f"\n=== {number} ===")
    print(f"  itemprop=full : n={len(full):3}  {full[0] if full else '-'}")
    if full:
        print(f"                  plain  -> {probe(full[0], UA)}")
        print(f"                  +Referer-> {probe(full[0], UA_REF)}")
    print(f"  thumbnails/   : n={len(set(thumb)):3}  {sorted(set(thumb))[0] if thumb else '-'}")
    if thumb:
        print(f"                  plain  -> {probe(sorted(set(thumb))[0], UA)}")
    print(f"  hashed path   : n={len(set(hashed)):3}  {sorted(set(hashed))[0] if hashed else '-'}")
    if hashed:
        print(f"                  plain  -> {probe(sorted(set(hashed))[0], UA)}")
