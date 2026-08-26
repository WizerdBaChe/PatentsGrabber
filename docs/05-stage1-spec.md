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
| **F-9** | **Search limits (S-6, answered).** `Range` span max **100** per call; `Range` end max **2000**. Beyond either → HTTP 400 `CLIENT.InvalidQuery`. So a 38,955-hit query exposes only its first 2,000. Zero results is **HTTP 404 `SERVER.EntityNotFound`** — an answer, not a failure | measured 2026-08-26 |
| **F-10** | `pn=US` restricts the result set to US publications server-side (`pa="Corning"` 38,955 → 22,592; a 50-row page came back 50/50 US). `cc=US` → `CLIENT.InvalidIndex`; `pn=US*` → `CLIENT.MinimumCharsBeforeTruncation` | measured 2026-08-26 |
| **F-11** | **`published-data/search/biblio` is what makes BR-8 possible.** The plain search returns document ids only; the biblio constituent carries applicant names twice — `@data-format="epodoc"` (normalised, `CORNING INC [US]`) and `"original"` (as filed, `CORNING INCORPORATED`) — plus titles, inventors and abstracts. Cost ~5–8 KB per result | measured 2026-08-26 |
| **F-12** | **S-7, answered: the variant problem is real and includes false positives.** `pa="Corning"` over 100 results → **9 distinct normalised names, 12 as-filed**, among them `OWENS CORNING INTELLECTUAL CAPITAL` (a different company), a Ningbo hospital named 康寧, `UNIV KENT STATE OHIO` and `PAROC GROUP OY`. `pa="Taiwan Semiconductor"` → 5 normalised / 9 as-filed for essentially one company | measured 2026-08-26 |
| **F-13** | **Google Patents lags OPS by months.** Of 24 US publications from 2026-06…2026-08 returned by OPS search, Google Patents had **0**. Since OPS sorts newest-first, the first page of a company search is precisely the part the Google-only card path cannot open | measured 2026-08-26 |
| **F-15** | **Google Patents carries EP full text**, in markup vintages the US-only parser did not know: a `<description>` element with `<p num="0001">` paragraphs, and `<claim>` / `<claim-text>` **elements** rather than `div.claim` / `div.claim-text`. Before the fix, EP3000000A1 produced 0 blocks from 50,785 characters and EP4000000A1 produced 0 claims | measured 2026-08-26, four EP samples now in `var/raw/` |
| **F-16** | **A machine-translated page carries BOTH languages in the DOM**: `<span class="notranslate"><span class="google-src-text">German original</span>English translation</span>`, with Google's own CSS hiding the first. Extracting it doubles every paragraph with text the reader did not ask for — and a text-coverage check cannot see it, because the untranslated half is in the flat text too. EP3000000A1: 72 such blocks, **53 % of the raw text** | measured 2026-08-26; `check_reading.py` now measures the drop explicitly |
| **F-17** | **OPS EP full text**: `description` is `p[]` with the number inside the string (`[0001]    The invention…`); `claims` is **ONE** `claim` element whose `claim-text` entries are split by a leading `N.` — an entry without one continues the previous claim. Treating each entry as a claim renumbers the whole set | measured 2026-08-26 (EP1000000, EP4793850) |
| **F-18** | **The EPO publishes the specification in the filing language only.** EP4794149A1 comes back in German (`@lang=DE`) and there is no translation to ask for; only the claims are translated, at grant (B1). The card states the language instead of handing over unexpected text | measured 2026-08-26 |
| **F-19** | **Claim dependency has to be detected in EN / DE / FR.** `nach Anspruch 1` and `selon la revendication 1` are exactly as dependent as `according to claim 1`, and English `according to any of the preceding claims` carries no number at all. Before the fix, all 9 claims of a German EP case were reported independent | measured 2026-08-26; both directions pinned in `check_search.py` |
| **F-20** | EP numbers: epodoc `EP1000000` (no kind code), docdb `EP.1000000.A1` — and the kind must be the document's actual one (`EP.1000000.A2` → 404). `pn=EP` and `(pn=US OR pn=EP)` work as search scopes | measured 2026-08-26 |
| **F-14** | **F-2 does not generalise.** Which serial width OPS holds a publication under is per RECORD: `US.2025383260.A1` works and `US.20250383260.A1` 404s, while `US.20260189299.A1` works and `US.2026189299.A1` 404s. Both widths must be offered as candidates. What stays invariant: epodoc never carries a kind code, docdb always does, and the Espacenet display form is valid as neither | measured 2026-08-26, pinned in `tests/test_ops_number_formats.py` |

