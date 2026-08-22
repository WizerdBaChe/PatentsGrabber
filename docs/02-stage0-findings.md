# Stage 0 實測發現與交付紀錄

- 日期：2026-08-20（**v2**，含一項數據更正與 UI 重構；v1 存於 `docs/archive/02-stage0-findings.v1-2026-08-20.md`）
- 範圍：US only、零金鑰、Google Patents 單一來源

> **v1 更正**：v1 記載「圖式 9/10」。該數字量的是「頁面標記中存在圖檔 URL」，**不是**「圖檔真的載得到」。
> 實測後正確數字為 **8/10 可實際取得**。成因與修正見 §2.3。這是一次量錯對象的錯誤，不是資料變動。
- 對應文件：`docs/01-concept-note.md`（v2）§8.2 Stage 0
- 原始證據：`var/probe/coverage.md`、`var/probe/coverage.json`、`var/raw/*.html`（11 份原始頁面）

---

## 1. 覆蓋率實測（n=10 真實美國專利 + 1 負控）

樣本刻意跨年代與型態：1980 / 1998 / 2001 / 2009 / 2011 / 2018 / 2021 / 2023 / 2020 / 2025，公告案與公開案各半。

**結論：18 個欄位中，16 個在 10/10 全部取得。** 完整矩陣見 `var/probe/coverage.md`。

| 欄位 | 取得率 | 備註 |
|---|---|---|
| 標題、摘要、說明書全文、請求項 | **10/10** | 說明書實測 17K–443K 字元，皆為可複製純文字 |
| 分類、法律狀態、法律事件、三個日期、申請人、發明人 | **10/10** | |
| 同族、相似文件、後引 (cited by) | **10/10** | |
| 圖式（**實際載得到**） | **8/10** | 見 §2.1、§2.3 |
| 原文件 PDF | 9/10 | 見 §2.2 |
| 前引 (backward) | 9/10 | US20200000001A1 頁面未列前案，屬真實資料缺漏 |

### 1.1 儀器校正（為什麼這組數字可信）

矩陣本身不足以當證據，必須先證明量測工具會說「有」也會說「沒有」：

- **正控 (positive control)**：US6285999B1（已公告專利，請求項必然存在）→ 取得 8,965 字元請求項。**通過**。
- **負控 (negative control)**：US99999999B2（不存在的號碼）→ 明確失敗並回報試過的候選，**沒有**回傳一張空卡。**通過**。
- **系統性失效偵測**：探針會自動標記「在全部文件上都缺席」的欄位為疑似選擇器錯誤。首輪確實抓到三個（見 §3），修正後歸零。

## 2. 兩個直接影響 UI 的真實限制

### 2.1 約 2000 年以前的老專利沒有可取用的圖檔

US4237224A（1980）經原始 HTML 逐一確認：`itemprop="full"` 0 個、`<img patentimages>` 0 個、整份頁面只有 2 處 patentimages 連結且都指向 PDF。

> **這不是 parser 缺陷，是 Google Patents 對該年代文件的實際狀態。**

**設計後果**：圖式檢視器對老案必須降級為「開啟 PDF」，且要明說原因，不能只給一個空框。已實作於 `service.ABSENCE_REASON['images']`。

### 2.2 最新公開案沒有 PDF

US20250383260A1（2025-12-18 公開，即使用者原始範例）：全頁 `.pdf` 命中 0 次、無 `citation_pdf_url`。對照 US20230123456A1 則有正常 PDF 連結。

> **最新公開案在 Google Patents 上還沒有 PDF。**

**設計後果**：這正是 Stage 1 接上 EPO OPS 的**具體價值定位**——OPS 的 `fullimage` 逐頁取得可以補上這個洞。在此之前，UI 顯示「此件無 PDF」警示徽章並說明原因。

### 2.3 最新公開案的圖檔網址存在但回 403（v1 漏掉的一項）

用截圖做外觀確認時發現：US20250383260A1 的圖片全部是破圖。DOM 層的驗證只數了 `<img>` 元素個數，**沒有驗證它們載入成功**——`naturalWidth` 為 0 才是真話。

逐一探測 `patentimages.storage.googleapis.com` 的 URL 形態後，成因明確：

| 文件 | `itemprop="full"` 的路徑形態 | 實測 |
|---|---|---|
| US6285999B1 / US8046721B2 / US20230123456A1 / US20200000001A1 | **雜湊路徑** `/de/9d/0c/<hash>/…` | **200 OK**，真實 PNG |
| **US20250383260A1**（2025-12-18 公開） | **無雜湊** `/US20250383260A1/…` | **403 Forbidden** |

`/thumbnails/…` 路徑同樣 403；加上 `Referer: https://patents.google.com/` 也無效。

> **最新公開案的圖檔尚未對外開放**，Google 在標記中仍列出網址。與 §2.2 沒有 PDF 是同一個成因。

**修正**（三處，缺一不可）：
1. **轉接器**：`service._verify_images()` 對第一張圖發一次 HEAD，非 200 就把整組標記為不可用並記錄實際狀態碼（同一件的圖都在同一個 bucket 路徑下，驗一張即可代表全組）。
2. **UI**：圖式區顯示真實原因與「頁面宣告 15 張，實測無法取得」；每個 `<img>` 加 `onerror`，**永遠不出現破圖圖示**。
3. **探針**：覆蓋矩陣改為量「可取得」而非「URL 存在」，並新增 `images_fetchable` 欄記錄狀態碼。

