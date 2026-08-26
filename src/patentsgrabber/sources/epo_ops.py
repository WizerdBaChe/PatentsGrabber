"""EPO Open Patent Services (OPS) v3.2 adapter.

Auth is OAuth2 client-credentials: POST {base}/auth/accesstoken with HTTP Basic
(consumer key : consumer secret) and grant_type=client_credentials, returning a
bearer token valid for ~20 minutes. The token is cached and refreshed a minute
early rather than on failure, so a request never fails for a reason we could
have prevented.

BR-6 (docs/01-concept-note.md): OPS reports its own accounting on every
response — x-registeredquotaperweek-used, x-individualquotaperhour-used and
x-throttling-control with per-service request allowances. Those counters are
authoritative and are what this client surfaces; no threshold is hard-coded,
because the Fair use charter defines it and the EPO may change it (T&C 3.3, 9).

Nothing here logs or returns the credential; see config.OpsConfig.describe().
"""

from __future__ import annotations

import base64
import io
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import OpsConfig, load_ops

TOKEN_PATH = "/auth/accesstoken"
REST = "/rest-services"
REFRESH_MARGIN_S = 60

# OPS bills and throttles per service, and reports the current per-minute
# allowance for each in x-throttling-control. Mapping a path to its service is
# what makes those numbers actionable instead of decorative.
SERVICE_OF_PATH = (
    ("published-data/images", "images"),
    ("published-data/search", "search"),
    ("family/", "inpadoc"),
    ("legal/", "inpadoc"),
    ("number-service", "other"),
)


class OpsError(RuntimeError):
    """An OPS call failed. Carries status and the service's own message."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class OpsAuthError(OpsError):
    """The credential was rejected — distinct from a data-level failure."""


def gmt_week() -> str:
    """The EPO's accounting week: Monday 00:00 to Sunday 24:00 GMT (T&C 4.2).

    Quota is metered per calendar week in GMT, not per process and not in local
    time, so usage must be bucketed the same way or the number means nothing.
    """
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@dataclass
class Usage:
    """Bytes spent against the free threshold, bucketed the way the EPO bills.

    The free threshold is defined by the EPO's Fair use charter and price list
    (T&C 3.3, 4.1-4.3) and the EPO may change it at any time (Article 9), so no
    number is hard-coded here: this counts, and the operator compares against the
    figure the EPO currently publishes.
    """

    week: str = field(default_factory=gmt_week)
    requests: int = 0
    bytes_served: int = 0

    # OPS reports its OWN accounting on every response. These are authoritative
    # where our local tally is only an estimate, so they are what we act on.
    server_week_used: int | None = None        # x-registeredquotaperweek-used (bytes)
    server_hour_used: int | None = None        # x-individualquotaperhour-used (bytes)
    system_state: str | None = None            # idle | busy | overloaded
    per_service: dict[str, tuple[str, int]] = field(default_factory=dict)

    def record(self, nbytes: int) -> None:
        current = gmt_week()
        if current != self.week:            # a new GMT week resets the local meter
            self.week, self.requests, self.bytes_served = current, 0, 0
        self.requests += 1
        self.bytes_served += nbytes

    def absorb_headers(self, headers) -> None:
        """Prefer the service's own counters over ours wherever it reports them."""
        low = {k.lower(): v for k, v in headers.items()}
        for attr, name in (("server_week_used", "x-registeredquotaperweek-used"),
                           ("server_hour_used", "x-individualquotaperhour-used")):
            raw = low.get(name)
            if raw and raw.strip().isdigit():
                setattr(self, attr, int(raw.strip()))
        ctl = low.get("x-throttling-control")
        if ctl:
            self.system_state = ctl.split("(")[0].strip() or None
            # e.g. "idle (images=green:200, search=green:30, ...)"
            for part in re.findall(r"(\w+)=(\w+):(\d+)", ctl):
                service, colour, per_min = part
                self.per_service[service] = (colour, int(per_min))

    def allowance(self, service: str) -> int | None:
        """Requests per minute OPS currently permits for a service, if it said."""
        hit = self.per_service.get(service)
        return hit[1] if hit else None

    def summary(self) -> str:
        mb = self.bytes_served / 1_048_576
        out = [f"{self.requests} requests, {mb:.2f} MB this session (GMT week {self.week})"]
        if self.server_week_used is not None:
            out.append(f"EPO week counter: {self.server_week_used / 1_048_576:.1f} MB")
        if self.server_hour_used is not None:
            out.append(f"EPO hour counter: {self.server_hour_used / 1_048_576:.1f} MB")
        if self.system_state:
            svc = ", ".join(f"{k}={v[0]}:{v[1]}/min" for k, v in sorted(self.per_service.items()))
            out.append(f"OPS is {self.system_state} [{svc}]")
        return " | ".join(out)


