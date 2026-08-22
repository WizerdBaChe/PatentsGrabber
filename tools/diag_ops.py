"""Find out WHY OPS answers 404 — by asking OPS, not by guessing.

verify_ops.py reported only `HTTP 404` and threw away the fault body, which is
where OPS says what it actually objected to (SERVER.EntityNotFound,
CLIENT.InvalidCountryCode, ...). This tool prints that body for every attempt.

It is built around one decisive control: EP1000000 is the EPO's OWN documented
example number. If EP1000000/biblio succeeds, the URL construction, the token and
the account's service entitlement are all proven good, and the failure is purely
about how US numbers must be written. If EP1000000 also fails, the problem is
upstream of any number format and the US matrix is irrelevant.

    python tools/diag_ops.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from patentsgrabber.config import MissingCredentials, load_ops   # noqa: E402
from patentsgrabber.sources.epo_ops import OpsClient, OpsError   # noqa: E402

JSON = "application/json"
XML = "application/xml"


def attempt(client: OpsClient, label: str, path: str, accept: str) -> bool:
    """One request; print status and, on failure, OPS's own fault text."""
    try:
        client.get(path, accept=accept)
        print(f"  OK    {label}\n        {accept}  {path}")
        return True
    except OpsError as exc:
        body = " ".join((exc.body or "").split())[:220]
        print(f"  {exc.status or 'ERR':<5} {label}\n        {accept}  {path}\n        fault: {body or '(empty body)'}")
        return False


def main() -> int:
    try:
        cfg = load_ops()
    except MissingCredentials as exc:
        print(f"\n{exc}\n")
        return 2
    print(f"credential: {cfg.describe()}")
    client = OpsClient(cfg)

    print("\n=== token ===")
    try:
        print(f"  OK    token acquired, length {len(client.token())}")
    except OpsError as exc:
        print(f"  FAIL  {exc}\n        {exc.body}")
        return 1

    print("\n=== CONTROL: the EPO's own documented example (EP1000000) ===")
    ctl_json = attempt(client, "EP1000000 biblio", "published-data/publication/epodoc/EP1000000/biblio", JSON)
    ctl_xml = False
    if not ctl_json:
        ctl_xml = attempt(client, "EP1000000 biblio", "published-data/publication/epodoc/EP1000000/biblio", XML)

    if not (ctl_json or ctl_xml):
        print("\n  VERDICT: the control failed too. The problem is NOT the US number format —\n"
              "  it is the URL, the token's entitlement, or the account's activated services.\n"
              "  Check on the EPO Developer Portal that the app has OPS added as a subscribed API.\n")
        return 1

    accept = JSON if ctl_json else XML
    if not ctl_json and ctl_xml:
        print("\n  NOTE: JSON was refused but XML worked — the adapter must stop asking for JSON.\n")

    print("\n=== US publication number matrix (2025 pre-grant, the Stage 0 gap doc) ===")
    us_pub = [
        ("epodoc 6-digit serial + kind",  "published-data/publication/epodoc/US2025383260A1/biblio"),
        ("epodoc 6-digit serial, no kind", "published-data/publication/epodoc/US2025383260/biblio"),
        ("epodoc 7-digit serial + kind",  "published-data/publication/epodoc/US20250383260A1/biblio"),
        ("epodoc 7-digit serial, no kind", "published-data/publication/epodoc/US20250383260/biblio"),
        ("docdb 6-digit serial",          "published-data/publication/docdb/US.2025383260.A1/biblio"),
        ("docdb 7-digit serial",          "published-data/publication/docdb/US.20250383260.A1/biblio"),
    ]
    pub_ok = [lbl for lbl, p in us_pub if attempt(client, lbl, p, accept)]

    print("\n=== US granted number matrix (US6285999B1 — long-established document) ===")
    us_grant = [
        ("epodoc with kind",    "published-data/publication/epodoc/US6285999B1/biblio"),
        ("epodoc without kind", "published-data/publication/epodoc/US6285999/biblio"),
        ("docdb",               "published-data/publication/docdb/US.6285999.B1/biblio"),
    ]
    grant_ok = [lbl for lbl, p in us_grant if attempt(client, lbl, p, accept)]

    print("\n=== number-service: let OPS translate the number for us ===")
    for lbl, p in [
        ("epodoc->docdb (grant)", "number-service/publication/epodoc/US6285999B1/docdb"),
        ("epodoc->docdb (2025 pub)", "number-service/publication/epodoc/US2025383260A1/docdb"),
        ("original->docdb (2025 pub)", "number-service/publication/original/US.20250383260.A1/docdb"),
    ]:
        attempt(client, lbl, p, accept)

    print("\n" + "=" * 66)
    print(f"Accept type that works : {accept}")
    print(f"US publication formats : {pub_ok or 'NONE — the 2025 document may not be in OPS yet'}")
    print(f"US granted formats     : {grant_ok or 'NONE'}")
    print(f"usage                  : {client.usage.summary()}")
    if grant_ok and not pub_ok:
        print("\n  READING: US numbers work, but the 2025 publication is absent from OPS.\n"
              "  That is data coverage, not a bug — OPS lags on the newest publications.\n"
              "  Stage 1 must therefore keep Google Patents as the source for very recent cases.")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
