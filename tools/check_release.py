"""Does the PACKAGED build actually work — not merely exist?

PyInstaller reports success for a bundle that cannot start. Every one of this
build's real risks is invisible at build time and obvious at run time:

  * an excluded module that turned out to be needed (`websockets`, `numpy` and
    friends are excluded here to keep the download small);
  * a hidden import uvicorn resolves from a configuration string;
  * the page itself not travelling into the bundle;
  * `paths` resolving to a directory inside the read-only program folder instead
    of the user's own — which is the failure that would only appear on somebody
    else's machine, after the zip had been published.

So this gate runs the executable, in a throwaway data directory, and asks it.

    python tools/check_release.py                       # finds build/dist/...
    python tools/check_release.py --exe path\\to\\PatentsGrabber.exe

Exit 0 = the build is publishable · 1 = it is not · 2 = there was nothing to test.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "build" / "dist" / "PatentsGrabber" / "PatentsGrabber.exe"

FAKE_KEY = "RELEASEGATEFAKEKEY00000000000000"
FAKE_SECRET = "RELEASEGATEFAKESECRET1111111111111111111"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def child_env(data_dir: Path, port: int) -> dict:
    env = dict(os.environ)
    env["PATENTSGRABBER_DATA"] = str(data_dir)
    env["PORT"] = str(port)
    for name in ("OPS_CONSUMER_KEY", "OPS_CONSUMER_SECRET", "OPS_BASE_URL"):
        env.pop(name, None)
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    a = ap.parse_args()
    exe = Path(a.exe)

    if not exe.is_file():
        print(f"  no packaged build at {exe} — INDETERMINATE, nothing was tested.")
        print("  Build one first:  powershell -File packaging\\build.ps1")
        return 2

    folder = exe.parent
    size_mb = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1_048_576
    print(f"\n=== the shape of the deliverable ===")
    print(f"  {folder}")
    check("one executable to double-click",
          len([f for f in folder.glob("*.exe")]) == 1,
          ", ".join(f.name for f in folder.glob("*.exe")))
    check("the payload is beside it, not scattered in the folder root",
          (folder / "_internal").is_dir(),
          f"{len(list(folder.iterdir()))} entries at the top level")
    check("README travels with it", (folder / "_internal" / "README.md").is_file()
          or (folder / "README.md").is_file())
    print(f"  total {size_mb:.0f} MB")

    print("\n=== the build's own self-report ===")
    tmp = Path(tempfile.mkdtemp(prefix="pg-release-gate-"))
    port = free_port()
    try:
        r = subprocess.run([str(exe), "--selftest"], env=child_env(tmp, port),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        report = (r.stdout or "") + (r.stderr or "")
        for line in report.splitlines():
            if line.strip():
                print("    " + line.rstrip())
        check("selftest passed", r.returncode == 0 and "SELFTEST PASS" in report,
              f"exit {r.returncode}")
        check("it knows it is packaged", "packaged" in report, "mode line above")
        check("its data root is the throwaway one, not the program folder",
              str(tmp) in report and str(folder) not in report.split("能力檢查")[0])

        print("\n=== it starts, and answers ===")
        proc = subprocess.Popen([str(exe), "--no-browser"], env=child_env(tmp, port),
                                cwd=str(folder),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        deadline, up = time.time() + 60, False
        while time.time() < deadline and not up:
            if proc.poll() is not None:
                break
            try:
                up = httpx.get(base + "/api/health", timeout=2).status_code == 200
            except httpx.HTTPError:
                time.sleep(0.4)
        check("the server came up", up, f"{base} within 60 s")
        if not up:
            proc.kill()
            print("\nFAILURES: the packaged build does not start — nothing further was tested.")
            return 1

        client = httpx.Client(base_url=base, timeout=20.0)
        health = client.get("/api/health").json()
        check("it reports its own version", bool(health.get("version")), str(health))

        page = client.get("/")
        check("it serves the page from inside the bundle",
              page.status_code == 200 and len(page.text) > 40_000,
              f"HTTP {page.status_code}, {len(page.text)} bytes")
        check("the page carries the settings panel", 'id="pop-settings"' in page.text)
        check("the page carries the rotation controls", 'id="figrotr"' in page.text)

        print("\n=== settings, in the packaged layout ===")
        s = client.get("/api/settings").json()
        check("starts unconfigured in a fresh data directory",
              s["ops"]["configured"] is False, str(s["ops"]))
        check("writes to the user's data directory, not the program folder",
              s["paths"]["settings_file"].startswith(str(tmp)), s["paths"]["settings_file"])
        r2 = client.post("/api/settings", json={"ops_key": FAKE_KEY, "ops_secret": FAKE_SECRET})
        check("a credential can be saved", r2.status_code == 200, f"HTTP {r2.status_code}")
        check("and no response repeats it back",
              FAKE_KEY not in r2.text and FAKE_SECRET not in r2.text)
        settings_file = Path(s["paths"]["settings_file"])
        check("it landed in the file it named", settings_file.is_file()
              and FAKE_KEY in settings_file.read_text(encoding="utf-8"))
        check("nothing was written into the program folder",
              not (folder / ".env").exists() and not (folder / "settings.env").exists()
              and not (folder / "var").exists())

        print("\n=== the local-only guard survived packaging ===")
        check("CONTROL: an ordinary request is accepted",
              client.get("/api/settings").status_code == 200)
        check("a rebound Host is refused",
              client.get("/api/settings", headers={"Host": "evil.example"}).status_code == 421)
        check("a cross-site Origin is refused",
              client.post("/api/settings", json={"ops_key": "x"},
                          headers={"Origin": "https://evil.example"}).status_code == 403)

        print("\n=== a second launch must not start a second server ===")
        second = subprocess.run([str(exe), "--no-browser"], env=child_env(tmp, port),
                                cwd=str(folder), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=90)
        check("the second copy stood down", second.returncode == 0
              and "已經有一份在執行" in (second.stdout or ""),
              (second.stdout or "").strip().splitlines()[-1:][0] if second.stdout else "no output")

        client.close()
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"  the checker itself failed: {type(exc).__name__}: {exc}")
        sys.exit(2)
