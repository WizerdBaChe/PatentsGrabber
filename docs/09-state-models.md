# 狀態模型（structural model）與缺口報告 — 2026-08-29

這一份是**可被 grep 的正本**；`docs/diagrams/state-snapshots.html` 是它的投影。
圖改了而這裡沒改，以這裡為準。

**資料來源（每一格都可追溯，沒有一格是憑印象填的）**

| 檢視 | 來源檔 |
|---|---|
| A 設定面板 | `src/patentsgrabber/web/index.html`（`renderSettings`／`settingsPost`／`wireSettings`）、`src/patentsgrabber/app.py` §settings API、`src/patentsgrabber/config.py` |
| B 圖式面板 | `index.html`（`FIGS`／`renderFigures`／`figShow`／`figLayout`／`figRotate`／`wireFigures`） |
| C 本機守門 | `app.py` `_guard`／`_host_is_local` |
| D 啟動器 | `src/patentsgrabber/launcher.py` |

判讀規則：`—` = 該事件在該狀態不可能發生（沒有觸發器）；
`ignored` = 會發生但程式明確不理它；空格是缺口，這份文件裡不允許有空格。

---

## A. 設定面板 state machine

狀態：`unknown`（尚未問過伺服器）· `unconfigured`（檔案裡沒有金鑰）·
`configured`（有金鑰）· `busy`（有一個 POST 在路上，按鈕全部 disabled）·
`unreachable`（問不到伺服器）。初始狀態 `unknown`；沒有終止狀態
（面板與頁面同壽）。

| 狀態 ＼ 事件 | open | load-ok | load-fail | test/save/clear | post-ok | post-fail | post-timeout | 關閉面板 |
|---|---|---|---|---|---|---|---|---|
| **unknown** | → unknown（清空兩個密碼欄、重新載入） | → unconfigured／configured | → unreachable | → busy | — | — | — | ignored |
| **unconfigured** | → unknown | — | — | → busy | → unconfigured／configured | → unconfigured | → unconfigured | ignored |
| **configured** | → unknown | — | — | → busy | → unconfigured／configured | → configured | → configured | ignored |
| **busy** | → unknown（面板可被 light-dismiss 後再開；重新載入會覆蓋掉舊回應） | — | — | **不可能**（按鈕 disabled） | → unconfigured／configured | → 回原狀態 | → 回原狀態 | ignored（`finally` 仍會解鎖按鈕） |
| **unreachable** | → unknown | — | — | → busy | → unconfigured／configured | → unreachable | → unreachable | ignored |

- 等待有出口：`busy` 有 **60 秒 AbortController 逾時**（`SETTINGS_TIMEOUT_MS`），
  比伺服器自己對 OPS 的 40 秒逾時長，所以「中止」代表**本機伺服器沒回話**，
  和「EPO 慢」是兩件不同的事，訊息也這樣寫。
- 等待有聲音：`busy` 期間 `#setstat` 由 `ticker()` 每 200 ms 更新一次經過秒數。
- 值的方向是單向的：`ops_key`／`ops_secret` 只會進去，回應永遠只有長度與末四碼
  （`config.hint`）。由 `tools/check_settings.py` 斷言，含正對照。

## B. 圖式面板 statechart（兩個平行區域）

**區域 1 — 來源與載入**。狀態：`none` · `google.loading` · `google.shown` ·
`ops.loading` · `ops.shown` · `error`。

| 狀態 ＼ 事件 | render(card) | show(i) | img.load | img.error | swap | ←／→ |
|---|---|---|---|---|---|---|
| **none** | → 依卡片重新決定 | ignored | — | — | —（沒有按鈕） | ignored |
| **google.loading** | → 依卡片 | → google.loading | → google.shown | → error | → ops.loading | → google.loading |
| **google.shown** | → 依卡片 | → google.loading | — | — | → ops.loading | → google.loading |
| **ops.loading** | → 依卡片 | → ops.loading | → ops.shown | → error | → google.loading | → ops.loading |
| **ops.shown** | → 依卡片 | → ops.loading | — | — | → google.loading | → ops.loading |
| **error** | → 依卡片 | → ＊.loading | → ＊.shown | → error | → 另一個來源 | → ＊.loading |

- `none` 不是陷阱狀態：`render(card)` 是它的出口，且畫面上會寫出原因與
  「改開原文件 PDF」的替代路徑。
- `＊.loading` 的等待同樣有聲音（`ticker`），OPS 模式 6 秒後補一句
  「EPO 逐頁提供，忙碌時會被它自己限流」——因為那正是
  `epo_ops._throttle()` 會做的事，不是當掉。
- **`＊.loading` 沒有逾時**：`<img>` 載入無法設逾時，只能靠瀏覽器自己的連線逾時
  觸發 `img.error`。這是已知且刻意接受的洞，列在下面 §缺口 G-2。

**區域 2 — 檢視變換**。三個互相獨立的維度，不是狀態：

| 維度 | 值 | 誰重設它 | 誰保留它 |
|---|---|---|---|
| `FIGS.rot` | 0／1／2／3（順時針 90°） | `renderFigures`（換文件、換來源） | `figShow`（翻頁保留：同一件專利的圖頁方向通常一致） |
| `FIT` | `page`／`width` | 沒有人（`localStorage` 持久化，跨文件） | 全部 |
| `zoom` | on／off | `figShow`（每次翻頁歸零） | `figLayout`（旋轉、改 fit 時保留） |

3 × 2 × 2 = 12 種組合，全部由 `figLayout()` 一個函式決定幾何，
`tools/check_figures.py` 覆蓋其中 6 種代表格（含四個 90° 循環回到原點）。

