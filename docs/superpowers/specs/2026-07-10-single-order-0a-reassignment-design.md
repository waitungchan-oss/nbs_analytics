# Single Order 0A Reassignment Design

## Goal

將正式 SQLite 中的來源單據號 `E9MF16613172500`，在 `2026-06` 由「上環服務點」精確重分配至 `0A 展覽會場專用` 的票務銷售額。此變更不得擴大至其他 E9 訂單、其他月份或其他分社。

## Scope

- 目標訂單：`E9MF16613172500`
- 限定月份：`2026-06`
- 原分社：`上環服務點`
- 目標分社：內部值 `展覽會場專用`，報表顯示為 `0A展覽會場專用`
- 業務分類：票務；現有資料來源標籤為 `套票all0709`
- 金額：`HKD 1,544`

現有 `2026-06 + E6 + 上環服務點 -> 0A` 規則保持不變。其他 E9 訂單即使在相同月份，也不因本次變更而自動重分配。

## Rule Design

在既有 `BRANCH_REASSIGNMENT_OVERRIDES` 契約中加入精確來源單據號條件，使 override 可同時按月份、原分社及完整訂單號判斷。規則必須使用完整、正規化後的 `來源單據號` 等值匹配，不使用 prefix 或模糊匹配。

單筆規則只有在以下條件全部成立時命中：

1. `統一日期`、`收款時間` 或既有日期 fallback 解析為 `2026-06`。
2. 目前 `銷售點` 或 `副表_銷售點` 為「上環服務點」。
3. 正規化後的 `來源單據號` 完全等於 `E9MF16613172500`。

命中後，同步將 `銷售點` 與 `副表_銷售點` 設為「展覽會場專用」，確保 dashboard、正式報表及後續 SQLite repair 使用一致歸屬。

## Data Flow

1. Upload processing 套用同一份 override，確保重新上傳後不會回到上環服務點。
2. SQLite repair 套用同一規則，修復目前正式資料庫中的既有記錄。
3. Repair 前建立 hot backup；失敗時不保留部分更新。
4. 修復成功後重建 dashboard cache，讓 Streamlit 與 API 讀取新歸屬。
5. 不修改收入排除口徑、不改金額、不新增或刪除交易，只改分社歸屬。

## Error Handling

- 找不到目標訂單時停止正式修復，並回報未更新，不以新增資料代替。
- 找到多筆相同來源單據號時，所有符合月份及原分社條件的正式收入行必須一致重分配；驗證同時回報命中行數與金額。
- 若修復後任何總額基線不符，視為驗收失敗，保留診斷證據並依現有 rollback 流程恢復。
- 不透過 Vue、Streamlit 顯示、export rounding 或 analysis layer 排除來調整驗收數字。

## Test Strategy

先以 TDD 建立失敗測試，再實作最小規則擴充：

- 精確訂單在 `2026-06` 且屬上環時，重分配至 0A。
- 相同月份的其他 E9 訂單保持原歸屬。
- 相同訂單在其他月份保持原歸屬。
- 相同訂單若不屬上環服務點，不命中此規則。
- Pipeline processing 與 SQLite repair 對同一輸入產生一致結果。
- 重新處理或重新上傳同一批資料後結果保持穩定。

## Acceptance Baselines

正式修復、cache rebuild 及驗收完成後，以下條件必須同時成立：

- `2026-06` 上環服務點正式銷售額：`HKD 0`
- `2026-06` 0A 展覽會場專用正式銷售額：`HKD 703,425`
- `2026-06` 全部分社加全部專職銷售組正式總銷售額：`HKD 9,083,241`
- `2026-05` frozen baseline：`HKD 12,057,968`
- 正式口徑仍為：`不含掛賬核銷與TT退款轉團款`

金額比較使用正式計算鏈路的未四捨五入值；畫面與 Excel 格式只作展示，不作基線修正來源。

## Verification

- 執行單筆 override、pipeline processing、database repair 的 targeted tests。
- 執行 upload、database、rollback、stability history 測試。
- 執行 Phase 2 precheck、dashboard service 與 dashboard API 測試。
- 執行 `scripts/system_manager.py acceptance`。
- 執行 Hermes read-only post-change check。
- 直接查詢正式 SQLite 並以正式 dashboard/report 計算鏈路核對四個驗收數字。

## Non-Goals

- 不把所有 `2026-06` 上環服務點資料無條件轉入 0A。
- 不把所有 E9 訂單轉入 0A。
- 不修改 `2026-07` 或其他月份的 E6、E9 歸屬。
- 不改動正式收入金額、收入排除規則、銷售員歸屬或交易人數計算。
