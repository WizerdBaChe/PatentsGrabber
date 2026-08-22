"""US patent number normalization.

Stage 0 scope: US only. Accepts the many shapes a US number is written in and
produces a canonical form plus the URL candidates to try against a source.

Recognized input shapes
-----------------------
Pre-grant publication (USPTO/Google form)  US20250383260A1, US 2025/0383260 A1
Pre-grant publication (Espacenet/DOCDB)    US2025383260A1   (6-digit serial)
Granted patent                             US11123456B2, 11,123,456, 7654321
Application number                         18/123,456       (detected, unsupported)

Why candidates rather than one answer: Google Patents URLs generally require the
kind code, and a bare granted number does not carry one. We emit an ordered
candidate list (most likely first) and let the source layer resolve it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Kind codes seen on US documents. Order matters for candidate generation:
# B2 (grant with prior pre-grant pub) is by far the most common post-2001 grant.
US_GRANT_KINDS = ("B2", "B1", "A")
US_PUB_KINDS = ("A1", "A2", "A9")

_KIND_RE = re.compile(r"([AB][129]?|[EPSH]\d?)$", re.IGNORECASE)
_APPLICATION_RE = re.compile(r"^(\d{2})/(\d{3},?\d{3})$")


class NumberError(ValueError):
    """Input could not be interpreted as a US patent number."""


@dataclass
class ParsedNumber:
    """Result of normalizing one user-supplied string."""

    raw: str
    kind_of_document: str  # "publication" | "grant" | "application"
    country: str = "US"
    serial: str = ""  # digits only, canonical width
    year: str = ""  # publication only
    kind_code: str | None = None
    canonical: str = ""  # e.g. US20250383260A1
    espacenet: str = ""  # e.g. US2025383260A1
    candidates: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def supported(self) -> bool:
        return self.kind_of_document in ("publication", "grant")


def _split_kind(token: str) -> tuple[str, str | None]:
    """Peel a trailing kind code off an alphanumeric token."""
    m = _KIND_RE.search(token)
    if not m:
        return token, None
    # Guard: a bare "A" on an all-digit body is a kind code; on an empty body it is not.
    body = token[: m.start()]
    if not body or not body.isdigit():
        return token, None
    return body, m.group(1).upper()


def normalize(raw: str) -> ParsedNumber:
    """Normalize a user-supplied US patent number string.

    Raises NumberError when the input cannot be interpreted at all. An
    application number parses successfully but is marked unsupported rather than
    rejected, so the caller can tell the user *why* instead of "invalid input".
    """
    if raw is None or not str(raw).strip():
        raise NumberError("empty input")

    text = str(raw).strip().upper()
    return _normalize_impl(raw, text)


def _normalize_impl(raw: str, text: str) -> ParsedNumber:
    bare = text.replace("US", "", 1) if text.startswith("US") else text
    bare = bare.strip()

    app = _APPLICATION_RE.match(bare.replace(" ", ""))
    if app:
        return ParsedNumber(
            raw=raw,
            kind_of_document="application",
            serial=app.group(1) + app.group(2).replace(",", ""),
            note=(
                "這是申請號 (application number)，不是公開號或公告號。"
                "Google Patents 不接受申請號查詢；Stage 1 接上 EPO OPS 後才支援。"
            ),
        )

    token = re.sub(r"[\s,/\-\.]", "", bare)
    if not token:
        raise NumberError(f"no digits found in {raw!r}")

    body, kind = _split_kind(token)
    if not body.isdigit():
        raise NumberError(f"cannot interpret {raw!r} as a US patent number")

    # 11 digits starting 19xx/20xx -> USPTO pre-grant publication (YYYY + 7)
    # 10 digits starting 19xx/20xx -> Espacenet/DOCDB form (YYYY + 6)
    if len(body) in (10, 11) and body[:2] in ("19", "20"):
        year, serial = body[:4], body[4:]
        serial = serial.zfill(7)
        return _publication(raw, year, serial, kind)

    if 1 <= len(body) <= 8:
        return _grant(raw, body, kind)

    raise NumberError(
        f"{raw!r} has {len(body)} digits, which matches no known US number shape"
    )


def _publication(raw: str, year: str, serial: str, kind: str | None) -> ParsedNumber:
    kinds = [kind] if kind else list(US_PUB_KINDS)
    canonical = f"US{year}{serial}{kind or US_PUB_KINDS[0]}"
    return ParsedNumber(
        raw=raw,
        kind_of_document="publication",
        serial=serial,
        year=year,
        kind_code=kind,
        canonical=canonical,
        espacenet=f"US{year}{serial.lstrip('0').zfill(6)}{kind or ''}",
        candidates=[f"US{year}{serial}{k}" for k in kinds],
    )


def _grant(raw: str, body: str, kind: str | None) -> ParsedNumber:
    serial = body.lstrip("0") or "0"
    kinds = [kind] if kind else list(US_GRANT_KINDS)
    return ParsedNumber(
        raw=raw,
        kind_of_document="grant",
        serial=serial,
        kind_code=kind,
        canonical=f"US{serial}{kind or US_GRANT_KINDS[0]}",
        espacenet=f"US{serial}{kind or ''}",
        candidates=[f"US{serial}{k}" for k in kinds],
    )
