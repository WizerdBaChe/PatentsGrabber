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
- Credentials live in `.env` only. Never print, log, or paste a key value.

## Stored cards carry the version of the code that built them

Two schema constants, same purpose: a fix must reach the cards already in the
library, not only the next lookup.

- `google_patents.READING_SCHEMA` — description blocks and the claim tree.
  Bumping it re-derives stored cards from `var/raw/` (no network).
- `service.OPS_CARD_SCHEMA` — the OPS-built card. Bumping it re-fetches from OPS.

**Bump the matching constant in the same commit as any change to what those
extractors produce.** Both were caught the hard way: a claim-parser fix and an
abstract-cleanup fix each landed while a stale card sat in the library.

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
- Gates: `tools/check_reading.py` (structure added without losing text, with
  controls both ways and a self-calibration case), `tools/verify_ops.py`,
  `tools/smoke_service.py`, `tests/test_ops_number_formats.py`
