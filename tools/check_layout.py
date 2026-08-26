"""Does the layout still follow the window? (R-9, R-10)

These two requirements were verified once, by eye, at 1920 and 2560 — and the
defect they were written for (a fixed 55 % text pane leaving ~800 px of dead
strip on a 2560 screen) is a NUMBER, not an aesthetic judgement. A number can be
gated, so it is gated here.

What this asserts, and nothing beyond it:

  1. R-9  the text pane is sized to its column: no wide dead strip beside the text
  2. R-9  the page never scrolls horizontally
  3. R-10 the automatic type pair follows the viewport, INSIDE the typographic
          range — and a reader who set their own values keeps them across a resize

Appearance — colour, spacing, whether it looks right — is still a human check.
This gate cannot see any of that and does not pretend to.

Measurements come from the page's own functions (`measurePx`, `READ`), read over
the DevTools protocol, so the gate reads what the browser laid out rather than
re-implementing the rule and testing its own copy.

    python tools/check_layout.py                 # starts its own server if needed
    python tools/check_layout.py --base http://127.0.0.1:8000

Exit codes: 0 pass · 1 a layout rule is broken · 2 could not determine (no Chrome).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from shoot import find_chrome                     # noqa: E402  (same Chrome discovery)

DOC = "US6285999B1"        # already in var/library.sqlite3: no network, no OPS quota

# Sizes that exist on this desk (CLAUDE.md). Checking 1024 is what hid the original
# defect, so the small end is deliberately absent.
SIZES = [(1920, 1080), (2560, 1440)]

# Pane minus column. The gap is real and wanted — paragraph-number gutter, body
# padding, the slack idealSplit() adds — but it is bounded. Measured 2026-08-26:
# 117 px at 1920, 120 px at 2560. The fixed-55 % defect read ~800.
MAX_DEAD_STRIP = 220

# R-10's declared range: never below the reading default, never past the point
# where the return sweep starts to fail.
FS_RANGE = (16, 18)
MEASURE_RANGE = (68, 76)

MEASURE_JS = """
(() => {
  const pane = document.querySelector('.pane.text');
  const figs = document.querySelector('.pane.figs');
  const doc  = document.querySelector('#tabbody .doc');
  const html = document.documentElement;
  const w = el => el ? el.getBoundingClientRect().width : null;
  return {
    vw: window.innerWidth,
    fs: READ.fs, measure: READ.measure, pinned: READ_SET_BY_USER,
    measurePx: measurePx(),
    pane: w(pane), figs: w(figs), column: w(doc),
    scrollW: html.scrollWidth, clientW: html.clientWidth,
  };
})()
"""

SETTLE_JS = "new Promise(r => setTimeout(() => r(true), %d))"

READY_JS = """
new Promise(res => {
  const t0 = Date.now();
  (function poll(){
    const d = document.querySelector('#tabbody .doc');
    if (d && d.getBoundingClientRect().width > 0) return res(true);
    if (Date.now() - t0 > 20000) return res(false);
    setTimeout(poll, 100);
  })();
})
"""


class Chrome:
    """Just enough DevTools protocol to resize a page and ask it what it measured."""

    def __init__(self, port: int = 9333):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = subprocess.Popen(
            [str(find_chrome()), "--headless=new", f"--remote-debugging-port={port}",
             f"--user-data-dir={self.tmp.name}", "--no-first-run", "--disable-gpu",
             "--no-default-browser-check", "--hide-scrollbars",
             "--window-size=1920,1080", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url, deadline = None, time.time() + 30
        while time.time() < deadline and ws_url is None:
            try:
                targets = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()
                ws_url = next((t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page"), None)
            except Exception:
                time.sleep(0.3)
        if ws_url is None:
            raise RuntimeError("Chrome did not open a debuggable page")
        self.ws = connect(ws_url, max_size=32 * 1024 * 1024)
        self.n = 0
        self.events: list[dict] = []
        self.send("Page.enable")

    def send(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)      # kept: navigation waits on these

    def open(self, url: str, timeout: float = 30.0):
        """Navigate and wait for load — evaluating during navigation loses the context."""
        self.events.clear()
        self.send("Page.navigate", url=url)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(e["method"] == "Page.loadEventFired" for e in self.events):
                return
            try:
                msg = json.loads(self.ws.recv(timeout=1.0))
            except TimeoutError:
                continue
            if "method" in msg:
                self.events.append(msg)
        raise RuntimeError(f"{url} did not finish loading in {timeout:.0f}s")

    def eval(self, js: str, await_promise: bool = False):
        r = self.send("Runtime.evaluate", expression=js, returnByValue=True,
                      awaitPromise=await_promise)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"page threw: {r['exceptionDetails'].get('text')}")
        return r["result"].get("value")

    def resize(self, width: int, height: int):
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                  deviceScaleFactor=1, mobile=False)
        # CDP viewport emulation does not dispatch `resize` to the page (measured
        # 2026-08-26), and the page debounces the event by 140 ms.
        self.eval("window.dispatchEvent(new Event('resize'))")
        self.eval(SETTLE_JS % 500, await_promise=True)

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.tmp.cleanup()


class Server:
    """Use the server already running, or start one and put it back afterwards."""

    def __init__(self, base: str):
        self.base, self.proc = base, None
        if self._up():
            print(f"using the server already at {base}")
            return
        port = base.rsplit(":", 1)[-1]
        env = {**os.environ, "PORT": port}
        self.proc = subprocess.Popen([sys.executable, "run.py"], cwd=str(ROOT), env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 40
        while time.time() < deadline:
            if self._up():
                print(f"started a server for this check at {base}")
                return
            time.sleep(0.4)
        raise RuntimeError(f"no server at {base} and one could not be started")

    def _up(self) -> bool:
        try:
            return httpx.get(self.base, timeout=1.5).status_code < 500
        except Exception:
            return False

    def close(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


FAILURES: list[tuple[str, str]] = []


def check(label: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    if not ok:
        FAILURES.append((label, detail))


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
    url = f"{a.base}/?q={DOC}&tab=description"
    chrome = None
    try:
        chrome = Chrome()
        chrome.open(url)
        if not chrome.eval(READY_JS, await_promise=True):
            print(f"the card for {DOC} never rendered — cannot measure a layout that is not there")
            return 2

        auto = {}
        print("\n=== 1. R-9 — the text pane is sized to its column, not to a fixed fraction ===")
        for w, h in SIZES:
            chrome.resize(w, h)
            m = chrome.eval(MEASURE_JS)
            auto[w] = m
            strip = m["pane"] - m["column"]
            check(f"{w}x{h}: no dead strip beside the text",
                  strip <= MAX_DEAD_STRIP,
                  f"pane {m['pane']:.0f} px, column {m['column']:.0f} px, "
                  f"gap {strip:.0f} px (limit {MAX_DEAD_STRIP}), figures {m['figs']:.0f} px")
            check(f"{w}x{h}: the column honours the measure",
                  m["column"] <= m["measurePx"] + 2,
                  f"column {m['column']:.0f} px against measure {m['measurePx']:.0f} px "
                  f"({m['measure']}ch at {m['fs']}px)")

        print("\n=== 2. R-9 — the page never scrolls sideways ===")
        for w, h in SIZES:
            chrome.resize(w, h)
            m = chrome.eval(MEASURE_JS)
            check(f"{w}x{h}: no horizontal overflow",
                  m["scrollW"] <= m["clientW"],
                  f"scrollWidth {m['scrollW']} vs clientWidth {m['clientW']}")

        print("\n=== 3. calibration — these two checks CAN fail ===")
        chrome.resize(2560, 1440)
        chrome.eval("localStorage.setItem('pg.split','90');"
                    "document.documentElement.style.setProperty('--split','90')")
        chrome.eval(SETTLE_JS % 250, await_promise=True)
        m = chrome.eval(MEASURE_JS)
        strip = m["pane"] - m["column"]
        check("a 90% text pane is reported as a dead strip", strip > MAX_DEAD_STRIP,
              f"pane {m['pane']:.0f} px around a {m['column']:.0f} px column "
              f"= {strip:.0f} px unused — the defect R-9 was written for")
        chrome.eval("localStorage.removeItem('pg.split')")

        chrome.eval("const d=document.createElement('div');d.id='pg-overflow-probe';"
                    "d.style.cssText='width:4000px;height:2px';document.body.appendChild(d)")
        chrome.eval(SETTLE_JS % 250, await_promise=True)
        m = chrome.eval(MEASURE_JS)
        check("a 4000 px element is reported as horizontal overflow",
              m["scrollW"] > m["clientW"],
              f"scrollWidth {m['scrollW']} vs clientWidth {m['clientW']}")
        chrome.eval("document.getElementById('pg-overflow-probe')?.remove()")

        print("\n=== 4. R-10 — the type pair follows the screen, within the range ===")
        small, large = auto[SIZES[0][0]], auto[SIZES[1][0]]
        check("a wider screen picks a larger pair",
              (large["fs"], large["measure"]) > (small["fs"], small["measure"]),
              f"{SIZES[0][0]}px -> {small['fs']}px/{small['measure']}ch, "
              f"{SIZES[1][0]}px -> {large['fs']}px/{large['measure']}ch")
        for m in (small, large):
            check(f"{m['vw']}px: the pair stays inside the typographic range",
                  FS_RANGE[0] <= m["fs"] <= FS_RANGE[1]
                  and MEASURE_RANGE[0] <= m["measure"] <= MEASURE_RANGE[1],
                  f"{m['fs']}px (allowed {FS_RANGE[0]}–{FS_RANGE[1]}), "
                  f"{m['measure']}ch (allowed {MEASURE_RANGE[0]}–{MEASURE_RANGE[1]})")
        check("the automatic pair is not recorded as a reader's choice",
              not small["pinned"] and not large["pinned"],
              f"READ_SET_BY_USER={small['pinned']}/{large['pinned']}")

        print("\n=== 5. R-10, the other direction — a reader's own values are never overwritten ===")
        chrome.eval("localStorage.setItem('pg.read', JSON.stringify("
                    "{fs:16, lh:1.8, measure:60, gap:1.0, numbers:true, serif:true, numerals:false}))")
        chrome.open(url)
        chrome.eval(READY_JS, await_promise=True)
        pinned = {}
        for w, h in SIZES:
            chrome.resize(w, h)
            pinned[w] = chrome.eval(MEASURE_JS)
        a1, a2 = pinned[SIZES[0][0]], pinned[SIZES[1][0]]
        check("a pinned 60ch survives the resize that moves the automatic pair",
              (a1["fs"], a1["measure"]) == (a2["fs"], a2["measure"]) == (16, 60),
              f"{a1['vw']}px -> {a1['fs']}px/{a1['measure']}ch, "
              f"{a2['vw']}px -> {a2['fs']}px/{a2['measure']}ch")
        check("and the page knows it is in the reader's mode",
              a1["pinned"] and a2["pinned"], f"READ_SET_BY_USER={a1['pinned']}/{a2['pinned']}")
        chrome.eval("localStorage.removeItem('pg.read')")

    finally:
        if chrome:
            chrome.close()
        server.close()

    print("\n" + "=" * 74)
    if FAILURES:
        for label, detail in FAILURES:
            print(f"  {label}: {detail}")
        print(f"\n{len(FAILURES)} layout check(s) FAILED")
        return 1
    print("layout follows the window at 1920 and 2560, in both automatic and pinned mode")
    print("(appearance — colour, spacing, whether it reads well — remains a human check)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
