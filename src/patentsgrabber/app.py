"""FastAPI app.

Serves one static page plus a small JSON API. Deliberately no Node/React: the
user has to be able to touch the real thing today, and a build step would sit
between them and that. The page is one file for the same reason the program is
one process — there is nothing to assemble before it runs.

Two things in here are not about patents at all and are load-bearing anyway:
`_guard` (below), which is what makes it safe for a page in a browser to hold
this program's credentials, and the `/api/settings` group, which is what makes
the program deliverable to somebody who does not have a source checkout.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import config, paths
from .service import ResolveError, Service
from .sources import epo_ops as ops

VERSION = "1.0.0"

paths.ensure_data_dirs()
WEB = paths.web_dir()

service = Service(paths.db_path(), raw_dir=paths.raw_dir(), cache_dir=paths.cache_dir())


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    service.close()


app = FastAPI(title="PatentsGrabber", version=VERSION, lifespan=lifespan)


# --------------------------------------------------------------------- guard

# Loopback is not the same as private. Any page in any tab of this browser can
# issue requests to 127.0.0.1, and this program now holds a credential and
# spends a metered quota — so "only this machine can reach it" was never the
# whole story, and stopped being most of it the moment settings became writable.
#
# Three checks, each closing a different door, all of them free:
#
#   Host       — a name the attacker controls, re-pointed at 127.0.0.1 (DNS
#                rebinding), arrives with `Host: evil.example`. Ours never does.
#                This is the one that matters, because it is the only attack
#                that turns a same-origin policy into no policy at all.
#   Origin     — present on every cross-origin write a browser makes. Absent on
#                our own GETs, which is why its absence cannot be an error.
#   Sec-Fetch-Site — `cross-site` on an <img>/<script>/<form> reach-in, which is
#                the one shape that carries no Origin. Chromium, Firefox and
#                Safari all send it; a client that does not is not blocked by
#                it, so it hardens the common case without inventing a rule the
#                other checks cannot back up.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _host_is_local(value: str | None) -> bool:
    """Is this `Host` (or an Origin's netloc) one of the loopback names?

    Parsed rather than pattern-matched, because the two forms do not agree on
    what a colon means: `127.0.0.1:8000` has one and it separates the port,
    `[::1]:8000` has four and only the last does. Counting colons got the
    bracketed form wrong — harmless while the server binds IPv4 only, and a
    silent lockout the day it does not.
    """
    if not value:
        return False
    host = value.strip().lower()
    if host.startswith("["):                       # [::1] or [::1]:8000
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:                     # 127.0.0.1:8000
        host = host.rsplit(":", 1)[0]
    return host in LOCAL_HOSTS


@app.middleware("http")
async def _guard(request: Request, call_next):
    if not _host_is_local(request.headers.get("host")):
        return JSONResponse(
            status_code=421,
            content={"error": "這個程式只接受來自本機的連線（收到的 Host 不是 127.0.0.1）。"},
        )
    origin = request.headers.get("origin")
    if origin and not _host_is_local(urlsplit(origin).netloc):
        return JSONResponse(status_code=403, content={"error": "跨站請求已被拒絕。"})
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse(status_code=403, content={"error": "跨站請求已被拒絕。"})
    return await call_next(request)


# ---------------------------------------------------------------- patent API

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
def api_query(q: str, refresh: bool = False, field: str = "pa", us_only: bool = True,
              scope: str | None = None, start: int = 1, size: int = 50):
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
    return service.search(text, field=field, us_only=us_only, scope=scope,
                          start=start, size=size)


@app.get("/api/search")
def api_search(q: str, field: str = "pa", us_only: bool = True, scope: str | None = None,
               start: int = 1, size: int = 50):
    """Search explicitly, e.g. when refining to one applicant-name spelling."""
    return service.search(q.strip(), field=field, us_only=us_only, scope=scope,
                          start=start, size=size)


@app.get("/api/library")
def api_library():
    return {"count": service.store.count(), "recent": service.store.recent(40)}


@app.get("/api/enrich")
def api_enrich(q: str, refresh: bool = False):
    """What EPO OPS adds for this document. Costs one OPS call, no page bytes."""
    return service.enrich(q.strip(), refresh=refresh)


@app.get("/api/ops/page")
def api_ops_page(link: str, page: int = 1):
    """One EPO drawing sheet, converted to PNG so a browser can show it.

    `X-Page-Source` says whether this sheet cost a request or came off disk. It
    is for diagnosis (curl, the network panel) and NOT what the page reads: an
    `<img>` load exposes no response headers to script, so the strip asks
    `/api/ops/cached-pages` instead. Said plainly here because the first version
    of this docstring claimed the page showed this header, which it cannot.
    """
    try:
        png, cached = service.ops_page_png(link, max(1, page))
    except ResolveError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    # Cached hard: a sheet already paid for against the quota never changes.
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "X-Page-Source": "cache" if cached else "ops"})


@app.get("/api/ops/cached-pages")
def api_ops_cached_pages(link: str):
    """Which sheets of this document are already on disk. Costs nothing.

    Exists so the page strip can mark them before anything is clicked: without
    it, "is this going to cost me a request?" is only answerable by spending one.
    """
    return {"link": link, "pages": service.ops_cached_pages(link)}


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


# -------------------------------------------------------------- settings API

class SettingsIn(BaseModel):
    """Only what the panel may send. A field left out is a field left alone.

    The browser is never given a secret, so it cannot send one back; an omitted
    `ops_secret` therefore has to mean "keep the stored one", and it does. That
    is the whole reason this is three optional fields and not a settings object.
    """

    ops_key: str | None = None
    ops_secret: str | None = None
    ops_base_url: str | None = None


def _sync_from_file() -> bool:
    """Adopt the settings file if it has changed since we last looked.

    The panel promises "re-read on every open, another window or a hand edit may
    have changed it". Without this it only re-read the FILE, while `configured`
    and the live OPS client still came from the environment loaded at startup —
    so a hand-edited key showed its new hint in the panel while every request
    kept using the old one. Two answers to one question is worse than a stale
    one. Returns True when something actually changed.
    """
    before = config.load_ops(required=False)
    config.reload_env()
    after = config.load_ops(required=False)
    if (before is None) != (after is None) or (after is not None and after != before):
        service.reset_ops()
        return True
    return False


def _settings_state() -> dict:
    stored = config.read_settings_file()
    cfg = config.load_ops(required=False)
    return {
        "ops": {
            "configured": cfg is not None,
            # Hints, never values: enough to tell two keys apart, not enough to use.
            "key_hint": config.hint(stored.get("OPS_CONSUMER_KEY", "")),
            "secret_hint": config.hint(stored.get("OPS_CONSUMER_SECRET", "")),
            "base_url": (stored.get("OPS_BASE_URL") or config.DEFAULT_BASE_URL),
            "default_base_url": config.DEFAULT_BASE_URL,
        },
        "paths": paths.describe(),
        "version": VERSION,
        # Non-empty only when a shell variable disagrees with the file. Silence
        # here would be the "I changed it and nothing happened" bug.
        "shadowed_by_environment": config.shadowed_keys(),
    }


@app.get("/api/settings")
def api_settings_get():
    adopted = _sync_from_file()
    state = _settings_state()
    # Reported, not silent: "I edited the file and the panel changed" is a
    # different event from "the panel just re-rendered", and only one of them
    # explains why the next lookup behaves differently.
    state["adopted_file_change"] = adopted
    return state


def _effective(body: SettingsIn) -> tuple[str, str, str]:
    """What a test would use: what was typed, falling back to what is stored."""
    stored = config.read_settings_file()
    key = (body.ops_key or stored.get("OPS_CONSUMER_KEY") or "").strip()
    secret = (body.ops_secret or stored.get("OPS_CONSUMER_SECRET") or "").strip()
    base = (body.ops_base_url or stored.get("OPS_BASE_URL")
            or config.DEFAULT_BASE_URL).strip().rstrip("/")
    return key, secret, base


def _probe(key: str, secret: str, base: str) -> dict:
    """Ask OPS whether this pair works. One auth request, no data quota."""
    if not key or not secret:
        return {"ok": False, "detail": "Consumer Key 或 Consumer Secret 是空的。"}
    try:
        # The SAME validator the save path uses, and for the sharper reason: this
        # is the call that actually sends the credential. A test button that
        # accepted a host the save button refuses would be the exfiltration path
        # the refusal exists to close.
        for name, value in (("OPS_CONSUMER_KEY", key), ("OPS_CONSUMER_SECRET", secret),
                            ("OPS_BASE_URL", base)):
            config.check_value(name, value)
    except config.RejectedValue as exc:
        return {"ok": False, "detail": str(exc)}
    client = ops.OpsClient(config.OpsConfig(key=key, secret=secret, base_url=base))
    try:
        client.token()
        return {"ok": True, "detail": f"金鑰有效，{base} 已回應 access token。"}
    except ops.OpsAuthError as exc:
        return {"ok": False, "detail": str(exc)}
    except ops.OpsError as exc:
        return {"ok": False, "detail": str(exc)}
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"連不上 {base}（{type(exc).__name__}）。"}
    finally:
        client.close()


@app.post("/api/settings/test")
def api_settings_test(body: SettingsIn):
    """Check a credential WITHOUT saving it. Nothing typed here reaches disk."""
    return _probe(*_effective(body))


@app.post("/api/settings")
def api_settings_post(body: SettingsIn):
    """Save, then re-read. The result is the new state, never the values saved.

    `write_settings` validates every value before it writes ANY of them, so a
    rejected field cannot leave the file half-updated.
    """
    try:
        config.write_settings({
            "OPS_CONSUMER_KEY": body.ops_key,
            "OPS_CONSUMER_SECRET": body.ops_secret,
            "OPS_BASE_URL": body.ops_base_url,
        })
    except config.RejectedValue as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    service.reset_ops()                       # the old client held the old key
    state = _settings_state()
    state["saved_to"] = str(config.settings_file())
    return state


@app.post("/api/settings/clear")
def api_settings_clear():
    """Remove the credential from disk. The panel's only destructive action."""
    config.write_settings({"OPS_CONSUMER_KEY": "", "OPS_CONSUMER_SECRET": ""})
    service.reset_ops()
    state = _settings_state()
    state["cleared"] = True
    return state


# ------------------------------------------------------------------ the page

@app.get("/api/health")
def api_health():
    """Is this port ours? The launcher asks before deciding to start a second one."""
    return {"app": "PatentsGrabber", "version": VERSION,
            "data_root": str(paths.data_root())}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
