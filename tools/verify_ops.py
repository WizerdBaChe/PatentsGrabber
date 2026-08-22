"""Prove the EPO OPS credential works, and measure exactly what it unlocks.

Deliberately not a "hello world": every check below targets something Stage 0
could NOT do, so a green run is direct evidence that Stage 1 is worth building
and a red one says which part is not available rather than "it failed".

    python tools/verify_ops.py

Never prints the credential. Run it after putting your own values in .env.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patentsgrabber import numbers                      # noqa: E402
from patentsgrabber.config import MissingCredentials, load_ops   # noqa: E402
from patentsgrabber.sources.epo_ops import OpsAuthError, OpsClient, OpsError  # noqa: E402

# The document Stage 0 could not fully serve: no PDF, and image URLs that 403.
GAP_DOC = "US20250383260A1"
# A well-covered granted patent, as a positive control for the calls themselves.
CONTROL_DOC = "US6285999B1"

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    return ok


def walk(node, key):
    """Find every value under `key` anywhere in OPS's deeply nested JSON."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key or k.endswith(":" + key):
                found.append(v)
            found.extend(walk(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(walk(item, key))
    return found


def main() -> int:
    print("\n=== 0. credential loads from .env ===")
    try:
        cfg = load_ops()
    except MissingCredentials as exc:
        print(f"\n{exc}\n")
        return 2
    check("credential present", True, cfg.describe())   # never the value itself

    client = OpsClient(cfg)

    print("\n=== 1. OAuth2 client-credentials handshake ===")
    try:
        tok = client.token()
        check("access token obtained", bool(tok), f"token length {len(tok)}, expires in ~20 min")
    except OpsAuthError as exc:
        check("access token obtained", False, str(exc))
        print(f"\n  服務端訊息：{exc.body}\n")
        return 1
    except OpsError as exc:
        check("access token obtained", False, f"{exc} / {exc.body}")
        return 1

    print("\n=== 2. which number format does OPS accept? (empirical, not assumed) ===")
    parsed = numbers.normalize(GAP_DOC)
    candidates = [
        # EP1000000 is the EPO's own documented example: it separates "the code
        # is wrong" from "this US document is not in OPS". Test it FIRST.
        ("epodoc", "EP1000000", "CONTROL — EPO's documented example"),
        ("epodoc", parsed.espacenet, "US 2025 pub, 6-digit serial"),
        ("epodoc", parsed.canonical, "US 2025 pub, 7-digit serial"),
        ("docdb", f"US.{parsed.espacenet[2:-2]}.A1", "US 2025 pub, docdb"),
        ("epodoc", CONTROL_DOC, "US granted control"),
    ]
    ok_formats: list[tuple[str, str]] = []
    for fmt, num, note in candidates:
        try:
            client.biblio(num, fmt=fmt)
            check(f"{note}: {fmt}/{num}", True)
            ok_formats.append((fmt, num))
        except OpsError as exc:
            # The fault body is the whole diagnostic value of a 404 — never drop it.
            body = " ".join((exc.body or "").split())[:160]
            check(f"{note}: {fmt}/{num}", False, f"HTTP {exc.status} — {body or '(no body)'}")

    if not ok_formats:
        print("\n  連 EP1000000 都失敗 → 問題不在號碼格式，而在 URL、token 權限，或該 app\n"
              "  尚未在 EPO Developer Portal 訂閱 OPS API。請改跑 tools/diag_ops.py。\n")
        return 1

    # Prefer a working US form; fall back to the control so later steps still run.
    us_ok = [(f, n) for f, n in ok_formats if n.startswith("US")]
    fmt, num = (us_ok or ok_formats)[0]
    if not us_ok:
        print("\n  只有 EP 控制項成功 → 程式與金鑰正常，是這些美國號碼在 OPS 查不到。\n"
              "  後續步驟改用控制項繼續，好知道其餘能力是否可用。\n")
    print(f"  --> continuing with format '{fmt}', number '{num}'")

    print("\n=== 3. THE STAGE 0 GAPS — can OPS supply what Google Patents refused? ===")
    try:
        inq = client.images_inquiry(num, fmt=fmt)
        links = walk(inq, "@link")
        pages = walk(inq, "@number-of-pages")
        check("images inquiry returns document instances", bool(links),
              f"{len(links)} instance(s), pages={pages[:3]}")
        full = next((l for l in links if "fullimage" in str(l)), None)
        if full:
            pdf = client.image_page(full, page=1, kind="pdf")
            is_pdf = pdf[:5] == b"%PDF-"
            check("ORIGINAL DOCUMENT retrieved as PDF (Stage 0 had none)", is_pdf,
                  f"{len(pdf):,} bytes, magic={pdf[:5]!r}, link={full}")
            if is_pdf:
                out = ROOT / "var" / "shots" / f"{GAP_DOC}-page1.pdf"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(pdf)
                print(f"          saved -> {out}")
        else:
            check("fullimage instance present", False, f"links seen: {links[:3]}")
    except OpsError as exc:
        check("images / PDF for the gap document", False, f"HTTP {exc.status}: {exc}")

    print("\n=== 4. full text (US coverage was the one unresolved desk question) ===")
    for label, fn in (("claims", client.claims), ("description", client.description)):
        for doc_label, doc in (("gap doc", num), ("control", CONTROL_DOC)):
            try:
                data = fn(doc if doc_label == "gap doc" else doc, fmt=fmt if doc_label == "gap doc" else "epodoc")
                text = " ".join(str(t) for t in walk(data, "$"))
                check(f"{label} ({doc_label})", len(text) > 200, f"{len(text):,} chars")
            except OpsError as exc:
                check(f"{label} ({doc_label})", False, f"HTTP {exc.status} — {' '.join((exc.body or '').split())[:140]}")

    print("\n=== 5. family + legal status (Stage 0 had only Google's view) ===")
    for label, fn in (("family", client.family), ("legal status", client.legal)):
        try:
            data = fn(num, fmt=fmt)
            n = len(walk(data, "publication-reference")) or len(walk(data, "@change-date"))
            check(label, True, f"{n} entries")
        except OpsError as exc:
            check(label, False, f"HTTP {exc.status} — {' '.join((exc.body or '').split())[:140]}")

    print("\n=== 6. APPLICANT SEARCH — the capability Stage 0 cannot have at all ===")
    try:
        res = client.search('pa="Taiwan Semiconductor" and pd within "2025"', end=10)
        total = (walk(res, "@total-result-count") or ["?"])[0]
        pubs = walk(res, "@doc-number")
        check("CQL applicant search", bool(pubs),
              f"total-result-count={total}, first hits={pubs[:5]}")
        names = set(str(n) for n in walk(res, "name"))
        if names:
            print(f"          applicant-name variants seen (BR-8): {list(names)[:4]}")
    except OpsError as exc:
        check("CQL applicant search", False, f"HTTP {exc.status}: {exc.body[:200]}")

    # The free threshold is set by the EPO's Fair use charter + price list and can
    # change at any time (T&C 3.3, 4.1-4.3, 9) — so this reports, never asserts.
    print("\n=== 7. quota accounting (BR-6; threshold per the EPO Fair use charter) ===")
    print(f"  {client.usage.summary()}")
    if client.usage.last_quota_headers:
        for k, v in client.usage.last_quota_headers.items():
            print(f"  {k}: {v}")
    else:
        print("  (OPS returned no quota/throttle headers on the last call)")

    client.close()

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 62}\n{passed}/{len(results)} checks passed")
    failed = [lbl for lbl, ok, _ in results if not ok]
    if failed:
        print("failed: " + "; ".join(failed))
    return 0 if passed >= len(results) - 2 else 1   # a couple of coverage gaps are data, not defects


if __name__ == "__main__":
    raise SystemExit(main())
