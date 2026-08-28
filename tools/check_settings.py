"""The settings panel: does it keep what it is given, and give back nothing?

Three properties, written as properties of the ASSET rather than as advice to
whoever edits it next:

  1. A credential put in through the API can be read back only as a length and a
     last-four. No response body, at any endpoint, may contain the value.
  2. A partial save leaves the untouched fields alone. "Change the base URL"
     must not silently wipe a key the browser was never given and cannot resend.
  3. The server answers only to a page that is genuinely on this machine. A
     loopback port is reachable from every tab in the browser, so `Host`,
     `Origin` and `Sec-Fetch-Site` are what stand between somebody's web page
     and this program's credential.

Every one of those has a POSITIVE CONTROL beside it. A guard that rejects
everything scores 100% against bad inputs alone, and a leak detector that
matches nothing scores 100% against a clean response — so this file also feeds
each instrument an input it MUST accept, and an input it MUST catch.

Runs its own server in a temporary data directory: it writes fake credentials,
and a gate that overwrote the operator's real ones would be run exactly once.

    python tools/check_settings.py

Exit 0 = clean, 1 = a property does not hold, 2 = the checker could not run.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

# Obvious fakes, long enough to look like the real thing to any leak detector.
FAKE_KEY = "CHECKSETTINGSFAKEKEY0000000000AB"
FAKE_SECRET = "CHECKSETTINGSFAKESECRET111111111111111CD"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(data_dir: Path, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["PATENTSGRABBER_DATA"] = str(data_dir)
    env["PORT"] = str(port)
    # A shell that happens to export a real credential must not leak into the
    # subject under test: this gate asserts what the FILE does.
    for name in ("OPS_CONSUMER_KEY", "OPS_CONSUMER_SECRET", "OPS_BASE_URL"):
        env.pop(name, None)
    return subprocess.Popen([sys.executable, str(ROOT / "run.py")], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(base: str, proc: subprocess.Popen, seconds: float = 25.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            r = httpx.get(base + "/api/health", timeout=2.0)
            if r.status_code == 200 and r.json().get("app") == "PatentsGrabber":
                return True
        except httpx.HTTPError:
            time.sleep(0.35)
    return False


def leaks(text: str) -> list[str]:
    """Which fake credential appears verbatim in this text."""
    return [name for name, value in (("key", FAKE_KEY), ("secret", FAKE_SECRET))
            if value in text]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pg-settings-gate-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = start(tmp, port)
    try:
        if not wait_ready(base, proc):
            print("  could not start a server for the check — nothing was measured")
            return 2
        client = httpx.Client(base_url=base, timeout=15.0)

        print("\n=== the leak detector itself (positive control) ===")
        check("catches a value it is looking for", leaks(f'{{"k":"{FAKE_KEY}"}}') == ["key"])
        check("does not fire on a clean body", leaks('{"ops":{"configured":false}}') == [])

        print("\n=== a data directory with no settings file ===")
        s = client.get("/api/settings").json()
        check("reports itself unconfigured", s["ops"]["configured"] is False, str(s["ops"]))
        check("no hint invented from nothing", s["ops"]["key_hint"] == "")
        check("says which file it would write", s["paths"]["settings_file"].startswith(str(tmp)),
              s["paths"]["settings_file"])

        print("\n=== saving a credential ===")
        r = client.post("/api/settings",
                        json={"ops_key": FAKE_KEY, "ops_secret": FAKE_SECRET})
        check("save accepted", r.status_code == 200, f"HTTP {r.status_code}")
        saved = r.json()
        check("now reports configured", saved["ops"]["configured"] is True)
        check("key hint states the true length", "32 字元" in saved["ops"]["key_hint"],
              saved["ops"]["key_hint"])
        check("key hint ends with the true last four", "00AB" in saved["ops"]["key_hint"],
              saved["ops"]["key_hint"])
        check("the save response carries no value", leaks(r.text) == [], str(leaks(r.text)))

        print("\n=== reading it back ===")
        body = client.get("/api/settings").text
        check("GET /api/settings carries no value", leaks(body) == [], str(leaks(body)))
        check("neither does /api/health", leaks(client.get("/api/health").text) == [])
        # The one place the value legitimately IS, so that "no value anywhere"
        # is a claim about responses and not an untested absolute.
        on_disk = (tmp / ".env").read_text(encoding="utf-8")
        check("the value did reach the file (control)", leaks(on_disk) == ["key", "secret"],
              str(leaks(on_disk)))
        check("file written LF-only", "\r\n" not in on_disk)

        print("\n=== a partial save must not erase what it was not given ===")
        r = client.post("/api/settings", json={"ops_base_url": "https://ops.epo.org/3.2"})
        after = r.json()
        check("key survived a base-url-only save", after["ops"]["configured"] is True,
              str(after["ops"]))
        check("secret hint unchanged", after["ops"]["secret_hint"] == saved["ops"]["secret_hint"],
              after["ops"]["secret_hint"])

        print("\n=== a bad base URL is refused, not stored ===")
        # The key and secret travel to this host as an HTTP Basic header, so the
        # field is a credential-exfiltration path unless it is constrained. Each
        # rejection below sits beside an acceptance, so "refuses everything" cannot
        # score as "refuses the right things".
        r = client.post("/api/settings", json={"ops_base_url": "http://ops.epo.org/3.2"})
        check("plain http refused", r.status_code == 400, f"HTTP {r.status_code}")
        r = client.post("/api/settings", json={"ops_base_url": "https://evil.example/3.2"})
        check("a host outside epo.org refused", r.status_code == 400,
              f'HTTP {r.status_code}: {r.text[:90]}')
        r = client.post("/api/settings", json={"ops_base_url": "https://ops.epo.org.evil.example/3.2"})
        check("a look-alike suffix refused", r.status_code == 400, f"HTTP {r.status_code}")
        r = client.post("/api/settings", json={"ops_base_url": "https://ops.epo.org/3.3"})
        check("CONTROL: another epo.org path IS accepted", r.status_code == 200,
              f"HTTP {r.status_code}")
        client.post("/api/settings", json={"ops_base_url": "https://ops.epo.org/3.2"})
        check("stored value untouched",
              client.get("/api/settings").json()["ops"]["base_url"] == "https://ops.epo.org/3.2")
        check("the test button applies the same rule as the save button",
              client.post("/api/settings/test",
                          json={"ops_key": "x" * 20, "ops_secret": "y" * 20,
                                "ops_base_url": "https://evil.example"}).json()["ok"] is False,
              "otherwise the credential leaves by the door the save path closed")

        print("\n=== a value may not carry a second line into the file ===")
        # One KEY=value per line: a newline in a value writes a setting nobody
        # asked for. The boundary that has to catch it is the API, not the reader.
        r = client.post("/api/settings",
                        json={"ops_key": "GOOD" + FAKE_KEY[4:] + "\nOPS_BASE_URL=https://evil.example"})
        check("newline in a value refused", r.status_code == 400,
              f'HTTP {r.status_code}: {r.text[:90]}')
        after_inject = (tmp / ".env").read_text(encoding="utf-8")
        check("nothing was injected into the file", "evil.example" not in after_inject)
        check("CONTROL: the same value without the newline IS accepted",
              client.post("/api/settings",
                          json={"ops_key": "GOOD" + FAKE_KEY[4:]}).status_code == 200)
        client.post("/api/settings", json={"ops_key": FAKE_KEY})

        print("\n=== a hand-edited file is adopted, not half-adopted ===")
        # The panel promises it re-reads on open. Before this check it re-read the
        # FILE while `configured` still came from the environment loaded at boot.
        edited = FAKE_KEY[:-4] + "ZZZZ"
        text = (tmp / ".env").read_text(encoding="utf-8")
        (tmp / ".env").write_text(text.replace(FAKE_KEY, edited), encoding="utf-8", newline="\n")
        s2 = client.get("/api/settings").json()
        check("the panel shows the hand-edited key", "ZZZZ" in s2["ops"]["key_hint"],
              s2["ops"]["key_hint"])
        check("and says it adopted a change rather than doing it silently",
              s2.get("adopted_file_change") is True, str(s2.get("adopted_file_change")))
        check("a second read reports no further change",
              client.get("/api/settings").json().get("adopted_file_change") is False)
        client.post("/api/settings", json={"ops_key": FAKE_KEY})

        print("\n=== the guard (each rejection beside an acceptance) ===")
        ok = client.get("/api/settings")
        check("CONTROL: an ordinary local request is accepted", ok.status_code == 200,
              f"HTTP {ok.status_code}")
        ok2 = client.get("/api/settings", headers={"Sec-Fetch-Site": "same-origin"})
        check("CONTROL: same-origin fetch is accepted", ok2.status_code == 200,
              f"HTTP {ok2.status_code}")
        ok3 = client.get("/api/settings", headers={"Origin": f"http://localhost:{port}"})
        check("CONTROL: our own Origin is accepted", ok3.status_code == 200,
              f"HTTP {ok3.status_code}")

        rebind = client.get("/api/settings", headers={"Host": "evil.example"})
        check("DNS-rebinding Host refused", rebind.status_code == 421, f"HTTP {rebind.status_code}")
        cross = client.post("/api/settings", json={"ops_key": "x"},
                            headers={"Origin": "https://evil.example"})
        check("cross-site Origin refused", cross.status_code == 403, f"HTTP {cross.status_code}")
        reach = client.get("/api/ops/page?link=x&page=1",
                           headers={"Sec-Fetch-Site": "cross-site"})
        check("cross-site <img> reach-in refused", reach.status_code == 403,
              f"HTTP {reach.status_code}")
        check("the refusal did not save anything",
              client.get("/api/settings").json()["ops"]["key_hint"] == saved["ops"]["key_hint"])

        print("\n=== clearing ===")
        r = client.post("/api/settings/clear")
        cleared = r.json()
        check("reports unconfigured again", cleared["ops"]["configured"] is False)
        check("value gone from the file", leaks((tmp / ".env").read_text(encoding="utf-8")) == [],
              str(leaks((tmp / ".env").read_text(encoding="utf-8"))))
        check("the clear response carries no value", leaks(r.text) == [])

        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                    # a checker that crashed did not check
        print(f"  the checker itself failed: {type(exc).__name__}: {exc}")
        sys.exit(2)
