# PatentsGrabber

整合式專利閱讀工具。輸入一個專利號碼，得到一張把「可複製全文 ＋ 圖式 ＋ 原文件 ＋ 同族分類」放在同一屏的專利卡。

**目前狀態：Stage 1（US ＋ EP）** —— 文字優先來自 Google Patents 單件頁，
**圖式與原文件 PDF 來自 EPO OPS**（需金鑰；沒有金鑰時自動退回 Stage 0 行為並說明原因）。
Google 沒有的件改用 OPS：**EP 案連全文一起補上，美國案只有書目**（OPS 全文不涵蓋美國）。

## 啟動

```powershell
python run.py
```

或直接雙擊 `run.bat`（會自動開瀏覽器）。服務位於 <http://127.0.0.1:8000>。

Stage 0 不需要任何 API 金鑰或註冊。

## 金鑰設定（Stage 1 才需要）

**金鑰只放 `.env`，不進版控，也不要貼進任何其他檔案、commit 訊息或對話。**

```powershell
copy .env.example .env
```

用文字編輯器打開 `.env`，把 EPO Developer Portal 上該 app 的 Consumer Key 與
Consumer Secret 填進去，存檔，然後驗證：

```powershell
python tools/verify_ops.py
```

這支腳本不做「hello world」——它逐項測試 Stage 0 做不到的能力（原文件 PDF、
圖式、全文、同族、法律狀態、申請人檢索），所以綠燈就是 Stage 1 可行的直接證據，
紅燈會指出是哪一項不可得。它從不印出金鑰值。

### 為什麼可以放心

- `.gitignore` 與 `git init` 在**任何金鑰檔存在之前**就建立好，所以金鑰從未有可被提交的時間窗。
- `.githooks/pre-commit` 每次提交都會掃描**即將進入提交的內容**；掃到疑似憑證就中止提交。
- 這個防護做過雙向校正：植入假憑證會被擋下（`git commit` 回非零、不產生提交），正常內容放行。
- 隨時可自行稽核全部已追蹤檔案：

```powershell
python tools/check_secrets.py --tracked
```

若金鑰**疑似外洩**：除了到 EPO Developer Portal 重新產生，依 OPS 條款 5.5 你**還必須通知 EPO**（patentdata@epo.org）。這是契約義務，不只是好習慣。

### 若查詢回 404

先跑診斷，它會把 OPS 自己的錯誤碼原文印出來，並用 EPO 官方範例號碼 `EP1000000`
分辨「程式錯」與「這件文件不在 OPS」：

```powershell
python tools/diag_ops.py
```

### 使用條款

接上 OPS 後有一條邊界從偏好變成契約義務：**不得把 OPS 資料「本身」提供給公眾**
（條款 3.2，違反依 8.3 可被立即終止）。本機自用不受影響，架成公開網站則會踩線。
完整分析見 `docs/03-ops-terms-compliance.md`。

## 公司名檢索（Stage 1-C）

同一個輸入框，**不必先選要查哪一種**：看起來像號碼就開專利卡，否則就當成名字送去 EPO OPS 的欄位檢索。

```
Corning                 → 申請人檢索
in=Larry Page           → 發明人檢索
ta=fiber array          → 標題／摘要檢索
```

結果列表每列可以直接點進去讀（就是同一張專利卡），並且**把不可靠的地方全部講出來**：

- **總數與可取數分開講**：`Corning` 有 22,592 件，但 OPS 只讓翻前 **2,000** 筆，介面直接寫「其餘 20,592 件無法翻到——請加條件收斂」。
- **申請人名稱變體攤開**（BR-8）：`Corning` 的 50 筆裡出現 7 種寫法，其中 `OWENS CORNING INTELLECTUAL CAPITAL`（完全不同公司）、寧波一家康寧醫院、`UNIV KENT STATE OHIO` 都混在裡面。點任一個名稱即可收斂到那個確切寫法。清單只涵蓋本頁，介面明說。
- **預設只看美國案**（CQL 加 `pn=US`，OPS 端過濾，非畫面過濾）。關掉之後 WO/EP/CN 案會出現，但會標明本工具讀不了並附 Espacenet 連結。
- **本次花費多少配額**直接寫在標頭（50 筆約 286 KB）。

