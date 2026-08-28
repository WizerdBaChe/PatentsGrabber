# Round 2026-08-29 — 可交付化（設定面板、圖式旋轉、單檔啟動器、公開發布）

## 0. Boundary contract（施工前先立，收尾時逐條回檢）

**Premises**
- P-env（verify）：Windows 11、Python 3.12.7、PyInstaller 6.19.0、`gh` 2.95.0 已登入
  `WizerdBaChe`。全部實測過，非假設。
- P-intent（report）：使用者要的是「別人（含未來的自己）拿到就能用」——所以設定要有 UI、
  啟動要是一個可執行檔、金鑰不可進版控。
- P-validity（verify）：`git log --all -- .env` 為空，`.env` 從未進過任何 commit。

**Interpretation forks**
- 「單一檔案或啟動器」→ 取 **onedir + 一顆 `PatentsGrabber.exe`**，不取 onefile。
  理由在 §4。若判斷錯誤，翻轉點只有一處：`packaging/patentsgrabber.spec` 的
  `EXE(...)`／`COLLECT(...)` 組合。
- 「語言選擇等等」帶問號 → 視為**可擴充性要求**，不是本輪功能。設定面板做成分區結構，
  但只放今天真的有作用的項目；不做假的語言下拉。

**Boundary inputs**
- 設定寫入位置：原始碼模式 = repo `.env`（既有 gate 全部不受影響）；封裝模式 =
  `%LOCALAPPDATA%\PatentsGrabber\settings.env`。
- 頁面只綁 127.0.0.1；任何會寫入或花配額的端點都必須擋跨站來源。

**Acceptance**
- 自動：`python tools/run_gates.py` 全綠 + 新增 `tools/check_settings.py`（設定往返、
  秘密不外流、跨站被擋）+ `tools/check_release.py`（打包產物真的起得起來）。
- 人工：`docs/08-uat-2026-08-29.md` 的編號清單（含壓力路徑）。

**Non-goals & degradation**
- 不做：多使用者、公開部署、翻譯引擎、語言 i18n、自動更新。
- 降級順序：先砍 release 附件簽章 → 再砍圖式旋轉的鍵盤快捷 → 再砍設定面板的連線測試。
  **保證核心**：設定可存可讀且不外洩、exe 起得來、GitHub 上沒有任何金鑰。