def _walk(node, key):
    """Collect every value stored under `key`, at any depth, in OPS's JSON.

    OPS JSON marks XML attributes as "@name" and text content as "$", and
    namespaces keys as "ops:thing" / "ns:thing" — so a plain dict lookup finds
    almost nothing. Matching on the suffix is what makes the payload usable.
    """
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key or k.endswith(":" + key) or k == "@" + key:
                out.append(v.get("$", v) if isinstance(v, dict) else v)
            out.extend(_walk(v, key))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk(item, key))
    return out


def ops_text(node) -> str:
    """All human-readable text under an OPS payload, whatever it nests it in.

    Full-text responses use different element names per constituent — claims put
    their prose in `claim-text`, descriptions in `p` — so extracting by a single
    guessed element name silently returns 0 characters and reads as "no data".
    Collecting every "$" text node is immune to that whole class of mistake.
    """
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$" and isinstance(v, str):
                out.append(v)
            elif not k.startswith("@"):
                out.append(ops_text(v))
    elif isinstance(node, list):
        out.extend(ops_text(i) for i in node)
    elif isinstance(node, str):
        out.append(node)
    return " ".join(t for t in out if t).strip()


def service_of(path: str) -> str:
    for prefix, service in SERVICE_OF_PATH:
        if path.startswith(prefix):
            return service
    return "retrieval"


