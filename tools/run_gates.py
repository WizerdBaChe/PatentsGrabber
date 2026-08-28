"""Run the gates in one go, and say what each score actually counts.

Two problems this solves. First, five gates that each have to be remembered are
five gates that get skipped; "did you run them" was previously answered from
memory. Second, the scores are not comparable: `check_reading` prints one line per
DOCUMENT (its denominator grows whenever a page is added to var/raw), while the
others print one line per CHECK. Two runs both saying 20/20 mean different things.
The summary below states the unit next to every score.

    python tools/run_gates.py              # local only: no network, no OPS quota
    python tools/run_gates.py --net        # + the ones that fetch Google Patents
    python tools/run_gates.py --all        # + the ones that spend OPS quota

Exit code is the worst outcome seen: 0 all clear · 1 something failed ·
2 something could not be determined (and was not counted as a pass).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# tier: "local"  nothing leaves this machine
#       "net"    fetches Google Patents single-document pages (BR-7), no OPS quota
#       "ops"    spends EPO OPS quota
GATES = [
    ("tools/check_reading.py", "local", "one line per DOCUMENT in var/raw",
     "structure recovered without losing text, controls both ways"),
    ("tests/test_ops_number_formats.py", "local", "one line per CHECK",
     "the number formats OPS actually accepts"),
    ("tests/test_extractor_fingerprint.py", "local", "one line per CHECK",
     "extractors cannot change without their schema constant moving"),
    ("tools/check_layout.py", "local", "one line per CHECK",
     "R-9/R-10: the layout follows the window (starts Chrome and, if needed, a server)"),
    ("tools/check_figures.py", "local", "one line per CHECK",
     "the drawing pane's geometry under rotation, fit and zoom (Chrome, no network)"),
    ("tools/check_settings.py", "local", "one line per CHECK",
     "settings round-trip, no credential in any response, the local-only guard"),
    ("tools/check_secrets.py --tracked", "local", "one line per TRACKED FILE",
     "no tracked file in this repository contains a credential"),
    ("tools/make_state_diagrams.py --check", "local", "one line per CHECK",
     "the state diagrams' own geometry: on grid, orthogonal, anchored, nothing pierced"),
    ("tools/check_diagrams.py", "local", "one line per CHECK",
     "the DRAWN diagram page matches the model (Chrome; catches dangling arrowheads)"),
    ("tools/smoke_service.py", "net", "one line per CHECK",
     "end-to-end lookup, storage, and the three ways it must fail loudly"),
    ("tools/check_freshness.py", "net", "one line per METRIC per document",
     "the archive still matches what the source serves today"),
    ("tools/verify_ops.py", "ops", "one line per CHECK",
     "OPS capabilities, including the ones asserted as unavailable"),
    ("tools/check_search.py", "ops", "one line per CHECK",
     "applicant search, BR-8 variants, EP/US scopes"),
]

VERDICT = {0: "pass", 1: "FAILED", 2: "could not determine"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", action="store_true", help="also run the gates that fetch pages")
    ap.add_argument("--all", action="store_true", help="also run the gates that spend OPS quota")
    a = ap.parse_args()
    tiers = {"local"} | ({"net"} if a.net or a.all else set()) | ({"ops"} if a.all else set())

    todo = [g for g in GATES if g[1] in tiers]
    skipped = [g for g in GATES if g[1] not in tiers]
    print(f"running {len(todo)} gate(s): {', '.join(sorted(tiers))}")
    if "ops" in tiers:
        print("NOTE: verify_ops and check_search spend EPO OPS quota (~20 requests, <1 MB).")
    print()

    results = []
    for path, tier, unit, what in todo:
        print("=" * 78)
        print(f"[{tier}] {path} — {what}")
        print("=" * 78, flush=True)
        start = time.time()
        # A gate may carry its own flags (check_secrets audits every tracked
        # file here, where the hook only judges the staged diff).
        code = subprocess.run([sys.executable, *path.split()], cwd=str(ROOT)).returncode
        results.append((path, tier, unit, code, time.time() - start))
        print()

    print("=" * 78)
    print("SUMMARY — what each score counts")
    print("=" * 78)
    for path, tier, unit, code, secs in results:
        print(f"  {VERDICT.get(code, f'exit {code}'):<20}{path:<40}{secs:6.1f}s   {unit}")
    for path, tier, unit, what in skipped:
        print(f"  {'not run (' + tier + ')':<20}{path:<40}{'':>7}   {unit}")
    if skipped:
        print("\n  A gate that did not run is not a gate that passed. Add --net / --all.")

    worst = max((code for _, _, _, code, _ in results), default=0)
    print()
    if worst == 0:
        print(f"all {len(results)} gate(s) clear")
    elif worst == 1:
        print("at least one gate FAILED — read its section above")
    else:
        print("at least one gate could not determine its verdict")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
