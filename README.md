# PatentsGrabber

整合式專利閱讀工具。輸入一個專利號碼，得到一張把「可複製全文 ＋ 圖式 ＋ 原文件 ＋ 同族分類」放在同一屏的專利卡。

**目前狀態：Stage 1（US ＋ EP）** —— 文字優先來自 Google Patents 單件頁，
**圖式與原文件 PDF 來自 EPO OPS**（需金鑰；沒有金鑰時自動退回 Stage 0 行為並說明原因）。
Google 沒有的件改用 OPS：**EP 案連全文一起補上，美國案只有書目**（OPS 全文不涵蓋美國）。

## 啟動

### 拿到打包版的人（不需要 Python）

下載 [Releases](../../releases) 裡的 `PatentsGrabber-<版本>-win64.zip`，
解壓縮到任何你有寫入權限的地方，雙擊 **`PatentsGrabber.exe`**。

- 瀏覽器會自己打開。**關掉那個黑色文字視窗就等於關掉程式**。
- 重複雙擊不會開出第二個伺服器——第二次只會把你帶到已經在跑的那一份。
- 查過的資料與圖式快取放在 `%LOCALAPPDATA%\PatentsGrabber`，**不在程式資料夾裡**，
  所以直接用新版蓋掉整個程式資料夾不會弄丟任何東西。
- 覺得哪裡不對，先跑一次自我檢查，它會逐項真的去 import，不是查表：

```powershell
.\PatentsGrabber.exe --selftest
```

### 從原始碼跑

```powershell
python run.py
```

服務位於 <http://127.0.0.1:8000>。沒有金鑰時仍可讀 Google Patents 有收錄的美國案。

## 金鑰設定（Stage 1 才需要）

**在畫面右上角按「設定」。** 貼上 EPO Developer Portal 上該 app 的
Consumer Key 與 Consumer Secret，可以先按「測試連線」確認，再按「儲存」。
立即生效，不用重開程式。

金鑰去哪裡了：

| 執行方式 | 設定檔位置 |
|---|---|
| 打包版 | `%LOCALAPPDATA%\PatentsGrabber\settings.env` |
| 原始碼 | 專案根目錄的 `.env`（git 忽略） |

面板本身**永遠不會顯示金鑰**，只顯示長度與末四碼——夠你分辨兩把金鑰不同，
不夠拿去用。任何一個 API 回應裡也不會有金鑰值，這件事由
`tools/check_settings.py` 每次跑 gate 時斷言，而且那支檢查器自己先被餵過
一個「一定要抓到」的字串。

「OPS 位址」只接受 `epo.org` 底下的主機：金鑰是以 HTTP Basic 標頭送到那個位址的，
所以它不能指向別的地方。

也可以直接用文字編輯器改上表的檔案；程式下次打開設定面板時會發現、自動改用新值，
並在畫面上寫一行「設定檔在外部被改過」——不會默默換掉。

改完之後驗證：

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
- **圖式檢視器**：縮圖列 ＋ 大圖 ＋ 點擊放大，`←` `→` 可翻頁，
  **`⟲` `⟳` 或 `[` `]` 左右轉 90°**

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

### 圖式：轉得動，而且轉了還看得完整

專利圖不論本身是橫是直，都印在**直式圖頁**上，所以橫式的圖到手時是躺著的。
`⟲` `⟳`（或鍵盤 `[` `]`）左右各轉 90°：

- 轉過之後**整張仍然完整在框內**，不是把圖轉出去再讓你捲。放大時捲軸也捲得到左上角。
- 角度**跟著同一件專利的翻頁保留**（同一件的圖頁方向通常一致），**換一件專利就歸零**。
- 「頁面／寬度」兩種填滿方式在轉過之後一樣有效。

這一段的幾何由 `tools/check_figures.py` 在真實 Chrome 裡量過——
包含「轉四次要回到一模一樣的位置」與「放大後角落必須捲得到」。

### EPO 的圖為什麼是一頁一頁進來

因為 OPS **就是一次只給一頁**（`fullimage` / `drawing` 每頁一個請求），
這是來源的形狀，不是這支程式慢。所以：

- 圖式列在 EPO 模式下是**頁碼**而不是縮圖——顯示 25 張縮圖等於默默送出 25 個計費請求。
- **已經在本機的頁碼底下有一條綠色底線**，點它不花配額；沒有底線的點下去會向 EPO 要一次。
- 等待時會寫出「向 EPO 取第 N 頁　3.2 秒」——**連等了多久都寫出來**，
  超過 6 秒還會補一句它可能正被 OPS 自己限流。
- 看過的頁永久留在 `var/ops-cache/`，下次（甚至沒有金鑰時）直接由本機取出。

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

**一個入口：**

