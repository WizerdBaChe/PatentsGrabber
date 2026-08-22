# EPO OPS 金鑰驗證結果（Stage 1 前置）

- 日期：2026-08-23
- 執行：`python tools/verify_ops.py`（金鑰由使用者自行填入 `.env`，本文件不含任何憑證）
- 結果：**11/14 通過**；三項未過中有**兩項是已確認的資料覆蓋事實**，一項是本工具的擷取缺陷（已修）

---

## 1. 最重要的結論：Stage 0 的兩個洞補起來了

| Stage 0 的限制 | OPS 實測結果 |
|---|---|
| `US20250383260A1` **沒有 PDF** | ✅ 取得 **54,764 bytes 的合法 PDF**（`%PDF-` magic），來源 `published-data/images/US/2025383260/A1/fullimage` |
| 同一件的**圖檔全數 HTTP 403** | ✅ 取得 **53,765 bytes 的 TIFF** 圖頁 |
| — | images inquiry 回 **3 個 document instance**，頁數 14 / 1 / 25 |

**這就是接 EPO OPS 的全部理由，現在有一手實證。**

## 2. 決定性發現：OPS 全文不涵蓋美國案

先前多次桌面查證都查不出定論，實測一次就定案：

| 呼叫 | 結果 |
|---|---|
| `claims` / `description` 對 **US** | ❌ `CLIENT.InvalidCountryCode` |
| `description` 對 **EP1000000**（同一支呼叫） | ✅ **10,804 字元** |

EP 對照組通過，證明**呼叫本身是正確的**，所以拒絕來自資料覆蓋而非程式。

### 後果：兩個來源都不可或缺

```
美國案文字（摘要／說明書／請求項） ── 只能來自 Google Patents
美國案圖式與原文件 PDF          ── 只能來自 EPO OPS
```

先前 Concept Note v2 把「美國案文字走 Google Patents」寫成設計選擇，當時是**推論**；
現在知道它是**唯一可行解**。這條已升級為 §7.3 的鐵則。

## 3. 申請人檢索確認可用

`pa="Taiwan Semiconductor"` → **total-result-count = 36,829**，回傳公開號如
`20260247699`、`20260245618`…。BR-8 所需的「申請人名稱變體」需改用其他
constituent 取得（search 回傳的是精簡書目），列為 Stage 1 工作項。

## 4. 號碼格式規則（已寫成測試釘住）

根因：先前把 **Espacenet 顯示格式** `US2025383260A1` 當成 API 輸入送出，導致全面 404。

| 形式 | 結果 |
|---|---|
| `epodoc/US6285999B1`（帶 kind） | ❌ 404 `SERVER.EntityNotFound` |
| `epodoc/US6285999`（不帶 kind） | ✅ |
| `docdb/US.2025383260.A1`（6 位序號） | ✅ |
| `docdb/US.20250383260.A1`（7 位序號） | ❌ 404 |
| `number-service/…/US2025383260A1/docdb` | ✅（**反而吃顯示格式**，故可作保險） |

**規則：epodoc 不得帶 kind code；docdb 須用 6 位序號且必須帶 kind。**
兩條都反直覺、公開文件未明說，已寫入 `tests/test_ops_number_formats.py`，
含一條「絕不可產生已知會 404 的字串」的斷言。

## 5. 配額：改用 OPS 自己的計數

OPS 每次回應都附上它自己的帳，比我方估算權威：

```
x-registeredquotaperweek-used : 93071
x-individualquotaperhour-used : 102147
x-throttling-control          : idle (images=green:200, inpadoc=green:60,
                                other=green:1000, retrieval=green:200, search=green:30)
```

- 本地 byte 累加降為輔助；顯示以 EPO 計數為準。
- 解析出**每服務每分鐘配額**：`search` 閒時 30／忙時 15，`images` 200／100。Stage 1 的節流依此，而非猜測。
- 依 T&C 4.2 依 **GMT 週**分桶；**不寫死任何門檻數字**（Fair use charter 定義且 EPO 可隨時變更）。

## 6. 本輪修掉的自身缺陷

| 缺陷 | 症狀 | 修法 |
|---|---|---|
| 送出 Espacenet 顯示格式 | 全部 404 | `ParsedNumber.epodoc` / `.docdb()` / `.docdb_candidates()` + `OpsClient.resolve()` |
| 丟棄 fault body | 404 無法診斷 | 一律帶出 OPS 的 `<code>` |
| 探針在第 2 步就 return | 後續能力從未測過 | 控制項優先、失敗不中止 |
| 用 `@doc-number` 找元素 | 檢索回 36,829 筆卻抽不出半筆 | `_walk()` 理解 OPS 的命名空間前綴與 `$` 文字節點 |
| 用寫死的元素名 `p` 取全文 | claims 回 0 字元被誤讀成「沒資料」 | `ops_text()` 收集所有 `$` 節點，對結構不敏感 |
| US 全文永遠亮紅字 | 已知事實被當成失敗 | 改為斷言「預期不可得」；若哪天真的回傳文字，反而報警（代表 OPS 覆蓋變了） |

## 7. review-when

- OPS 若開始供應美國全文 → §2 失效，來源分工可簡化（探針會主動報警）。
- Google Patents 若改 `robots.txt` 或要求登入 → 美國案文字**失去唯一來源**，屬阻斷性風險。
- EPO 變更免費門檻或 OPS 升版 → §5 需重讀。
