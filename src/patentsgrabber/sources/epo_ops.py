"""EPO Open Patent Services (OPS) v3.2 adapter.

Auth is OAuth2 client-credentials: POST {base}/auth/accesstoken with HTTP Basic
(consumer key : consumer secret) and grant_type=client_credentials, returning a
bearer token valid for ~20 minutes. The token is cached and refreshed a minute
early rather than on failure, so a request never fails for a reason we could
have prevented.

BR-6 (docs/01-concept-note.md): the free tier's real ceiling is a fair-use
4 GB/week, so this client tracks bytes served and surfaces the quota headers OPS
returns instead of discovering the limit by hitting it.

Nothing here logs or returns the credential; see config.OpsConfig.describe().
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import OpsConfig, load_ops

TOKEN_PATH = "/auth/accesstoken"
REST = "/rest-services"
REFRESH_MARGIN_S = 60


class OpsError(RuntimeError):
    """An OPS call failed. Carries status and the service's own message."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class OpsAuthError(OpsError):
    """The credential was rejected — distinct from a data-level failure."""


@dataclass
class Usage:
    """What this process has spent, so BR-6 is observable rather than assumed."""

    requests: int = 0
    bytes_served: int = 0
    last_throttle: str | None = None          # OPS X-Throttling-Control header
    last_quota_headers: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        mb = self.bytes_served / 1_048_576
        return (f"{self.requests} requests, {mb:.2f} MB this session"
                + (f" | throttle: {self.last_throttle}" if self.last_throttle else ""))


class OpsClient:
    def __init__(self, cfg: OpsConfig | None = None, timeout: float = 40.0):
        self.cfg = cfg or load_ops()
        self.usage = Usage()
        self._token: str | None = None
        self._expires_at: float = 0.0
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

    def get(self, path: str, *, accept: str = "application/json",
            params: dict[str, Any] | None = None, raw: bool = False) -> Any:
        """GET a rest-services path. `raw` returns the httpx.Response (binary)."""
        url = self.cfg.base_url + REST + ("" if path.startswith("/") else "/") + path
        r = self._client.get(
            url,
            headers={"Authorization": f"Bearer {self.token()}", "Accept": accept},
            params=params,
        )
        self.usage.requests += 1
        self.usage.bytes_served += len(r.content)
        if "X-Throttling-Control" in r.headers:
            self.usage.last_throttle = r.headers["X-Throttling-Control"]
        self.usage.last_quota_headers = {
            k: v for k, v in r.headers.items() if "quota" in k.lower() or "throttl" in k.lower()
        }

        if r.status_code == 403:
            raise OpsError("OPS 回 403 — 可能是配額用盡或此服務未授權給你的帳號。",
                           403, r.text[:400])
        if r.status_code == 404:
            raise OpsError(f"OPS 查無此資料：{path}", 404, r.text[:300])
        if r.status_code >= 400:
            raise OpsError(f"OPS {r.status_code} on {path}", r.status_code, r.text[:400])

        if raw:
            return r
        return r.json() if accept.endswith("json") else r.text

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

    def number_convert(self, number: str, *, from_fmt: str = "epodoc",
                       to_fmt: str = "docdb", kind: str = "publication") -> dict:
        """OPS number-service — the canonical fix for format-mismatch lookups."""
        return self.get(f"number-service/{kind}/{from_fmt}/{number}/{to_fmt}")

    def search(self, cql: str, *, start: int = 1, end: int = 25) -> dict:
        """CQL search. Applicant is `pa=`, inventor `in=`, title+abstract `ta=`."""
        return self.get("published-data/search",
                        params={"q": cql, "Range": f"{start}-{end}"})

    def close(self) -> None:
        self._client.close()
