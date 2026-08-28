"""Screenshot a page AFTER driving it — for states you cannot reach by URL.

`shoot.py` photographs whatever a URL renders. A popover that has to be opened,
a drawing that has to be turned, an error that has to be provoked: none of those
are addressable, and all of them are exactly the states worth showing somebody.
So this one navigates, runs a snippet, waits, and captures.

The browser pane in this environment is hidden and cannot composite pixels, so
every appearance check and every screenshot for a human goes through here.

    python tools/shoot_ui.py --url http://127.0.0.1:8000/?q=US6285999B1 \\
        --js "document.querySelector('#setchip').click()" --out var/shots/settings.png
    python tools/shoot_ui.py --url file:///.../state-snapshots.html --full \\
        --out var/shots/diagrams.png

Exit 0 = written · 1 = the page or the snippet failed · 2 = no Chrome.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_layout import Chrome                    # noqa: E402
from shoot import find_chrome                      # noqa: E402

SETTLE = "new Promise(r => setTimeout(() => r(true), %d))"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--js", default="", help="run after load, before the shot")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--wait", type=int, default=1200, help="ms after --js")
    ap.add_argument("--ready", default="", help="JS promise to await before --js")
    ap.add_argument("--full", action="store_true", help="capture past the viewport")
    a = ap.parse_args()

    try:
        find_chrome()
    except SystemExit:
        print("Chrome not found.")
        return 2

    chrome = Chrome(port=9336)
    try:
        chrome.resize(a.width, a.height)
        chrome.open(a.url)
        if a.ready:
            if not chrome.eval(a.ready, await_promise=True):
                print("  the readiness condition never became true — not shooting a half page.")
                return 1
        chrome.eval(SETTLE % 600, await_promise=True)
        if a.js:
            chrome.eval(a.js)
            chrome.eval(SETTLE % a.wait, await_promise=True)
        out = chrome.screenshot(a.out, full_page=a.full)
    finally:
        chrome.close()

    size = out.stat().st_size
    if size < 5000:
        print(f"  {out} is only {size} bytes — that is a blank page, not a screenshot.")
        return 1
    print(f"  {out}  ({size // 1024} KB, {a.width}x{a.height})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"  failed: {type(exc).__name__}: {exc}")
        sys.exit(1)