### Google Patents 比 OPS 慢好幾個月

實測 2026-06 到 2026-08 的 24 件美國公開案，**Google Patents 一件都沒有**。而 OPS 檢索是新到舊排序，所以公司檢索的第一頁正好全是 Google 還沒收錄的。

因此當 Google 沒有某件時，專利卡改由 **OPS 書目**組成：標題、申請人、發明人、日期、CPC、摘要照常顯示，圖式與原文件掃描照常可看，**只有全文明說沒有**（OPS 的全文不涵蓋美國案）。這張卡是暫時性的——下次再查會重試 Google，收錄後自動換成完整版。

## 歐洲案（Stage 1-D）

號碼加 `EP` 就走歐洲案；純數字仍視為美國案。

```
EP1000000A1     EP 1 000 000 B1     EP3000000
```

- **文字**：Google Patents 有 EP 全文，優先用它（免配額）。它沒有的件（例如上週才公開的）改由 **OPS 全文**提供——這是 EP 跟 US 的關鍵差別：**OPS 的全文涵蓋 EP，不涵蓋美國**。
- **語言**：EPO 只以**申請語言**公開說明書，所以德商的 EP 案就是德文。卡片會標「全文語言 DE」並說明原因，不會默默丟一片德文給你（本工具不內建翻譯）。
- **請求項相依判定跨三種語言**：`nach Anspruch 1`、`selon la revendication 1`、`according to any of the preceding claims` 都算附屬項。修正前，一件德文 EP 案的 9 條請求項全被標成獨立項。
- **檢索**可選範圍：只看美國案／只看歐洲案／美國＋歐洲／全世界（在 OPS 端過濾）。

## 可以輸入什麼

號碼寬容輸入 —— 下列格式都會解析到同一件：

```
US20250383260A1
US 2025/0383260 A1
2025/0383260
US2025383260A1        (Espacenet 格式)
6,285,999             (無 kind code，系統自動試 B2 → B1 → A)
5960411
```

申請號（`18/123,456`）目前**不支援**，但會明確告知原因，不會靜默失敗。

## 畫面怎麼分區

畫面只有兩層，**主層佔滿視窗、次層收進浮層**：

- **主層**：左邊文字（摘要／請求項／說明書，分頁切換），右邊圖式檢視器。中間那條線**可以拖曳**調整左右比例，雙擊還原 55/45，比例會記住。
- **次層**：標頭列的一排 chip，每個都帶數字（`分類 5`、`引證 73/232`）。點開才展開內容——書目、分類、同族、相似文件、引證、法律事件、欄位來源與缺漏、原站連結。按 Esc 或點旁邊關閉。

主層內容：

- 標題、摘要、**說明書全文**（純文字，可複製、可搜尋）
- **請求項逐項拆解**，獨立項置頂高亮、附屬項摺疊
- **圖式檢視器**：縮圖列 ＋ 大圖 ＋ 點擊放大，`←` `→` 可翻頁

## 為人類閱讀而做的事（其他平台沒有特別做的部分）

專利說明書是 2 萬到 44 萬字元的連續文字。Google Patents、Espacenet、PATENTSCOPE
都把它塞進一欄、寬度隨視窗、行距固定。本工具把來源**已經標好、但被丟掉**的結構撿回來：

- **分段**：標題、段落、清單、表格各自成塊；`[0042]` 段號掛在左側頁邊，不擠進句子裡
- **請求項是一棵樹**：前言一段，每個限制條件（limitation）各一行縮排；
  `claim 1` 這種交叉引用可以點，直接跳到該項並閃一下
- **`FIG. 3` 可以點**：點了右邊圖式就翻到該張。對應關係是**推測**（一張圖頁可能含多個 FIG.），
  介面會明說，不會假裝精準
- **章節跳轉**：說明書標題自動變成目錄
- **閱讀設定**（會記住）：字級、行距、行寬、段距、段號顯示、襯線字體、元件編號高亮
- 這些全部來自原始標記，**沒有任何 AI 改寫、摘要或翻譯**——結構拿不到就不顯示

