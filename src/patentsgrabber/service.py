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

# Structured fields only (CIM §4 boundary 1): no free-text concept search.
SEARCH_FIELDS = {
    "pa": "申請人",
    "in": "發明人",
    "ta": "標題／摘要",
    "ti": "標題",
    "ab": "摘要",
}
FIELD_PREFIX_RE = re.compile(r"^(pa|in|ta|ti|ab)\s*=\s*", re.IGNORECASE)
SEARCH_PAGE = 50          # ~5-8 KB per result measured; 50 keeps a page under 400 KB

# Version of the OPS-built card, for the same reason google_patents.READING_SCHEMA
# exists: a stored card must not keep whatever the extractor produced on the day
# it was written. BUMP when card_from_ops or the OPS field extraction changes.
#   1  first version
#   2  abstract cleaned of paragraph numbers and <3 > subscript markup
OPS_CARD_SCHEMA = 2


def parse_query(text: str, field: str = "pa") -> tuple[str, str]:
    """(field, term). An explicit `in=Larry Page` prefix wins over the default."""
    term = (text or "").strip()
    match = FIELD_PREFIX_RE.match(term)
    if match:
        field, term = match.group(1).lower(), term[match.end():].strip()
    if field not in SEARCH_FIELDS:
        field = "pa"
    return field, term


