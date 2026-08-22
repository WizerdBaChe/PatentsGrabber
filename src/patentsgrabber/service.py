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

from pathlib import Path

import httpx

from . import numbers
from .sources import google_patents as gp
from .store import Store

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
    "pdf_link": "Google Patents 尚未提供此件的 PDF（最新公開案常見）。Stage 1 接上 EPO OPS 後可改由 fullimage 逐頁取得。",
    "images": "Google Patents 未提供可直接取用的圖檔（約 2000 年以前的老案常見，僅有整份 PDF）。",
    "images_forbidden": (
        "此件的圖檔網址存在於頁面標記中，但 Google 的儲存空間拒絕存取 (HTTP 403)——"
        "最新公開案常見，圖片尚未對外開放。Stage 1 接上 EPO OPS 後可改由其 images 服務取得。"
    ),
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


class Service:
    def __init__(self, db_path: Path, raw_dir: Path | None = None):
        self.store = Store(db_path)
        self.raw_dir = raw_dir
        self.client = httpx.Client(headers=gp.HEADERS, timeout=30.0, follow_redirects=True)

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
                    return hit

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
            "_from_store": False,
        }

    def close(self) -> None:
        self.client.close()
