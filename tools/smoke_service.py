"""End-to-end smoke test of the service layer (network + store + card assembly).

Checks the behaviours the UI depends on, not just "it returned something":
  - a bare number with no kind code resolves by trying candidates
  - the second identical lookup is served from the local library, not the network
  - an unsupported input (application number) explains itself instead of erroring
  - a nonexistent number fails loudly and reports what was tried
  - long lists are capped WITH the total stated
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patentsgrabber.service import ResolveError, Service  # noqa: E402

DB = ROOT / "var" / "smoke.sqlite3"
if DB.exists():
    DB.unlink()

svc = Service(DB)
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


print("\n=== bare number, no kind code (must try candidates) ===")
card = svc.lookup("6,285,999")
check("resolved", card["number"] == "US6285999B1", f"got {card['number']}")
check("title present", bool(card["title"]), (card["title"] or "")[:60])
check("independent claims identified", len(card["independent_claims"]) > 0,
      f"indep={card['independent_claims']}")
check("description is real text", len(card["description"] or "") > 5000,
      f"{len(card['description'] or '')} chars")
check("forward citations capped with total stated",
      card["forward_citations"]["truncated"] and card["forward_citations"]["total"] > 1000,
      f"total={card['forward_citations']['total']} cap={card['forward_citations']['cap']}")
check("provenance recorded for claims",
      bool(card["provenance"]["claim_list"]["selector"]),
      card["provenance"]["claim_list"]["selector"])

print("\n=== same query again (must come from the local library) ===")
again = svc.lookup("US6285999B1")
check("served from store", again.get("_from_store") is True)

print("\n=== application number (unsupported, must explain) ===")
try:
    svc.lookup("18/123,456")
    check("explained rather than crashed", False, "no error raised")
except ResolveError as exc:
    check("explained rather than crashed", "申請號" in str(exc), str(exc)[:70])

print("\n=== nonexistent number (must fail loudly, report attempts) ===")
try:
    svc.lookup("US99999999B2")
    check("failed loudly", False, "returned a card for a nonexistent patent")
except ResolveError as exc:
    check("failed loudly", True, f"tried={exc.tried}")

print("\n=== garbage input ===")
try:
    svc.lookup("hello world")
    check("rejected garbage", False)
except ResolveError as exc:
    check("rejected garbage", True, str(exc)[:60])

print("\n=== library bookkeeping ===")
check("library has the fetched document", svc.store.count() == 1, f"count={svc.store.count()}")

svc.close()
print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