## EPO OPS 補上的兩個洞

| Stage 0 的限制 | 現在 |
|---|---|
| 最新公開案圖檔 403、破圖 | EPO 300 dpi 圖式頁（`US20250383260A1` → 14 頁） |
| 最新公開案沒有 PDF | EPO 逐頁取回後合併成一份 PDF（25 頁） |
| 2000 年以前老案完全沒有圖 | 改用 EPO 的原文件掃描（`US4237224A` → 10 頁），並標明是原文件而非獨立圖式 |
| Google 有圖時 | 仍用 Google（免費、即時），另提供「改用 EPO 高解析」切換 |

圖式頁**逐頁**取用（縮圖列改成頁碼列），一次查詢只花一個影像清單呼叫，
看過的頁面存進 `var/ops-cache/`，重看不再計入配額。
另有 **INPADOC 同族與法律事件** chip，點開才向 EPO 取。

其他：

- 每個欄位標註來源；**拿不到的欄位會說明原因，不會留空白，也不會出現破圖**
- `?q=US6285999B1` 深連結，專利卡可以加書籤或分享；加 `&tab=claims` 可直接連到某一分頁
- 查過的每一件都會存進本地 `var/library.sqlite3`（個人專利庫，非快取），重查不會再打網路

## 已知限制（實測確認，非缺陷）

- **OPS 的全文不涵蓋美國案**（`CLIENT.InvalidCountryCode`，EP 對照組正常）。所以美國案的
  文字只能來自 Google Patents，圖式與原文件只能來自 OPS——兩個來源缺一不可。
- ~~約 2000 年以前的老專利沒有可取用的圖檔~~ → 已由 EPO 原文件掃描補上。
- ~~最新公開案沒有 PDF、圖檔 403~~ → 已由 EPO images / fullimage 補上。
- 只做美國案。EP/TW/CN/JP/KR 尚未支援。
- 只能用號碼查，**還不能用公司名檢索**——Google Patents 的搜尋被其 `robots.txt` 排除；
  申請人檢索走 EPO OPS 的 CQL，已驗證可行（`pa="Taiwan Semiconductor"` → 36,829 筆），
  但介面尚未做（下一輪）。
- 合併 25 頁的 EPO 原文件 PDF 約需 10–20 秒（逐頁取回），只在第一次。

## 開發者工具

```powershell
python tools/check_reading.py             # 分段結構是否只加結構、沒吃掉字（含正／負控與儀器校正）
python tools/check_search.py              # 檢索是否誠實（截斷宣告、名稱變體、CQL 注入、正負對照）
python tools/probe_coverage.py            # 覆蓋率探針（用本地快取的 HTML）
python tools/probe_coverage.py --refresh  # 重新抓取所有樣本
python tools/smoke_service.py             # 端到端煙霧測試
python tools/verify_ops.py                # EPO OPS 能力驗證（需金鑰）
python tools/diag_images.py               # 圖檔網址形態診斷
python tools/shoot.py US8046721B2 out.png --tab description   # 用本機 Chrome 截圖（可加 --dark）
```

`check_reading.py` 會刻意餵自己一份「把段落標記拿掉」的頁面，**那一份必須不通過**——
一個永遠不會失敗的檢查等於沒有檢查。

## 文件

- `docs/01-concept-note.md` —— 概念層：痛點、邊界、業務規則、資料源勘查
- `docs/02-stage0-findings.md` —— Stage 0 實測發現與交付紀錄
- `docs/03-ops-terms-compliance.md` —— OPS 使用條款分析（3.2 的公開散布禁令）
- `docs/04-ops-verification-results.md` —— 金鑰能力驗證結果
- `docs/05-stage1-spec.md` —— **Stage 1 工作規格**：規則彙整、已定案事實、工作切分、閱讀優化需求、驗收
- `docs/archive/` —— 已被取代的文件版本（不刪除）

## 資料來源與界線

本工具只抓取 `patents.google.com/patent/{number}` 這種**單件文件頁**，該路徑被 Google 的 `robots.txt` 明確允許（`Disallow: /*` ＋ `Allow: /patent/`）。**不會、也不應**用來自動化其搜尋端點。