def build_cql(term: str, *, field: str = "pa", us_only: bool = True) -> str:
    """Build the CQL query. The term is quoted, never concatenated raw.

    Inside double quotes CQL treats AND / OR / NOT as literal text, so quoting
    is what keeps a company name with "and" in it from becoming an operator —
    and what stops a stray quote in the input from rewriting the query. OPS
    documents no escape sequence, so an embedded quote is dropped rather than
    passed through.
    """
    safe = " ".join((term or "").replace('"', " ").split())
    cql = f'{field}="{safe}"'
    if us_only:
        # pn=US restricts to US publications. Verified 2026-08-26: a 50-row page
        # of `pa="Corning" AND pn=US` came back 50/50 US. `cc=US` is not a valid
        # index and `pn=US*` is refused (truncation needs 3 characters).
        cql += " AND pn=US"
    return cql

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

    # --------------------------------------------------------------- search

    def search(self, text: str, *, field: str = "pa", us_only: bool = True,
               start: int = 1, size: int = SEARCH_PAGE) -> dict:
        """Structured-field search over EPO OPS (BR-1's second entry point).

        Google Patents' search is excluded by its robots.txt (BR-7), so this can
        only go to OPS. Everything the reader needs to distrust the result set
        correctly is returned with it: the true total, how much of it is
        reachable at all, and which applicant-name spellings actually appear.
        """
        field, term = parse_query(text, field)
        if not term:
            return {"kind": "results", "available": False, "query": "",
                    "reason": "空的查詢。輸入公司名或發明人姓名，或直接輸入專利號碼。"}

        client = self.ops_client()
        if client is None:
            return {"kind": "results", "available": False, "query": term,
                    "reason": self._ops_reason or "EPO OPS 不可用。"}

        cql = build_cql(term, field=field, us_only=us_only)
        start = max(1, min(int(start or 1), ops.SEARCH_MAX_DEPTH))
        size = max(1, min(int(size or SEARCH_PAGE), ops.SEARCH_MAX_SPAN))
        end = min(start + size - 1, ops.SEARCH_MAX_DEPTH)

        spent = client.usage.bytes_served
        try:
            found = client.search_biblio(cql, start=start, end=end)
        except ops.OpsError as exc:
            # "No results" is an answer, not a failure: OPS says it with a 404.
            if exc.status == 404 and "EntityNotFound" in (exc.body or ""):
                return {"kind": "results", "available": True, "query": term, "field": field,
                        "us_only": us_only, "cql": cql, "total": 0, "fetched": 0,
                        "results": [], "applicant_variants": [],
                        "reason": f"OPS 沒有符合 {SEARCH_FIELDS[field]}「{term}」的公開文件。",
                        "quota": client.usage.summary()}
            self.store.log_lookup(term, None, False, f"search failed: {_fault(exc)}")
            return {"kind": "results", "available": False, "query": term,
                    "reason": f"EPO OPS 檢索失敗（{_fault(exc)}）。"}

        # A row is only clickable if our card path can actually resolve it: the
        # search is worldwide-capable but the reader is US-only (Stage 1).
        for row in found["results"]:
            row["openable"] = self._openable(row)

        found.update({
            "kind": "results",
            "available": True,
            "query": term,
            "field": field,
            "field_label": SEARCH_FIELDS[field],
            "us_only": us_only,
            "cql": cql,
            "page_size": size,
            "reachable": min(found["total"], ops.SEARCH_MAX_DEPTH),
            "depth_capped": found["total"] > ops.SEARCH_MAX_DEPTH,
            "max_depth": ops.SEARCH_MAX_DEPTH,
            "bytes": client.usage.bytes_served - spent,
            "quota": client.usage.summary(),
        })
        self.store.log_lookup(term, None, True,
                              f"search {cql} -> {found['total']} hits, page {start}-{end}")
        return found

    def card_from_ops(self, query: str) -> dict | None:
        """A card built from OPS for a document Google Patents does not carry.

        Measured 2026-08-26: Google Patents had NONE of 24 US publications from
        2026-06 to 2026-08, while OPS search returns them newest-first — so the
        first page of a company search is exactly the part the Google-only card
        path cannot open. Failing there would make the search look broken while
        the document is sitting in OPS. What OPS cannot supply for a US case is
        the full text (established fact F-1), and that absence is stated rather
        than left blank (BR-3).
        """
        client = self.ops_client()
        if client is None:
            return None
        try:
            parsed = numbers.normalize(query)
            fmt, resolved = client.resolve(parsed)
            row = ops.parse_biblio(client.biblio(resolved, fmt=fmt))
        except (numbers.NumberError, ops.OpsError):
            return None
        if not row:
            return None

        absent = (
            "Google Patents 尚未收錄此件（最新公開案常見，實測 2026 年 6–8 月的公開案"
            "一件都還沒有），而 OPS 的全文不涵蓋美國案，所以現在沒有可複製的全文。"
            "原文件掃描與圖式可以看。"
        )
        empty = {"items": [], "total": 0, "truncated": False, "cap": None}
        card = {
            "query": query,
            "canonical": parsed.canonical,
            "espacenet": parsed.espacenet,
            "kind_of_document": parsed.kind_of_document,
            "number": row["number"] or parsed.canonical,
            "url": None,
            "title": row["title"],
            "abstract": row["abstract"],
            "description": None,
            "description_blocks": [],
            "claims_text": None,
            "claims": [],
            "independent_claims": [],
            "images": [],
            "images_declared": 0,
            "pdf_link": None,
            "classifications": {"items": row["classifications"], "total": len(row["classifications"]),
                                "truncated": False, "cap": None},
            "family": dict(empty), "similar_documents": dict(empty),
            "backward_citations": dict(empty), "forward_citations": dict(empty),
            "legal_events": dict(empty),
            "legal_status": None,
            "publication_date": row["date"],
            "filing_date": None,
            "priority_date": None,
            "assignee": row["applicants"],
            "inventors": row["inventors"],
            "provenance": {key: {"source": "EPO OPS", "selector": f"{fmt}/{resolved} biblio",
                                 "present": True}
                           for key in ("title", "abstract", "classifications",
                                       "assignee", "inventors", "publication_date")},
            "missing": [{"field": field, "reason": absent}
                        for field in ("description", "claims", "images", "pdf_link")],
            "links": {
                "google": f"https://patents.google.com/patent/{row['number']}/en",
                "espacenet": f"https://worldwide.espacenet.com/patent/search?q=pn%3D{row['number']}",
                "patentscope": f"https://patentscope.wipo.int/search/en/result.jsf?query={row['number']}",
            },
            "ops": None,
            "_reading_schema": gp.READING_SCHEMA,
            "_ops_card_schema": OPS_CARD_SCHEMA,
            "_ops_only": True,
            "_ops_only_reason": absent,
            "_from_store": False,
        }
        self.store.put(card["number"], card["title"], "epo_ops", card)
        self.store.log_lookup(query, card["number"], True, "card from OPS (no Google record)")
        return card

    @staticmethod
    def _openable(row: dict) -> bool:
        try:
            return numbers.normalize(row.get("number") or "").supported
        except numbers.NumberError:
            return False

    def classify(self, text: str) -> str:
        """'number' or 'query' — the user never has to say which (BR-1)."""
        term = (text or "").strip()
        if not term:
            return "query"
        if FIELD_PREFIX_RE.match(term):
            return "query"
        try:
            return "number" if numbers.normalize(term).supported else "query"
        except numbers.NumberError:
            return "query"

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

        # An exact match in the library short-circuits the network entirely —
        # unless it is an OPS-only card, which is provisional by construction:
        # Google Patents indexes newer publications eventually, and when it does
        # the reader should get the full text instead of the stub forever.
        provisional = None
        if not refresh:
            for candidate in parsed.candidates:
                hit = self.store.get(candidate)
                if hit and hit.get("_ops_only"):
                    provisional = hit
                    break
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

        if provisional:                       # Google still does not have it
            if provisional.get("_ops_card_schema") != OPS_CARD_SCHEMA:
                rebuilt = self.card_from_ops(query)   # the extractor has moved on
                if rebuilt:
                    return rebuilt
            self.store.log_lookup(query, provisional.get("number"), True, "OPS-only card from store")
            return provisional
        self.store.log_lookup(query, None, False, last_error or "all candidates failed")
        raise ResolveError(
            f"找不到這件專利。已嘗試 {len(tried)} 種 kind code 組合都沒有結果——"
            f"請確認號碼是否正確，或補上 kind code（例如 B2 / A1）。",
            tried=tried,
        )

    # ------------------------------------------------------------------ #

    def _upgrade(self, number: str, card: dict) -> dict:
        """Re-derive reading structure whenever the extractor is newer than the card.

        The raw page is already on disk, so this costs no network and no quota.
        Without it a library entry keeps whatever the parser produced on the day
        it was read — which, after a parser fix, looks exactly like the fix not
        working. The stored schema version is what makes this automatic instead
        of something someone has to remember.
        """
        if not self.raw_dir:
            return card
        if card.get("_reading_schema") == gp.READING_SCHEMA:
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
        # Even an empty result is recorded: a document that genuinely has no
        # description (a reexamination certificate) must not be re-parsed on
        # every single read.
        updates = {
            "description_blocks": blocks,
            "claims": claims,
            "independent_claims": [c["num"] for c in claims if not c["dependent"]],
            "_reading_schema": gp.READING_SCHEMA,
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
            "_reading_schema": gp.READING_SCHEMA,
            "_from_store": False,
        }

    def close(self) -> None:
        self.client.close()
        if self._ops is not None:
            self._ops.close()
