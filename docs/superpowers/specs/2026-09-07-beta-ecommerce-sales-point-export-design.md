# Beta 電商組銷售點比較匯出 Spec

## 目標

在 `Beta comparison export` 中，以銷售點「市場及電商部-電子商務組」取代原本專職銷售表格的資料內容，同時保留正式 Dashboard、Forecast、正式 export、SQLite、baseline 與 revenue scope 的既有語意。

## 背景與問題定義

目前 `pipeline.build_dashboard_data()` 以 `TARGET_DEPT_FOR_REP` 判定專職通路，並生成「專職」統計表；`TARGET_DEPT_FOR_REP` 同時被 Pipeline、Dashboard 與 Forecast 使用。因此直接把它改成電商組會造成正式業務分類遷移，不符合本需求。

本功能只建立 Beta workbook variant。Beta 的替代表格以 `銷售點` 欄位篩選，而不是以 `團負責人部門` 篩選。`I6 -> 市場及電商部-電子商務組` 是來源 prefix mapping，只有經資料處理後銷售點解析為該值的資料才會進入 Beta 表格。

## 功能契約

### Beta 範圍

- Beta 只套用於 `Beta comparison export`，不改既有正式 export。
- Beta 保留原 workbook 的總表、匹配結果、分社表與其他非專職表格。
- Beta workbook 中原「專職」表格的位置改用電商組資料，sheet name 使用「市場及電商部-電子商務組」前綴，明確標示這是 Beta 電商組版本；正式 workbook 的原「專職」sheet names 完全不變。
- Beta 對應的專職表格至少包括：經營統計、旅行團統計、票務總計、每天旅行團交易人數、每天票務交易數量、線路種類每天統計。欄位結構沿用既有表格契約，避免下游讀取器破壞性變更。
- Beta sheet names 固定為：`市場及電商部-電子商務組_經營統計`、`市場及電商部-電子商務組_旅行團統計`、`市場及電商部-電子商務組_票務總計`、`市場及電商部-電子商務組_每天旅行團交易人數`、`市場及電商部-電子商務組_每天票務交易數量`、`市場及電商部-電子商務組_線路種類每天統計`。

### 資料篩選與銷售員

- 唯一正式篩選條件是：`銷售點 == "市場及電商部-電子商務組"`。
- `團負責人部門 == "市場及電商部-電子商務組"` 但銷售點不是該值的資料不得混入。
- Beta 顯示該銷售點內所有實際 `銷售員`，不套用原 `SALES_REP_LIST`。
- `銷售員` 為空白、`NaN` 或只含空白時，統一標準化為「未指定」。
- 不將銷售員名稱改成部門或銷售點名稱。
- 若沒有符合資料，輸出合法的空表與既定欄位，不 fallback 到原專職資料。

### 收入與退款口徑

- Beta 沿用現有三種資料範圍的排除規則。
- 正式比較版本固定不含「掛賬核銷」與「TT 退款轉團款」；不得重複扣減。
- 不改 2026-05 frozen baseline `HKD 12,057,968`，也不改正式 revenue scope。

### 隔離與 identity

- Beta workbook 必須有獨立的 variant／cache／manifest identity，不得覆蓋或冒充正式 export。
- identity 至少包含 export variant、scope、sales-point filter、資料 fingerprint、rules fingerprint 與 schema version。
- Beta identity 不得把 `TARGET_DEPT_FOR_REP` 或原 `SALES_REP_LIST` 當成電商組的資料條件。
- I6 mapping 的變更只影響重新處理來源資料後的銷售點解析；不自動回寫既有 SQLite。

## 非目標與治理邊界

- 不修改 `TARGET_DEPT_FOR_REP`、專職銷售的正式分流、Forecast、Dashboard channel 或 target governance。
- 不修改正式 SQLite、baseline、GMV/revenue ledger、business rules schema 或 upload flow。
- 不新增 Governance Graph、Memory Hub、Memory Sidecar、approval、dispatch、workflow control 或 Agent Operations 寫入能力。
- Governance Graph、Memory Hub、Memory Sidecar 與 local agent 僅提供 read-only、non-authoritative exploration context，不能產生資料條件、提升 gate 或寫入正式狀態。

## 失敗處理與 rollback

- 缺少 `銷售點` 或 `銷售員` 欄位時，依既有 normalized-column contract 補空值；不可猜測或以 `團負責人部門` 替代。
- Beta 篩選或 workbook generation 失敗時，回傳可定位的錯誤，不產生看似正式的檔案。
- Beta 失敗不得影響同一次操作的正式 export cache 或既有資料。
- Rollback 以移除 Beta variant wiring、保留既有正式 path 即可；不需資料庫 migration 或資料修復。

## 驗證與 acceptance criteria

- focused pytest 證明：銷售點篩選、全量銷售員、空白轉「未指定」、不套用 `SALES_REP_LIST`、空資料、退款排除、sheet identity 與 cache isolation。
- regression pytest 證明正式專職表格、Dashboard/Forecast 與原有 export 結果不變。
- fresh Strict Review 必須檢查 diff、spec、測試與正式邊界，且 findings-first PASS。
- full pytest、Hermes post-change check、UI acceptance 分開執行及報告；任何一個 gate 失敗不得由其他 gate 取代。
- 所有結果必須 source-bound 至實際 HEAD／worktree fingerprint；不得沿用舊 PASS。

## 最小改動原則

優先在 export composition boundary 加入可明確傳遞的 Beta filter／variant，重用既有統計 builder 與欄位投影；不把 Beta 條件塞進 `TARGET_DEPT_FOR_REP`，不複製整套 pipeline，不做大型重構。
