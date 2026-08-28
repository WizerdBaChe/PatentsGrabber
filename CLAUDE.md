# PatentsGrabber — project rules

ops-relaxation: L1

## Environment (measured, not assumed)

- **Displays in real use: 1920×1080 (FHD) and 2560×1440 (QHD).** Verify UI at
  those sizes. Checking only 1024/1440 hides wide-screen faults — it hid one:
  a fixed 55% text pane left a dead strip beside a measure-capped column.
  `tools/shoot.py` defaults to 1920×1080; pass `--width 2560 --height 1440` for QHD.
- Windows 11, local single user, one process (`python run.py`), no build step.
- Python 3.12; Pillow, pypdf and PyMuPDF are already installed on this machine.

## Rules that bind every change here

- **No silent waiting.** Anything taking more than ~300 ms says what it is
  waiting for and how long it has been waiting. Phases shown must be real
  phases, never a fake progress bar. (user ruling, 2026-08-26)
- **No silent gaps.** A field that could not be fetched states why (BR-3); a
  truncated list states the cap and the true total; a drawing that fails to load
  says so instead of rendering a broken image.
- **Local use only.** Since OPS was connected this is contractual, not a
  preference: OPS T&C 3.2 forbids making OPS data itself available to the public
  (breach → termination under 8.3). No public deployment, no multi-user.
- **Google Patents: single-document pages only**, never its search endpoint
  (`robots.txt` allows `/patent/`, disallows the rest).
- **Quota is metered by OPS's own headers**, never by a hard-coded threshold;
  page images are fetched only when looked at and cached under `var/ops-cache/`.
- Credentials live in the settings file only (`.env` in a checkout,
  `%LOCALAPPDATA%\PatentsGrabber\settings.env` when packaged; both via
  `paths.settings_path()`). Never print, log, or paste a key value.

## No response may contain a credential, and the guard is not optional

Two properties of the ASSET, both gated by `tools/check_settings.py` (and again
against the packaged build by `tools/check_release.py`):

- **No HTTP response body may contain a key or secret value.** Hints only —
  length plus the last four characters, via `config.hint`. The browser is never
  given a secret, which is why an omitted field on POST means "keep the stored
  one" and cannot mean anything else.
- **A loopback port is reachable from every tab in the browser.** `app._guard`
  rejects a non-loopback `Host` (DNS rebinding), a foreign `Origin`, and
  `Sec-Fetch-Site: cross-site`. Each rejection has a matching must-ACCEPT case
  in the gate; a guard tested only against bad inputs scores full marks by
  refusing everything.

`OPS_BASE_URL` is validated against `config.ALLOWED_BASE_HOSTS`, by the same
validator on both the save and the test path — the key travels there as an HTTP
Basic header, so a free-text host is a credential-exfiltration field. Loosening
that list means loosening where the credential can go.

## Two modes, one program

`paths.py` decides where data lives: a checkout keeps everything under the repo
root (every gate asserts against `var/…` by that path), a packaged build keeps it
under `%LOCALAPPDATA%`. **Never write beside the executable** — a program folder
is not writable in the general case, and that failure only appears on somebody
else's machine.

`packaging/build.ps1` will not produce a release that has not been started and
driven. This is not belt and braces: the first packaged build reported success
and died instantly on a relative import, because PyInstaller runs its entry
script as `__main__` with no package context. `tools/check_release.py` is what
caught it, and it is why the entry point's import is absolute.

## Stored cards carry the version of the code that built them

Two schema constants, same purpose: a fix must reach the cards already in the
library, not only the next lookup.

- `google_patents.READING_SCHEMA` — description blocks and the claim tree.
  Bumping it re-derives stored cards from `var/raw/` (no network).
- `service.OPS_CARD_SCHEMA` — the OPS-built card. Bumping it re-fetches from OPS.

**Bump the matching constant in the same commit as any change to what those
extractors produce.** Both were caught the hard way: a claim-parser fix and an
abstract-cleanup fix each landed while a stale card sat in the library.

This is no longer only a sentence: `tests/test_extractor_fingerprint.py` pins a
fingerprint of the extractors' executable structure (AST, docstrings stripped —
comments and formatting do not trip it) next to each constant. Change an
extractor without bumping, and it fails and tells you what to do.

## Number formats are per record, not per rule

Which serial width OPS holds a US publication under differs by document
(`US.2025383260.A1` vs `US.20260189299.A1`; the other width 404s in each case).
Candidates offer both widths; never "simplify" that back to one. epodoc never
carries a kind code, docdb always does, and the Espacenet display form
(`US2025383260A1`) is an API input for neither. Pinned in
`tests/test_ops_number_formats.py`.

## Jurisdictions differ in what each source can give

- **US**: text from Google Patents only. OPS full text does NOT cover US (F-1),
  so a US document Google lacks gets bibliography + scanned original, no text.
- **EP**: text from Google Patents when it has the document, from **OPS full
  text** when it does not — OPS covers EP. The EPO publishes the specification
  in the **filing language only**, so an EP case can legitimately be German or
  French; the card states the language rather than translating (no translation
  engine, by CIM §4 boundary 3).
- Claim dependency detection therefore has to work in EN, DE and FR
  (`google_patents.DEPENDS_RE`, shared by both parsers, controls in
  `tools/check_search.py`).

## Google Patents lags OPS by months

Measured 2026-08-26: of 24 US publications from 2026-06…08, Google Patents had
none. Search results are newest-first, so the first page of a company search is
exactly the part with no Google record. That is why the card falls back to OPS
bibliographic data instead of failing — and why that fallback is provisional and
re-tried on the next read.

## Where things are written down

- `docs/01-concept-note.md` — business rules BR-1…BR-8, boundaries, staging
- `docs/05-stage1-spec.md` — Stage 1 scope, settled facts (F-1…F-8), acceptance
- `docs/06-stage1-review.md` — which rule is guarded by which gate, and where the
  gates are blind. Read it before assuming a green run proves something.
- Gates: run them with **`python tools/run_gates.py`** (`--net` adds the ones that
  fetch pages, `--all` adds the two that spend OPS quota). It also states what
  each score's denominator is, because they are not the same unit.
  Individually: `tools/check_reading.py` (structure added without losing text,
  controls both ways, self-calibrating), `tools/check_layout.py` (R-9/R-10 over
  DevTools at 1920 and 2560), `tools/check_figures.py` (the drawing pane's
  geometry under rotation, fit and zoom), `tools/check_settings.py` (settings
  round-trip, no credential in any response, the local-only guard),
  `tools/check_diagrams.py` (the drawn state diagrams match their model),
  `tools/check_freshness.py` (the archive still matches the live source),
  `tools/verify_ops.py`, `tools/check_search.py`, `tools/smoke_service.py`,
  `tests/test_ops_number_formats.py`, `tests/test_extractor_fingerprint.py`
- **Every gate here carries a positive control**, because a checker that cannot
  fail is not a checker. When you add one, add the input it must catch in the
  same commit. Two of this project's gates were wrong in exactly the way a
  one-sided calibration hides: `check_search`'s "an exact name collapses to one
  applicant" was pinned to OPS's applicant normalisation, which changed
  (2026-08-29) — the product was right and the assertion was stale. An
  assertion calibrated against a corpus we do not control **expires by default**
  and gets a `review-when` line naming the event, never a date.
