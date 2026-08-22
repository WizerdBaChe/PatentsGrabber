"""Pin the OPS input-number rules that cost a live debugging round to find.

Every expectation below was OBSERVED against the live OPS API on 2026-08-23, not
inferred from documentation. They are counter-intuitive enough that a future
refactor would plausibly "fix" them back into the broken form, which is exactly
what a test is for:

    epodoc/US6285999B1        -> 404 SERVER.EntityNotFound   (kind code rejected)
    epodoc/US6285999          -> 200                          (no kind code)
    docdb/US.20250383260.A1   -> 404 SERVER.EntityNotFound   (7-digit serial)
    docdb/US.2025383260.A1    -> 200                          (6-digit serial)

The Espacenet display form US2025383260A1 is valid as NEITHER format; it is a
display string, and using it as an API input is the defect this file guards.

    python -m pytest tests/ -q      (or: python tests/test_ops_number_formats.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patentsgrabber import numbers  # noqa: E402

CASES_EPODOC = [
    # (user input, expected epodoc -- never carries a kind code)
    ("US20250383260A1", "US2025383260"),
    ("US 2025/0383260 A1", "US2025383260"),
    ("US2025383260A1", "US2025383260"),
    ("US6285999B1", "US6285999"),
    ("6,285,999", "US6285999"),
    ("5960411", "US5960411"),
]

CASES_DOCDB = [
    # (user input, expected docdb -- 6-digit serial for publications, kind required)
    ("US20250383260A1", "US.2025383260.A1"),
    ("US 2025/0383260 A1", "US.2025383260.A1"),
    ("US2025383260A1", "US.2025383260.A1"),
    ("US6285999B1", "US.6285999.B1"),
]

# Forms proven to fail against the live API — the code must never emit them.
MUST_NOT_EMIT = {"US2025383260A1", "US6285999B1", "US.20250383260.A1", "US20250383260A1"}


def test_epodoc_never_carries_kind_code():
    for raw, expected in CASES_EPODOC:
        got = numbers.normalize(raw).epodoc
        assert got == expected, f"{raw!r}: epodoc {got!r} != {expected!r}"
        assert not got[-2:].upper() in ("A1", "A2", "A9", "B1", "B2"), \
            f"{raw!r}: epodoc {got!r} still carries a kind code"


def test_docdb_uses_six_digit_serial_and_keeps_kind():
    for raw, expected in CASES_DOCDB:
        got = numbers.normalize(raw).docdb()
        assert got == expected, f"{raw!r}: docdb {got!r} != {expected!r}"


def test_never_emits_a_form_the_api_rejects():
    for raw, _ in CASES_EPODOC + CASES_DOCDB:
        p = numbers.normalize(raw)
        for emitted in [p.epodoc, p.docdb(), *p.docdb_candidates()]:
            assert emitted not in MUST_NOT_EMIT, \
                f"{raw!r} emitted {emitted!r}, a form the live API answers 404 to"


def test_kindless_input_produces_candidates_in_likelihood_order():
    p = numbers.normalize("6,285,999")
    assert p.docdb_candidates() == ["US.6285999.B2", "US.6285999.B1", "US.6285999.A"]
    # US6285999 is in fact a B1; the candidate walk is what finds it.


def test_espacenet_display_form_is_kept_but_is_not_an_api_input():
    p = numbers.normalize("US20250383260A1")
    assert p.espacenet == "US2025383260A1"      # still correct for display / deep links
    assert p.espacenet != p.epodoc              # and must not be confused with the API form
    assert p.espacenet not in p.docdb_candidates()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}\n        {exc}")
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILURE(S)'}")
    raise SystemExit(1 if failures else 0)
