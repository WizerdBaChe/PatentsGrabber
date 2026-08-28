"""Credential and settings loading — and the only place that writes them.

Credentials live in a dotenv file (`paths.settings_path()`, git-ignored in a
checkout) and are read through here only. Nothing in this module ever returns a
secret in a printable form: `describe()` exists so diagnostics can say "the key
is present and looks right" without ever putting the value into a log, a
traceback, a screenshot, or a chat window.

Writing is new in this round, because a program that can only be configured by
editing a file in its own source tree cannot be handed to anybody. The write
path obeys the same rule as the read path: it takes a value in, it never gives
one back.

**The file wins over the process environment.** `load_dotenv(override=True)` is
deliberate: what somebody sets in the settings panel has to be what the program
then uses, or the panel is lying. A pre-existing environment variable is not
silently discarded, though — `shadowed_keys()` reports every one whose value the
file overrode, so the difference is visible instead of mysterious.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from . import paths

KEYS = ("OPS_CONSUMER_KEY", "OPS_CONSUMER_SECRET", "OPS_BASE_URL")
DEFAULT_BASE_URL = "https://ops.epo.org/3.2"

# What the process environment held BEFORE the file was applied. Captured once,
# at import, because after `override=True` there is no other way to know.
_PROCESS_ENV: dict[str, str | None] = {k: os.environ.get(k) for k in KEYS}

PLACEHOLDERS = {"", "<your-consumer-key>", "<your-consumer-secret>", "changeme"}


def settings_file() -> Path:
    """The dotenv file in effect. A function, not a constant: under a packaged
    build the answer depends on the environment, which a module-level constant
    would freeze at import time."""
    return paths.settings_path()


def reload_env() -> None:
    """Re-read the settings file into the process environment."""
    path = settings_file()
    if path.exists():
        load_dotenv(path, override=True)


reload_env()

# Kept for the tools that print it. Correct in a checkout, which is the only
# place those tools run.
ENV_PATH = paths.REPO_ROOT / ".env"


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


#: A real OPS consumer key is 24-48 characters. Below this, four characters is a
#: large fraction of the whole thing, so the hint stops being a hint — say the
#: value is too short instead, which is the useful answer anyway.
HINT_MIN_LEN = 12


def hint(value: str) -> str:
    """A one-line, non-reversible description of a credential.

    Four characters of a 24-character key is not enough to use and is enough to
    tell two keys apart, which is the only question the panel has to answer.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) < HINT_MIN_LEN:
        return f"{len(value)} 字元（比 OPS 金鑰短很多，可能貼錯）"
    return f"{len(value)} 字元 · 結尾 …{value[-4:]}"


class RejectedValue(ValueError):
    """A setting the panel may not store. Carries a sentence, never the value."""


# Everything this program talks to at EPO lives under one domain. The base URL is
# documented as "leave it alone unless the EPO publishes a new API version", so
# constraining it costs no legitimate use — and it closes the one path by which a
# form field could send the CREDENTIAL somewhere else: the key and secret travel
# as an HTTP Basic header to whatever host this names.
ALLOWED_BASE_HOSTS = ("epo.org",)

_CONTROL_CHARS = set(range(0x00, 0x20)) | {0x7F}


def check_value(name: str, value: str) -> str:
    """Validate one setting on its way in. Returns it, or raises RejectedValue.

    The file format is one `KEY=value` per line, so a value carrying a newline
    would write a second setting nobody asked for. That is an injection at a
    trust boundary, and the boundary is here.
    """
    value = (value or "").strip()
    if any(ord(ch) in _CONTROL_CHARS for ch in value):
        raise RejectedValue(f"{name} 含有換行或控制字元，無法存進設定檔。"
                            "請重新複製一次，不要連同前後的空行一起貼。")
    if name == "OPS_BASE_URL" and value:
        from urllib.parse import urlsplit
        parts = urlsplit(value)
        if parts.scheme != "https":
            raise RejectedValue("OPS 位址必須是 https:// 開頭。")
        host = (parts.hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in ALLOWED_BASE_HOSTS):
            raise RejectedValue(
                f"OPS 位址只接受 {'／'.join(ALLOWED_BASE_HOSTS)} 底下的主機。"
                "金鑰會以 HTTP Basic 標頭送到這個位址，所以它不能指向別的地方。")
    return value


