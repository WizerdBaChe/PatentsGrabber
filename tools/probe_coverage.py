"""Stage 0 acceptance probe — resolves M1/M2/M3 of the boundary contract.

M1  number normalization against a fixed expectation table
M2  coverage matrix: real US documents x fields, each cell carrying the selector
    that produced it, WITH a positive control (a granted patent, where claims
    must exist) and a negative control (a number that does not exist, which must
    fail loudly rather than yield an empty card)
M3  raw HTML persisted before parsing

Why the controls: a boolean "claims: absent" is ambiguous between "the page has
no claims" and "our parser missed them". A run that cannot tell those apart is
not evidence. The positive control calibrates the instrument; the negative
control proves the instrument can say no.

Usage:  python tools/probe_coverage.py
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from patentsgrabber import numbers  # noqa: E402
from patentsgrabber.sources import google_patents as gp  # noqa: E402

RAW_DIR = ROOT / "var" / "raw"
OUT_DIR = ROOT / "var" / "probe"

# --------------------------------------------------------------------------- #
# M1 — number normalization expectations
# --------------------------------------------------------------------------- #
# (input, expected canonical, expected kind_of_document)
M1_CASES = [
    ("US20250383260A1", "US20250383260A1", "publication"),
    ("US 2025/0383260 A1", "US20250383260A1", "publication"),
    ("us20250383260a1", "US20250383260A1", "publication"),
    ("2025/0383260", "US20250383260A1", "publication"),
    ("US2025383260A1", "US20250383260A1", "publication"),  # Espacenet 6-digit serial
    ("US6285999B1", "US6285999B1", "grant"),
    ("6,285,999", "US6285999B2", "grant"),  # no kind given -> most common first
    ("US 11,000,000 B2", "US11000000B2", "grant"),
    ("5960411", "US5960411B2", "grant"),
    ("18/123,456", "", "application"),  # detected, unsupported, must not raise
]

# --------------------------------------------------------------------------- #
# M2 — documents to probe
# --------------------------------------------------------------------------- #
DOCS = [
    ("US20250383260A1", "使用者原始範例 (pre-grant A1, 2025)"),
    ("US6285999B1", "POSITIVE CONTROL — granted patent, claims MUST exist"),
    ("US5960411A", "granted, pre-2001 kind code A"),
    ("US8046721B2", "granted B2"),
    ("US4237224A", "granted, 1980 vintage"),
    ("US11000000B2", "granted B2, 8-digit number"),
    ("US10000000B2", "granted B2, 8-digit number"),
    ("US7479949B2", "granted B2"),
    ("US20230123456A1", "pre-grant A1, 2023"),
    ("US20200000001A1", "pre-grant A1, first serial of 2020"),
    ("US99999999B2", "NEGATIVE CONTROL — must fail loudly, never an empty card"),
]

FIELDS = [
    "title",
    "abstract",
    "description",
    "claims",
    "images",
    "pdf_link",
    "classifications",
    "family",
    "similar_documents",
    "backward_citations",
    "forward_citations",
    "legal_status",
    "legal_events",
    "publication_date",
    "filing_date",
    "priority_date",
    "assignee",
    "inventors",
]

# Reuse saved HTML unless --refresh is passed: re-hitting the source to re-test a
# parser change is both slower and rude, and the raw dumps exist precisely so a
# parser fix can be validated offline.
USE_CACHE = "--refresh" not in sys.argv


def run_m1() -> tuple[bool, list[str]]:
    lines, ok = [], True
    for raw, expected, expected_kind in M1_CASES:
        try:
            p = numbers.normalize(raw)
            got, got_kind = p.canonical, p.kind_of_document
        except numbers.NumberError as exc:
            got, got_kind = f"ERROR: {exc}", "error"
        passed = (got == expected) and (got_kind == expected_kind)
        ok &= passed
        lines.append(
            f"{'PASS' if passed else 'FAIL'}  {raw!r:24} -> {got!r:20} [{got_kind}]"
            + ("" if passed else f"   expected {expected!r} [{expected_kind}]")
        )
    return ok, lines


def size_of(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return f"{len(value)}c"
    if isinstance(value, list):
        return f"{len(value)}"
    return "y"


def run_m2() -> tuple[list[dict], list[str]]:
    results, notes = [], []
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(headers=gp.HEADERS, timeout=30.0, follow_redirects=True)
    try:
        for number, label in DOCS:
            entry = {"number": number, "label": label}
            cached = RAW_DIR / f"{number}.html"
            try:
                if USE_CACHE and cached.exists():
                    doc = gp.parse(
                        cached.read_text(encoding="utf-8"),
                        number,
                        gp.BASE.format(number=number, lang="en"),
                        200,
                        cached,
                    )
                else:
                    doc = gp.get(number, raw_dir=RAW_DIR, client=client, polite_delay=1.0)
                entry["status"] = "ok"
                entry["http"] = doc.http_status
                # Presence of an image URL in the markup is NOT the same as an image
                # the user can see: the newest publications list URLs that answer 403.
                urls = doc.fields["images"].value or []
                if urls:
                    try:
                        hr = client.head(urls[0], follow_redirects=True)
                        entry["images_fetchable"] = f"{hr.status_code}"
                        if hr.status_code not in (200, 206):
                            doc.fields["images"].selector += f" (URL 存在但 HTTP {hr.status_code})"
                            doc.fields["images"].value = []
                    except Exception as exc:
                        entry["images_fetchable"] = f"ERR {type(exc).__name__}"
                        doc.fields["images"].value = []
                else:
                    entry["images_fetchable"] = "-"
                entry["fields"] = {
                    k: {
                        "present": doc.fields[k].present,
                        "size": size_of(doc.fields[k].value),
                        "selector": doc.fields[k].selector,
                    }
                    for k in FIELDS
                    if k in doc.fields
                }
            except gp.FetchError as exc:
                entry["status"] = "fetch_error"
                entry["error"] = str(exc)
            except Exception as exc:  # parser bug, not a source verdict
                entry["status"] = "parser_crash"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                notes.append(f"{number}: PARSER CRASH\n{traceback.format_exc()}")
            results.append(entry)
            print(f"  {number:20} {entry['status']}")
    finally:
        client.close()
    return results, notes


def render_matrix(results: list[dict]) -> str:
    header = "| document | " + " | ".join(FIELDS) + " |"
    sep = "|---" * (len(FIELDS) + 1) + "|"
    rows = [header, sep]
    for r in results:
        if r["status"] != "ok":
            rows.append(f"| **{r['number']}** | " + " | ".join([f"_{r['status']}_"] + [""] * (len(FIELDS) - 1)) + " |")
            continue
        cells = []
        for f in FIELDS:
            info = r["fields"].get(f)
            cells.append(f"{'Y' if info and info['present'] else '.'} {info['size'] if info else '-'}")
        rows.append(f"| {r['number']} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def render_selectors(results: list[dict]) -> str:
    """The ruler beside the rate: which selector produced each field, per doc."""
    lines = []
    for r in results:
        if r["status"] != "ok":
            continue
        lines.append(f"\n### {r['number']}")
        for f in FIELDS:
            info = r["fields"].get(f)
            if not info:
                continue
            mark = "Y" if info["present"] else "."
            lines.append(f"  {mark} {f:20} {info['size']:>6}  {info['selector'] or '(no selector matched)'}")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n=== M1: number normalization ===")
    m1_ok, m1_lines = run_m1()
    for line in m1_lines:
        print("  " + line)
    print(f"  --> M1 {'PASS' if m1_ok else 'FAIL'}")

    print("\n=== M2: coverage probe ===")
    results, notes = run_m2()

    matrix = render_matrix(results)
    selectors = render_selectors(results)

    # Controls
    pos = next((r for r in results if r["number"] == "US6285999B1"), None)
    neg = next((r for r in results if r["number"] == "US99999999B2"), None)
    pos_ok = bool(pos and pos["status"] == "ok" and pos["fields"].get("claims", {}).get("present"))
    neg_ok = bool(neg and neg["status"] == "fetch_error")

    # A field absent on EVERY document is a selector bug far more often than it is
    # a property of ten unrelated patents. Flag it rather than let it read as data.
    parsed = [r for r in results if r["status"] == "ok"]
    always_absent = [
        f
        for f in FIELDS
        if parsed and not any(r["fields"].get(f, {}).get("present") for r in parsed)
    ]

    report = [
        "# Stage 0 coverage probe",
        "",
        f"- M1 number normalization: **{'PASS' if m1_ok else 'FAIL'}**",
        f"- Positive control (US6285999B1 claims present): **{'PASS' if pos_ok else 'FAIL'}**"
        + ("" if pos_ok else "  <- instrument NOT calibrated; matrix absences are uninterpretable"),
        f"- Negative control (US99999999B2 fails loudly): **{'PASS' if neg_ok else 'FAIL'}**"
        + ("" if neg_ok else f"  <- got status={neg['status'] if neg else 'missing'}"),
        f"- Fields absent on ALL {len(parsed)} parsed documents: "
        + (
            f"**{', '.join(always_absent)}** <- SUSPECT SELECTOR BUG, not a data absence"
            if always_absent
            else "none"
        ),
        "",
        "Cell format: `Y/.` presence, then size (chars for text, count for lists).",
        "",
        matrix,
        "",
        "## Selectors that produced each field",
        selectors,
    ]
    if notes:
        report += ["", "## Parser crashes", "```", *notes, "```"]

    (OUT_DIR / "coverage.md").write_text("\n".join(report), encoding="utf-8")
    (OUT_DIR / "coverage.json").write_text(
        json.dumps({"m1_pass": m1_ok, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + matrix)
    print(f"\nM1={m1_ok} positive_control={pos_ok} negative_control={neg_ok}")
    print(f"report -> {OUT_DIR / 'coverage.md'}")
    return 0 if (m1_ok and pos_ok and neg_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
