"""Prove the applicant search tells the truth about what it found.

A result list is easy to make look right and hard to trust: it can silently
truncate, silently collapse a company's name variants, silently accept an
injected query, or silently hand back rows the reader cannot open. Each of those
gets a check here, and each check is written so that it CAN fail.

Controls, both directions:
  positive  a company that certainly has US patents must return rows, and the
            country restriction must actually restrict
  negative  a nonsense name must return zero WITHOUT raising, and must not be
            reported as an error
  variants  a broad name must expose more than one applicant spelling (that is
            the whole point of BR-8); an exact name must collapse to one
  injection a query carrying a quote must not change the shape of the CQL

Costs a handful of OPS search calls (5-30/min allowance), so it is a gate to run
before shipping, not on every save.

    python tools/check_search.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patentsgrabber.service import Service, build_cql, parse_query   # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    return ok


def main() -> int:
    svc = Service(ROOT / "var" / "library.sqlite3",
                  raw_dir=ROOT / "var" / "raw",
                  cache_dir=ROOT / "var" / "ops-cache")
    if svc.ops_client() is None:
        print("EPO OPS credentials not available — cannot check the search.")
        return 2

    print("\n=== 1. CQL construction (offline, no calls) ===")
    field, term = parse_query("in=Larry Page")
    check("an explicit field prefix wins", (field, term) == ("in", "Larry Page"), f"{field}={term!r}")
    # The invariant is structural, not lexical: whatever the reader types must
    # end up as ONE quoted term plus the optional country clause. Inside quotes
    # CQL treats OR as literal text, so its presence there is harmless — a check
    # that flagged it would fail on the perfectly safe `pa="Barnes OR Noble"`.
    SAFE_CQL = re.compile(r'^(pa|in|ta|ti|ab)="[^"]*"( AND pn=US)?$')
    injected = build_cql('Corning" OR pa="Apple', field="pa", us_only=True)
    check("a quote in the input cannot open a second clause",
          bool(SAFE_CQL.match(injected)), injected)
    check("calibration: the matcher rejects a genuinely broken query",
          not SAFE_CQL.match('pa="Corning" OR pa="Apple"'),
          "hand-built two-clause CQL must not match")
    check("US restriction is part of the query, not a display filter",
          build_cql("Corning").endswith("AND pn=US"), build_cql("Corning"))
    check("no restriction when the reader turns it off",
          "pn=US" not in build_cql("Corning", us_only=False), build_cql("Corning", us_only=False))

    print("\n=== 2. positive control — a company with a real US portfolio ===")
    found = svc.search("Taiwan Semiconductor", size=25)
    ok = found.get("available") and found.get("total", 0) > 1000 and found.get("fetched", 0) > 0
    check("applicant search returns rows", bool(ok),
          f"total={found.get('total'):,} fetched={found.get('fetched')} "
          f"bytes={found.get('bytes', 0):,}")
    countries = {r["country"] for r in found.get("results", [])}
    check("pn=US actually restricts the result set", countries == {"US"}, f"countries={countries}")
    openable = sum(1 for r in found.get("results", []) if r.get("openable"))
    check("rows say whether the card can open them",
          openable > 0 and all("openable" in r for r in found["results"]),
          f"{openable}/{found.get('fetched')} openable")

    print("\n=== 3. truncation is declared, never silent ===")
    check("the reachable depth is stated when it is below the total",
          found.get("depth_capped") is (found.get("total", 0) > found.get("max_depth", 0))
          and found.get("reachable") == min(found.get("total", 0), found.get("max_depth", 0)),
          f"total={found.get('total'):,} reachable={found.get('reachable'):,} "
          f"capped={found.get('depth_capped')}")

    print("\n=== 4. BR-8 — one company is many strings ===")
    broad = svc.search("Corning", size=50)
    variants = broad.get("applicant_variants", [])
    check("a broad name exposes more than one applicant spelling", len(variants) > 1,
          f"{len(variants)} distinct: " + "; ".join(v["name"][:34] for v in variants[:4]))
    check("each variant carries the as-filed spelling behind it",
          all(v.get("originals") for v in variants),
          f"e.g. {variants[0]['name']} <- {[o['name'] for o in variants[0]['originals'][:2]]}"
          if variants else "")
    exact = svc.search("CORNING RESEARCH & DEVELOPMENT CORPORATION", size=25)
    check("an exact name collapses to one applicant (the refine path works)",
          len(exact.get("applicant_variants", [])) == 1,
          f"{[v['name'] for v in exact.get('applicant_variants', [])]}")
    check("a name with & survives into CQL", "&" in exact.get("cql", ""), exact.get("cql"))

    print("\n=== 5. negative control — nothing found is an ANSWER, not a failure ===")
    empty = svc.search("ZZZQ Nonexistent Applicant WXYZ")
    check("zero results, reported as zero",
          empty.get("available") is True and empty.get("total") == 0 and not empty.get("results"),
          f"available={empty.get('available')} total={empty.get('total')} "
          f"reason={(empty.get('reason') or '')[:48]}")
    check("and it says which field it looked in", bool(empty.get("reason")), empty.get("reason"))

    print("\n=== 6. input dispatch (BR-1) ===")
    cases = {"US6285999B1": "number", "6,285,999": "number", "US 2025/0383260 A1": "number",
             "Corning": "query", "in=Larry Page": "query", "台積電": "query"}
    wrong = {k: svc.classify(k) for k, v in cases.items() if svc.classify(k) != v}
    check("numbers go to the card, names go to the search", not wrong, f"misrouted: {wrong}")

    print("\n=== 7. quota ===")
    client = svc.ops_client()
    print(f"  {client.usage.summary()}")

    svc.close()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 66}\n{passed}/{len(results)} checks passed")
    if failed := [label for label, ok, _ in results if not ok]:
        print("failed: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