```powershell
python tools/run_gates.py          # 本機層（不連網、不花配額）
python tools/run_gates.py --net    # ＋會抓 Google Patents 的那幾關
python tools/run_gates.py --all    # ＋會花 EPO OPS 配額的那兩關
```

摘要會在每個分數旁邊寫出**它的單位**，因為那些分數不是同一種東西
（`check_reading` 一份文件一行，其餘一項檢查一行）。

個別執行：

```powershell
python tools/check_reading.py             # 分段結構是否只加結構、沒吃掉字（含正／負控與儀器校正）
python tools/check_layout.py              # R-9／R-10：版面隨視窗（1920 與 2560，走 DevTools）
python tools/check_figures.py             # 圖式面板幾何：旋轉、填滿方式、放大後的可捲範圍
python tools/check_settings.py            # 設定往返、金鑰不出現在任何回應、本機守門
python tools/check_secrets.py --tracked   # 全部已追蹤檔案的憑證掃描
python tools/check_diagrams.py            # 畫出來的狀態圖與模型一致（會抓斷掉的箭頭定義）
python tools/check_search.py              # 檢索是否誠實（截斷宣告、名稱變體、CQL 注入、正負對照）
python tools/smoke_service.py             # 端到端煙霧測試
python tools/verify_ops.py                # EPO OPS 能力驗證（需金鑰）
python tools/probe_coverage.py            # 覆蓋率探針（用本地快取的 HTML）
python tools/diag_images.py               # 圖檔網址形態診斷
python tools/shoot.py US8046721B2 out.png --tab description   # 用本機 Chrome 截圖（可加 --dark）
python tools/shoot_ui.py --url http://127.0.0.1:8000/?q=US6285999B1 `
    --js "figRotate(1)" --out out.png                          # 先操作再截圖
python tools/make_state_diagrams.py       # 重畫 docs/diagrams/state-snapshots.html
```

`check_reading.py` 會刻意餵自己一份「把段落標記拿掉」的頁面，**那一份必須不通過**——
一個永遠不會失敗的檢查等於沒有檢查。同樣的正對照現在每一支新 gate 都有：
洩漏偵測器先被餵一個已知會洩漏的字串，守門員的每個「必須擋」旁邊都有一個「必須放行」，
幾何檢查器先被餵一條離格、穿過節點、沒錨定的邊。

## 打包成可交付的檔案

```powershell
powershell -File packaging\build.ps1 -Clean
```

會依序做：跑完本機層 gate → PyInstaller（onedir）→ **啟動打包出來的 exe 並實際操作它**
→ 壓成 `release\PatentsGrabber-<版本>-win64.zip` 並附 SHA256。

任何一步失敗就停，不會產出一包「檔案存在但起不來」的東西——
第一次打包正是這樣被擋下來的（相對 import 在 PyInstaller 的 `__main__` 脈絡下必死，
而 PyInstaller 回報成功）。

用 onedir 而不是 onefile 是刻意的：onefile 每次啟動都把整包解到
`%TEMP%\_MEIxxxxxx`，而這個程式的關法就是按視窗的 X，那不是正常結束，
解出來的目錄會累積而且沒有人能安全地清（`_MEI*` 是所有 PyInstaller onefile 程式共用的名字）。

## 文件

- `docs/01-concept-note.md` —— 概念層：痛點、邊界、業務規則、資料源勘查
- `docs/02-stage0-findings.md` —— Stage 0 實測發現與交付紀錄
- `docs/03-ops-terms-compliance.md` —— OPS 使用條款分析（3.2 的公開散布禁令）
- `docs/04-ops-verification-results.md` —— 金鑰能力驗證結果
- `docs/05-stage1-spec.md` —— **Stage 1 工作規格**：規則彙整、已定案事實、工作切分、閱讀優化需求、驗收
- `docs/06-stage1-review.md` —— 哪一條規則由哪一關守著，以及那些關卡在哪裡是瞎的
- `docs/07-delivery-round.md` —— 2026-08-29 交付輪的邊界契約
- `docs/09-state-models.md` —— **狀態模型正本**：設定面板、圖式面板、守門判定表、啟動器，含缺口報告
- `docs/diagrams/state-snapshots.html` —— 上面那份的圖形投影（由 `tools/make_state_diagrams.py` 產生）
- `docs/10-review-2026-08-29.md` —— 該輪深度審查（＋`.findings.json`／`.coverage.json`）
- `docs/11-uat-2026-08-29.md` —— **人工驗收清單**：機器看不到的 26 項
- `docs/archive/` —— 已被取代的文件版本（不刪除）

## 資料來源與界線

本工具只抓取 `patents.google.com/patent/{number}` 這種**單件文件頁**，該路徑被 Google 的 `robots.txt` 明確允許（`Disallow: /*` ＋ `Allow: /patent/`）。**不會、也不應**用來自動化其搜尋端點。
