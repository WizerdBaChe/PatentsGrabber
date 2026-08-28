"""Where this program keeps its data, and where it reads its settings.

Two modes, one rule.

*A checkout* keeps everything under the repository root, exactly where it has
always been. That is deliberate: every gate in `tools/` asserts against
`var/raw/`, `var/library.sqlite3` and `var/ops-cache/` by that path, and a
packaging change that quietly moved them would break the instruments that prove
the product works.

*A packaged build* keeps everything under the user's own application-data
folder. The directory a PyInstaller bundle sits in is not writable in the
general case — Program Files, a network share, a zip someone extracted into a
read-only place — and a program that writes its database next to its executable
fails there with an error nobody can act on. `%LOCALAPPDATA%` is per-user,
always writable, and survives replacing the program folder with a newer build,
which is what an "upgrade" is when there is no installer.

The web assets go the other way: they are part of the program, so they travel
inside the bundle and are read from there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "PatentsGrabber"

#: Source-checkout root: .../src/patentsgrabber/paths.py -> two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]


def frozen() -> bool:
    """True inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """The directory PyInstaller unpacked (onedir: the `_internal` folder).

    Only meaningful when `frozen()`; falls back to the package directory so a
    caller does not have to branch.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parent.parent


def data_root() -> Path:
    """Everything this program writes lives under here.

    `PATENTSGRABBER_DATA` overrides both modes. It exists for one honest reason:
    the release smoke test has to run a packaged build without touching the data
    the developer is using, and a test that pollutes real data is a test nobody
    runs twice.
    """
    override = (os.environ.get("PATENTSGRABBER_DATA") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if not frozen():
        return REPO_ROOT
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def settings_path() -> Path:
    """The file the settings panel reads and writes.

    Same dotenv format in both modes, so `.env.example` still teaches the real
    shape and a developer can keep editing the file by hand. The name differs
    only because `.env` is a convention of a source tree, not of an installed
    program: a dot-file in `%LOCALAPPDATA%` is hidden from the person who is
    supposed to be able to find it.
    """
    return data_root() / (".env" if not frozen() else "settings.env")


def var_dir() -> Path:
    return data_root() / "var"


def db_path() -> Path:
    return var_dir() / "library.sqlite3"


def raw_dir() -> Path:
    return var_dir() / "raw"


def cache_dir() -> Path:
    return var_dir() / "ops-cache"


def log_path() -> Path:
    return data_root() / "patentsgrabber.log"


def web_dir() -> Path:
    """The static page. Inside the bundle when frozen, beside the code when not."""
    if frozen():
        return bundle_root() / "patentsgrabber" / "web"
    return Path(__file__).resolve().parent / "web"


def ensure_data_dirs() -> Path:
    """Create the writable tree. Returns the root so a caller can print it."""
    root = data_root()
    for path in (root, var_dir(), raw_dir(), cache_dir()):
        path.mkdir(parents=True, exist_ok=True)
    return root


def describe() -> dict[str, str]:
    """Safe to print, safe to show in the UI: paths only, never a value."""
    return {
        "mode": "packaged" if frozen() else "source checkout",
        "data_root": str(data_root()),
        "settings_file": str(settings_path()),
        "settings_exists": "yes" if settings_path().exists() else "no",
        "library": str(db_path()),
        "ops_cache": str(cache_dir()),
    }