def tiff_to_png(blob: bytes) -> bytes:
    """OPS serves drawings as TIFF, which no browser renders. Convert, don't crop.

    The sheets are bilevel 300-dpi scans (2550x3300 measured), so the 1-bit mode
    is kept: it is both smaller than greyscale and exactly what the source is.
    Resolution is preserved because zooming into a drawing is the point.
    """
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise OpsError(
            "無法把 EPO 的 TIFF 圖頁轉成瀏覽器看得懂的格式：缺少 Pillow。"
            "請安裝：pip install Pillow"
        ) from exc
    image = Image.open(io.BytesIO(blob))
    if getattr(image, "n_frames", 1) > 1:
        image.seek(0)                      # a multi-page TIFF: this call is one page
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def merge_pdf_pages(pages: list[bytes]) -> bytes:
    """OPS returns the original document one page at a time; stitch them back."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise OpsError(
            "無法把 EPO 的逐頁 PDF 合併成一份文件：缺少 pypdf。"
            "請安裝：pip install pypdf（或逐頁下載）"
        ) from exc
    writer = PdfWriter()
    for blob in pages:
        for page in PdfReader(io.BytesIO(blob)).pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@dataclass
class ImageInstance:
    """One retrievable rendition of a document, as images-inquiry describes it."""

    desc: str                    # Drawing | FullDocument | FirstPageClipping
    link: str
    pages: int
    formats: list[str] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"desc": self.desc, "link": self.link, "pages": self.pages,
                "formats": self.formats, "sections": self.sections}


class OpsClient:
    def __init__(self, cfg: OpsConfig | None = None, timeout: float = 40.0):
        self.cfg = cfg or load_ops()
        self.usage = Usage()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._calls: dict[str, deque] = {}
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    # ------------------------------------------------------------------ auth

    def _basic(self) -> str:
        raw = f"{self.cfg.key}:{self.cfg.secret}".encode()
        return base64.b64encode(raw).decode()

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - REFRESH_MARGIN_S:
            return self._token
        r = self._client.post(
            self.cfg.base_url + TOKEN_PATH,
            headers={"Authorization": f"Basic {self._basic()}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
        if r.status_code in (400, 401, 403):
            raise OpsAuthError(
                "OPS 拒絕這組金鑰。請確認 .env 裡的 OPS_CONSUMER_KEY / "
                "OPS_CONSUMER_SECRET 與 EPO Developer Portal 上該 app 的值一致，"
                "且該 app 已啟用（新建的 app 有時要等幾分鐘才生效）。",
                r.status_code, r.text[:400],
            )
        if r.status_code != 200:
            raise OpsError(f"token endpoint returned {r.status_code}", r.status_code, r.text[:400])
        payload = r.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 1200))
        return self._token

    # ----------------------------------------------------------------- calls

    def _throttle(self, service: str) -> None:
        """Stay under the per-minute allowance OPS is currently advertising.

        Sleeping only when the trailing minute is actually full keeps small
        bursts (a 25-page document) at full speed while a long run still cannot
        trip the limit. The allowance comes from the service's own header, so a
        busy OPS automatically slows us down instead of us guessing a number.
        """
        allowance = self.usage.allowance(service)
        if not allowance:
            return
        window = self._calls.setdefault(service, deque())
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= allowance:
            time.sleep(max(0.0, 60 - (now - window[0])) + 0.05)
        window.append(time.monotonic())

    def get(self, path: str, *, accept: str = "application/json",
            params: dict[str, Any] | None = None, raw: bool = False) -> Any:
        """GET a rest-services path. `raw` returns the httpx.Response (binary)."""
        url = self.cfg.base_url + REST + ("" if path.startswith("/") else "/") + path
        self._throttle(service_of(path))
        r = self._client.get(
            url,
            headers={"Authorization": f"Bearer {self.token()}", "Accept": accept},
            params=params,
        )
        self.usage.record(len(r.content))
        self.usage.absorb_headers(r.headers)

        if r.status_code == 403:
            raise OpsError("OPS 回 403 — 可能是配額用盡或此服務未授權給你的帳號。",
                           403, r.text[:400])
        if r.status_code == 404:
            raise OpsError(f"OPS 查無此資料：{path}", 404, r.text[:300])
        if r.status_code >= 400:
            raise OpsError(f"OPS {r.status_code} on {path}", r.status_code, r.text[:400])

        if raw:
            return r
        if accept.endswith("json"):
            try:
                return r.json()
            except ValueError:
                # OPS documents XML as its native form; some services ignore the
                # JSON Accept and answer XML anyway. Returning the text beats
                # raising a decode error that hides a perfectly good response.
                return r.text
        return r.text

    # ------------------------------------------------------ typed operations

    def biblio(self, number: str, fmt: str = "epodoc") -> dict:
        return self.get(f"published-data/publication/{fmt}/{number}/biblio")

    def abstract(self, number: str, fmt: str = "epodoc") -> dict:
        return self.get(f"published-data/publication/{fmt}/{number}/abstract")

    def claims(self, number: str, fmt: str = "epodoc") -> dict:
        return self.get(f"published-data/publication/{fmt}/{number}/claims")

    def description(self, number: str, fmt: str = "epodoc") -> dict:
        return self.get(f"published-data/publication/{fmt}/{number}/description")

    def family(self, number: str, fmt: str = "epodoc") -> dict:
        return self.get(f"family/publication/{fmt}/{number}")

    def legal(self, number: str, fmt: str = "epodoc") -> dict:
        return self.get(f"legal/publication/{fmt}/{number}")

    def images_inquiry(self, number: str, fmt: str = "epodoc") -> dict:
        """Step 1 of image retrieval: what images exist, and how many pages."""
        return self.get(f"published-data/publication/{fmt}/{number}/images")

    def image_page(self, link: str, page: int = 1, kind: str = "pdf") -> bytes:
        """Step 2: one page of a document instance returned by images_inquiry.

        `link` is the `link` attribute OPS gave us, e.g.
        published-data/images/US/2025383260/A1/fullimage
        """
        accept = "application/pdf" if kind == "pdf" else "image/tiff"
        r = self.get(f"{link}.{kind}", accept=accept, params={"Range": page}, raw=True)
        return r.content

    # -------------------------------------------------- images & original doc

    def instances(self, number: str, fmt: str = "epodoc") -> list[ImageInstance]:
        """What renditions exist for this document, and how many pages each has.

        Measured shape (2026-08-26): three instances — `Drawing` (the drawing
        sheets, link `.../thumbnail`), `FirstPageClipping`, and `FullDocument`
        (the original document, link `.../fullimage`). Drawing and FullDocument
        offer only application/pdf and application/tiff, which is why a browser
        cannot be pointed at them directly.
        """
        payload = self.images_inquiry(number, fmt=fmt)
        raw = _walk(payload, "document-instance")
        found: list[ImageInstance] = []
        for group in raw:
            for item in (group if isinstance(group, list) else [group]):
                if not isinstance(item, dict):
                    continue
                link = str(item.get("@link") or "")
                if not link:
                    continue
                sections = []
                for sec in _walk(item, "document-section"):
                    for entry in (sec if isinstance(sec, list) else [sec]):
                        if isinstance(entry, dict) and entry.get("@name"):
                            sections.append({"name": entry["@name"],
                                             "start": int(entry.get("@start-page", 1) or 1)})
                formats = []
                for group in _walk(item, "document-format"):
                    for entry in (group if isinstance(group, list) else [group]):
                        value = entry.get("$") if isinstance(entry, dict) else entry
                        if isinstance(value, str):
                            formats.append(value)
                found.append(ImageInstance(
                    desc=str(item.get("@desc") or "unknown"),
                    link=link,
                    pages=int(item.get("@number-of-pages") or 1),
                    formats=formats,
                    sections=sections,
                ))
        return found

    def drawing_png(self, link: str, page: int = 1) -> bytes:
        return tiff_to_png(self.image_page(link, page=page, kind="tiff"))

    def document_pdf(self, link: str, pages: int, *, max_pages: int = 80) -> tuple[bytes, int]:
        """The original document as one PDF. Returns (bytes, pages_included).

        OPS serves `fullimage` one page per request, so a 25-page patent costs 25
        calls. The cap is stated rather than silent: a truncated document that
        claims to be complete is worse than one that says where it stopped.
        """
        wanted = min(pages, max_pages)
        blobs = [self.image_page(link, page=n, kind="pdf") for n in range(1, wanted + 1)]
        return merge_pdf_pages(blobs), wanted

    # ------------------------------------------------------ INPADOC readings

    @staticmethod
    def _docdb_id(node) -> dict:
        """The docdb document-id inside a publication/application reference."""
        ids = node.get("document-id") if isinstance(node, dict) else None
        for entry in (ids if isinstance(ids, list) else [ids] if ids else []):
            if isinstance(entry, dict) and entry.get("@document-id-type") == "docdb":
                text = lambda key: (entry.get(key) or {}).get("$") if isinstance(entry.get(key), dict) else None
                return {"country": text("country"), "number": text("doc-number"),
                        "kind": text("kind"), "date": text("date")}
        return {}

    def family_members(self, number: str, fmt: str = "epodoc") -> list[dict]:
        """INPADOC family: every publication of the same invention, worldwide."""
        payload = self.family(number, fmt=fmt)
        out: list[dict] = []
        for group in _walk(payload, "family-member"):
            for member in (group if isinstance(group, list) else [group]):
                if not isinstance(member, dict):
                    continue
                pub = self._docdb_id(member.get("publication-reference") or {})
                app = self._docdb_id(member.get("application-reference") or {})
                if not pub.get("number"):
                    continue
                out.append({
                    "publication": f"{pub.get('country') or ''}{pub['number']}{pub.get('kind') or ''}",
                    "country": pub.get("country"),
                    "date": pub.get("date"),
                    "application": f"{app.get('country') or ''}{app.get('number') or ''}" if app else None,
                    "family_id": member.get("@family-id"),
                })
        return out

    def legal_events(self, number: str, fmt: str = "epodoc") -> list[dict]:
        """INPADOC legal events for this publication, newest first where dated."""
        payload = self.legal(number, fmt=fmt)
        out: list[dict] = []
        for group in _walk(payload, "legal"):
            for event in (group if isinstance(group, list) else [group]):
                if not isinstance(event, dict) or "@code" not in event:
                    continue
                date = None
                detail = []
                for key, value in event.items():
                    if not isinstance(value, dict):
                        continue
                    label = value.get("@desc") or ""
                    if label == "Gazette DATE":
                        date = value.get("$")
                    elif "$" in value and label and label not in ("Country Code", "Kind Code",
                                                                  "Document Number", "IPR Type",
                                                                  "Filing / Published Document"):
                        detail.append(f"{label}: {value['$']}")
                    else:
                        for sub in value.values():        # nested L5xx blocks
                            if isinstance(sub, dict) and sub.get("@desc") and "$" in sub:
                                detail.append(f"{sub['@desc']}: {sub['$']}")
                out.append({
                    "date": date,
                    "code": str(event.get("@code") or "").strip(),
                    "title": str(event.get("@desc") or "").strip(),
                    "detail": " · ".join(dict.fromkeys(detail))[:400],
                })
        out.sort(key=lambda e: e["date"] or "", reverse=True)
        return out

    def number_convert(self, number: str, *, from_fmt: str = "epodoc",
                       to_fmt: str = "docdb", kind: str = "publication") -> dict:
        """OPS number-service — the canonical fix for format-mismatch lookups."""
        return self.get(f"number-service/{kind}/{from_fmt}/{number}/{to_fmt}")

    def search(self, cql: str, *, start: int = 1, end: int = 25) -> dict:
        """CQL search. Applicant is `pa=`, inventor `in=`, title+abstract `ta=`."""
        return self.get("published-data/search",
                        params={"q": cql, "Range": f"{start}-{end}"})

    def resolve(self, parsed) -> tuple[str, str]:
        """Find an input format OPS actually accepts for this number.

        Order matters and is empirical (2026-08-23): docdb with the kind code is
        the precise form, so it is tried first; epodoc without a kind code is the
        fallback; number-service is the last resort because it costs an extra
        call but can translate a form the data endpoints reject.

        Returns (format, number). Raises OpsError if nothing resolves.
        """
        for candidate in parsed.docdb_candidates():
            try:
                self.biblio(candidate, fmt="docdb")
                return ("docdb", candidate)
            except OpsError as exc:
                if exc.status != 404:
                    raise
        try:
            self.biblio(parsed.epodoc, fmt="epodoc")
            return ("epodoc", parsed.epodoc)
        except OpsError as exc:
            if exc.status != 404:
                raise
        # Last resort: let OPS translate the display form it does accept here.
        conv = self.number_convert(parsed.espacenet, from_fmt="epodoc", to_fmt="docdb")
        body = "".join(str(v) for v in _walk(conv, "doc-number"))
        kind = "".join(str(v) for v in _walk(conv, "kind"))
        if body:
            num = f"{parsed.country}.{body}.{kind or parsed.kind_code or 'A1'}"
            self.biblio(num, fmt="docdb")
            return ("docdb", num)
        raise OpsError(f"OPS 無法解析號碼 {parsed.raw!r}（已試 docdb、epodoc 與 number-service）")

    def close(self) -> None:
        self._client.close()
