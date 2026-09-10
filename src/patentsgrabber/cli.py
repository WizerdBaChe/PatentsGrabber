"""The agent-facing surface: the same library, without a browser.

Everything this file exposes already existed — `Service` has been a plain
function layer since Stage 1, and the drawing sheets have always landed on disk
as PNG. What did not exist was a way for anything other than a browser tab to
ask for them. A caller that cannot run JavaScript, cannot hold a session and
cannot look at pixels needs three things this program was not giving it: one
JSON object on stdout, a file PATH rather than a byte stream, and a truthful
statement of what the call cost.

Three rules hold this contract together, and each one is a property the gate
(`tools/check_cli.py`) asserts rather than advice for whoever edits next:

  1. **`--json` puts exactly one JSON object on stdout and nothing else.**
     Every human line, every warning, every note goes to stderr. A caller that
     parses stdout can therefore never parse a progress message by accident —
     which is the failure that silently succeeds on the wrong thing.
  2. **The default is lean**, for `lookup` AND for `search`. A card comes back
     as identity, dates, parties, counts and paths; the abstract, the claims and
     the description arrive only under `--full`. The reason is not size: an
     agent's context leaves this machine, and a conservative default can be
     widened later while the reverse is not true (user ruling, 2026-09-11).
     Enforced twice — by name (`TEXT_KEYS`) and by length (`LEAN_TEXT_LIMIT`),
     because a list of names only covers the fields somebody remembered.
  3. **Spending is opt-in and always reported.** Resolving a document's images
     costs OPS calls but no page bytes — that split is BR-6's, not this file's.
     Page bytes are fetched only under `--pages`, capped at MAX_PAGES, and every
     OPS-touching command reports the quota counters OPS itself returned —
     including when it FAILS, which is the response most likely to be retried.
     `--pages 11` is sheet 11, never "the first 11": when one reading costs a
     request and the other costs eleven, the ambiguous argument means the cheap
     one (user ruling, 2026-09-11, after an agent lost a request to the other).

The envelope is the same shape for every command, including failures, because a
caller that has to branch on shape before it can read an error will not read the
error. `ok`, `command`, `cost`, and then either `data` or `error`.

    python pgb.py lookup US20250383260A1 --json
    python pgb.py figures US20250383260A1 --pages 1-3 --json
    python pgb.py doctor --json

Exit codes are the error's `code`, so a shell can branch without parsing:
0 ok · 1 not_found · 2 not_configured · 3 refused · 4 upstream_fault ·
5 internal_error (a bug here, not a fact about the document) · 6 busy (another
process is holding the library — retrying later is the right move).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import __version__, config, paths
from .service import SEARCH_SCOPES, ResolveError, Service

# The version of THIS output contract, not of the program. A caller pins the
# shape it parses; bump this when a key changes meaning or disappears.
SCHEMA = 1

# Sheets, not figures: OPS serves one page per request and a figure may span
# pages or share one. The cap is what stops a loop from spending a week's quota
# in a minute; the GUI has no such cap because a human clicking is its own cap.
MAX_PAGES = 20

# Not a quota rule — a sanity rule, and it is checked on the NUMBER before any
# range is built. No patent has five thousand sheets, so a page number above
# this is a typo or a hallucination, and refusing it costs nothing.
MAX_PAGE_NUMBER = 5000

# Removed from a card unless --full. `claims` is in here because each entry
# carries the claim's text; the numbering survives lean mode as counts.
TEXT_KEYS = ("abstract", "description", "description_blocks", "claims_text", "claims")

# The names above are a DENYLIST, and a denylist is only as current as whoever
# last edited it: add a text-bearing field to the card and lean mode would send
# it out in full, silently, while every check still passed. So there is a second
# rule that needs no list — any string longer than this, anywhere in the payload,
# is replaced by its length. Titles, dates, numbers and URLs are far below it.
LEAN_TEXT_LIMIT = 400

EXIT = {"ok": 0, "not_found": 1, "not_configured": 2, "refused": 3,
        "upstream_fault": 4, "internal_error": 5, "busy": 6}


class Refused(Exception):
    """The CLI itself declined — a cap, a malformed argument, a policy line."""


# ------------------------------------------------------------------ plumbing

def note(text: str) -> None:
    """A line for a human. Never stdout: stdout belongs to the parser."""
    print(text, file=sys.stderr, flush=True)


@contextmanager
def waiting(what: str, every: float = 3.0):
    """Say what is being waited for, and for how long, while it is happening.

    "No silent waiting" is a user ruling this program already keeps in the
    browser (2026-08-26), and it does not stop applying because the caller is a
    program: a command line that has printed nothing for fifteen seconds is
    indistinguishable from one that has hung, and the caller's only move is to
    kill it. The ticks go to stderr, so they cannot reach a JSON parser.
    """
    stop = threading.Event()

    def tick() -> None:
        started = time.monotonic()
        while not stop.wait(every):
            note(f"  …仍在{what}（已 {time.monotonic() - started:.0f} 秒）")

    thread = threading.Thread(target=tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()


def emit(payload: dict, *, as_json: bool, human) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        human(payload)


def envelope(command: str, data: dict, *, cost: dict, quota: str | None = None) -> dict:
    return {"ok": True, "schema": SCHEMA, "command": command,
            "cost": cost, "quota": quota, "data": data}


NO_COST = {"network": "none", "ops_requests": 0}


def failure(command: str, code: str, message: str, *, cost: dict | None = None,
            quota: str | None = None, **extra) -> dict:
    """A failure carries the same `cost` and `quota` a success would.

    The contract says every response is the same envelope, and the first
    version broke it exactly where it mattered: a `figures` call whose every
    page 404'd had spent real OPS requests and returned no `cost` at all, so the
    caller had nothing to report and no way to know it had been charged. A
    failure is the response most likely to be retried, which makes it the one
    that can least afford to hide what the last attempt cost.
    """
    return {"ok": False, "schema": SCHEMA, "command": command,
            "cost": cost or dict(NO_COST), "quota": quota,
            "error": {"code": code, "message": message, **extra}}


def spent(client, network: str, **extra) -> dict:
    """What this process has actually spent, named the way `cost` promises.

    `network` is derived from what happened, never from which flag was passed:
    a `--pages` run served entirely from disk is `store`, and reporting it as
    `epo_ops` beside `ops_requests: 0` gives a caller two answers to one
    question. Found in review, 2026-09-11.
    """
    return {"network": network, "ops_requests": client.usage.requests if client else 0, **extra}


def _quota(client) -> str | None:
    """OPS's own counters, but only once this process has actually seen them."""
    return client.usage.summary() if client and client.usage.requests else None