> F-5/F-6/F-7 are the mechanical basis for §4. The reading optimizations below are **not** heuristics layered on flat text — they recover structure the source already publishes and Stage 0 discarded.

---

## 3. Stage 1 work breakdown

| ID | Unit | This round | Rationale |
|---|---|---|---|
| **S1-A** | OPS drawings + original document PDF, filling the two Stage 0 holes | **YES** | `docs/04` §1 calls this "the entire reason for connecting OPS". It repairs the user's own example document |
| **S1-B** | INPADOC family + legal events from OPS (richer than the Google table) | **YES, on demand** | One extra call, only when the user opens the panel |
| **S1-C** | Applicant / company CQL search + result list + BR-8 name variants | **YES** (2026-08-26, second round) | Built after the reading work landed; brought its own discovery, F-13, which forced the OPS-only card below |
| **S1-C′** | **OPS-only card**: when Google Patents does not have a document, build the card from OPS biblio (title, applicants, inventors, dates, CPC, abstract) with the full text declared absent and the reasons stated | **YES**, forced by F-13 | Without it the first page of every company search dead-ends in "查不到" while the document sits in OPS. Provisional by construction: it is re-tried against Google on the next read |
| **S1-D** | EP jurisdiction (EP number parsing, EP text, EP search scope) | **YES** (2026-08-26, fourth round) | Turned out to be mostly parser work, not a second pipeline: Google Patents carries EP full text, and OPS covers EP where Google does not |
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
| **R-8** | **No silent waiting.** Every wait over ~300 ms names what it is waiting for and how long it has been waiting. The phases shown are the real ones (parse → Google Patents → EPO inquiry); no fake progress | user ruling 2026-08-26 |
| **R-9** | **The layout follows the window.** The reading column is capped at the measure for readability, so the text pane is sized to the column and the remainder goes to the drawing; the column centres in its pane. Verified at 1920×1080 and 2560×1440, not at 1024 | user ruling 2026-08-26 — a fixed 55% split wasted ~800 px on a 2560 screen |
| **R-10** | **The line follows the screen too, inside the typographic range.** Default type size and measure are picked from the viewport (68ch/16px → 72ch/17px → 76ch/18px), never "fill the window": past ~90 characters the return sweep fails. Any control the reader touches pins their values permanently; the panel states which mode it is in | user question 2026-08-26 — sizing only the pane left the line at 68ch on every display |

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

### 5.2 Wide-screen layout and progress feedback (2026-08-26, second pass)

| Check | Result |
|---|---|
| R-9 at **2560×1440** | split auto-computes to **30.0 %** → text pane 768 px, figure pane 1785 px, column 544 px centred (gaps 105/120 px). Before: fixed 55 % → 1408 px pane around the same 544 px column, i.e. ~800 px dead |
| R-9 at **1920×1080** | split **34.0 %** → text pane 653 px, figure pane 1260 px, column 540 px, page overflow 0 px |
| Layout follows the reading controls | measure 68→100 ch: pane 768→909 px; then size 16→20 px: pane →1124 px; 回到預設 returns to 768 px. A manually dragged split still wins; double-click on the splitter (or 回到預設) restores automatic |
| Drawing fit control | fit-page 691×894 in a 1259 px pane (569 px slack) → fit-width 1244×1610, slack 15 px, top not clipped, scrolls vertically; the choice survives a page change and is persisted |
| R-8 during a lookup | activity bar animating, button disabled and labelled 查詢中…, heading names the number, step 1 marked done / step 2 active / step 3 pending, elapsed counter ticking (0.6 s sample) |
| R-8 during EPO enrichment | chip observed passing through **`EPO 取得中…`** → `EPO 16圖/25頁`; PDF chip shows `原文件 PDF · EPO 確認中…` until the inquiry answers |
| R-8 on the 25-page original | clicking it raises: "EPO 正在逐頁取回 **25** 頁並合併，約 **20** 秒…" |
| R-8 per drawing page | the overlay says `向 EPO 取第 n 頁…` rather than a bare spinner |
| R-10 at 2560×1440 | auto 18 px / 76 ch → column **681 px** (was 544), panes 801 / 1752 |
| R-10 at 1920×1080 | auto 17 px / 72 ch → column **609 px**, text pane 726 px |
| R-10 reader override | setting measure to 60 ch pins it: unchanged across a resize; 回到預設 clears the stored values and returns to the automatic pair. The panel states which mode is active |
| R-10 instrument note | CDP viewport emulation does **not** dispatch `resize` to the page, so the re-pick was verified by dispatching the event (17/72 → 18/76 at 2560). A real window drag stays a human check |

