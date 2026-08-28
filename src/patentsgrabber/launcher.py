"""The thing you double-click.

`run.py` starts a server and leaves you to find the browser yourself; that is
right for a checkout and wrong for a program somebody was handed. This is the
packaged entry point, and it is responsible for four things `run.py` is not:

  * saying where its data lives, before anything can go wrong, so that a failure
    later has a place the reader already knows to look;
  * not starting a SECOND copy on top of one already running. Double-clicking an
    icon twice is not a mistake anybody should have to avoid — the second launch
    finds the first, opens the browser at it and gets out of the way;
  * opening the browser, because a console window printing a URL is a program
    that has not finished starting;
  * failing where it can be read. A console window that vanishes takes the
    traceback with it, so a crash is written to a log file AND held on screen.

Console, not windowed, on purpose. The window is the program's off switch: with
no tray icon and no menu, a windowless server can only be stopped through Task
Manager, and a program you cannot stop is worse than one that looks plain.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser

import httpx

from . import paths

DEFAULT_PORT = 8000
PORT_SPAN = 12               # 8000..8011, then give up and say so


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _ours(port: int) -> dict | None:
    """Is the program already listening there? Returns its health, or None.

    Asking rather than assuming: something else on this machine may hold 8000,
    and opening a browser at somebody else's server would be a confusing lie.
    """
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.5)
        data = r.json()
        return data if r.status_code == 200 and data.get("app") == "PatentsGrabber" else None
    except Exception:
        return None


def choose_port() -> tuple[int, dict | None]:
    """(port to use, the already-running instance found there if any)."""
    override = (os.environ.get("PORT") or "").strip()
    if override.isdigit():
        # Still ask who is there. Returning the number unexamined made a second
        # launch on a pinned port try to bind an occupied socket and crash,
        # instead of standing down — the same "already running" case the loop
        # below handles, missed only because the port came from elsewhere.
        port = int(override)
        if _port_free(port):
            return port, None
        running = _ours(port)
        if running:
            return port, running
        raise RuntimeError(
            f"PORT 指定了 {port}，但那個連接埠已經被別的程式占用，而且它不是 "
            f"PatentsGrabber。請換一個 PORT，或關掉占用它的程式。")
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN):
        if _port_free(port):
            return port, None
        running = _ours(port)
        if running:
            return port, running
    raise RuntimeError(
        f"連 {DEFAULT_PORT}–{DEFAULT_PORT + PORT_SPAN - 1} 都被占用了。"
        f"請關掉占用這些連接埠的程式，或設定環境變數 PORT 指定一個。"
    )


def open_browser_when_ready(url: str, port: int) -> None:
    """Open the page only once the server answers.

    Opening it immediately shows the reader a connection error for the second or
    two the server needs — which reads as "the program is broken", not as "the
    program is starting".
    """
    deadline = time.time() + 25
    while time.time() < deadline:
        if _ours(port):
            webbrowser.open(url)
            return
        time.sleep(0.25)
    print(f"  伺服器 25 秒內沒有回應。請自己開：{url}")


# Everything the program needs that is NOT part of its own source: if one of
# these is missing from a packaged build, the failure appears only when a reader
# opens an EPO drawing, which may be weeks later and on somebody else's machine.
# `--selftest` asks the question at a time when the answer is still cheap, and
# `tools/check_release.py` asks it of every build before it is published.
CAPABILITIES = [
    ("PIL", "把 EPO 的 TIFF 圖頁轉成瀏覽器看得懂的 PNG"),
    ("pypdf", "把 EPO 的逐頁 PDF 合併成一份原文件"),
    ("bs4", "解析 Google Patents 的頁面"),
    ("lxml", "上面那個解析器實際用的引擎"),
    ("httpx", "所有對外連線"),
    ("uvicorn.protocols.http.auto", "HTTP 伺服器（打包後最容易被漏掉的一個）"),
    ("uvicorn.loops.auto", "事件迴圈"),
    ("uvicorn.lifespan.on", "啟動與關閉掛鉤"),
]


def selftest() -> int:
    """Report what this build can actually do. Exit 1 if anything is missing."""
    import importlib

    from .app import VERSION

    print(f"PatentsGrabber {VERSION}")
    for key, value in paths.describe().items():
        print(f"  {key:<16}{value}")
    print("\n  能力檢查（每一項都是真的 import，不是查表）")
    missing = []
    for module, what in CAPABILITIES:
        try:
            importlib.import_module(module)
            print(f"  OK    {module:<34}{what}")
        except Exception as exc:
            print(f"  FAIL  {module:<34}{what}  — {type(exc).__name__}: {exc}")
            missing.append(module)
    page = paths.web_dir() / "index.html"
    ok_page = page.is_file() and page.stat().st_size > 10_000
    print(f"  {'OK  ' if ok_page else 'FAIL'}  {'the page itself':<34}{page}")
    if not ok_page:
        missing.append("web/index.html")
    print("\n" + ("SELFTEST PASS" if not missing else "SELFTEST FAIL: " + ", ".join(missing)))
    return 1 if missing else 0


def main() -> int:
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    quiet_browser = "--no-browser" in argv

    root = paths.ensure_data_dirs()
    print("=" * 62)
    print("  PatentsGrabber")
    print("=" * 62)
    print(f"  資料夾　{root}")
    print(f"  設定檔　{paths.settings_path()}"
          f"{'' if paths.settings_path().exists() else '　（還沒有，可在畫面裡的「設定」建立）'}")

    try:
        port, running = choose_port()
    except RuntimeError as exc:
        print(f"\n  {exc}")
        input("\n  按 Enter 關閉…")
        return 1

    url = f"http://127.0.0.1:{port}/"

    if running:
        # Already up — this window has nothing to add, and leaving it open would
        # imply it is the one serving.
        print(f"\n  已經有一份在執行（{url}），改開那一份。")
        if not quiet_browser:
            webbrowser.open(url)
        time.sleep(1.5)
        return 0

    print(f"  網址　　{url}")
    print(f"  記錄檔　{paths.log_path()}")
    print("\n  關閉這個視窗就會停止程式。瀏覽器關掉不影響，重開網址即可。")
    print("=" * 62 + "\n")

    if not quiet_browser:
        threading.Thread(target=open_browser_when_ready, args=(url, port), daemon=True).start()

    import uvicorn                       # imported late: it is the slow one

    from .app import app                 # noqa: F401  (object, not import string:
                                         # a frozen build has no module path to reload)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


def run() -> int:
    """`main`, wrapped so a crash is legible after the fact and on screen."""
    try:
        return main()
    except KeyboardInterrupt:
        return 0
    except Exception:
        detail = traceback.format_exc()
        try:
            with open(paths.log_path(), "a", encoding="utf-8") as fh:
                fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{detail}")
        except Exception:
            pass                          # a logging failure must not hide the crash
        print("\n" + "=" * 62)
        print("  PatentsGrabber 啟動失敗。以下是原因，同一份也寫進了記錄檔：")
        print(f"  {paths.log_path()}")
        print("=" * 62)
        print(detail)
        input("\n  按 Enter 關閉…")
        return 1


if __name__ == "__main__":
    sys.exit(run())
