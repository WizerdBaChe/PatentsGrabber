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

## Reading structure

`google_patents.READING_SCHEMA` versions the description-block and claim-tree
extraction. **Bump it whenever that extraction changes**: stored cards carry the
version they were built with and are re-derived from `var/raw/` on the next read,
so a parser fix reaches the existing library instead of only new lookups.

## Where things are written down

- `docs/01-concept-note.md` — business rules BR-1…BR-8, boundaries, staging
- `docs/05-stage1-spec.md` — Stage 1 scope, settled facts (F-1…F-8), acceptance
- Gates: `tools/check_reading.py` (structure added without losing text, with
  controls both ways and a self-calibration case), `tools/verify_ops.py`,
  `tools/smoke_service.py`, `tests/test_ops_number_formats.py`