## C. 本機守門 decision table

條件三個，判決三種。`Sec-Fetch-Site` 只有 `cross-site` 一個值會擋，
其餘（`same-origin`／`same-site`／`none`／不存在）一律放行——**else 分支是明寫的**。

| # | `Host` | `Origin` | `Sec-Fetch-Site` | 判決 | 擋掉的是什麼 |
|---|---|---|---|---|---|
| 1 | 非本機／缺 | ＊ | ＊ | **421** | DNS rebinding：攻擊者把自己的網域指到 127.0.0.1 |
| 2 | 本機 | 缺 | 缺／非 cross-site | 放行 | —（這就是本頁自己的 GET） |
| 3 | 本機 | 缺 | `cross-site` | **403** | `<img>`／`<script>`／`<form>` 直接伸手（這種請求不帶 Origin） |
| 4 | 本機 | 本機 | 非 cross-site | 放行 | —（本頁自己的 POST） |
| 5 | 本機 | 本機 | `cross-site` | **403** | 同 3 |
| 6 | 本機 | 非本機 | ＊ | **403** | 別的網頁對這個埠發的寫入請求 |

- 條件空間已覆蓋完：`Host` 二分、`Origin` 三分（缺／本機／非本機）、
  `Sec-Fetch-Site` 二分（cross-site／其他），6 列窮盡 2×3×2 中所有不重複判決。
- 正對照：第 2、4 列在 `check_settings.py` 與 `check_release.py` 各有一個
  **必須放行**的斷言。只驗拒絕的守門員，全擋光也是滿分。

## D. 啟動器流程

```
start
 └─ --selftest ?          ── yes ─→ 逐項 import 能力檢查 ─→ exit 0/1
     │ no
     ├─ ensure_data_dirs()（%LOCALAPPDATA%\PatentsGrabber，或原始碼樹的 repo 根）
     ├─ choose_port()
     │    ├─ PORT 有設 ─→ 該埠空著？ 是 → 用它 ／ 否 → 問它是不是我們的
     │    └─ 否則 8000…8011 逐一：空著 → 用它
     │                              被占且 /api/health 認得 → 已在執行
     │                              被占且不認得 → 換下一個
     │         └─ 12 個都不行 ─→ 印出原因、停住視窗、exit 1
     ├─ 已在執行？ ─→ 開瀏覽器指向那一份、exit 0（不會開第二個伺服器）
     ├─ 印出資料夾／網址／記錄檔／怎麼停止
     ├─ 背景執行緒：伺服器答得出 /api/health 才開瀏覽器（最多等 25 秒）
     └─ uvicorn.run(127.0.0.1:port)
          └─ 任何例外 ─→ 寫進 patentsgrabber.log ＋ 印在螢幕上 ＋ 等 Enter
```

- 「自己的裝置起不來」有狀態：`run()` 把整個 `main()` 包起來，
  失敗時視窗**不會關掉**，訊息同時進檔案。
- 競態（先看到埠是空的、綁定時被別人搶走）不會靜默：uvicorn 綁不上會丟例外，
  走上面那條路徑。

---

## 缺口報告

| ID | 缺口 | 嚴重度 | 這一輪的處置 |
|---|---|---|---|
| **G-1** | 設定面板 `busy` 沒有逾時，一個掛住的請求會讓按鈕永遠 disabled | 中 | **已修**：60 秒 `AbortController`，逾時訊息明說是本機伺服器沒回話 |
| **G-2** | `＊.loading` 無法設逾時（`<img>` 的限制），只能等瀏覽器自己的連線逾時 | 低 | **接受並寫明**：改以每 200 ms 的經過秒數＋6 秒後的限流說明取代「不知道在等什麼」 |
| **G-3** | 兩個新的等待都違反了本專案「不得靜默等待」的規則（只說在等什麼、沒說等了多久） | 中 | **已修**：`ticker()` 一個共用實作，兩處共用 |
| **G-4** | `_host_is_local` 用數冒號判斷埠，`[::1]:8000` 會被判成非本機 | 低（目前只綁 IPv4，打不到） | **已修**：改成解析括號形式；同時移除清單裡的 `0.0.0.0`（沒有人會送這個 Host） |
| **G-5** | `X-Page-Source` 標頭的註解宣稱「頁面會顯示它」——`<img>` 讀不到回應標頭，做不到 | 低（文件與實作不符） | **已修**：註解改成實話，並指出頁面實際問的是 `/api/ops/cached-pages` |
| **G-6** | `PORT` 環境變數指定的埠沒有先問「是不是已經有一份在跑」，第二次啟動會綁不上而當掉 | 中 | **已修**：`choose_port()` 對指定埠也做同樣的探測 |
| **G-7** | `__main__.py` 用相對 import，PyInstaller 以 `__main__` 執行時沒有 package 脈絡 → 打包成功但一啟動就死 | **高** | **已修**：改絕對 import。抓到它的是 `tools/check_release.py`，不是人眼 |
| **G-8** | `renderSettings` 印出的「環境變數與設定檔不同」警告，會被之後任何一次 test/save 的訊息覆蓋掉 | 低 | **接受**：下次打開面板就會重新出現；不值得為此加一個常駐區塊 |
| **G-9** | 設定面板沒有「語言」這一類未來項目 | — | **不是缺口**：本輪刻意不做假的下拉選單（見 `docs/07` 的 interpretation fork）。面板已切成分區，加一區的成本是一段 HTML |

G-2 與 G-8 是**留著的洞**，不是忘記的洞：兩者都寫進了畫面或文件，
沒有一個是靠讀者自己猜出來的。
