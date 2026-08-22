"""Credential and settings loading.

Credentials live in `.env` (git-ignored) and are read through here only. Nothing
in this module ever returns a secret in a printable form: `describe()` exists so
diagnostics can say "the key is present and looks right" without ever putting the
value into a log, a traceback, a screenshot, or a chat window.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"

load_dotenv(ENV_PATH)

PLACEHOLDERS = {"", "<your-consumer-key>", "<your-consumer-secret>", "changeme"}


class MissingCredentials(RuntimeError):
    """No usable OPS credentials — carries instructions, never a value."""


@dataclass(frozen=True)
class OpsConfig:
    key: str
    secret: str
    base_url: str

    def describe(self) -> str:
        """Safe-to-print evidence that a credential is loaded. Never the value."""
        return (
            f"key: {len(self.key)} chars, ends '…{self.key[-3:]}' | "
            f"secret: {len(self.secret)} chars, ends '…{self.secret[-3:]}' | "
            f"base: {self.base_url}"
        )

    def __repr__(self) -> str:  # defensive: keep secrets out of tracebacks
        return f"OpsConfig({self.describe()})"


def load_ops(required: bool = True) -> OpsConfig | None:
    key = (os.getenv("OPS_CONSUMER_KEY") or "").strip()
    secret = (os.getenv("OPS_CONSUMER_SECRET") or "").strip()
    base = (os.getenv("OPS_BASE_URL") or "https://ops.epo.org/3.2").strip().rstrip("/")

    if key in PLACEHOLDERS or secret in PLACEHOLDERS:
        if not required:
            return None
        raise MissingCredentials(
            "找不到可用的 EPO OPS 金鑰。\n"
            f"  1. 複製 .env.example 成 .env：  copy .env.example .env\n"
            f"  2. 用文字編輯器打開 {ENV_PATH}\n"
            "  3. 把 OPS_CONSUMER_KEY / OPS_CONSUMER_SECRET 換成你自己的值\n"
            "  4. 存檔後重跑。.env 已被 git 忽略，不會進版控。\n"
            "  （金鑰只放這個檔案，不要貼進其他檔案、commit 訊息或對話。）"
        )
    return OpsConfig(key=key, secret=secret, base_url=base)


def ops_available() -> bool:
    return load_ops(required=False) is not None
