"""Is the archive still what the source publishes today?

`tools/check_reading.py` compares the parser against the pages saved in `var/raw/`.
That makes it a regression test, not a detector: if Google Patents changed its
description markup tomorrow, that checker would still report every document green,
because it is reading yesterday's files.

This probe closes that hole from the other side. It re-fetches a few documents and
compares the STRUCTURE the parser recovers now against the structure recovered from
the archived copy. Text is not compared word for word — a published document does
not change, but boilerplate around it does. Counts are the signal: blocks, claims,
limitations, figure references. A markup change shows up as a collapse.

    python tools/check_freshness.py            # the standing sample
    python tools/check_freshness.py US7479949B2

BR-7: these are single-document pages, the only Google Patents surface this project
touches. No OPS quota is involved.

Exit codes: 0 pass · 1 drift found · 2 could not determine (no network / no archive).
A probe that cannot reach the source says so; it never reports a pass it did not earn.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patentsgrabber.sources import google_patents as gp   # noqa: E402

RAW = ROOT / "var" / "raw"

# One document per markup vintage that has actually bitten this project:
#   US6285999B1      2001 grant   — claim limitations as SIBLINGS of the preamble
#   US20250383260A1  2025 pub     — ul.description / para-num vintage, many figrefs
#   EP1000000A1      EP           — <description><p num> and <claim> ELEMENTS
SAMPLE = ["US6285999B1", "US20250383260A1", "EP1000000A1"]

# A published specification does not change. Anything past this is either a markup
# change or a different page being served, and both are worth a look.
TOLERANCE = 0.10
METRICS = ("blocks", "paras", "claims", "claim_parts", "figrefs", "chars")


def measure(html: str, number: str) -> dict | None:
    try:
        doc = gp.parse(html, number, "file://archive", 200, None)
    except gp.FetchError:
        return None
    blocks = doc.fields["description_blocks"].value or []
    claims = doc.fields["claim_list"].value or []
    return {
        "blocks": len(blocks),
        "paras": sum(1 for b in blocks if b["type"] == "para"),
        "claims": len(claims),
        "claim_parts": sum(len(c.get("parts") or []) for c in claims),
        "figrefs": sum(len(b.get("figs") or []) for b in blocks),
        "chars": sum(len(b.get("text") or "") for b in blocks),
    }


def compare(archived: dict, live: dict) -> list[str]:
    """Differences worth reporting, in the parser's own terms."""
    problems = []
    for m in METRICS:
        was, now = archived[m], live[m]
        if was > 0 and now == 0:
            problems.append(f"{m}: {was} -> 0 (the extractor found nothing where it used to)")
        elif was and abs(now - was) / was > TOLERANCE:
            problems.append(f"{m}: {was:,} -> {now:,} ({(now - was) / was:+.0%})")
    return problems


def mangle(html: str) -> str:
    """The same wound tools/check_reading.py uses for its calibration page."""
    return (html.replace('class="description-paragraph"', 'class="nope"')
                .replace('class="description-line"', 'class="nope"'))


def main(argv: list[str]) -> int:
    wanted = argv[1:] or SAMPLE
    print(f"comparing {len(wanted)} archived page(s) against what the source serves now")
    print(f"tolerance {TOLERANCE:.0%} on {', '.join(METRICS)}\n")
    print(f"{'document':<22}{'metric':<14}{'archived':>10}{'live':>10}  verdict")
    print("-" * 72)

    failures, checked, live_pages = [], 0, {}
    for number in wanted:
        path = RAW / f"{number}.html"
        if not path.exists():
            print(f"{number:<22}(no archived copy in var/raw — nothing to compare against)")
            continue
        archived = measure(path.read_text(encoding="utf-8", errors="replace"), number)
        if archived is None:
            print(f"{number:<22}(archived copy carries no patent record — skipped)")
            continue

        try:
            _, html, _ = gp.fetch_html(number, raw_dir=None)   # never overwrite the baseline
        except Exception as exc:                                # network, block, 404
            print(f"{number:<22}COULD NOT FETCH: {exc}")
            failures.append((number, "INDETERMINATE"))
            continue
        live_pages[number] = html
        live = measure(html, number)
        if live is None:
            print(f"{number:<22}live page carries NO patent record — the source changed or blocked us")
            failures.append((number, "live page has no record"))
            continue

        checked += 1
        problems = compare(archived, live)
        for m in METRICS:
            flag = "drift" if any(p.startswith(m + ":") for p in problems) else "ok"
            print(f"{number if m == METRICS[0] else '':<22}{m:<14}"
                  f"{archived[m]:>10,}{live[m]:>10,}  {flag}")
        if problems:
            failures.append((number, "; ".join(problems)))
        print()

    if not checked:
        print("nothing could be compared — no network, or no archived pages.")
        print("INDETERMINATE: this probe did not run. That is not a pass.")
        return 2

    # ---- instrument calibration: the comparator must be able to report a change
    number, html = next(iter(live_pages.items()))
    archived = measure((RAW / f"{number}.html").read_text(encoding="utf-8", errors="replace"), number)
    broken = measure(mangle(html), number)
    if broken is None or not compare(archived, broken):
        print(f"  FAIL  calibration: a page with its paragraph markup removed compared "
              f"CLEAN against {number} — this probe cannot detect a markup change")
        failures.append(("CALIBRATION", "the comparator cannot fail"))
    else:
        print(f"  PASS  calibration: markup removed from the live {number} is caught "
              f"({compare(archived, broken)[0]})")

    print("\n" + "=" * 72)
    if any(why == "INDETERMINATE" for _, why in failures):
        print("some documents could not be fetched; the ones that were, are reported above")
    real = [(n, w) for n, w in failures if w != "INDETERMINATE"]
    if real:
        for n, w in real:
            print(f"  {n}: {w}")
        print(f"\n{len(real)} document(s) no longer match the archive — re-sweep the markup "
              f"(docs/05 §7) and refresh var/raw before trusting check_reading.py")
        return 1
    print(f"all {checked} document(s) still parse the way the archive does; "
          f"check_reading.py is measuring a corpus that is still current")
    return 0 if not failures else 2


if __name__ == "__main__":
    start = time.time()
    code = main(sys.argv)
    print(f"({time.time() - start:.1f}s)")
    raise SystemExit(code)
