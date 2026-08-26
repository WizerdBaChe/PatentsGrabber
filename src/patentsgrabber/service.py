"""Orchestration: a user-typed string in, one normalized patent card out.

Implements the business rules from docs/01-concept-note.md that the UI depends on:

BR-1  accepts any of the shapes a US number is written in; the caller does not
      pre-classify the input
BR-3  absent fields are reported WITH a reason, never silently blank
BR-4  every field carries which source and which selector produced it
BR-5  every resolved document is written to the local library

Long lists (forward citations can run to thousands) are capped for display, and
the cap is stated in the payload — a silently truncated list reads as complete.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from . import numbers
from .config import MissingCredentials
from .sources import epo_ops as ops
from .sources import google_patents as gp
from .store import Store

# A link handed back to us by the browser is untrusted input, even on localhost:
# it selects both an upstream OPS path and a cache filename. Only the three
# shapes images-inquiry actually produces are accepted.
OPS_LINK_RE = re.compile(
    r"^published-data/images/[A-Z]{2}/[0-9A-Za-z]+/[A-Z0-9]+/(thumbnail|fullimage|firstpage)$"
)

DISPLAY_CAPS = {
    "backward_citations": 60,
    "forward_citations": 60,
    "similar_documents": 25,
    "family": 25,
    "legal_events": 40,
    "classifications": 30,
}

# Why a field can be missing. Shown to the user instead of an empty box (BR-3).
ABSENCE_REASON = {
    "pdf_link": "Google Patents 尚未提供此件的 PDF（最新公開案常見）；本工具改向 EPO OPS 取得原文件。",
    "images": (
        "Google Patents 未提供可直接取用的圖檔（約 2000 年以前的老案常見，僅有整份 PDF）；"
        "本工具改向 EPO OPS 取得圖式頁。"
    ),
    "images_forbidden": (
        "此件的圖檔網址存在於頁面標記中，但 Google 的儲存空間拒絕存取 (HTTP 403)——"
        "最新公開案常見，圖片尚未對外開放；本工具改向 EPO OPS 取得圖式頁。"
    ),
    "description_blocks": "此件頁面沒有可辨識的說明書段落結構，只能以純文字呈現。",
    "backward_citations": "此件頁面未列出引用前案。",
    "forward_citations": "尚無後續專利引用此件。",
    "family": "頁面未列出同族申請案。",
    "similar_documents": "Google 未提供相似文件建議。",
    "claim_list": "無法從頁面結構取出逐項請求項；請改看 claims 全文。",
}


class ResolveError(RuntimeError):
    def __init__(self, message: str, tried: list[str] | None = None):
        super().__init__(message)
        self.tried = tried or []


def _fault(exc: ops.OpsError) -> str:
    """OPS names its objection in the body; a bare status code is undiagnosable."""
    body = " ".join((exc.body or "").split())
    code = body.split("<code>")[1].split("</code>")[0] if "<code>" in body else ""
    return f"HTTP {exc.status} {code}".strip()


class Service:
    def __init__(self, db_path: Path, raw_dir: Path | None = None, cache_dir: Path | None = None):
        self.store = Store(db_path)
        self.raw_dir = raw_dir
        self.cache_dir = cache_dir
        self.client = httpx.Client(headers=gp.HEADERS, timeout=30.0, follow_redirects=True)
        self._ops: ops.OpsClient | None = None
        self._ops_reason: str | None = None

    # --------------------------------------------------------------- EPO OPS

    def ops_client(self) -> ops.OpsClient | None:
        """The OPS client, or None with a stated reason. Never raises.

        Stage 0 must keep working when no credential is present: OPS absence is a
        reportable state (BR-3), not a crash. The reason is kept so the UI can say
        which of "no key" / "key rejected" it is instead of an empty panel.
        """
        if self._ops is None and self._ops_reason is None:
            try:
                self._ops = ops.OpsClient()
            except MissingCredentials:
                self._ops_reason = (
                    "尚未設定 EPO OPS 金鑰。把 Consumer Key / Secret 填進 .env 後重開即可"
                    "取得高解析圖式與原文件 PDF（設定步驟見 README）。"
                )
            except Exception as exc:                       # defensive: never break a lookup
                self._ops_reason = f"EPO OPS 無法初始化：{exc}"
        return self._ops

    def _cache_path(self, link: str, filename: str) -> Path | None:
        if not self.cache_dir or not OPS_LINK_RE.match(link):
            return None
        folder = self.cache_dir / link.replace("published-data/images/", "").replace("/", "_")
        folder.mkdir(parents=True, exist_ok=True)
        return folder / filename

    def enrich(self, number: str, *, refresh: bool = False) -> dict:
        """What EPO OPS can add for this document: drawing sheets and the original.

        One inquiry call per document and no page bytes — the pages themselves are
        fetched only when the reader actually looks at them (BR-6). The result is
        stored on the card, so a document read twice costs one call, not two.
        """
        stored = self.store.get(number)
        if stored and stored.get("ops") and not refresh:
            return stored["ops"]

        client = self.ops_client()
        if client is None:
            return {"available": False, "source": "EPO OPS", "reason": self._ops_reason}

        try:
            parsed = numbers.normalize(number)
            fmt, resolved = client.resolve(parsed)
            instances = client.instances(resolved, fmt=fmt)
        except numbers.NumberError as exc:
            return {"available": False, "source": "EPO OPS", "reason": f"號碼無法解析：{exc}"}
        except ops.OpsError as exc:
            return {"available": False, "source": "EPO OPS",
                    "reason": f"EPO OPS 取不到此件的影像清單（{_fault(exc)}）。"}

        def pick(desc: str) -> dict | None:
            hit = next((i for i in instances if i.desc == desc), None)
            return hit.as_dict() if hit else None

        payload = {
            "available": True,
            "source": "EPO OPS",
            "resolved": f"{fmt}/{resolved}",
            "drawings": pick("Drawing"),
            "fullimage": pick("FullDocument"),
            "firstpage": pick("FirstPageClipping"),
            "quota": client.usage.summary(),
            "reason": None,
        }
        if not payload["drawings"]:
            payload["reason"] = "EPO OPS 有此件，但沒有單獨的圖式頁（老案常見）；仍可取原文件。"
        self.store.patch(number, {"ops": payload})
        return payload

    def ops_page_png(self, link: str, page: int) -> bytes:
        """One drawing sheet as PNG, from disk if we already paid for it."""
        if not OPS_LINK_RE.match(link):
            raise ResolveError("不合法的 EPO 影像位址。")
        client = self.ops_client()
        if client is None:
            raise ResolveError(self._ops_reason or "EPO OPS 不可用。")
        cached = self._cache_path(link, f"p{page:04d}.png")
        if cached and cached.exists():
            return cached.read_bytes()
        try:
            png = client.drawing_png(link, page)
        except ops.OpsError as exc:
            raise ResolveError(f"EPO OPS 取不到第 {page} 頁（{_fault(exc)}）。") from exc
        if cached:
            cached.write_bytes(png)
        return png

    def ops_document_pdf(self, link: str, pages: int) -> tuple[bytes, int]:
        """The original document, stitched from OPS's per-page PDFs and cached."""
        if not OPS_LINK_RE.match(link):
            raise ResolveError("不合法的 EPO 影像位址。")
        client = self.ops_client()
        if client is None:
            raise ResolveError(self._ops_reason or "EPO OPS 不可用。")
        cached = self._cache_path(link, f"document-{pages}p.pdf")
        if cached and cached.exists():
            return cached.read_bytes(), pages
        try:
            pdf, included = client.document_pdf(link, pages)
        except ops.OpsError as exc:
            raise ResolveError(f"EPO OPS 取不到原文件（{_fault(exc)}）。") from exc
        if cached and included == pages:
            cached.write_bytes(pdf)
        return pdf, included

    def ops_inpadoc(self, number: str) -> dict:
        """INPADOC family and legal events — the reading Google Patents cannot give.

        On demand only: two calls that most lookups never need.
        """
        client = self.ops_client()
        if client is None:
            return {"available": False, "reason": self._ops_reason}
        try:
            parsed = numbers.normalize(number)
            fmt, resolved = client.resolve(parsed)
        except (numbers.NumberError, ops.OpsError) as exc:
            return {"available": False, "reason": f"EPO OPS 無法解析此號碼：{exc}"}

        out: dict = {"available": True, "source": "EPO OPS", "resolved": f"{fmt}/{resolved}"}
        for key, call in (("family", client.family_members), ("legal", client.legal_events)):
            try:
                out[key] = call(resolved, fmt=fmt)
            except ops.OpsError as exc:
                out[key] = []
                out[f"{key}_reason"] = f"EPO OPS 未提供（{_fault(exc)}）。"
        out["quota"] = client.usage.summary()
        return out

    # ------------------------------------------------------------------ #

    def lookup(self, query: str, *, refresh: bool = False) -> dict:
        try:
            parsed = numbers.normalize(query)
        except numbers.NumberError as exc:
            self.store.log_lookup(query, None, False, str(exc))
            raise ResolveError(f"無法辨識這個輸入：{exc}") from exc

        if not parsed.supported:
            self.store.log_lookup(query, None, False, parsed.note)
            raise ResolveError(parsed.note or "此輸入型態目前不支援")

        # An exact match in the library short-circuits the network entirely.
        if not refresh:
            for candidate in parsed.candidates:
                hit = self.store.get(candidate)
                if hit:
                    self.store.log_lookup(query, candidate, True, "from store")
                    return self._upgrade(candidate, hit)

        tried, last_error = [], None
        for candidate in parsed.candidates:
            tried.append(candidate)
            try:
                doc = gp.get(candidate, raw_dir=self.raw_dir, client=self.client, polite_delay=0)
            except gp.FetchError as exc:
                last_error = str(exc)
                continue
            card = self._build_card(query, parsed, doc)
            self.store.put(candidate, card.get("title"), "google_patents", card)
            self.store.log_lookup(query, candidate, True, "fetched")
            return card

        self.store.log_lookup(query, None, False, last_error or "all candidates failed")
        raise ResolveError(
            f"找不到這件專利。已嘗試 {len(tried)} 種 kind code 組合都沒有結果——"
            f"請確認號碼是否正確，或補上 kind code（例如 B2 / A1）。",
            tried=tried,
        )

    # ------------------------------------------------------------------ #

    def _upgrade(self, number: str, card: dict) -> dict:
        """Re-derive reading structure for cards stored before it existed.

        The raw page is already on disk, so this costs no network and no quota.
        Without it, every document read before this upgrade keeps rendering as
        flat text forever — which looks exactly like the feature not working.
        """
        if card.get("description_blocks") or not self.raw_dir:
            return card
        raw = self.raw_dir / f"{number}.html"
        if not raw.exists():
            return card
        try:
            doc = gp.parse(raw.read_text(encoding="utf-8", errors="replace"),
                           number, card.get("url") or "", 200, raw)
        except Exception:                      # a stale raw file must not break a read
            return card
        blocks = doc.fields["description_blocks"].value or []
        claims = doc.fields["claim_list"].value or []
        if not blocks and not claims:
            return card
        updates = {
            "description_blocks": blocks,
            "claims": claims,
            "independent_claims": [c["num"] for c in claims if not c["dependent"]],
            "_upgraded_from_raw": True,
        }
        self.store.patch(number, updates)
        card.update(updates)
        return card

    def _verify_images(self, urls: list[str]) -> tuple[bool, int | None]:
        """Confirm the drawing URLs actually resolve before the UI promises them.

        Google's markup lists image URLs for documents whose files are not public
        yet — the newest publications hand out a path that answers 403. Counting
        URLs in the HTML therefore over-reports coverage, and the user sees broken
        images. One HEAD on the first URL settles it for the whole set, which is
        safe because a document's drawings all live in the same bucket path.
        """
        if not urls:
            return False, None
        try:
            r = self.client.head(urls[0], follow_redirects=True)
            if r.status_code == 405:  # bucket refuses HEAD; fall back to a ranged GET
                r = self.client.get(urls[0], headers={"Range": "bytes=0-0"}, follow_redirects=True)
            return (r.status_code in (200, 206)), r.status_code
        except Exception:
            return False, None

    def _build_card(self, query: str, parsed: numbers.ParsedNumber, doc: gp.PatentDoc) -> dict:
        f = doc.fields

        def val(key):
            return f[key].value if key in f else None

        image_urls = val("images") or []
        images_ok, image_status = self._verify_images(image_urls)
        if image_urls and not images_ok:
            # Present in the markup but unusable — record it as absent, with the real reason.
            f["images"].value = []
            f["images"].selector = f"{f['images'].selector} (URL 存在但 HTTP {image_status})"

        provenance, missing = {}, []
        for key, fld in f.items():
            provenance[key] = {
                "source": "Google Patents",
                "selector": fld.selector,
                "present": fld.present,
            }
            if not fld.present:
                reason_key = "images_forbidden" if (key == "images" and image_urls) else key
                missing.append(
                    {"field": key, "reason": ABSENCE_REASON.get(reason_key, "此來源未提供此欄位。")}
                )

        claims = val("claim_list") or []
        independent = [c for c in claims if not c["dependent"]]

        def capped(key):
            items = val(key) or []
            cap = DISPLAY_CAPS.get(key)
            if cap and len(items) > cap:
                return {"items": items[:cap], "total": len(items), "truncated": True, "cap": cap}
            return {"items": items, "total": len(items), "truncated": False, "cap": cap}

        return {
            "query": query,
            "canonical": parsed.canonical,
            "espacenet": parsed.espacenet,
            "kind_of_document": parsed.kind_of_document,
            "number": doc.number,
            "url": doc.url,
            "title": val("title"),
            "abstract": val("abstract"),
            "description": val("description"),
            "description_blocks": val("description_blocks") or [],
            "claims_text": val("claims"),
            "claims": claims,
            "independent_claims": [c["num"] for c in independent],
            "images": val("images") or [],
            "images_declared": len(image_urls),   # what the page claimed, before verification
            "pdf_link": val("pdf_link"),
            "classifications": capped("classifications"),
            "family": capped("family"),
            "similar_documents": capped("similar_documents"),
            "backward_citations": capped("backward_citations"),
            "forward_citations": capped("forward_citations"),
            "legal_status": val("legal_status"),
            "legal_events": capped("legal_events"),
            "publication_date": val("publication_date"),
            "filing_date": val("filing_date"),
            "priority_date": val("priority_date"),
            "assignee": val("assignee") or [],
            "inventors": val("inventors") or [],
            "provenance": provenance,
            "missing": missing,
            "links": {
                "google": doc.url,
                "espacenet": f"https://worldwide.espacenet.com/patent/search?q=pn%3D{parsed.espacenet or doc.number}",
                "patentscope": f"https://patentscope.wipo.int/search/en/result.jsf?query={doc.number}",
            },
            "ops": None,          # filled by enrich(), on demand, from EPO OPS
            "_from_store": False,
        }

    def close(self) -> None:
        self.client.close()
        if self._ops is not None:
            self._ops.close()