def load_ops(required: bool = True) -> OpsConfig | None:
    key = (os.getenv("OPS_CONSUMER_KEY") or "").strip()
    secret = (os.getenv("OPS_CONSUMER_SECRET") or "").strip()
    base = (os.getenv("OPS_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")

    if key in PLACEHOLDERS or secret in PLACEHOLDERS:
        if not required:
            return None
        raise MissingCredentials(
            "找不到可用的 EPO OPS 金鑰。\n"
            "  在畫面右上角按「設定」，把 Consumer Key / Secret 填進去即可。\n"
            f"  （也可以直接編輯 {settings_file()}；這個檔案不會進版控。）\n"
            "  金鑰從 EPO Developer Portal https://developers.epo.org/ 免費申請。"
        )
    return OpsConfig(key=key, secret=secret, base_url=base)


def ops_available() -> bool:
    return load_ops(required=False) is not None


def shadowed_keys() -> list[str]:
    """Settings whose process-environment value the file overrode.

    Empty in every ordinary run. Non-empty means somebody exported
    `OPS_CONSUMER_KEY` in their shell and the file disagrees — which is exactly
    the situation where "I changed it and nothing happened" comes from, so the
    panel says it out loud.
    """
    out = []
    for key in KEYS:
        before = (_PROCESS_ENV.get(key) or "").strip()
        after = (os.environ.get(key) or "").strip()
        if before and before != after:
            out.append(key)
    return out


# ------------------------------------------------------------------- writing

def read_settings_file() -> dict[str, str]:
    """The file's own key/value pairs, unexpanded. `{}` when it does not exist.

    Deliberately not `dotenv_values()`: this has to round-trip the file we wrote,
    and a hand-rolled two-line parser cannot surprise us with interpolation.
    """
    path = settings_file()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        out[name.strip()] = value.strip().strip('"').strip("'")
    return out


HEADER = """\
# PatentsGrabber 設定檔。這個檔案由「設定」面板寫入，也可以直接手動編輯。
#
# 金鑰只放這個檔案，不要貼進其他檔案、commit 訊息或對話。
# 申請：EPO Developer Portal https://developers.epo.org/ ，建立 app 後選
# NON-PAYING 等級，把 Consumer Key / Consumer Secret 填在下面。
"""


def write_settings(values: dict[str, str | None]) -> Path:
    """Merge `values` into the settings file and re-load it. Returns the path.

    A `None` value means "leave whatever is already there" — that is how the
    panel saves a changed base URL without the browser ever having to hold, or
    send back, a secret it was never given.
    """
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    merged = read_settings_file()
    for name, value in values.items():
        if value is None:
            continue
        value = check_value(name, value)         # raises before anything is written
        if value:
            merged[name] = value
        else:
            merged.pop(name, None)

    lines = [HEADER]
    for name in KEYS:
        if name in merged:
            lines.append(f"{name}={merged[name]}")
    for name in sorted(set(merged) - set(KEYS)):     # never drop somebody's own keys
        lines.append(f"{name}={merged[name]}")
    body = "\n".join(lines) + "\n"

    # Written LF-only and via a temporary file in the same directory: a half
    # written settings file would lock the user out of their own program, and
    # os.replace is atomic on Windows for a same-volume rename.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8", newline="\n")
    os.replace(tmp, path)

    # The process must reflect the new file immediately; the caller then has to
    # drop any OPS client it built from the old one (Service.reset_ops).
    for name in KEYS:
        os.environ.pop(name, None)
    reload_env()
    return path