Human-eye (must be confirmed in the real browser; see the delivery checklist):

5. `US20250383260A1` — Stage 0 showed no drawings and no PDF. Now shows EPO drawing sheets and offers the original PDF.
6. Long description (`US7479949B2`, 1074 figrefs) reads as paragraphs with numbers in the gutter; scrolling is not janky.
7. Reading controls change size/leading/measure live and survive a reload.

---

### 5.3 S1-C applicant search (2026-08-26, third round)

`tools/check_search.py` — **16/16**, controls in both directions plus one calibration:

| Check | Result |
|---|---|
| CQL injection | `Corning" OR pa="Apple` → `pa="Corning OR pa= Apple" AND pn=US`: still ONE quoted term. The matcher is calibrated — it rejects a hand-built two-clause query, so the check can fail |
| Positive control | `pa="Taiwan Semiconductor" AND pn=US` → 30,569 hits, 25 rows, **countries = {US}**, 25/25 marked openable |
| Truncation | total 30,569, reachable 2,000, `depth_capped=true` — stated in the header, not silently cut |
| BR-8 broad name | `pa="Corning"` → 7 distinct applicant spellings, each carrying its as-filed form |
| BR-8 refine | clicking a variant → `pa="CORNING RESEARCH & DEVELOPMENT CORPORATION" AND pn=US` → 701 hits, variants collapse to 1. `&` survives into CQL |
| Negative control | nonsense applicant → `available=true, total=0` with a reason naming the field — never an error |
| BR-1 dispatch | numbers → card, names → search, `in=` prefix honoured, 台積電 routed as a name; 0 misrouted |
| Browser flow | list (50 rows, 7 variants) → row → OPS-only card (EPO 14 sheets / 37 pages, drawing loaded at 2550 px, description pane states the reason) → back chip returns to the list in ~1 s **without another OPS call** |
| Worldwide toggle | unchecking 只看美國案 drops `pn=US`; rows become WO/EP/US and 37 of 50 are marked un-openable with an Espacenet link each |
| Page cost | 50 rows ≈ 286 KB; stated in the header so the reader can see what a search costs |

### 5.4 S1-D EP jurisdiction (2026-08-26, fourth round)

| Check | Result |
|---|---|
| `tools/check_reading.py` | **20/20** documents (four EP samples added), description coverage 0.990–1.000, claim coverage 1.000 |
| EP extraction, before → after | EP3000000A1 (translated from German) 0 → **72 blocks / 22 claims**; EP4000000A1 (WIPO OCR) 0 → **15 claims**; EP1000000A1 and EP2000000A1 unchanged |
| Untranslated original dropped | EP3000000A1: 72 source blocks, 50,736 → 23,902 chars (**53 % dropped**), measured by the checker rather than asserted |
| `tools/check_search.py` | **20/20**, including a nine-case both-directions control for EN/DE/FR claim dependency |
| Claim dependency, before → after | EP4794149A1 (German): 9 of 9 claims reported independent → **1 independent, 8 dependent**; EP4793850A1: 8 → 1 independent (`according to any of the preceding claims` now caught) |
| EP card via Google | EP1000000A1 → 28 blocks / 11 claims / 6 images; EP2000000A1 → 129 blocks / 15 claims / 8 images; `EP 1 000 000 B1` resolves to EP1000000B1 |
| EP card via OPS (Google missing) | EP4793850A1 (published 7 days earlier) → **192 blocks / 11 claims**, full text from OPS; EP4794149A1 → 67 blocks / 9 claims, `全文語言 DE` stated on the card |
| EP images | OPS drawings work for EP: EP1000000A1 6 sheets / 12-page original; EP4794149A1 3 sheets, sheet loaded at 2480 px |
| Search scope | `pn=EP` → 75,230 Siemens EP publications, all EP; `(pn=US OR pn=EP)` → 105,554 mixed. Four scope chips replace the US-only checkbox |
| BR-8 across subsidiaries | Siemens EP page 1 exposes SIEMENS AG [DE] 17, SIEMENS MOBILITY 8, SIEMENS HEALTHINEERS 6, SIEMENS GAMESA [DK] 5, SIEMENS RAIL AUTOMATION [ES] 3 — the corporate group, spelled out |

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
