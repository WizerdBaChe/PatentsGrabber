"""The schema constants must move when the extractors move.

`CLAUDE.md` says: bump `google_patents.READING_SCHEMA` or `service.OPS_CARD_SCHEMA`
in the same commit as any change to what those extractors produce, because that
constant is what re-derives the cards already sitting in the library. That rule
was written after being broken twice — and until now it was only a sentence, i.e.
it relied on somebody remembering it.

This test turns it into a property of the code: a fingerprint of the extractors'
executable structure is pinned next to each constant. Change an extractor without
bumping, and this fails with instructions.

The fingerprint is taken over the AST with docstrings removed, so comments,
formatting and docstring edits do NOT trip it. Only a change to what the code
actually does does — which is exactly the set of changes that can invalidate a
stored card.

    python tests/test_extractor_fingerprint.py
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patentsgrabber import service                      # noqa: E402
from patentsgrabber.sources import epo_ops as ops       # noqa: E402
from patentsgrabber.sources import google_patents as gp # noqa: E402

# ---------------------------------------------------------------- what is covered
#
# READING_SCHEMA covers the description blocks and the claim tree: everything that
# turns saved markup into what the reading pane shows. OPS_CARD_SCHEMA covers the
# card built from OPS when Google Patents has no record, including the two full-text
# parsers that feed it.
#
# Anything NOT listed here is, by construction, declared irrelevant to a stored
# card. Adding an extractor means adding it to this list.

READING_PARTS = [
    gp._description_blocks, gp._block_kind, gp._para_number, gp._ancestor_kinds,
    gp._inline, gp._inline_raw, gp._wrap, gp._figs_in, gp._fig_number,
    gp._claim_list, gp._claim_parts, gp._claim_texts, gp._is_claim_part,
    gp._is_source_text,
]
READING_CONSTANTS = [gp.DEPENDS_RE.pattern, gp._PARA_CLASSES, sorted(gp._INLINE_KEEP)]

OPS_CARD_PARTS = [
    service.Service.card_from_ops, ops.parse_fulltext_description,
    ops.parse_fulltext_claims, ops._fulltext_part,
]
OPS_CARD_CONSTANTS: list = []

# ------------------------------------------------------------------- the pins
#
# (schema version, fingerprint). Both must match. To update after a deliberate
# change: bump the constant in the source, run this file, paste the digest it
# prints. If you find yourself updating the digest WITHOUT bumping the constant,
# stop — that is the exact mistake this test exists to catch.

PINS = {
    # Baseline taken 2026-08-27 at main@cacfde9: the code that produced the cards
    # currently in var/library.sqlite3. Nothing was bumped to record it.
    "READING_SCHEMA": (4, "e027f1c2c67e511bfd324f0ffb859e9c5586ae2960dbe4e31807f769d0962a51"),
    "OPS_CARD_SCHEMA": (4, "cf8c7b5596360d05ab4b915e147dabcd2601f8d5ad68d849585fce7ed7d1903c"),
}


def _semantic_source(obj) -> str:
    """AST dump with docstrings dropped: code structure, not its presentation."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def fingerprint(parts, constants) -> str:
    h = hashlib.sha256()
    for obj in parts:
        h.update(obj.__qualname__.encode())
        h.update(_semantic_source(obj).encode())
    for const in constants:
        h.update(repr(const).encode())
    return h.hexdigest()


def _digest_of_text(sources: list[str]) -> str:
    h = hashlib.sha256()
    for src in sources:
        tree = ast.parse(src)
        h.update(ast.dump(tree).encode())
    return h.hexdigest()


CASES: list = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def test_reading_extractors_match_their_pinned_schema():
    want_version, want_digest = PINS["READING_SCHEMA"]
    got = fingerprint(READING_PARTS, READING_CONSTANTS)
    assert gp.READING_SCHEMA == want_version, (
        f"READING_SCHEMA is {gp.READING_SCHEMA}, this test pins {want_version}. "
        f"If the bump was deliberate, update PINS with version {gp.READING_SCHEMA} "
        f"and digest {got}."
    )
    assert got == want_digest, (
        "a reading extractor changed but READING_SCHEMA is still "
        f"{gp.READING_SCHEMA}. Cards already in the library were built by the OLD "
        "extractor and will not be re-derived until the constant moves.\n"
        f"    bump google_patents.READING_SCHEMA to {gp.READING_SCHEMA + 1}\n"
        f"    then set PINS['READING_SCHEMA'] = ({gp.READING_SCHEMA + 1}, '{got}')"
    )


@case
def test_ops_card_builder_matches_its_pinned_schema():
    want_version, want_digest = PINS["OPS_CARD_SCHEMA"]
    got = fingerprint(OPS_CARD_PARTS, OPS_CARD_CONSTANTS)
    assert service.OPS_CARD_SCHEMA == want_version, (
        f"OPS_CARD_SCHEMA is {service.OPS_CARD_SCHEMA}, this test pins {want_version}. "
        f"If the bump was deliberate, update PINS with version {service.OPS_CARD_SCHEMA} "
        f"and digest {got}."
    )
    assert got == want_digest, (
        "the OPS-built card changed but OPS_CARD_SCHEMA is still "
        f"{service.OPS_CARD_SCHEMA}. Provisional cards stored from OPS will keep the "
        "old shape until the constant moves.\n"
        f"    bump service.OPS_CARD_SCHEMA to {service.OPS_CARD_SCHEMA + 1}\n"
        f"    then set PINS['OPS_CARD_SCHEMA'] = ({service.OPS_CARD_SCHEMA + 1}, '{got}')"
    )


@case
def test_every_covered_extractor_still_exists():
    """A rename is a change too — and it would otherwise fail as an import error."""
    for obj in READING_PARTS + OPS_CARD_PARTS:
        assert callable(obj), f"{obj!r} is no longer callable"
        assert inspect.getsource(obj), f"no source for {obj.__qualname__}"


@case
def test_calibration_the_fingerprint_can_actually_fail():
    """A hash that never changes would pass forever and prove nothing."""
    same = _digest_of_text(["def f():\n    return 1\n"])
    again = _digest_of_text(["def f():\n    # a comment is not a behaviour change\n    return 1\n"])
    changed = _digest_of_text(["def f():\n    return 2\n"])
    assert same == again, "comments must NOT trip the fingerprint"
    assert same != changed, "a changed return value MUST trip the fingerprint"


def main() -> int:
    failures = []
    for fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            failures.append((fn.__name__, str(exc)))
            print(f"  FAIL  {fn.__name__}\n        {exc}")
        else:
            print(f"  PASS  {fn.__name__}")
    print()
    if failures:
        print(f"{len(failures)} of {len(CASES)} checks FAILED")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
