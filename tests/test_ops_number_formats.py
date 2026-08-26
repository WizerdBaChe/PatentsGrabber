"""Pin the OPS input-number rules that cost a live debugging round to find.

Every expectation below was OBSERVED against the live OPS API, not inferred from
documentation. They are counter-intuitive enough that a future refactor would
plausibly "fix" them back into a broken form, which is exactly what this is for.

    2026-08-23
    epodoc/US6285999B1        -> 404 SERVER.EntityNotFound   (kind code rejected)
    epodoc/US6285999          -> 200                          (no kind code)
    docdb/US.20250383260.A1   -> 404 SERVER.EntityNotFound   (7-digit serial)
    docdb/US.2025383260.A1    -> 200                          (6-digit serial)

    2026-08-26 — the 6-digit finding does NOT generalise
    docdb/US.2026189299.A1    -> 404 SERVER.EntityNotFound
    docdb/US.20260189299.A1   -> 200                          (full 7-digit serial)
    epodoc/US2026189299       -> 404
    epodoc/US20260189299      -> 200

    So which serial width OPS holds a publication under is a property of the
    RECORD, not a rule. Both widths must be offered as candidates; picking one
    loses whichever documents use the other. What stays invariant is the shape:
    epodoc never carries a kind code, docdb always does, and the Espacenet
    display form (US2025383260A1) is valid as NEITHER.

    python -m pytest tests/ -q      (or: python tests/test_ops_number_formats.py)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patentsgrabber import numbers  # noqa: E402

KIND_CODES = ("A1", "A2", "A9", "B1", "B2")

# (input, a docdb form the live API answered 200 to)
PROVEN_DOCDB = [
    ("US20250383260A1", "US.2025383260.A1"),      # 2026-08-23
    ("US 2025/0383260 A1", "US.2025383260.A1"),
    ("US2025383260A1", "US.2025383260.A1"),
    ("US20260189299A1", "US.20260189299.A1"),     # 2026-08-26, the other width
    ("US6285999B1", "US.6285999.B1"),
]

# (input, an epodoc form the live API answered 200 to)
PROVEN_EPODOC = [
    ("US20250383260A1", "US2025383260"),
    ("US20260189299A1", "US20260189299"),
    ("US6285999B1", "US6285999"),
    ("6,285,999", "US6285999"),
    ("5960411", "US5960411"),
]

# Valid as neither format: a display string, not an API input.
DISPLAY_FORMS = {"US2025383260A1", "US6285999B1", "US20250383260A1"}


def test_epodoc_never_carries_kind_code():
    for raw, _ in PROVEN_EPODOC + PROVEN_DOCDB:
        for emitted in numbers.normalize(raw).epodoc_candidates():
            assert not emitted.upper().endswith(KIND_CODES), \
                f"{raw!r}: epodoc {emitted!r} carries a kind code"


def test_epodoc_candidates_contain_the_form_that_worked():
    for raw, expected in PROVEN_EPODOC:
        got = numbers.normalize(raw).epodoc_candidates()
        assert expected in got, f"{raw!r}: {expected!r} missing from epodoc candidates {got}"


def test_docdb_candidates_cover_both_serial_widths():
    for raw, expected in PROVEN_DOCDB:
        got = numbers.normalize(raw).docdb_candidates()
        assert expected in got, f"{raw!r}: {expected!r} missing from docdb candidates {got}"


def test_docdb_is_always_country_digits_kind():
    """Guards the shape, not just the value.

    A resolver bug once concatenated every doc-number in a number-service reply
    and asked OPS for `US.US2026189299A12026189299A1.A1`, which exists nowhere.
    A docdb body is digits; anything else is a construction error.
    """
    shape = re.compile(r"^[A-Z]{2}\.\d+\.[A-Z]\d?$")
    for raw, _ in PROVEN_DOCDB + PROVEN_EPODOC:
        for emitted in numbers.normalize(raw).docdb_candidates():
            assert shape.match(emitted), f"{raw!r}: docdb {emitted!r} is malformed"


def test_never_emits_a_display_form_as_an_api_input():
    for raw, _ in PROVEN_EPODOC + PROVEN_DOCDB:
        p = numbers.normalize(raw)
        for emitted in p.epodoc_candidates() + p.docdb_candidates():
            assert emitted not in DISPLAY_FORMS, \
                f"{raw!r} emitted {emitted!r}, a display string the API rejects"


def test_kindless_input_produces_candidates_in_likelihood_order():
    p = numbers.normalize("6,285,999")
    assert p.docdb_candidates() == ["US.6285999.B2", "US.6285999.B1", "US.6285999.A"]
    # US6285999 is in fact a B1; the candidate walk is what finds it.


def test_espacenet_display_form_is_kept_but_is_not_an_api_input():
    p = numbers.normalize("US20250383260A1")
    assert p.espacenet == "US2025383260A1"      # still correct for display / deep links
    assert p.espacenet not in p.epodoc_candidates()
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
