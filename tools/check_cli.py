"""The command-line surface: does it keep its three promises to a caller?

`tools/check_settings.py` asserts what the HTTP surface may not leak. This is
the same argument one surface later: the CLI now hands a card, a file path and a
quota figure to something that is not a person, and each of those is a promise
that can rot silently. Written as properties of the ASSET, not as advice:

  1. **`--json` puts exactly one JSON object on stdout.** Every human line goes
     to stderr. A caller that parses stdout must never be able to parse a
     progress note by accident.
  2. **The default is lean, and the withholding is declared.** No abstract, no
     claims, no description unless `--full` — and `text_withheld` says so, so a
     caller cannot mistake "we did not send it" for "this patent has none".
  3. **Nothing prints a credential**, on either stream, in any command.
  4. **Spending is opt-in and reported**, and a run that fetched nothing when it
     was asked to fetch exits non-zero rather than looking like an empty result.
  5. **Two processes can hold the library at once** (the CLI and the server).

Every one of those has a control that must fail. A lean-mode check passes
trivially against a card with no text in it, and a leak detector that matches
nothing scores full marks on a clean stream — so this file also feeds each
instrument the input it MUST catch: `--full` for property 2, a stream that does
contain the fake credential for property 3, an exclusive database lock for
property 5.

**The ruler, stated beside the score** (global gate rule): property 5's
behavioural half — four concurrent writers all succeed — is a FLOOR, not a
proof. At this size those four would very likely also succeed under the old
rollback journal, so the deterministic assertion is `journal_mode`, and the
control is what proves the behavioural check can observe a lock at all.

Runs entirely in a temporary data directory seeded with a synthetic card. No
network, no OPS quota, and it cannot touch the operator's real library or their
real credentials.

    python tools/check_cli.py

Exit 0 = clean, 1 = a property does not hold, 2 = the checker could not run.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PGB = ROOT / "pgb.py"

# Obvious fakes, shaped like the real thing so any leak detector would match.
FAKE_KEY = "CHECKCLIFAKEKEY00000000000000AB"
FAKE_SECRET = "CHECKCLIFAKESECRET1111111111111111111CD"

# The document the synthetic card stands in for. Never fetched: the whole point
# of seeding the store is that this gate runs with the network unplugged.
NUMBER = "US20250383260A1"
LINK = "published-data/images/US/2025383260/A1/thumbnail"

TEXT_KEYS = ("abstract", "description", "description_blocks", "claims_text", "claims")

# Kept in step with cli.EXIT by hand rather than imported, so that a change to
# the CLI's own table has to be noticed here too.
EXIT_BUSY = 6

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


# ----------------------------------------------------------------- the subject

def pgb(data_dir: Path, *argv: str, timeout: float = 90.0) -> tuple[int, str, str]:
    """One CLI run in an isolated data directory, with the real key kept out.

    A shell that happens to export a working OPS credential must not reach the
    subject: this gate asserts what the CLI does with the settings FILE it was
    given, and a leaked-in credential would let a "no network" run spend quota.
    """
    env = dict(os.environ)
    env["PATENTSGRABBER_DATA"] = str(data_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    for name in ("OPS_CONSUMER_KEY", "OPS_CONSUMER_SECRET", "OPS_BASE_URL"):
        env.pop(name, None)
    proc = subprocess.run([sys.executable, str(PGB), *argv], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def seed(data_dir: Path) -> None:
    """A card in the library, with text in every field lean mode must remove.

    Seeded through raw SQL rather than through `Service`, because a gate that
    builds its fixture with the code under test cannot tell a broken extractor
    from a broken assertion.
    """
    card = {
        "query": NUMBER, "number": NUMBER, "canonical": NUMBER,
        "espacenet": "US2025383260A1", "kind_of_document": "A1",
        "title": "SEEDED FIXTURE — not a real patent",
        "url": f"https://patents.google.com/patent/{NUMBER}/en",
        "abstract": "ABSTRACTTEXTMARKER " * 20,
        "description": "DESCRIPTIONTEXTMARKER " * 200,
        "description_blocks": [{"kind": "p", "text": "DESCRIPTIONTEXTMARKER"} for _ in range(7)],
        "claims_text": "CLAIMSTEXTMARKER " * 40,
        "claims": [{"num": str(i), "text": "CLAIMSTEXTMARKER", "dependent": i != 1}
                   for i in range(1, 6)],
        "independent_claims": ["1"],
        "images": [], "images_declared": 0, "pdf_link": None,
        "classifications": {"items": [], "total": 0, "truncated": False, "cap": None},
        "family": {"items": [], "total": 0, "truncated": False, "cap": None},
        "similar_documents": {"items": [], "total": 0, "truncated": False, "cap": None},
        "backward_citations": {"items": [], "total": 0, "truncated": False, "cap": None},
        "forward_citations": {"items": [], "total": 0, "truncated": False, "cap": None},
        "legal_status": None,
        "legal_events": {"items": [], "total": 0, "truncated": False, "cap": None},
        "publication_date": "2025-12-18", "filing_date": "2024-06-01",
        "priority_date": "2023-06-02",
        "assignee": ["SEEDED ASSIGNEE"], "inventors": ["SEEDED INVENTOR"],
        "provenance": {"title": {"source": "Google Patents", "selector": "h1", "present": True},
                       "images": {"source": "Google Patents", "selector": "-", "present": False}},
        "missing": [{"field": "images", "reason": "此來源未提供此欄位。"}],
        "links": {"google": "-", "espacenet": "-", "patentscope": "-"},
        # An OPS payload already on the card is what makes `figures --offline`
        # answerable with the network unplugged.
        "ops": {"available": True, "source": "EPO OPS", "resolved": "docdb/US.2025383260.A1",
                "drawings": {"desc": "Drawing", "link": LINK, "pages": 14, "formats": ["tiff"]},
                "fullimage": None, "firstpage": None, "quota": None, "reason": None},
    }
    db = data_dir / "var" / "library.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    # The TABLES come from the program's own schema — a gate that hand-copies
    # DDL tests its copy, and this one did: the first run failed on a `lookups`
    # column the fixture had invented. The CARD below is still built by hand,
    # which is the part that matters: a fixture built by the extractor cannot
    # tell a broken extractor from a broken assertion.
    sys.path.insert(0, str(ROOT / "src"))
    from patentsgrabber.store import SCHEMA
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?)",
                 (NUMBER, card["title"], "google_patents", "2026-09-11T00:00:00+00:00",
                  json.dumps(card, ensure_ascii=False)))
    conn.commit()
    conn.close()

    # Two cached sheets, so the "already paid for" path is exercised without OPS.
    folder = data_dir / "var" / "ops-cache" / LINK.replace("published-data/images/", "").replace("/", "_")
    folder.mkdir(parents=True, exist_ok=True)
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                        "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")
    for page in (1, 2):
        (folder / f"p{page:04d}.png").write_bytes(png)

    (data_dir / ".env").write_text(
        f"OPS_CONSUMER_KEY={FAKE_KEY}\nOPS_CONSUMER_SECRET={FAKE_SECRET}\n",
        encoding="utf-8")


def leaks(text: str) -> list[str]:
    found = []
    if FAKE_KEY in text:
        found.append("key")
    if FAKE_SECRET in text:
        found.append("secret")
    return found


# ---------------------------------------------------------------- the checks

def check_stream_split(data: Path) -> None:
    print("\n=== stdout carries one JSON object and nothing else ===")
    code, out, err = pgb(data, "lookup", NUMBER, "--json")
    check("lookup --json exits 0", code == 0, f"exit {code}")
    parsed = None
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as exc:
        check("stdout parses as JSON", False, str(exc))
    else:
        check("stdout parses as JSON", isinstance(parsed, dict))
        check("the envelope is the documented shape",
              parsed.get("ok") is True and parsed.get("schema") == 1
              and "cost" in parsed and "data" in parsed)
    check("the human note went to stderr", "查詢" in err, err.strip()[:60])
    check("...and NOT to stdout", "查詢" not in out)

    # Control: the detector above would be worthless if stdout were never
    # anything but JSON. Without --json it must NOT parse — that is what proves
    # the check is measuring the flag rather than the program.
    code2, out2, _ = pgb(data, "lookup", NUMBER)
    ok = False
    try:
        json.loads(out2)
    except json.JSONDecodeError:
        ok = True
    check("CONTROL: without --json stdout is human text, not JSON", ok,
          out2.strip().splitlines()[0] if out2.strip() else "(empty)")

    # `--json` before the subcommand must mean the same thing as after it.
    _, out3, _ = pgb(data, "--json", "lookup", NUMBER)
    check("--json before the subcommand behaves identically",
          json.loads(out3).get("ok") is True if out3.strip() else False)

    # The root shim may not drift from the module it wraps.
    env = dict(os.environ, PATENTSGRABBER_DATA=str(data), PYTHONIOENCODING="utf-8",
               PYTHONPATH=str(ROOT / "src"))
    for name in ("OPS_CONSUMER_KEY", "OPS_CONSUMER_SECRET", "OPS_BASE_URL"):
        env.pop(name, None)
    mod = subprocess.run([sys.executable, "-m", "patentsgrabber.cli", "doctor", "--json"],
                         cwd=str(ROOT), env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=90)
    shim = pgb(data, "doctor", "--json")[1]
    check("pgb.py and `python -m patentsgrabber.cli` agree",
          mod.returncode == 0 and json.loads(mod.stdout) == json.loads(shim),
          f"module exit {mod.returncode}")


def check_lean_default(data: Path) -> None:
    print("\n=== the default withholds text, and says that it did ===")
    _, out, _ = pgb(data, "lookup", NUMBER, "--json")
    card = json.loads(out)["data"]
    present = [k for k in TEXT_KEYS if k in card]
    check("no text-bearing key survives lean mode", not present, f"found {present}")
    blob = json.dumps(card, ensure_ascii=False)
    for marker in ("ABSTRACTTEXTMARKER", "CLAIMSTEXTMARKER", "DESCRIPTIONTEXTMARKER"):
        check(f"no {marker} anywhere in the lean payload", marker not in blob)
    check("the withholding is declared", isinstance(card.get("text_withheld"), dict))
    check("counts survive so the caller knows what it did not get",
          card.get("counts", {}).get("claims") == 5
          and card["counts"]["description_blocks"] == 7,
          json.dumps(card.get("counts"), ensure_ascii=False))
    check("BR-3's absence reasons survive lean mode", bool(card.get("missing")))
    check("BR-4's per-field source survives lean mode",
          card.get("sources", {}).get("title") == "Google Patents")

    # Control: the same assertions against --full must FAIL to find nothing.
    # A lean check passes trivially on a card that never had text in it.
    _, out2, _ = pgb(data, "lookup", NUMBER, "--full", "--json")
    full = json.loads(out2)["data"]
    blob2 = json.dumps(full, ensure_ascii=False)
    check("CONTROL: --full DOES carry every text key",
          all(k in full for k in TEXT_KEYS),
          f"missing {[k for k in TEXT_KEYS if k not in full]}")
    check("CONTROL: --full DOES contain the seeded text markers",
          all(m in blob2 for m in ("ABSTRACTTEXTMARKER", "CLAIMSTEXTMARKER",
                                   "DESCRIPTIONTEXTMARKER")))
    check("CONTROL: --full does not claim text was withheld",
          "text_withheld" not in full)


def check_no_credential(data: Path) -> None:
    print("\n=== the leak detector itself (positive control) ===")
    check("CONTROL: the detector catches a stream that DOES carry the key",
          leaks(f"OPS_CONSUMER_KEY={FAKE_KEY} OPS_CONSUMER_SECRET={FAKE_SECRET}")
          == ["key", "secret"])
    on_disk = (data / ".env").read_text(encoding="utf-8")
    check("CONTROL: the credential really did reach the settings file",
          leaks(on_disk) == ["key", "secret"])

    print("\n=== no command prints a credential on either stream ===")
    for argv in (["doctor", "--json"], ["doctor"], ["lookup", NUMBER, "--json"],
                 ["lookup", NUMBER, "--full", "--json"], ["lookup", NUMBER, "--full"],
                 ["figures", NUMBER, "--offline", "--json"],
                 ["figures", NUMBER, "--pages", "99", "--json"],
                 ["figures", NUMBER, "--pages", "3-4", "--json"],
                 ["search", "Corning", "--json"], ["search", "Corning", "--full", "--json"],
                 ["pdf", NUMBER, "--json"], ["pdf", NUMBER, "--pages", "0", "--json"],
                 ["lookup", "not-a-number", "--json"]):
        _, out, err = pgb(data, *argv)
        found = leaks(out) + leaks(err)
        check(f"pgb {' '.join(argv)}", not found, f"leaked {found}")

    _, out, _ = pgb(data, "doctor", "--json")
    check("doctor reports configured-or-not as a boolean, not a hint",
          json.loads(out)["data"]["ops_configured"] is True)


def check_spend_is_opt_in(data: Path) -> None:
    print("\n=== spending is opt-in, capped, and reported ===")
    _, out, _ = pgb(data, "figures", NUMBER, "--offline", "--json")
    got = json.loads(out)
    check("figures without --pages costs no OPS request",
          got["ok"] and got["cost"]["ops_requests"] == 0 and got["cost"]["pages_fetched"] == 0,
          json.dumps(got.get("cost")))
    check("...and says why nothing was fetched", "not_fetched" in got["data"])
    check("...while still reporting which sheets are already on disk",
          got["data"]["cached_pages"] == [1, 2], str(got["data"]["cached_pages"]))

    code, out, _ = pgb(data, "figures", NUMBER, "--pages", "1-2", "--json")
    got = json.loads(out)
    check("sheets already on disk are served without spending",
          code == 0 and got["cost"]["ops_requests"] == 0 and got["cost"]["pages_fetched"] == 0)
    paths = [Path(f["path"]) for f in got["data"]["files"]]
    check("...and come back as paths that exist",
          len(paths) == 2 and all(p.is_file() for p in paths),
          str(paths[:1]))

    # Control: the same command for a sheet NOT on disk must not quietly
    # succeed. There is no credential that works here, so this is the offline
    # case — and it has to exit non-zero rather than returning an empty file
    # list, which a caller cannot tell from "this document has no page 7".
    code, out, _ = pgb(data, "figures", NUMBER, "--pages", "7-7", "--json")
    got = json.loads(out)
    check("CONTROL: a sheet that would cost a request fails loudly, not silently",
          code != 0 and got["ok"] is False, f"exit {code}")
    check("...with a code a caller can branch on",
          got.get("error", {}).get("code") in ("not_configured", "upstream_fault"),
          got.get("error", {}).get("code"))

    # And the in-between case: some sheets on disk, some not. That is a partial
    # success and must SAY so per-page rather than pretending either way.
    code, out, _ = pgb(data, "figures", NUMBER, "--pages", "1-7", "--json")
    got = json.loads(out)
    check("a partly-served request succeeds and names the pages it could not get",
          code == 0 and got["ok"] is True
          and [f["page"] for f in got["data"]["files"]] == [1, 2]
          and [f["page"] for f in got["data"]["failed"]] == [3, 4, 5, 6, 7],
          f"exit {code}, got {len(got['data']['files'])} files "
          f"and {len(got['data']['failed'])} failures")

    code, out, _ = pgb(data, "figures", NUMBER, "--pages", "1-40", "--json")
    check("over the cap is refused before any request", code == 3
          and json.loads(out)["error"]["code"] == "refused", f"exit {code}")
    code, out, _ = pgb(data, "figures", NUMBER, "--pages", "zero", "--json")
    check("CONTROL: a malformed --pages is refused too, not coerced", code == 3, f"exit {code}")
    code, out, _ = pgb(data, "lookup", "Corning", "--json")
    check("a company name is refused by lookup and pointed at search", code == 3,
          json.loads(out)["error"]["message"][:40])


def check_search_and_pdf(data: Path) -> None:
    """The two commands the first version of this gate never ran.

    That omission is why a BLOCKER shipped green: `search` handed out one full
    OPS abstract per result row, on a command with no `--full` to have opted
    into, and `run_gates.py` still said ALL PASS. The lesson is not "add search"
    but "a gate's command matrix must cover every command, or its verdict is
    only about the subset somebody remembered".

    **The ruler**: there is no live OPS here, so the leaning is checked on the
    FUNCTION with a synthetic OPS-shaped payload, plus a structural check that
    the command still routes through it. The live search path is
    `check_search.py`, which spends quota and runs in the `ops` tier.
    """
    print("\n=== search returns a lean list too, and pdf validates both ends ===")
    sys.path.insert(0, str(ROOT / "src"))
    from patentsgrabber import cli

    raw = {"query": "pa=corning", "total": 2, "results": [
        {"number": "US1A", "title": "One", "abstract": "SEARCHABSTRACTMARKER " * 30},
        {"number": "US2A", "title": "Two", "abstract": "SEARCHABSTRACTMARKER " * 30}]}
    lean = cli.lean_search(raw)
    blob = json.dumps(lean, ensure_ascii=False)
    check("no result row keeps its abstract", "SEARCHABSTRACTMARKER" not in blob)
    check("...and each row says how long the one it lost was",
          all(r.get("abstract_chars", 0) > 0 for r in lean["results"]),
          str([r.get("abstract_chars") for r in lean["results"]]))
    check("...and the withholding is declared", "text_withheld" in lean)
    check("CONTROL: the untouched result DOES carry the abstracts",
          "SEARCHABSTRACTMARKER" in json.dumps(raw, ensure_ascii=False))

    src = (ROOT / "src" / "patentsgrabber" / "cli.py").read_text(encoding="utf-8")
    body = src.split("def cmd_search(")[1].split("\ndef ")[0]
    check("cmd_search still routes through lean_search", "lean_search(" in body)
    check("...and only when --full was not given", "if not args.full:" in body)

    # `Service.search` reports an OPS failure as a PAYLOAD, not an exception, so
    # the first version wrapped it in a success envelope: exit 0, `ok: true`,
    # and a `reason` inside saying it had failed. This gate's own control line
    # is what caught it — the control asserted "a well-formed search is not
    # refused" and got exit 0 from a search that could not possibly have worked.
    check("a search that failed upstream is told apart from one that found nothing",
          cli.search_fault({"available": False, "reason": "EPO OPS 檢索失敗（HTTP 403）。"})
          == "upstream_fault")
    check("...and from one with no credential",
          cli.search_fault({"available": False, "reason": "尚未設定 EPO OPS 金鑰。"})
          == "not_configured")
    code, out, _ = pgb(data, "search", "Corning", "--json")
    check("a search that cannot reach OPS exits non-zero", code != 0,
          f"exit {code} — {json.loads(out).get('error', {}).get('code')}")
    check("CONTROL: zero results is an ANSWER, not a failure",
          "not result.get(\"available\")" in
          (ROOT / "src" / "patentsgrabber" / "cli.py").read_text(encoding="utf-8"),
          "the branch keys on `available`, not on an empty results list")

    code, out, _ = pgb(data, "pdf", NUMBER, "--pages", "0", "--json")
    check("pdf refuses a page count below 1", code == 3,
          f"exit {code} - {json.loads(out).get('error', {}).get('message', '')[:40]}")
    code, _, _ = pgb(data, "pdf", NUMBER, "--pages", "999", "--json")
    check("CONTROL: pdf refuses one above the cap too", code == 3, f"exit {code}")


def check_long_text_backstop(data: Path) -> None:
    """The named denylist is not the only thing between a caller and a wall of
    patent text. This is the rule that needs no list to stay current."""
    print("\n=== a text field nobody remembered to name is still withheld ===")
    sys.path.insert(0, str(ROOT / "src"))
    from patentsgrabber import cli

    card = {"title": "short", "surprise_note": "UNLISTEDTEXTMARKER " * 60,
            "nested": [{"deep": "UNLISTEDTEXTMARKER " * 60}], "year": 2026}
    out, caught = cli.strip_long_text(card)
    check("a long string in an UNLISTED top-level field is replaced",
          "UNLISTEDTEXTMARKER" not in json.dumps(out, ensure_ascii=False))
    check("...including one nested inside a list of dicts",
          "nested[0].deep" in caught, str(caught))
    check("...and every removal is named so it can be declared",
          len(caught) == 2, str(caught))
    check("CONTROL: short values are untouched",
          out["title"] == "short" and out["year"] == 2026)
    limit = cli.LEAN_TEXT_LIMIT
    check("CONTROL: a string exactly at the limit survives",
          cli.strip_long_text("x" * limit)[0] == "x" * limit)
    check("CONTROL: one character over does not",
          cli.strip_long_text("x" * (limit + 1))[1] == [""])


def check_pages_argument(data: Path) -> None:
    """`--pages` decides what a mistake costs, so its parsing is a quota control."""
    print("\n=== --pages: a bare number is THAT page, and no range can be huge ===")
    sys.path.insert(0, str(ROOT / "src"))
    from patentsgrabber import cli

    check("a bare number means that one page", cli.parse_pages("11") == [11])
    check("a range means the range", cli.parse_pages("1-3") == [1, 2, 3])
    check("a list means those pages", cli.parse_pages("2,5") == [2, 5])
    for spec in ("1-99999999999", "99999999999", "1-40", "0", "-3", "zero", "3-1"):
        refused = False
        try:
            cli.parse_pages(spec)
        except cli.Refused:
            refused = True
        check(f"CONTROL: --pages {spec!r} is refused", refused)
    # The blocker this replaced: the cap used to run AFTER the list was built,
    # so a huge range materialised first. Timing is what proves the order.
    t0 = time.time()
    try:
        cli.parse_pages("1-99999999999")
    except cli.Refused:
        pass
    spent_s = time.time() - t0
    check("...refused instantly, not after materialising the range",
          spent_s < 1.0, f"{spent_s:.3f}s")


def check_absent_pages(data: Path) -> None:
    """A 404 that has to be paid for twice is a quota leak with a receipt."""
    print("\n=== a sheet EPO does not hold is remembered, not re-bought ===")
    folder = data / "var" / "ops-cache" / LINK.replace(
        "published-data/images/", "").replace("/", "_")
    marker = folder / "absent-pages.json"

    code, out, _ = pgb(data, "figures", NUMBER, "--pages", "7-7", "--json")
    first = json.loads(out)
    check("an unfetchable sheet fails loudly and still reports its cost",
          code != 0 and "cost" in first, f"exit {code}")
    # With no credential the failure is NOT a 404, so nothing may be remembered:
    # a negative cache that recorded "the key was missing" as "the page does not
    # exist" would be worse than having none.
    check("CONTROL: a failure that is not a 404 is not remembered",
          not marker.exists(), "absent-pages.json was written for a non-404")

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(
        {"link": LINK, "pages": {"9": "EPO OPS 取不到第 9 頁（HTTP 404）。"}},
        ensure_ascii=False), encoding="utf-8")
    code, out, _ = pgb(data, "figures", NUMBER, "--pages", "9-9", "--json")
    got = json.loads(out)
    failed = got.get("error", {}).get("failed") or got.get("data", {}).get("failed") or []
    check("a remembered 404 is served without touching OPS",
          got["cost"]["ops_requests"] == 0 and failed and failed[0]["from"] == "cache",
          json.dumps(got.get("cost")))
    check("...and the run still fails rather than looking like an empty result",
          code != 0, f"exit {code}")
    _, out, _ = pgb(data, "figures", NUMBER, "--offline", "--json")
    check("...and the free listing call now names the absent sheets",
          json.loads(out)["data"].get("known_absent", {}).get("pages") == [9])
    marker.unlink()


def check_cost_is_honest(data: Path) -> None:
    print("\n=== cost says what happened, not which flag was passed ===")
    _, out, _ = pgb(data, "figures", NUMBER, "--pages", "1-2", "--json")
    got = json.loads(out)
    check("pages served entirely from disk report network=store",
          got["cost"]["network"] == "store" and got["cost"]["ops_requests"] == 0,
          json.dumps(got["cost"]))
    _, out, _ = pgb(data, "figures", NUMBER, "--pages", "7-7", "--json")
    got = json.loads(out)
    check("CONTROL: a FAILING call carries cost and quota too",
          "cost" in got and "quota" in got, str(sorted(got.keys())))
    check("...with exactly the keys a success has, minus data plus error",
          set(got.keys()) == {"ok", "schema", "command", "cost", "quota", "error"},
          str(sorted(got.keys())))


def check_usage_errors(data: Path) -> None:
    """A bad flag is a refusal, and it answers in the shape the caller asked for.

    Stock argparse exits 2, which is `not_configured` in this program's table —
    so "you spelled a flag wrong" and "there is no credential" were the same
    exit code, and a caller branching on it would have gone looking for a key
    that was there all along. It also prints usage to stderr and nothing to
    stdout, leaving a `--json` caller with no JSON on the one path where it most
    needs to know why.
    """
    print("\n=== a bad argument refuses like everything else refuses ===")
    for argv, label in ((["search", "Corning", "--scope", "UK", "--json"], "unknown --scope"),
                        (["lookup", NUMBER, "--nope", "--json"], "unknown flag"),
                        (["figures", "--json"], "missing positional"),
                        (["search", "Corning", "--size", "0", "--json"], "--size below 1"),
                        (["search", "Corning", "--size", "500", "--json"], "--size above 100"),
                        (["search", "Corning", "--start", "0", "--json"], "--start below 1")):
        code, out, err = pgb(data, *argv)
        ok_json = False
        try:
            payload = json.loads(out)
            ok_json = payload.get("ok") is False and payload["error"]["code"] == "refused"
        except (json.JSONDecodeError, KeyError):
            payload = {}
        check(f"{label}: exit 3, not argparse's 2", code == 3, f"exit {code}")
        check(f"{label}: answered in JSON when --json was asked for", ok_json,
              (out or err).strip().splitlines()[0][:60] if (out or err).strip() else "(silent)")

    # CONTROL: the same command spelled correctly must NOT be refused, or this
    # section would score full marks against a parser that rejected everything.
    code, out, _ = pgb(data, "figures", NUMBER, "--offline", "--json")
    check("CONTROL: a well-formed command is not refused", code == 0, f"exit {code}")
    code, _, _ = pgb(data, "search", "Corning", "--scope", "EP", "--size", "5", "--json")
    check("CONTROL: a documented --scope value is not refused as a bad argument",
          code != 3, f"exit {code} (non-zero here = OPS unreachable, which is correct)")


def check_two_processes(data: Path) -> None:
    print("\n=== the library survives two processes (CLI + the running server) ===")
    _, out, _ = pgb(data, "doctor", "--json")
    mode = json.loads(out)["data"]["journal_mode"]
    check("the library is in WAL mode", mode == "wal", f"journal_mode={mode}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = [f.result()[0] for f in
                 [pool.submit(pgb, data, "lookup", NUMBER, "--json") for _ in range(4)]]
    check("four concurrent writers all succeed", all(c == 0 for c in codes),
          f"exits {codes} in {time.time() - t0:.1f}s")
    print("     ruler: at this size four writers would very likely also pass under the")
    print("     old rollback journal — the deterministic assertion is journal_mode above;")
    print("     the control below is what proves this behavioural check can see a lock.")

    # Control: hold the database exclusively and the CLI must fail LOUDLY inside
    # its own timeout. A concurrency check that cannot be made to fail is not
    # measuring concurrency.
    conn = sqlite3.connect(data / "var" / "library.sqlite3", timeout=1.0)
    conn.execute("PRAGMA busy_timeout=1000")
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("INSERT INTO lookups (query, ok, detail, at) VALUES ('lock',1,'x','now')")
        t0 = time.time()
        try:
            code, out, err = pgb(data, "lookup", NUMBER, "--json", timeout=60)
        except subprocess.TimeoutExpired:
            check("CONTROL: an exclusive lock is reported, not hung on", False,
                  "the CLI hung instead of reporting the lock")
            return
        parsed = json.loads(out) if out.strip() else {}
        check("CONTROL: an exclusive lock is reported, not hung on",
              code == EXIT_BUSY and parsed.get("error", {}).get("code") == "busy",
              f"exit {code} after {time.time() - t0:.1f}s — "
              f"{parsed.get('error', {}).get('code')}")
        # The same wait must not be a SILENT one (user ruling, 2026-08-26): a
        # command line that prints nothing for fifteen seconds is one a caller
        # can only kill. The ticks are on stderr, so they cost the parser nothing.
        check("...and the wait announced itself while it was happening",
              "仍在" in err, err.strip().splitlines()[-1][:60] if err.strip() else "(silent)")
        check("...without polluting the parsed stream", "仍在" not in out)
    finally:
        conn.rollback()
        conn.close()


def check_version_pin() -> None:
    print("\n=== the version the CLI reports is the version that ships ===")
    app_src = (ROOT / "src" / "patentsgrabber" / "app.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "([^"]+)"', app_src, re.MULTILINE)
    if not match:
        check("app.py still declares VERSION where build.ps1 reads it", False)
        return
    check("app.py still declares VERSION where build.ps1 reads it", True, match.group(1))
    init_src = (ROOT / "src" / "patentsgrabber" / "__init__.py").read_text(encoding="utf-8")
    init = re.search(r'^__version__ = "([^"]+)"', init_src, re.MULTILINE)
    check("__init__.__version__ agrees with app.VERSION",
          bool(init) and init.group(1) == match.group(1),
          f"{init.group(1) if init else '?'} vs {match.group(1)}")


def main() -> int:
    if not PGB.is_file():
        print(f"  pgb.py is not where this checker expects it ({PGB})")
        return 2
    with tempfile.TemporaryDirectory(prefix="pgb-gate-") as tmp:
        data = Path(tmp)
        seed(data)
        check_stream_split(data)
        check_lean_default(data)
        check_no_credential(data)
        check_spend_is_opt_in(data)
        check_search_and_pdf(data)
        check_long_text_backstop(data)
        check_pages_argument(data)
        check_absent_pages(data)
        check_cost_is_honest(data)
        check_usage_errors(data)
        check_two_processes(data)
        check_version_pin()
    print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                       # a broken checker is not a pass
        print(f"  the checker itself failed: {type(exc).__name__}: {exc}")
        sys.exit(2)
