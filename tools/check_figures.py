"""The drawing pane's geometry: does a rotated sheet still fit, and still scroll?

Patent drawings are printed on portrait sheets whatever the drawing's own
orientation, so a landscape figure arrives lying on its side. Turning it is the
fix — and turning an image is the one UI operation where the obvious
implementation is silently wrong: `transform: rotate()` leaves the LAYOUT box
unrotated, so the sheet overlaps the pane's edges and the scrollbars run along
the wrong axis. Nothing about that is visible in a test that only asks whether
the button exists.

What this asserts, all of it a number the browser laid out:

  1. upright, fit-page      the sheet is inside the pane on both axes
  2. a quarter turn         the visible box swaps its sides and is STILL inside
  3. four quarter turns     returns to exactly the starting geometry
  4. fit-width + turned     the visible width fills the pane, height overflows
  5. zoomed                 the pane's scrollable area grows to hold it, and its
                            top-left corner is reachable (the `margin:auto` rule)

Appearance is still a human check; this gate cannot see whether a drawing looks
right, only whether it is where the reader was promised it would be.

    python tools/check_figures.py
    python tools/check_figures.py --base http://127.0.0.1:8000

Exit codes: 0 pass · 1 the geometry is wrong · 2 could not determine (no Chrome).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_layout import Chrome, Server          # noqa: E402  (one Chrome driver, not two)
from shoot import find_chrome                    # noqa: E402

DOC = "US6285999B1"      # already in var/library.sqlite3: no network, no OPS quota
SIZE = (1920, 1080)      # CLAUDE.md: this desk's smaller real display
TOL = 1.5                # subpixel rounding; a real failure is off by tens of px

# Wait for a drawing that is actually laid out — `complete` is not enough, the
# size is assigned by figLayout() on the load event.
READY_JS = """
new Promise(res => {
  const t0 = Date.now();
  (function poll(){
    const i = document.querySelector('#figbig');
    if (i && i.naturalWidth && i.offsetWidth > 1) return res(true);
    if (Date.now() - t0 > 25000) return res(false);
    setTimeout(poll, 120);
  })();
})
"""

# getBoundingClientRect() on a transformed element returns the AXIS-ALIGNED box,
# which is exactly "what the reader sees". That is the measurement this whole
# gate is built on.
MEASURE_JS = """
(() => {
  const top = document.querySelector('#figtop'),
        wrap = document.querySelector('#figwrap'),
        img = document.querySelector('#figbig');
  if(!top || !wrap || !img) return null;
  const ir = img.getBoundingClientRect(), wr = wrap.getBoundingClientRect();
  return {
    rot: FIGS.rot, fit: FIT, zoom: img.classList.contains('zoom'),
    cw: top.clientWidth, ch: top.clientHeight,
    natW: img.naturalWidth, natH: img.naturalHeight,
    ownW: img.offsetWidth, ownH: img.offsetHeight,
    visW: ir.width, visH: ir.height,
    wrapW: wr.width, wrapH: wr.height,
    scrollW: top.scrollWidth, scrollH: top.scrollHeight,
  };
})()
"""

SETTLE = "new Promise(r => setTimeout(() => r(true), 260))"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def near(a: float, b: float, tol: float = TOL) -> bool:
    return abs(a - b) <= tol


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    a = ap.parse_args()

    try:
        find_chrome()
    except SystemExit:
        print("Chrome not found — INDETERMINATE: this gate did not run. That is not a pass.")
        return 2

    server = Server(a.base)
    chrome = None
    try:
        chrome = Chrome()
        chrome.resize(*SIZE)
        chrome.open(f"{a.base}/?q={DOC}")
        if not chrome.eval(READY_JS, await_promise=True):
            print("  the drawing never laid out — nothing was measured")
            return 2

        def m():
            chrome.eval(SETTLE, await_promise=True)
            return chrome.eval(MEASURE_JS)

        def turn(n: int):
            chrome.eval(f"figRotate({n})")

        print(f"\n=== upright, fit to page ({SIZE[0]}x{SIZE[1]}) ===")
        chrome.eval("FIT='page'; figLayout()")
        up = m()
        check("the sheet has a size at all", up["visW"] > 10 and up["visH"] > 10,
              f"{up['visW']:.0f}x{up['visH']:.0f} in {up['cw']}x{up['ch']}")
        check("inside the pane horizontally", up["visW"] <= up["cw"] + TOL,
              f"{up['visW']:.1f} vs pane {up['cw']}")
        check("inside the pane vertically", up["visH"] <= up["ch"] + TOL,
              f"{up['visH']:.1f} vs pane {up['ch']}")
        check("one side actually touches the pane (it is FIT, not merely small)",
              near(up["visW"], up["cw"], 2) or near(up["visH"], up["ch"], 2),
              f"{up['visW']:.1f}x{up['visH']:.1f} in {up['cw']}x{up['ch']}")
        check("aspect ratio preserved",
              near(up["visH"] / up["visW"], up["natH"] / up["natW"], 0.01),
              f"drawn {up['visH']/up['visW']:.4f} vs natural {up['natH']/up['natW']:.4f}")
        check("wrapper is the box the sheet occupies",
              near(up["wrapW"], up["visW"]) and near(up["wrapH"], up["visH"]),
              f"wrap {up['wrapW']:.1f}x{up['wrapH']:.1f} vs sheet {up['visW']:.1f}x{up['visH']:.1f}")

        print("\n=== one quarter turn right ===")
        turn(1)
        r1 = m()
        check("the page recorded the turn", r1["rot"] == 1, f"rot={r1['rot']}")
        check("CONTROL: the geometry actually moved (a no-op turn would pass everything else)",
              not near(r1["visW"], up["visW"]) or not near(r1["visH"], up["visH"]),
              f"before {up['visW']:.0f}x{up['visH']:.0f} after {r1['visW']:.0f}x{r1['visH']:.0f}")
        check("the visible box is the sheet on its side",
              near(r1["visW"], r1["ownH"]) and near(r1["visH"], r1["ownW"]),
              f"visible {r1['visW']:.1f}x{r1['visH']:.1f}, sheet {r1['ownW']:.1f}x{r1['ownH']:.1f}")
        check("still inside the pane horizontally", r1["visW"] <= r1["cw"] + TOL,
              f"{r1['visW']:.1f} vs pane {r1['cw']}")
        check("still inside the pane vertically", r1["visH"] <= r1["ch"] + TOL,
              f"{r1['visH']:.1f} vs pane {r1['ch']}")
        check("turned, a portrait sheet now uses the pane's WIDTH",
              r1["visW"] > up["visW"],
              f"{up['visW']:.0f} -> {r1['visW']:.0f} px across a {r1['cw']} px pane")
        check("wrapper followed the turn",
              near(r1["wrapW"], r1["visW"]) and near(r1["wrapH"], r1["visH"]),
              f"wrap {r1['wrapW']:.1f}x{r1['wrapH']:.1f}")

        print("\n=== three more turns must come back to where it started ===")
        turn(1); turn(1); turn(1)
        back = m()
        check("rotation state wrapped to 0", back["rot"] == 0, f"rot={back['rot']}")
        check("geometry identical to the start",
              near(back["visW"], up["visW"]) and near(back["visH"], up["visH"]),
              f"{back['visW']:.1f}x{back['visH']:.1f} vs {up['visW']:.1f}x{up['visH']:.1f}")

        print("\n=== turning left is the mirror of turning right ===")
        turn(-1)
        left = m()
        check("one turn left lands on 3", left["rot"] == 3, f"rot={left['rot']}")
        check("same visible box as one turn right",
              near(left["visW"], r1["visW"]) and near(left["visH"], r1["visH"]),
              f"{left['visW']:.1f}x{left['visH']:.1f} vs {r1['visW']:.1f}x{r1['visH']:.1f}")
        turn(1)

        print("\n=== fill-the-width, turned ===")
        chrome.eval("figRotate(1)")
        chrome.eval("FIT='width'; figLayout()")
        w1 = m()
        check("the visible width fills the pane", near(w1["visW"], w1["cw"], 2),
              f"{w1['visW']:.1f} vs pane {w1['cw']}")
        check("and the pane can scroll down it", w1["scrollH"] >= w1["visH"] - TOL,
              f"scrollable {w1['scrollH']} for a {w1['visH']:.0f} px sheet")
        chrome.eval("figRotate(-1); FIT='page'; figLayout()")

        print("\n=== zoom, upright ===")
        z0 = m()
        chrome.eval("document.querySelector('#figbig').click()")
        z1 = m()
        check("zoom enlarged the sheet", z1["visW"] > z0["visW"] * 1.5,
              f"{z0['visW']:.0f} -> {z1['visW']:.0f}")
        check("the pane became scrollable to hold it",
              z1["scrollW"] >= z1["visW"] - TOL and z1["scrollH"] >= z1["visH"] - TOL,
              f"scroll {z1['scrollW']}x{z1['scrollH']} for {z1['visW']:.0f}x{z1['visH']:.0f}")
        # `margin:auto` rather than `align-items:center`: a centred flex item
        # larger than its scroll container cannot be scrolled back to its own top.
        reachable = chrome.eval(
            "(() => { const t=document.querySelector('#figtop');"
            " t.scrollTop = 0; t.scrollLeft = 0;"
            " const i=document.querySelector('#figbig').getBoundingClientRect(),"
            "       r=t.getBoundingClientRect();"
            " return {dx: i.left - r.left, dy: i.top - r.top}; })()")
        check("its top-left corner is reachable by scrolling",
              reachable["dx"] >= -TOL and reachable["dy"] >= -TOL,
              f"corner sits {reachable['dx']:.1f},{reachable['dy']:.1f} from the pane's corner")
        chrome.eval("document.querySelector('#figbig').click()")

        print("\n=== a new document starts upright ===")
        chrome.eval("figRotate(1)")
        chrome.eval("renderFigures(CARD)")
        fresh = m()
        check("rotation reset by a re-render", fresh["rot"] == 0, f"rot={fresh['rot']}")

    finally:
        if chrome:
            chrome.close()
        server.close()

    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                     # a checker that crashed did not check
        print(f"  the checker itself failed: {type(exc).__name__}: {exc}")
        sys.exit(2)