**教訓**：`querySelectorAll('img').length` 不是圖片載入成功的證據。DOM 結構驗證與外觀驗證是兩件事，前者過不代表後者過——這次是靠實際截圖才抓到。

## 3. 首輪抓到並修正的三個 parser 缺陷

三者都表現為「全部 10 件都是 0」，若不做系統性失效偵測，會被誤讀成「Google Patents 沒有這些資料」而寫進設計文件。

| 欄位 | 錯誤原因 | 修正 |
|---|---|---|
| `description` | 用了不限標籤的 `find(itemprop="description")`，被每個分類列裡的 `<span itemprop="description">` 搶先命中，只拿到 20–60 字元 | 限定 `section[itemprop="description"]` |
| `family` | 沿用 `tr[itemprop="alsoPublishedAs"]`，但此頁面根本沒有這個 itemprop | 改為 `section[itemprop="family"] tr[itemprop="applications"]` |
| `inventors` | 沿用 `meta[name="citation_inventor"]`，此頁面不存在該 meta | 改為 `dd[itemprop="inventor"]` |

**教訓**：一個欄位在所有樣本上都缺席，幾乎必然是量測工具壞了，不是十件互不相干的專利剛好都沒有。此判斷已寫成探針的自動檢查，不依賴記憶。

## 4. 額外發現的可用欄位

`similarDocuments`（Google 自己算出的相似文件）結構乾淨且每件都有，實測 7–25 筆。**對「確認研究方向」這個用途特別有價值**，已納入專利卡。這是原設計未預期的收穫。

## 5. 交付內容

| 路徑 | 說明 |
|---|---|
| `run.bat` / `run.py` | 一鍵啟動，開瀏覽器到 `http://127.0.0.1:8000` |
| `src/patentsgrabber/numbers.py` | 號碼正規化（BR-1）|
| `src/patentsgrabber/sources/google_patents.py` | Google Patents 轉接器，每欄位記錄命中的選擇器 |
| `src/patentsgrabber/store.py` | 本地 SQLite 個人專利庫（BR-5，非快取）|
| `src/patentsgrabber/service.py` | 編排、缺漏理由、長列表上限宣告 |
| `src/patentsgrabber/web/index.html` | 專利卡 UI（圖式檢視器優先）|
| `tools/probe_coverage.py` | 覆蓋率探針（含正控／負控／系統性失效偵測／圖檔可取得性）|
| `tools/smoke_service.py` | 端到端煙霧測試 |
| `tools/shoot.py` | 用本機已安裝的 Chrome 做行程外截圖（不需下載 Playwright）|
| `tools/diag_images.py` | 圖檔網址形態診斷 |
| `tools/inspect_markup.py` | 頁面標記結構檢視（改 parser 時用）|

## 5.2 UI 重構（依使用者回饋：「為了塞滿而塞滿」）

初版把六個區塊都做成同等份量的全版面卡片，導致三個 460px 的擠壓捲動框並列、底部四張表格互相爭空間。重構後：

| 層級 | 內容 | 載體 |
|---|---|---|
| **主層** | 圖式檢視器、文字區（摘要／請求項／說明書） | 佔滿視窗，可拖拉分割（預設文 55 / 圖 45，比例記憶於 localStorage）|
| **次層** | 書目、分類、同族、相似文件、引證、法律事件、來源與缺漏、原站連結 | 標頭列的**帶計數 chip → 原生 popover** |

- 全版面卡片數：**6 → 0**；三個擠壓捲動框 → 一個滿高閱讀區＋分頁切換。
- 用瀏覽器原生 `popover` ＋ CSS anchor positioning（Chrome 148 實測支援），無定位函式庫；非 Chrome 環境降級為置中浮層。
- chip 上帶計數（`分類 5`、`引證 73/232`），資訊密度留在一行；計數同時寫進 `aria-label`。
- BR-3／BR-4 的揭露義務不變，只是換載體：欄位來源與缺漏理由收進「來源」popover，有缺漏時 chip 顯示 `⚠n`。
- 新增 `?q=<號碼>` 深連結，專利卡可被書籤／分享。

**實測**：popover `type=auto`、Esc 可關、互斥、真實點擊 light-dismiss 皆通過；294 張圖的極端案例縮圖列水平捲動、`loading=lazy`、頁面兩軸皆不溢出。

### 5.1 Stage 0 的技術取捨（決策紀錄）

**未使用 React／Node。** Concept Note 的目標架構是 FastAPI + React，但 Stage 0 改用 FastAPI 直接吐一頁靜態 HTML。理由：Stage 0 的目的是讓使用者**今天就摸到真實成品**，而一個建置步驟會擋在中間。React 留到 Stage 1——並排比對與標記功能會讓 UI 真正變成有狀態，屆時才划算。此為分階段選擇，不是架構反轉。

## 6. 尚未驗證的事

| 項目 | 狀態 |
|---|---|
| **外觀（版面、配色、重疊、裁切）** | **部分驗證。** 已用 `tools/shoot.py` 取得 1440×900 淺色截圖三張（`var/shots/`）並確認版面成立；**深色模式、其他視窗尺寸、實際字型渲染皆未驗證**，須由使用者在真實環境確認。 |
| 說明書極長（443K 字元）時的捲動流暢度 | 未量測 |
| 連續大量查詢時 Google Patents 的節流行為 | 未測；目前每次查詢間無延遲，屬單人手動使用強度 |
| 非 US 案 | 超出 Stage 0 範圍 |
