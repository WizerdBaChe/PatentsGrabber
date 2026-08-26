"""Prove the reading-structure extraction adds structure WITHOUT losing text.

Runs offline against the pages already saved in var/raw/ — no network, no quota.

Two failure modes are being guarded against, and each needs its own control:

  1. The extractor silently drops content (a vintage of markup it does not know).
     Guarded by comparing the concatenated block text against the section's own
     flat text, minus the two things blocks deliberately relocate (the "Description"
     <h2> label and the [0001] para-num tokens).

  2. The CHECKER always says PASS (a gate that cannot fail is not a gate).
     Guarded by a synthetic negative: the same page with its paragraph markup
     renamed. That document MUST fail. If it passes, the instrument is broken and
     every green line above it is meaningless.

    python tools/check_reading.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bs4 import BeautifulSoup                                    # noqa: E402

from patentsgrabber.sources import google_patents as gp          # noqa: E402

RAW = ROOT / "var" / "raw"
COVERAGE_MIN = 0.98          # blocks must carry ~all of the section's text
COVERAGE_MAX = 1.02

# Documents whose figure references are a matter of record (measured 2026-08-26).
FIGREF_POSITIVE = {"US8046721B2", "US20250383260A1", "US7479949B2"}
FIGREF_NEGATIVE = {"US4237224A", "US6285999B1", "US5960411A"}   # pre-2000: none marked


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def reference_text(html: str) -> str:
    """The description section's own text, minus what blocks move elsewhere."""
    soup = BeautifulSoup(html, "lxml")
    section = soup.find("section", attrs={"itemprop": "description"})
    if not section:
        return ""
    for tag in section.find_all(["h2", "para-num"]):
        tag.decompose()
    return norm(section.get_text(" ", strip=True))


def analyse(path: Path) -> dict | None:
    html = path.read_text(encoding="utf-8", errors="replace")
    number = path.stem
    try:
        doc = gp.parse(html, number, "file://" + str(path), 200, None)
    except gp.FetchError:
        return None                      # negative control page: no record at all

    blocks = doc.fields["description_blocks"].value or []
    ref = reference_text(html)
    got = norm(" ".join(b.get("text", "") for b in blocks))
    claims = doc.fields["claim_list"].value or []

    # Claims get their own coverage number. Measuring only the description let a
    # real defect through once: claims whose limitations are SIBLINGS of the
    # preamble rendered as the preamble alone — which looks like a short claim,
    # not like a failure. Whitespace is removed on both sides because the flat
    # text carries spacing artefacts from the markup's own <b>2</b> spans.
    claim_ref = claim_got = 0
    soup = BeautifulSoup(html, "lxml")
    section = soup.find("section", attrs={"itemprop": "claims"})
    if section:
        for div in section.find_all("div", class_="claim"):
            if div.get("num"):
                claim_ref += len(re.sub(r"\s+", "", div.get_text(" ", strip=True)))
    for c in claims:
        pieces = [c.get("lead") or ""] + [p.get("text", "") for p in (c.get("parts") or [])]
        claim_got += len(re.sub(r"\s+", "", " ".join(pieces)))

    return {
        "number": number,
        "blocks": len(blocks),
        "headings": sum(1 for b in blocks if b["type"] == "heading"),
        "paras": sum(1 for b in blocks if b["type"] == "para"),
        "numbered": sum(1 for b in blocks if b["type"] == "para" and b.get("num")),
        "pre": sum(1 for b in blocks if b["type"] == "pre"),
        "figrefs": sum(len(b.get("figs") or []) for b in blocks),
        "claims": len(claims),
        "claim_parts": sum(len(c.get("parts") or []) for c in claims),
        "claim_cover": (claim_got / claim_ref) if claim_ref else 1.0,
        "ref_chars": len(ref),
        "got_chars": len(got),
        "coverage": (len(got) / len(ref)) if ref else (1.0 if not blocks else 0.0),
        "empty_blocks": sum(1 for b in blocks if not (b.get("text") or "").strip()),
        "has_description": bool(ref),
    }


