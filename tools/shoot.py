"""Out-of-process screenshots via the installed Chrome (no Playwright download).

The Claude Code browser pane is hidden by default and cannot composite pixels, so
appearance checks are taken here instead. Uses the ?q= deep link so a card is
already on screen when the shot is taken.

    python tools/shoot.py US20250383260A1 out.png [--dark] [--width 1440]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def find_chrome() -> Path:
    for p in CHROME_CANDIDATES:
        if p.exists():
            return p
    which = shutil.which("chrome") or shutil.which("google-chrome")
    if which:
        return Path(which)
    sys.exit("Chrome not found; edit CHROME_CANDIDATES in tools/shoot.py")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("number")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--delay", type=int, default=6000, help="ms to wait for fetch+render")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"{a.base}/?q={a.number}"

    with tempfile.TemporaryDirectory() as profile:
        cmd = [
            str(find_chrome()),
            "--headless=new",
            f"--user-data-dir={profile}",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={a.width},{a.height}",
            f"--screenshot={out}",
            f"--virtual-time-budget={a.delay}",
        ]
        if a.dark:
            # KNOWN DISTORTION, read before trusting a dark shot: --force-dark-mode
            # also runs Chrome's auto-darkening, which inverts images it classifies
            # as icons. Patent thumbnails are small line art and get inverted; the
            # large figure does not. That inversion is an artifact of this flag, NOT
            # how the app renders under a real `prefers-color-scheme: dark`.
            # --blink-settings=preferredColorScheme=N was tried (N=1 and N=2,
            # 2026-08-20) and is ignored in headless — both still render light.
            # True dark-mode appearance therefore needs a human eye.
            cmd += ["--force-dark-mode", "--enable-features=WebContentsForceDark"]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if not out.exists() or out.stat().st_size == 0:
        print(r.stdout[-800:])
        print(r.stderr[-800:])
        return 1
    print(f"{out}  ({out.stat().st_size // 1024} KB, {a.width}x{a.height}{', dark' if a.dark else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
