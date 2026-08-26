"""FastAPI app for Stage 0.

Serves one static page plus a small JSON API. Deliberately no Node/React at this
stage: Stage 0 exists to let the user touch the real thing today, and a build
step would sit between them and that. React arrives in Stage 1, when side-by-side
comparison and annotation make the UI genuinely stateful.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from .service import ResolveError, Service

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
DB = ROOT / "var" / "library.sqlite3"
RAW = ROOT / "var" / "raw"
OPS_CACHE = ROOT / "var" / "ops-cache"

service = Service(DB, raw_dir=RAW, cache_dir=OPS_CACHE)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    service.close()


app = FastAPI(title="PatentsGrabber", version="0.1.0", lifespan=lifespan)


@app.get("/api/patent")
def api_patent(q: str, refresh: bool = False):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="空的查詢")
    try:
        return service.lookup(q.strip(), refresh=refresh)
    except ResolveError as exc:
        return JSONResponse(
            status_code=404,
            content={"error": str(exc), "tried": getattr(exc, "tried", [])},
        )


@app.get("/api/query")
def api_query(q: str, refresh: bool = False, field: str = "pa",
              us_only: bool = True, start: int = 1, size: int = 50):
    """The single front door: a number gives a card, anything else gives a list.

    BR-1 says the reader never has to declare which kind of thing they typed, so
    the classification lives here rather than in the page.
    """
    text = (q or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="空的查詢")
    if service.classify(text) == "number":
        try:
            card = service.lookup(text, refresh=refresh)
            return {"kind": "card", **card}
        except ResolveError as exc:
            # Google not having it is not the same as the document not existing:
            # OPS carries recent publications months before Google indexes them.
            fallback = service.card_from_ops(text)
            if fallback:
                return {"kind": "card", **fallback}
            return JSONResponse(status_code=404,
                                content={"kind": "card", "error": str(exc),
                                         "tried": getattr(exc, "tried", [])})
    return service.search(text, field=field, us_only=us_only, start=start, size=size)


@app.get("/api/search")
def api_search(q: str, field: str = "pa", us_only: bool = True,
               start: int = 1, size: int = 50):
    """Search explicitly, e.g. when refining to one applicant-name spelling."""
    return service.search(q.strip(), field=field, us_only=us_only, start=start, size=size)


@app.get("/api/library")
def api_library():
    return {"count": service.store.count(), "recent": service.store.recent(40)}


@app.get("/api/enrich")
def api_enrich(q: str, refresh: bool = False):
    """What EPO OPS adds for this document. Costs one OPS call, no page bytes."""
    return service.enrich(q.strip(), refresh=refresh)


@app.get("/api/ops/page")
def api_ops_page(link: str, page: int = 1):
    """One EPO drawing sheet, converted to PNG so a browser can show it."""
    try:
        png = service.ops_page_png(link, max(1, page))
    except ResolveError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    # Cached hard: a sheet already paid for against the quota never changes.
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/ops/pdf")
def api_ops_pdf(link: str, pages: int = 1, name: str = "document"):
    """The original document, stitched from EPO's per-page PDFs."""
    try:
        pdf, included = service.ops_document_pdf(link, max(1, pages))
    except ResolveError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "document"
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{safe}-EPO-{included}p.pdf"',
        "X-Pages-Included": str(included),
    })


@app.get("/api/ops/inpadoc")
def api_ops_inpadoc(q: str):
    """INPADOC family + legal events. Two OPS calls, so: only when asked for."""
    return service.ops_inpadoc(q.strip())


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