def verdict(r: dict) -> tuple[bool, str]:
    if not r["has_description"]:
        return True, "no description section (nothing to segment)"
    if r["blocks"] == 0:
        return False, "description exists but produced ZERO blocks"
    if r["empty_blocks"]:
        return False, f"{r['empty_blocks']} empty block(s)"
    if not (COVERAGE_MIN <= r["coverage"] <= COVERAGE_MAX):
        return False, f"text coverage {r['coverage']:.3f} outside [{COVERAGE_MIN}, {COVERAGE_MAX}]"
    if r["claims"] and not (COVERAGE_MIN <= r["claim_cover"] <= COVERAGE_MAX):
        return False, f"claim coverage {r['claim_cover']:.3f} — limitations are being dropped"
    return True, ""


def main() -> int:
    files = sorted(RAW.glob("*.html"))
    if not files:
        print(f"no saved pages in {RAW} — run tools/probe_coverage.py --refresh first")
        return 2

    rows, failures = [], []
    print(f"{'document':<22}{'blocks':>7}{'head':>6}{'para':>6}{'#num':>6}{'pre':>5}"
          f"{'figs':>6}{'clm':>5}{'parts':>6}{'cover':>8}{'clmcov':>8}  verdict")
    print("-" * 100)
    for path in files:
        r = analyse(path)
        if r is None:
            print(f"{path.stem:<22}{'—':>7}   (not a patent record — negative control page)")
            continue
        rows.append(r)
        ok, why = verdict(r)
        if not ok:
            failures.append((r["number"], why))
        print(f"{r['number']:<22}{r['blocks']:>7}{r['headings']:>6}{r['paras']:>6}"
              f"{r['numbered']:>6}{r['pre']:>5}{r['figrefs']:>6}{r['claims']:>5}"
              f"{r['claim_parts']:>6}{r['coverage']:>8.3f}{r['claim_cover']:>8.3f}"
              f"  {'ok' if ok else 'FAIL: ' + why}")

    # ---- systematic-failure detection (Stage 0 lesson: all-zero means the tool broke)
    print()
    for metric in ("blocks", "headings", "paras", "figrefs", "claim_parts"):
        if rows and all(r[metric] == 0 for r in rows):
            failures.append((metric, "zero on EVERY document — extractor is broken, not the data"))
            print(f"  SYSTEMATIC FAILURE: {metric} is 0 on all {len(rows)} documents")

    # ---- controls: the counter must say yes AND no
    by_num = {r["number"]: r for r in rows}
    for num in sorted(FIGREF_POSITIVE & by_num.keys()):
        ok = by_num[num]["figrefs"] > 0
        print(f"  {'PASS' if ok else 'FAIL'}  positive control: {num} reports "
              f"{by_num[num]['figrefs']} figure references (expected > 0)")
        if not ok:
            failures.append((num, "positive control: expected figure references, found none"))
    for num in sorted(FIGREF_NEGATIVE & by_num.keys()):
        ok = by_num[num]["figrefs"] == 0
        print(f"  {'PASS' if ok else 'FAIL'}  negative control: {num} reports "
              f"{by_num[num]['figrefs']} figure references (expected 0)")
        if not ok:
            failures.append((num, "negative control: invented figure references"))

    # ---- instrument calibration: a deliberately broken page MUST fail
    sample = next((f for f in files if f.stem == "US6285999B1"), files[0])
    broken = sample.read_text(encoding="utf-8", errors="replace").replace(
        'class="description-paragraph"', 'class="nope"').replace(
        'class="description-line"', 'class="nope"')
    tmp = ROOT / "var" / "_calibration.html"
    tmp.write_text(broken, encoding="utf-8")
    try:
        cal = analyse(tmp)
        cal_ok, cal_why = verdict(cal) if cal else (True, "")
        if cal_ok:
            failures.append(("CALIBRATION", "a page with its paragraph markup removed still PASSED "
                                             "— this checker cannot fail and proves nothing"))
            print("  FAIL  calibration: broken page passed; the checker is not measuring anything")
        else:
            print(f"  PASS  calibration: broken page correctly fails ({cal_why})")
    finally:
        tmp.unlink(missing_ok=True)

    print("\n" + "=" * 92)
    if failures:
        print(f"{len(failures)} problem(s):")
        for who, why in failures:
            print(f"  - {who}: {why}")
        return 1
    print(f"all {len(rows)} documents pass: structure added, text intact, controls both ways")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
