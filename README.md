# PatentsGrabber

整合式專利閱讀工具。輸入一個專利號碼，得到一張把「可複製全文 ＋ 圖式 ＋ 原文件 ＋ 同族分類」放在同一屏的專利卡。

**目前狀態：Stage 0** —— 美國專利、零金鑰、單一來源（Google Patents 單件頁）。

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

其他：

- 每個欄位標註來源；**拿不到的欄位會說明原因，不會留空白，也不會出現破圖**
- `?q=US6285999B1` 深連結，專利卡可以加書籤或分享
- 查過的每一件都會存進本地 `var/library.sqlite3`（個人專利庫，非快取），重查不會再打網路

## 已知限制（實測確認，非缺陷）

- **約 2000 年以前的老專利沒有可取用的圖檔**，只有整份 PDF。
- **最新公開案（如 2025 年）沒有 PDF，圖檔網址也回 403**——標記裡有圖，實際取不到。工具會明說，不會給你破圖。
- 上述兩點的補救都在 Stage 1（接 EPO OPS 的 images 與 fullimage 服務）。
- 只做美國案。EP/TW/CN/JP/KR 已明確延後。
- 只能用號碼查，**不能用公司名檢索** —— Google Patents 的搜尋被其 `robots.txt` 排除，申請人檢索必須等 Stage 1 的 EPO OPS 金鑰。

## 開發者工具

```powershell
python tools/probe_coverage.py            # 覆蓋率探針（用本地快取的 HTML）
python tools/probe_coverage.py --refresh  # 重新抓取所有樣本
python tools/smoke_service.py             # 端到端煙霧測試
python tools/diag_images.py               # 圖檔網址形態診斷
python tools/shoot.py US8046721B2 out.png # 用本機 Chrome 截圖（可加 --dark）
```

## 文件

- `docs/01-concept-note.md` —— 概念層：痛點、邊界、業務規則、資料源勘查
- `docs/02-stage0-findings.md` —— Stage 0 實測發現與交付紀錄
- `docs/archive/` —— 已被取代的文件版本（不刪除）

## 資料來源與界線

本工具只抓取 `patents.google.com/patent/{number}` 這種**單件文件頁**，該路徑被 Google 的 `robots.txt` 明確允許（`Disallow: /*` ＋ `Allow: /patent/`）。**不會、也不應**用來自動化其搜尋端點。
