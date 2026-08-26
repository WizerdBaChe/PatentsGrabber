# Stage 1 — Working Spec (rules, requirements, work breakdown)

- Date: 2026-08-26 · Status: **in build**
- Upstream: `docs/01-concept-note.md` (CIM, rules) · `docs/02-stage0-findings.md` (Stage 0 measurements) · `docs/04-ops-verification-results.md` (OPS capability evidence)
- This file invents no new rules. It restates the binding ones next to the code they constrain, pins the facts that are already settled by measurement, and slices Stage 1 into buildable units.
- Primary consumer: the session that builds Stage 1. Sections the user rules on are in Traditional Chinese (§6).

---

## 1. Binding rules and what each one forces in Stage 1 code

| Rule | Statement (from CIM §6) | What it forces here |
|---|---|---|
| **BR-1** | Input tolerance; the user never pre-classifies the input | `numbers.normalize()` stays the single entry; OPS number formats are derived from `ParsedNumber`, never re-guessed |
| **BR-2** | Source priority varies **per field**, not one fixed order | Text ← Google Patents; drawings / original document ← OPS. Encoded per field, not per document |
| **BR-3** | A missing field must state **why**; never silently blank | Every OPS failure path produces a reason string that reaches the UI. An unavailable EPO drawing says "OPS returned 404/403", not an empty pane |
| **BR-4** | Every field carries its source | `provenance[field].source` becomes `"Google Patents"` **or** `"EPO OPS"`; the source popover must show both |
| **BR-5** | Looked up = kept (personal library, not a cache) | OPS enrichment metadata is persisted into the card; fetched image pages and PDFs are kept on disk under `var/ops-cache/` |
| **BR-6** | Self-limit against the OPS free tier | One inquiry call per document; **page bytes only on demand**; disk cache means a re-read costs zero quota; OPS's own counters are surfaced, no threshold hard-coded |
| **BR-7** | Google Patents single-document pages only, never its search | Unchanged. Applicant search, when built, goes to OPS CQL only |
| **BR-8** | Applicant-name variants must be exposed, never collapsed | Applies to S1-C (not in this round's scope) |

Boundaries that touch this round (CIM §4): **no public deployment** — after OPS is connected this is a contractual duty, not a preference (OPS T&C 3.2 forbids making OPS data itself available to the public; `docs/03-ops-terms-compliance.md`). Local single-user only. No FTO/legal interpretation. No headless browser on the main path.

---

## 2. Settled facts — evidence exists, do not re-litigate

| # | Fact | Evidence |
|---|---|---|
| **F-1** | **OPS full text does not cover US.** `claims`/`description` for US → `CLIENT.InvalidCountryCode`; the same call against `EP1000000` returns 10,804 chars | `docs/04` §2, `tools/verify_ops.py` §4 |
| **F-2** | epodoc **must not** carry a kind code; docdb **must** use the 6-digit serial **with** kind | `docs/04` §4, `tests/test_ops_number_formats.py` |
| **F-3** | Images inquiry returns 3 instances: `@desc="Drawing"` → `…/thumbnail` (drawing sheets), `FirstPageClipping` → `…/firstpage`, `FullDocument` → `…/fullimage`. Formats for Drawing/FullDocument are `application/pdf` + `application/tiff` only — **no PNG/JPEG**, so a browser cannot display them directly | measured 2026-08-26: US20250383260A1 → 14/1/25 pages; US6285999B1 → 3/…/11 pages |
| **F-4** | A `…/thumbnail` TIFF page is a **full sheet at ~300 dpi** (2550×3300, mode `1` bilevel, 27.5 KB), decodable by Pillow and convertible to PNG (~48 KB) | measured 2026-08-26, same probe |
| **F-5** | Google Patents description markup has **two vintages**: `div.description` + `div.description-paragraph` (grants/older) and `ul.description` + `li > para-num[num="[0001]"] + div.description-line` (newer publications). Both use `<heading>` for section titles and `<description-of-drawings>` as a wrapper | 12 saved pages in `var/raw/`, measured 2026-08-26 |
| **F-6** | Figure references are **already semantic**: `<figref idrefs="DRAWINGS">FIG. <b>3</b></figref>` — 0 in pre-2000 documents, 9–1074 in modern ones. Reference numerals are marked as `<figure-callout>` | same sweep |
| **F-7** | Claims are **already segmented in the markup**, in **two shapes**: limitations nested inside the preamble's `div.claim-text` (`US20250383260A1`, `US5960411A`, `US4237224A`, `US8046721B2`) **or** as siblings of it (`US6285999B1`). Plus `<claim-ref idref="CLM-00001">` cross-references. The Stage 0 parser flattens all of it with `get_text(" ")` | same sweep; the sibling shape was found by eye on a screenshot after the nested-only parser rendered a claim as its preamble alone — which is why claim coverage is now measured, not just description coverage |
| **F-8** | Quota is metered by OPS's own headers (`x-registeredquotaperweek-used`, `x-throttling-control`), bucketed by GMT week. Observed 2026-08-26: system state `overloaded` → images 50/min, search 5/min | `epo_ops.Usage`, live headers |

> F-5/F-6/F-7 are the mechanical basis for §4. The reading optimizations below are **not** heuristics layered on flat text — they recover structure the source already publishes and Stage 0 discarded.

---

## 3. Stage 1 work breakdown

| ID | Unit | This round | Rationale |
|---|---|---|---|
| **S1-A** | OPS drawings + original document PDF, filling the two Stage 0 holes | **YES** | `docs/04` §1 calls this "the entire reason for connecting OPS". It repairs the user's own example document |
| **S1-B** | INPADOC family + legal events from OPS (richer than the Google table) | **YES, on demand** | One extra call, only when the user opens the panel |
| **S1-C** | Applicant / company CQL search + result list + BR-8 name variants | no — next round | A second entry point with its own UI surface; mixing it into this round would ship both at 60% |
| **S1-D** | EP jurisdiction (EP full text via OPS, EP number parsing) | no — next round | `numbers.py` is US-only by construction; EP needs its own normalization + card path |
| **S1-E** | Number normalization through OPS number-service | partial (already the last-resort path in `OpsClient.resolve`) | Promoting it to primary costs a call per lookup for no measured gain |

**Degradation order if the round runs short**: drop S1-B → drop reference-numeral highlighting (§4 R-7) → drop the EPO high-resolution opt-in for documents whose Google images already work. **Guaranteed core**: S1-A for documents where Google has no usable drawings/PDF, plus §4 R-1…R-5.

---

## 4. Human-reading requirements (the part the incumbent platforms do not optimize)

The claim to beat is narrow and honest: Google Patents, Espacenet and PATENTSCOPE all render specification text as an undifferentiated column sized to the browser window. None of them lets a reader set measure/leading, none links a `FIG. 3` mention to the drawing, and none keeps the claim tree readable as a tree.

| ID | Requirement | Basis |
|---|---|---|
| **R-1** | Description renders as **blocks** — headings, numbered paragraphs, preformatted blocks — never one flattened run of text. Paragraph numbers (`[0042]`) sit in a gutter, not inline | F-5 |
| **R-2** | Typography is user-controlled and persisted: font size, line height, measure (`ch`), paragraph number visibility, serif/sans. Defaults tuned for long-form reading (≈16px / 1.8 / 68ch), not for filling the window | — |
| **R-3** | Section outline built from `<heading>`, jump-to-section from the reading pane | F-5 |
| **R-4** | `FIG. n` in the text is clickable and drives the drawing pane. The number→sheet mapping is **approximate** (a sheet may carry several figures) and must say so rather than pretend | F-6 |
| **R-5** | Claims render as a tree: preamble, then one indented block per limitation; `claim N` cross-references are clickable and scroll to that claim | F-7 |
| **R-6** | Plain, copyable text is preserved for every structured view — structure must never cost the user their copy-paste | CIM §1 (the original pain) |
| **R-7** | Reference numerals (`figure-callout`) get a subtle marker; off by default | F-6 |

**Non-goals for §4**: no re-writing, summarizing, or translating of patent text; no AI-generated reading aids. Structure comes from the source markup or it is not shown.

---

## 5. Acceptance (blind-executable)

Machine-checkable:

1. `python tools/check_reading.py` — over the 12 saved pages in `var/raw/`: every document with a description yields ≥1 block; no block is empty; the concatenated block text length is within 2% of the flat `description` field (structure added, content not lost); documents known to carry `figref` report ≥1 figure reference.
2. `python tools/verify_ops.py` — still ≥11/14, with the US full-text arms still asserting "expected unavailable".
3. `python tools/smoke_service.py` — end-to-end lookup still returns a card for `US6285999B1` and still fails loudly for `US99999999B2`.
4. `/api/enrich?q=US20250383260A1` returns `drawings.pages == 14` and `fullimage.pages == 25`.

### 5.1 Measured results (2026-08-26, this round)

| Check | Result |
|---|---|
| `tools/check_reading.py` | **12/12 documents pass**, description coverage 0.992–1.000, **claim coverage 1.000**; figure-reference controls pass in both directions; calibration page (paragraph markup removed) correctly **fails** at 0.007 |
| Claim-coverage metric, calibrated against the defect it was written for | replaying the pre-fix parser on `US6285999B1` (limitations are siblings, not nested) reads **0.282** and would fail the gate; the fixed parser reads 1.000. The nested-markup document reads 1.000 under both, so the metric does not cry wolf |
| `tools/verify_ops.py` | **14/14** (was 11/14 at the last record; the three former reds are now asserted as expected outcomes) |
| `tools/smoke_service.py` | **ALL PASS** |
| `tests/test_ops_number_formats.py` | **ALL PASS** |
| `/api/enrich?q=US20250383260A1` | `drawings.pages = 14`, `fullimage.pages = 25` ✔ |
| Drawing actually renders | `US20250383260A1` sheet 1 loads at **2550×3300** (`naturalWidth`, not an element count — the Stage 0 lesson) |
| `/api/ops/pdf` (10 pages) | 1,034,346 bytes, `%PDF-` magic, 11.4 s first time, **0.00 s** from cache |
| Untrusted link rejected | `?link=../../secret` → HTTP 502, refused before any fetch |
| No-credential degradation | card still builds (115 blocks); enrich/inpadoc/page all return an actionable reason, nothing raises |
| Structure in the DOM | `US20250383260A1`: 86 paragraphs, 86 gutter numbers, 3 headings, 42 clickable figure references, 20 claims / 24 limitation blocks |
| Regression, accepted Stage 0 behaviour | 294-thumbnail case: all `loading=lazy`, strip scrolls internally (2736→499 px), page does **not** overflow horizontally at 1440×900 (`scrollWidth == clientWidth == 1440`) |

Human-eye (must be confirmed in the real browser; see the delivery checklist):

5. `US20250383260A1` — Stage 0 showed no drawings and no PDF. Now shows EPO drawing sheets and offers the original PDF.
6. Long description (`US7479949B2`, 1074 figrefs) reads as paragraphs with numbers in the gutter; scrolling is not janky.
7. Reading controls change size/leading/measure live and survive a reload.

---

## 6. 本輪範圍決定與待裁決事項

**本輪做**：S1-A（EPO 圖式與原文件 PDF）＋ S1-B（INPADOC 同族／法律事件，隨點隨取）＋ §4 的閱讀優化 R-1…R-6。

**本輪不做，且是刻意的**：

- **S1-C 申請人／公司名檢索**：它是「第二條入口」，會長出自己的結果列表 UI 與 BR-8 名稱變體收斂介面。與本輪混做，兩邊都只會做到六成。
- **S1-D EP 管轄**：`numbers.py` 目前是 US-only 的結構，EP 要自己的號碼正規化與卡片路徑。
- **R-7 引用元件編號高亮**：預設關閉的加值功能，排在降級順序第二位。

**待你裁決**：

1. 下一輪先做 S1-C（公司名檢索）還是 S1-D（EP 管轄）？
2. 圖式預設來源：Google 有圖時就用 Google（省配額、載入快），EPO 高解析設為選項——這是目前的實作選擇。若你希望**一律用 EPO 300 dpi**，說一聲即可改預設。
3. 是否要把 `ops-relaxation: L1` 寫進專案的 `CLAUDE.md`（目前此專案沒有這個檔）。

---

## 7. review-when

- OPS starts serving US full text → F-1 dies, the source split in CIM §7.3 simplifies (`tools/verify_ops.py` alarms on this by design).
- Google Patents changes `robots.txt` or requires login → US text loses its only source; blocking risk.
- Google Patents changes the description/claims markup vocabulary → F-5/F-6/F-7 and `tools/check_reading.py` need a re-sweep. The checker is the tripwire: it compares structure against flat text on every run.
- EPO changes the free-tier threshold or OPS versions up → F-8 and `epo_ops.Usage` need re-reading.
