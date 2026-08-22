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
from fastapi.responses import FileResponse, JSONResponse

from .service import ResolveError, Service

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "web"
DB = ROOT / "var" / "library.sqlite3"
RAW = ROOT / "var" / "raw"

service = Service(DB, raw_dir=RAW)


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


@app.get("/api/library")
def api_library():
    return {"count": service.store.count(), "recent": service.store.recent(40)}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
