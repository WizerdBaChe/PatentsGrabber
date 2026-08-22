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
        ("epodoc", parsed.espacenet),        # US2025383260A1
        ("epodoc", parsed.canonical),        # US20250383260A1
        ("docdb", f"US.{parsed.year}{parsed.serial}.A1"),
    ]
    working: tuple[str, str] | None = None
    for fmt, num in candidates:
        try:
            client.biblio(num, fmt=fmt)
            check(f"biblio via {fmt}: {num}", True)
            working = working or (fmt, num)
        except OpsError as exc:
            check(f"biblio via {fmt}: {num}", False, f"HTTP {exc.status}")
    if not working:
        print("\n  沒有任何號碼格式成功——先確認這件公開案是否已在 OPS 收錄。\n")
        return 1
    fmt, num = working
    print(f"  --> Stage 1 will use format '{fmt}' with number '{num}'")

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
                check(f"{label} ({doc_label})", False, f"HTTP {exc.status}")

    print("\n=== 5. family + legal status (Stage 0 had only Google's view) ===")
    for label, fn in (("family", client.family), ("legal status", client.legal)):
        try:
            data = fn(num, fmt=fmt)
            n = len(walk(data, "publication-reference")) or len(walk(data, "@change-date"))
            check(label, True, f"{n} entries")
        except OpsError as exc:
            check(label, False, f"HTTP {exc.status}")

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

    print("\n=== 7. quota accounting (BR-6: 4 GB/week fair use) ===")
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