def read_absent(service: Service, link: str) -> dict[str, str]:
    """Sheets this document's own image store has already answered 404 for.

    Kept beside the PNGs it belongs to rather than in the library, because it
    is a fact about one image instance and it should disappear with the cache
    if the cache is ever cleared. Any problem reading it is not worth failing a
    command over — the worst case is that a 404 gets paid for twice.
    """
    path = service._cache_path(link, "absent-pages.json")
    if not path or not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in (loaded.get("pages") or {}).items()}
    except (OSError, ValueError):
        return {}


def write_absent(service: Service, link: str, pages: dict[str, str]) -> None:
    path = service._cache_path(link, "absent-pages.json")
    if not path:
        return
    try:
        path.write_text(json.dumps(
            {"link": link, "pages": pages,
             "note": "EPO 的影像清單宣稱有這些頁，實際取用時回 404。--refresh 可忘掉重問。"},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def busy_or_internal(command: str, exc: sqlite3.OperationalError) -> dict:
    """A locked library is a wait worth retrying; anything else here is a bug.

    Told apart because the two need opposite responses from a caller: `busy`
    means come back in a moment, `internal_error` means stop and read the trace.
    Collapsing them would make every transient lock look like a defect.
    """
    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
        return failure(command, "busy",
                       "專利庫正被另一個行程佔用（等了 15 秒仍未釋放）。"
                       "多半是程式視窗正在寫入——稍後重試即可。",
                       detail=str(exc))
    return failure(command, "internal_error", f"OperationalError: {exc}")


def as_page(text: str, whole: str) -> int:
    """One page number, refused before it becomes a `range()`.

    The bound is checked on the NUMBER, not on the list it would produce. That
    ordering is the whole point: `--pages 1-99999999999` parsed first and capped
    afterwards materialises a list with a hundred billion entries, and the
    refusal that was supposed to stop it never runs. Found in review, 2026-09-11.
    """
    text = text.strip()
    if not text.isdigit():
        raise Refused(f"看不懂的頁碼：{whole!r}")
    page = int(text)
    if page < 1:
        raise Refused(f"頁碼要從 1 起算：{whole!r}")
    if page > MAX_PAGE_NUMBER:
        raise Refused(f"頁碼 {page} 不像真的（上限 {MAX_PAGE_NUMBER}）：{whole!r}")
    return page


def parse_pages(spec: str) -> list[int]:
    """`11` -> [11] · `2,5` -> [2,5] · `1-4` -> [1,2,3,4] · combinations.

    A bare number is THAT page, not the first N (user ruling, 2026-09-11). The
    first version read it as "the first N sheets" and the first agent to use the
    tool asked for `--pages 11` meaning sheet 11, got eleven sheets' worth of
    requests, and could not see that it had. When one reading costs a request
    and the other costs N, the cheap one is what an ambiguous argument must mean.
    """
    spec = (spec or "").strip()
    if not spec:
        return []
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            lo_i, hi_i = as_page(lo, part), as_page(hi, part)
            if hi_i < lo_i:
                raise Refused(f"頁碼區間不合法：{part!r}")
            if hi_i - lo_i + 1 > MAX_PAGES:
                raise Refused(f"{part!r} 是 {hi_i - lo_i + 1} 頁，一次最多 {MAX_PAGES} 頁。")
            pages.extend(range(lo_i, hi_i + 1))
        else:
            pages.append(as_page(part, part))
        if len(pages) > MAX_PAGES:
            raise Refused(f"一次最多取 {MAX_PAGES} 頁。")
    return sorted(set(pages))


def lean(card: dict) -> dict:
    """The card with its text taken out — and the removal declared, not hidden.

    BR-3 says a field that could not be supplied states why. The same obligation
    applies to a field this program deliberately withheld: silence here would be
    indistinguishable from "this patent has no claims", which is the exact
    misreading BR-3 exists to prevent.
    """
    out = {k: v for k, v in card.items() if k not in TEXT_KEYS and not k.startswith("_")}
    claims = card.get("claims") or []
    blocks = card.get("description_blocks") or []
    out["counts"] = {
        "claims": len(claims),
        "independent_claims": card.get("independent_claims") or [],
        "description_blocks": len(blocks),
        "images": len(card.get("images") or []),
        "abstract_chars": len(card.get("abstract") or ""),
        "description_chars": len(card.get("description") or ""),
    }
    out, caught = strip_long_text(out)
    out["text_withheld"] = {
        "keys": list(TEXT_KEYS),
        "also_withheld": caught,
        "reason": "預設不輸出全文（使用者裁定：保守預設，可再放寬）。",
        "how": "加上 --full 取得摘要、請求項與說明書全文。",
    }
    return out


def strip_long_text(node, path: str = "", limit: int = LEAN_TEXT_LIMIT):
    """Replace every long string anywhere below `node` with its length.

    The backstop behind `TEXT_KEYS`. A denylist protects the fields somebody
    remembered; this protects the ones they will add later, including fields
    nested inside a list of dicts where a reviewer would never look. Returns the
    rewritten value and the dotted paths it caught, so the removal can be
    declared rather than being a silent shortening.
    """
    caught: list[str] = []
    if isinstance(node, str):
        if len(node) > limit:
            caught.append(path)
            return f"（{len(node)} 字元，預設不輸出；--full）", caught
        return node, caught
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            out[key], found = strip_long_text(value, f"{path}.{key}" if path else str(key), limit)
            caught.extend(found)
        return out, caught
    if isinstance(node, list):
        out_list = []
        for index, value in enumerate(node):
            item, found = strip_long_text(value, f"{path}[{index}]", limit)
            out_list.append(item)
            caught.extend(found)
        return out_list, caught
    return node, caught


def search_fault(result: dict) -> str:
    """Which kind of failure a non-available search payload describes.

    The two need opposite responses: a caller told `not_configured` should send
    the user to the settings panel, one told `upstream_fault` should stop and
    report. Decided on the reason text because that is all `Service.search`
    hands back — it does not raise, so there is no exception type to read.
    """
    reason = result.get("reason") or ""
    return "not_configured" if ("金鑰" in reason or "OPS 不可用" in reason) else "upstream_fault"


def lean_search(result: dict) -> dict:
    """A result list without its abstracts — the same ruling `lean()` keeps.

    Its own function so it can be tested without a live OPS search: the gate
    runs offline, and the first version of this command shipped WITHOUT this
    step precisely because nothing offline was exercising `search` at all.
    Twenty-five rows each carrying a full OPS abstract left the machine on a
    command that had no `--full` to have opted into.
    """
    rows = []
    for row in result.get("results") or []:
        row = dict(row)
        text = row.pop("abstract", None)
        row["abstract_chars"] = len(text or "")
        rows.append(row)
    out, caught = strip_long_text(dict(result, results=rows))
    out["text_withheld"] = {
        "keys": ["abstract"],
        "also_withheld": caught,
        "reason": "檢索結果預設不輸出摘要全文（使用者裁定：保守預設）。",
        "how": "加上 --full，或用 `lookup <號碼> --full` 只取你真的要讀的那一件。",
    }
    return out


def with_provenance(card: dict) -> dict:
    """Condense `provenance` to one source per field, keeping `missing` intact.

    BR-4 (every field says who gave it) survives lean mode; the CSS selector
    that proved it does not — that is diagnosis for a human reading the page,
    not something a caller acts on.
    """
    prov = card.get("provenance") or {}
    card["sources"] = {k: v.get("source") for k, v in prov.items()}
    card.pop("provenance", None)
    return card


# ------------------------------------------------------------------ commands

def cmd_lookup(service: Service, args) -> dict:
    query = args.number.strip()
    if service.classify(query) != "number":
        raise Refused(f"{query!r} 看起來不是專利號碼。要查公司或發明人請用 `search`。")
    note(f"查詢 {query}…（庫內命中則不連網）")
    try:
        card = service.lookup(query, refresh=args.refresh)
        source = "store" if card.get("_from_store") else "google_patents"
    except ResolveError as exc:
        note("Google Patents 沒有這一件，改問 EPO OPS…")
        card = service.card_from_ops(query)
        if not card:
            return failure("lookup", "not_found", str(exc),
                           tried=getattr(exc, "tried", []))
        source = "epo_ops"
    payload = with_provenance(card if args.full else lean(card))
    payload["library"] = str(paths.db_path())
    client = service.ops_client()
    return envelope("lookup", payload, cost=spent(client, source), quota=_quota(client))


def cmd_search(service: Service, args) -> dict:
    """A result list, and — like `lookup` — without its text unless asked.

    This was the review's find: the first version handed `Service.search()`'s
    rows straight out, and every row carries an OPS abstract. Twenty-five
    abstracts leaving the machine on a command with no `--full` to have opted
    into is a bigger breach of the same ruling than the one `lookup` was
    carefully written to keep.
    """
    term = args.term.strip()
    if not term:
        raise Refused("空的檢索詞。")
    if not 1 <= args.size <= 100:
        raise Refused(f"--size 要在 1 到 100 之間（收到 {args.size}）。")
    if args.start < 1:
        raise Refused(f"--start 要從 1 起算（收到 {args.start}）。")
    client = service.ops_client()
    if client is None:
        return failure("search", "not_configured", service._ops_reason or "EPO OPS 不可用。")
    note(f"以 {args.field} 檢索「{term}」…（EPO OPS，會花配額）")
    result = service.search(term, field=args.field, us_only=not args.worldwide,
                            scope=args.scope, start=args.start, size=args.size)
    if not result.get("available"):
        # `Service.search` reports an OPS failure as a payload, not an
        # exception, so wrapping it in a success envelope told the caller the
        # search worked while the payload said it had not. Zero results are NOT
        # this case: OPS answers "nothing matched" with `available: True` and an
        # empty list, and that is an answer.
        return failure("search", search_fault(result), result.get("reason") or "檢索失敗。",
                       cost=spent(client, "epo_ops"), quota=_quota(client))
    if not args.full:
        result = lean_search(result)
    return envelope("search", result, cost=spent(client, "epo_ops"),
                    quota=client.usage.summary())


def cmd_figures(service: Service, args) -> dict:
    """Where this CLI earns its place: the sheets Google Patents does not have.

    Two costs, deliberately separated. Resolving which images exist is an
    inquiry — OPS calls, no page bytes — and happens by default because without
    it there is nothing to report. Page bytes cost per sheet and happen only
    under `--pages`, which is why the default run of this command can be put in
    a loop and the `--pages` run cannot.
    """
    number = args.number.strip()
    pages = parse_pages(args.pages) if args.pages else []

    stored = service.store.get(number)
    cached_meta = bool(stored and stored.get("ops"))
    if args.offline and not cached_meta:
        return failure("figures", "not_found",
                       "庫內沒有這一件的影像清單，而 --offline 禁止連線取得。",
                       how="拿掉 --offline（一次 inquiry 呼叫，不含圖頁位元組）。")
    if not cached_meta and service.ops_client() is None:
        return failure("figures", "not_configured",
                       service._ops_reason or "EPO OPS 不可用。")

    note(f"查 {number} 的影像清單…" + ("（庫內已有，不連網）" if cached_meta else "（EPO OPS inquiry）"))
    ops_meta = service.enrich(number)
    client = service.ops_client()

    if not ops_meta.get("available"):
        return failure("figures", "upstream_fault",
                       ops_meta.get("reason") or "EPO OPS 沒有回應影像清單。",
                       cost=spent(client, "epo_ops"), quota=_quota(client))

    drawings = ops_meta.get("drawings") or ops_meta.get("fullimage")
    if not drawings:
        return failure("figures", "not_found",
                       ops_meta.get("reason") or "EPO OPS 有此件，但沒有可取的影像。",
                       cost=spent(client, "epo_ops"), quota=_quota(client))

    link = drawings.get("link")
    total = drawings.get("pages")
    already = service.ops_cached_pages(link)
    absent = {} if args.refresh else read_absent(service, link)
    files: list[dict] = []
    fetch_failed: list[dict] = []
    learned: dict[str, str] = {}

    for page in pages:
        if total and page > total:
            fetch_failed.append({"page": page, "reason": f"這一份只有 {total} 頁。", "from": "meta"})
            continue
        if str(page) in absent:
            # Already asked, already told no. EPO's inquiry says how many sheets
            # a document has, but not which of them it actually holds: this
            # document reports 14 and 404s on 11, 12 and 13. Without remembering
            # that, "check before you spend" cannot see a per-page gap and the
            # same 404 is paid for again on every retry.
            fetch_failed.append({"page": page, "reason": absent[str(page)], "from": "cache"})
            continue
        note(f"  取第 {page} 頁…" + ("（快取）" if page in already else "（EPO OPS，計費）"))
        try:
            _, cached = service.ops_page_png(link, page)
        except ResolveError as exc:
            fetch_failed.append({"page": page, "reason": str(exc), "from": "ops"})
            if "404" in str(exc):          # a 404 is a fact; a 5xx or a timeout is not
                learned[str(page)] = str(exc)
            continue
        path = service._cache_path(link, f"p{page:04d}.png")
        files.append({"page": page, "path": str(path), "from": "cache" if cached else "ops"})

    if learned:
        write_absent(service, link, absent | learned)

    # Some sheets fetched and some not is a partial success and says so (the
    # `failed` list is BR-3 applied to pages). NOTHING fetched when pages were
    # asked for is a failure, and has to exit non-zero — otherwise a missing
    # credential looks to a caller exactly like a document with no drawings.
    fetched = sum(1 for f in files if f["from"] == "ops")
    network = "epo_ops" if (fetched or client and client.usage.requests) else "store"

    if pages and not files:
        code = "not_configured" if service.ops_client() is None else "upstream_fault"
        return failure("figures", code,
                       service._ops_reason if code == "not_configured"
                       else "要求的圖頁一頁都取不到。",
                       failed=fetch_failed,
                       cost=spent(client, network, pages_fetched=fetched),
                       quota=_quota(client))

    data = {
        "number": number,
        "resolved": ops_meta.get("resolved"),
        "drawings": {"link": link, "pages": total, "desc": drawings.get("desc")},
        "cached_pages": service.ops_cached_pages(link),
        "files": files,
        "failed": fetch_failed,
    }
    if absent:
        data["known_absent"] = {
            "pages": sorted(int(k) for k in absent),
            "reason": "EPO 的清單說有這幾頁，但實際取的時候是 404。記下來，重問不再計費。",
            "how": "--refresh 會忘掉這份紀錄重問一次。",
        }
    if not pages:
        data["not_fetched"] = {
            "reason": "未指定 --pages，本次沒有取任何圖頁位元組（不計費）。",
            "how": f"--pages 11 只取第 11 頁；--pages 1-3 取一段；--pages 2,5 取指定幾頁。"
                   f"一次上限 {MAX_PAGES} 頁。",
        }
    return envelope("figures", data,
                    cost=spent(client, network, pages_fetched=fetched),
                    quota=_quota(client))


def cmd_pdf(service: Service, args) -> dict:
    number = args.number.strip()
    # Both ends, not just the top one. `--pages 0` passed the cap check in the
    # first version and reached `merge_pdf_pages([])`, which builds an empty PDF
    # — a file that exists, opens, and contains nothing, which is worse than a
    # refusal because it looks like an answer. Found in review, 2026-09-11.
    if args.pages < 1:
        raise Refused("--pages 至少要是 1。")
    if args.pages > MAX_PAGES:
        raise Refused(f"一次最多取 {MAX_PAGES} 頁，這次要求 {args.pages} 頁。")
    if service.ops_client() is None:
        return failure("pdf", "not_configured", service._ops_reason or "EPO OPS 不可用。")
    note(f"查 {number} 的影像清單…")
    ops_meta = service.enrich(number)
    client = service.ops_client()
    if not ops_meta.get("available"):
        return failure("pdf", "upstream_fault",
                       ops_meta.get("reason") or "EPO OPS 沒有回應影像清單。",
                       cost=spent(client, "epo_ops"), quota=_quota(client))
    instance = ops_meta.get("fullimage") or ops_meta.get("drawings")
    if not instance:
        return failure("pdf", "not_found", "EPO OPS 有此件，但沒有可取的原文件影像。",
                       cost=spent(client, "epo_ops"), quota=_quota(client))
    link = instance["link"]
    note(f"  取前 {args.pages} 頁併成 PDF…（EPO OPS，計費）")
    try:
        _, included = service.ops_document_pdf(link, args.pages)
    except ResolveError as exc:
        return failure("pdf", "upstream_fault", str(exc),
                       cost=spent(client, "epo_ops"), quota=_quota(client))
    path = service._cache_path(link, f"document-{args.pages}p.pdf")
    return envelope("pdf", {
        "number": number,
        "path": str(path),
        "pages_requested": args.pages,
        "pages_included": included,
        "truncated": included < args.pages,
        "total_pages": instance.get("pages"),
    }, cost=spent(client, "epo_ops", pages_fetched=included), quota=_quota(client))


def cmd_doctor(service: Service, args) -> dict:
    """What is set up, where things are, what this process can and cannot do.

    Never a credential — not a prefix, not a suffix, not a length of the secret
    beyond what `config.hint` already shows the settings panel. A caller asking
    "am I configured" needs a boolean, and a boolean is what it gets.
    """
    cfg = config.load_ops(required=False)
    journal = service.store.conn.execute("PRAGMA journal_mode").fetchone()[0]
    return envelope("doctor", {
        "app_version": __version__,
        "ops_configured": cfg is not None,
        "paths": paths.describe(),
        "library_documents": service.store.count(),
        "journal_mode": journal,
        "max_pages_per_call": MAX_PAGES,
        "text_default": "lean（--full 才輸出全文）",
        "python": sys.version.split()[0],
    }, cost={"network": "none", "ops_requests": 0})


# --------------------------------------------------------------- human print

def human_lookup(payload: dict) -> None:
    d = payload["data"]
    print(f"{d.get('number')} — {d.get('title') or '(無標題)'}")
    print(f"  公開日 {d.get('publication_date')} · 申請日 {d.get('filing_date')}")
    print(f"  申請人 {', '.join(d.get('assignee') or []) or '—'}")
    counts = d.get("counts")
    if counts:
        print(f"  請求項 {counts['claims']} 項（獨立 {counts['independent_claims']}）· "
              f"說明書 {counts['description_blocks']} 段 · 圖 {counts['images']} 張")
        print("  全文未輸出（--full）")
    else:
        # --full was passed, so the text IS here. Saying nothing about it made
        # the human output of `--full` look identical to lean mode minus the
        # notice — the flag appeared to do nothing. Found in review, 2026-09-11.
        claims = d.get("claims") or []
        print(f"  請求項 {len(claims)} 項 · 說明書 {len(d.get('description') or '')} 字元 · "
              f"摘要 {len(d.get('abstract') or '')} 字元（全文已輸出，用 --json 取用）")
        if d.get("abstract"):
            print(f"  摘要：{d['abstract'][:200]}…")
    for miss in d.get("missing") or []:
        print(f"  缺：{miss['field']} — {miss['reason']}")
    print(f"  庫：{d.get('library')}")


def human_figures(payload: dict) -> None:
    d = payload["data"]
    print(f"{d['number']} — 圖式 {d['drawings']['pages']} 頁（{d['resolved']}）")
    print(f"  已在磁碟：{d['cached_pages'] or '無'}")
    for f in d["files"]:
        print(f"  第 {f['page']} 頁 [{f['from']}] {f['path']}")
    for f in d.get("failed") or []:
        print(f"  第 {f['page']} 頁 取不到 — {f['reason']}")
    if "not_fetched" in d:
        print(f"  {d['not_fetched']['reason']}")
        print(f"  {d['not_fetched']['how']}")


def human_generic(payload: dict) -> None:
    print(json.dumps(payload.get("data", payload), ensure_ascii=False, indent=2))


# --------------------------------------------------------------------- entry

class Parser(argparse.ArgumentParser):
    """argparse, but its refusals look like this program's other refusals.

    Two things stock argparse gets wrong for a caller that is a program.
    It exits 2 on a usage error, and 2 in this program's table means
    `not_configured` — so "you passed a flag wrong" and "there is no credential"
    were indistinguishable to anything branching on the exit code alone. And it
    prints a usage block to stderr with nothing on stdout, so a caller that
    asked for `--json` gets no JSON at all on the one path where it most needs
    to know why. Both are answered here: exit 3 (`refused`, which is what a bad
    argument is), and the same envelope every other refusal uses.
    """

    def error(self, message: str):
        wants_json = "--json" in sys.argv
        self.print_usage(sys.stderr)
        payload = failure(sys.argv[1] if len(sys.argv) > 1 else "pgb", "refused",
                          message, how="`--help` 列出這個子指令接受的旗標。")
        emit(payload, as_json=wants_json, human=human_error)
        raise SystemExit(EXIT["refused"])


def build_parser() -> argparse.ArgumentParser:
    # `--json` is accepted on BOTH sides of the subcommand, because an agent
    # writing `pgb lookup X --json` should not be taught that this program wants
    # its flags in an unusual place. SUPPRESS is what makes that safe: without
    # it the subparser's own default would overwrite a `--json` given before the
    # subcommand, and the caller would silently get human text to parse.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="stdout 只放一個 JSON 物件；人看的訊息一律走 stderr")

    ap = Parser(
        prog="pgb", parents=[common],
        description="PatentsGrabber 的命令列介面：給 agent 用的那一面。")
    sub = ap.add_subparsers(dest="command", required=True, parser_class=Parser)

    def add(name: str, help: str):
        return sub.add_parser(name, help=help, parents=[common])

    p = add("lookup", "一件專利的卡片（預設精簡，--full 才給全文）")
    p.add_argument("number")
    p.add_argument("--full", action="store_true", help="連摘要、請求項、說明書全文一起輸出")
    p.add_argument("--refresh", action="store_true", help="忽略庫內版本，重新讀取來源")

    p = add("search", "申請人／發明人／標題檢索（EPO OPS，計費）")
    p.add_argument("term")
    p.add_argument("--full", action="store_true", help="連每一筆的摘要全文一起輸出")
    p.add_argument("--field", default="pa", choices=["pa", "in", "ti", "ab", "ta"],
                   help="pa 申請人／in 發明人／ti 標題／ab 摘要／ta 標題或摘要")
    # `choices` rather than a free string: an unrecognised scope used to fall
    # back to US without saying so, which is the silent gap this project's own
    # rules forbid — a caller asking for UK would have got US-only results and
    # no hint that its argument was ignored.
    p.add_argument("--scope", default=None, choices=list(SEARCH_SCOPES),
                   help="US（預設）／EP／USEP／ALL；由 OPS 端過濾，不是畫面過濾")
    p.add_argument("--worldwide", action="store_true", help="不限 US（等同 --scope ALL）")
    p.add_argument("--start", type=int, default=1, help="從第幾筆開始（OPS 最多翻到 2000）")
    p.add_argument("--size", type=int, default=25, help="回傳幾筆（1–100，預設 25）")

    p = add("figures", "圖式：先報有什麼，--pages 才真的取圖檔")
    p.add_argument("number")
    p.add_argument("--pages", default=None,
                   help=f"11（只取第 11 頁）／1-4（一段）／2,5（指定幾頁）；"
                        f"一次上限 {MAX_PAGES} 頁。不給就不取任何位元組")
    p.add_argument("--offline", action="store_true", help="只用庫內已有的影像清單，完全不連線")
    p.add_argument("--refresh", action="store_true",
                   help="忘掉「這幾頁 EPO 回過 404」的紀錄，重新問一次")

    p = add("pdf", "原文件掃描檔併成一個 PDF（EPO OPS，計費）")
    p.add_argument("number")
    p.add_argument("--pages", type=int, default=1)

    add("doctor", "設定狀態、資料位置、能力界線（不連網，不含任何金鑰）")
    return ap


HUMAN = {"lookup": human_lookup, "figures": human_figures}
RUN = {"lookup": cmd_lookup, "search": cmd_search, "figures": cmd_figures,
       "pdf": cmd_pdf, "doctor": cmd_doctor}


def run(argv: list[str] | None = None) -> int:
    # A Windows console defaults to cp950 here, and a Chinese absence reason is
    # what this program exists to print. Reconfiguring is cheaper than deciding
    # per-string whether it will survive the encoder.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):        # already wrapped, or not a TTY
            pass

    args = build_parser().parse_args(argv)
    paths.ensure_data_dirs()

    as_json = getattr(args, "json", False)
    try:
        with waiting("開啟專利庫（另一個 PatentsGrabber 行程可能正在寫入）"):
            service = Service(paths.db_path(), raw_dir=paths.raw_dir(),
                              cache_dir=paths.cache_dir())
    except sqlite3.OperationalError as exc:
        payload = busy_or_internal("startup", exc)
        emit(payload, as_json=as_json, human=human_error)
        return EXIT[payload["error"]["code"]]

    try:
        try:
            with waiting(f"執行 {args.command}"):
                payload = RUN[args.command](service, args)
        except Refused as exc:
            payload = failure(args.command, "refused", str(exc))
        except ResolveError as exc:
            payload = failure(args.command, "upstream_fault", str(exc))
        except sqlite3.OperationalError as exc:
            payload = busy_or_internal(args.command, exc)
        except Exception as exc:
            # A traceback on stderr and nothing on stdout is the one failure a
            # caller cannot handle: it looks identical to the program not
            # running. Anything unforeseen still leaves the envelope intact,
            # with the exception named — the trace is on stderr for a human.
            import traceback
            traceback.print_exc()
            payload = failure(args.command, "internal_error",
                              f"{type(exc).__name__}: {exc}")
        emit(payload, as_json=as_json,
             human=HUMAN.get(args.command, human_generic) if payload["ok"] else human_error)
        return EXIT["ok"] if payload["ok"] else EXIT.get(payload["error"]["code"], 1)
    finally:
        service.close()


def human_error(payload: dict) -> None:
    err = payload["error"]
    print(f"失敗（{err['code']}）：{err['message']}")
    if err.get("tried"):
        print(f"  已嘗試：{', '.join(err['tried'])}")
    if err.get("how"):
        print(f"  {err['how']}")


if __name__ == "__main__":
    sys.exit(run())
