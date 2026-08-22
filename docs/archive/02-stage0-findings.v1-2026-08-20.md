# Stage 0 實測發現與交付紀錄

- 日期：2026-08-20
- 範圍：US only、零金鑰、Google Patents 單一來源
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
| 圖式 | 9/10 | 見 §2.1 |
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
| `tools/probe_coverage.py` | 覆蓋率探針（含正控／負控／系統性失效偵測）|
| `tools/smoke_service.py` | 端到端煙霧測試 |

### 5.1 Stage 0 的技術取捨（決策紀錄）

**未使用 React／Node。** Concept Note 的目標架構是 FastAPI + React，但 Stage 0 改用 FastAPI 直接吐一頁靜態 HTML。理由：Stage 0 的目的是讓使用者**今天就摸到真實成品**，而一個建置步驟會擋在中間。React 留到 Stage 1——並排比對與標記功能會讓 UI 真正變成有狀態，屆時才划算。此為分階段選擇，不是架構反轉。

## 6. 尚未驗證的事

| 項目 | 狀態 |
|---|---|
| **外觀（版面、配色、重疊、裁切）** | **未驗證。** 結構已用 DOM 讀取確認，但畫面對不對必須由使用者在真實瀏覽器確認。測試綠 ≠ 畫面對。 |
| 說明書極長（443K 字元）時的捲動流暢度 | 未量測 |
| 連續大量查詢時 Google Patents 的節流行為 | 未測；目前每次查詢間無延遲，屬單人手動使用強度 |
| 非 US 案 | 超出 Stage 0 範圍 |
