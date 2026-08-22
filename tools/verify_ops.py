"""Prove the EPO OPS credential works, and measure exactly what it unlocks.

Deliberately not a "hello world": every check targets something Stage 0 could NOT
do, so a green run is direct evidence that Stage 1 is worth building and a red one
names which capability is unavailable rather than saying "it failed".

Number formats are no longer guessed — OpsClient.resolve() applies the rules
established against the live API on 2026-08-23 and pinned in
tests/test_ops_number_formats.py.

    python tools/verify_ops.py

Never prints the credential. Run it after putting your own values in .env.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patentsgrabber import numbers                                      # noqa: E402
from patentsgrabber.config import MissingCredentials, load_ops          # noqa: E402
from patentsgrabber.sources.epo_ops import (                            # noqa: E402
    OpsAuthError, OpsClient, OpsError, _walk,
)

GAP_DOC = "US20250383260A1"   # Stage 0 could serve neither its PDF nor its drawings
US_CONTROL = "US6285999B1"    # long-established US grant
EP_CONTROL = "EP1000000"      # the EPO's own documented example

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    return ok


def fault(exc: OpsError) -> str:
    """OPS names its objection in the body; a bare status code is undiagnosable."""
    body = " ".join((exc.body or "").split())
    code = ""
    if "<code>" in body:
        code = body.split("<code>")[1].split("</code>")[0]
    return f"HTTP {exc.status} {code}".strip()


def main() -> int:
    print("\n=== 0. credential loads from .env ===")
    try:
        cfg = load_ops()
    except MissingCredentials as exc:
        print(f"\n{exc}\n")
        return 2
    check("credential present", True, cfg.describe())

    client = OpsClient(cfg)

    print("\n=== 1. OAuth2 client-credentials handshake ===")
    try:
        check("access token obtained", bool(client.token()), "expires in ~20 min")
    except OpsAuthError as exc:
        check("access token obtained", False, f"{exc}\n          {exc.body}")
        return 1

    print("\n=== 2. number resolution (rules are tested, not guessed) ===")
    resolved: dict[str, tuple[str, str]] = {}
    for label, raw in (("gap doc", GAP_DOC), ("US control", US_CONTROL)):
        try:
            fmt, num = client.resolve(numbers.normalize(raw))
            resolved[label] = (fmt, num)
            check(f"{label} {raw} -> {fmt}/{num}", True)
        except OpsError as exc:
            check(f"{label} {raw}", False, fault(exc))
    if "gap doc" not in resolved:
        print("\n  無法解析 gap doc — 後續改用 US control 繼續。\n")
    fmt, num = resolved.get("gap doc") or resolved.get("US control") or ("epodoc", EP_CONTROL)

    print("\n=== 3. THE STAGE 0 GAPS — drawings and the original document ===")
    try:
        inq = client.images_inquiry(num, fmt=fmt)
        links = _walk(inq, "@link")
        pages = _walk(inq, "@number-of-pages")
        check("images inquiry", bool(links), f"{len(links)} instance(s), pages={pages}")
        full = next((l for l in links if "fullimage" in str(l)), None)
        if full:
            pdf = client.image_page(full, page=1, kind="pdf")
            ok = pdf[:5] == b"%PDF-"
            check("ORIGINAL DOCUMENT as PDF (Google Patents had none)", ok,
                  f"{len(pdf):,} bytes from {full}")
            if ok:
                out = ROOT / "var" / "ops" / f"{GAP_DOC}-page1.pdf"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(pdf)
                print(f"          saved -> {out}")
            tiff = client.image_page(full, page=1, kind="tiff")
            check("DRAWING page as TIFF (Google Patents answered 403)",
                  len(tiff) > 1000, f"{len(tiff):,} bytes")
    except OpsError as exc:
        check("images / PDF", False, fault(exc))

    print("\n=== 4. full text — is CLIENT.InvalidCountryCode US-specific? ===")
    # Hypothesis from the previous run: OPS full text does not cover US. Testing
    # the same call against EP is what separates "not covered" from "our bug".
    for label, (f2, n2) in (("US", (fmt, num)), ("EP control", ("epodoc", EP_CONTROL))):
        for part, fn in (("claims", client.claims), ("description", client.description)):
            try:
                data = fn(n2, fmt=f2)
                text = " ".join(str(t) for t in _walk(data, "p"))
                check(f"{part} [{label}]", len(text) > 200, f"{len(text):,} chars")
            except OpsError as exc:
                check(f"{part} [{label}]", False, fault(exc))

    print("\n=== 5. family + legal status ===")
    for label, fn in (("family", client.family), ("legal status", client.legal)):
        try:
            data = fn(num, fmt=fmt)
            check(label, True, f"{len(_walk(data, 'publication-reference'))} references")
        except OpsError as exc:
            check(label, False, fault(exc))

    print("\n=== 6. APPLICANT SEARCH — impossible in Stage 0 (robots-excluded) ===")
    try:
        res = client.search('pa="Taiwan Semiconductor"', end=10)
        total = _walk(res, "@total-result-count")
        docs = _walk(res, "doc-number")
        names = [str(n) for n in _walk(res, "name")]
        check("CQL applicant search", bool(docs),
              f"total={total[0] if total else '?'} | first numbers={docs[:5]}")
        if names:
            uniq = sorted(set(names))
            print(f"          applicant-name variants (BR-8, {len(uniq)} distinct): {uniq[:4]}")
    except OpsError as exc:
        check("CQL applicant search", False, fault(exc))

    print("\n=== 7. quota — using OPS's own counters, not our estimate ===")
    print(f"  {client.usage.summary()}")

    client.close()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 64}\n{passed}/{len(results)} checks passed")
    if failed := [l for l, ok, _ in results if not ok]:
        print("failed: " + "; ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
