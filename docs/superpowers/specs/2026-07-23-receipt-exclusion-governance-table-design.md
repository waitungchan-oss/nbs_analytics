# Receipt Exclusion Governance Table Design

狀態：待使用者審閱
日期：2026-07-23
適用系統：NBS Analytics Streamlit Receipt Exclusion Governance

## 1. 背景

目前「Receipt Exclusion Governance」先以唯讀表格顯示 active 規則，再為每條
規則垂直建立一個「預覽撤銷 #N」按鈕。規則增加後，按鈕會持續拉長頁面，而且
操作者必須來回對照按鈕編號與表格內容，才知道即將撤銷哪個 `receiptNo`、
`sourceOrderNo` 與 `exclusionKind`。

本設計把 active 規則與撤銷選擇整合成固定高度、可捲動的單選表格，表格下方只
保留一組共用預覽／確認操作。既有撤銷預演、暫存資料庫重播及 baseline gate
保持不變。

## 2. 目標

1. 讓每個撤銷操作直接對應完整規則 identity，不再依賴分離的編號按鈕。
2. Active 規則以固定高度表格呈現，資料超出顯示範圍時可垂直或水平捲動。
3. 第一版一次只允許撤銷一條規則，降低批量誤操作風險。
4. 保留「先預覽、後確認」兩階段流程及 fail-closed 行為。
5. 已撤銷規則保留唯讀查閱能力，但不混入 active 規則的操作區。

## 3. 不變量

- 正式口徑仍為：`不含掛賬核銷與TT退款轉團款`。
- `2026-05` frozen baseline 仍為 `HKD 12,057,968`。
- 不修改 SQLite registry schema、quarantine evidence、正式 facts table、
  baseline registry、upload、rollback 或報表計算。
- 不新增批量撤銷、跳過預演或直接刪除 registry row 的入口。
- `Context Agent`、`Review Agent`、Hermes 與 Agent Operations 的權限不變。
- Streamlit 只提交 rule ID 與 preview fingerprint，不信任瀏覽器回傳的
  `receiptNo`、`sourceOrderNo` 或 `exclusionKind` 作正式寫入依據。
- Quarantine raw payload、evidence payload 與完整 proposal fingerprint
  不顯示在治理表格。

## 4. 採用方案

採用 Streamlit 原生 `st.data_editor`：

- 只有第一欄「選取」可編輯，資料欄全部 disabled。
- `num_rows="fixed"`，不允許新增或刪除規則。
- 固定表格高度，預設 `320px`；超出時由 Streamlit 提供捲動。
- 不引入第三方 grid 或自訂 JavaScript component。
- 表格下方只顯示一個「預覽撤銷所選規則」按鈕。
- 預演通過後才顯示或啟用「確認撤銷所選規則」按鈕。

選擇原生 `st.data_editor` 的原因：

1. 與目前 Streamlit UI 及 DataFrame 使用方式一致。
2. 可直接測試單選驗證、disabled 欄位及 widget key。
3. 避免新增前端套件、component lifecycle 或額外安全邊界。

## 5. Active 規則表格

### 5.1 顯示欄位

| 顯示欄位 | Read Model 欄位 | 可編輯 |
|---|---|---|
| 選取 | UI-only boolean | 是 |
| 規則 ID | `id` | 否 |
| 收款單號 | `receiptNo` | 否 |
| 來源單據號 | `sourceOrderNo` | 否 |
| 排除類型 | `exclusionKind` | 否 |
| 建立時間 | `createdAt` | 否 |
| 建立者 | `createdBy` | 否 |
| 稽核事件數 | `eventCount` | 否 |

第一版不顯示 `evidenceHash`、`proposalFingerprint`、
`createdOperationId` 或 quarantine payload。這些欄位仍保留在 read model／
SQLite audit 中，但不屬於日常撤銷操作所需資訊。

### 5.2 單選規則

`st.data_editor` 的 checkbox 欄本身允許多選，因此 rendering layer 必須執行
deterministic 驗證：

- 選取 0 條：停用預覽按鈕。
- 選取 1 條：允許執行預覽。
- 選取超過 1 條：顯示「一次只能選取一條永久排除規則」，停用預覽與確認。

不自動替使用者取消其他勾選，避免 UI 在未明示的情況下改變選擇。

### 5.3 Widget identity

表格 widget key 必須包含 `registryRevision` 的 bounded token。Registry 狀態改變
或撤銷成功後，新的 read model 會產生新的 widget identity，避免沿用已失效的
勾選狀態。

## 6. 預覽與確認狀態

### 6.1 預覽

使用者選取一條規則並按「預覽撤銷所選規則」後，Streamlit 只把該 active rule
的 integer ID 傳給既有 `preview_revoke(rule_id)` callback。既有 service 繼續：

1. 取得 cross-process upload lease。
2. 在暫存資料庫重播 quarantine evidence。
3. 執行正式口徑與 monthly baseline gate。
4. 回傳 revocation preview status 與 `previewFingerprint`。

Rendering layer 不重算收入、baseline 或正式 acceptance。

### 6.2 預覽摘要

預覽區直接顯示所選 identity：

- 規則 ID
- `receiptNo`
- `sourceOrderNo`
- `exclusionKind`
- preview status
- gate 摘要

預覽區不得輸出 quarantine raw payload。大型 gate payload 應以目前既有摘要或
收合區顯示，不把整份 JSON 直接鋪在頁面上。

### 6.3 確認資格

「確認撤銷所選規則」只有在以下條件全部成立時才可用：

1. 目前恰好選取一條 active 規則。
2. Preview `status == "revocation_ready"`。
3. Preview rule ID 等於目前選取 rule ID。
4. `previewFingerprint` 非空。
5. Preview 綁定的 `registryRevision` 等於目前 snapshot revision。

Session state 保存的是 bounded preview state：

```json
{
  "registryRevision": "revision-token",
  "ruleId": 4,
  "previewFingerprint": "sha256",
  "status": "revocation_ready",
  "summary": {}
}
```

不把 SQLite row、quarantine payload 或 callback 放入 session state。

## 7. Stale State 規則

以下任一情況必須清除或忽略舊 preview，並停用確認：

- 使用者把選擇從規則 A 改為規則 B。
- 使用者取消全部選擇。
- 使用者同時選取多條規則。
- `registryRevision` 改變。
- 選中的 rule ID 不再出現在 active snapshot。
- Preview fingerprint 缺失或 preview status 不再是 `revocation_ready`。

即使 UI state 未及時清除，controller／service 仍必須使用既有 fingerprint 與
registry 驗證 fail closed；rendering 層只是額外避免誤按。

## 8. 已撤銷規則

Revoked 規則放在「查看已撤銷規則」expander 內，以固定高度唯讀
`st.dataframe` 顯示：

- 規則 ID
- `receiptNo`
- `sourceOrderNo`
- `exclusionKind`
- `revokedAt`
- `revokedBy`
- 稽核事件數

該表格不提供恢復、重新啟用或刪除操作。若未來需要重新啟用，必須另立設計，
不得把它暗藏在本次撤銷 UI 修改內。

## 9. 空值、錯誤與進行中狀態

- 沒有 active 規則：顯示「目前沒有生效中的永久排除規則」，不顯示操作按鈕。
- Preview service 失敗：顯示可定位的錯誤，保留表格，但不啟用確認。
- Confirm service 失敗：顯示錯誤並保持 fail closed，不自行改表格資料。
- Confirm 成功：沿用現有 success message 與 `st.rerun()`，重新讀取 registry。
- Callback 執行期間：使用 Streamlit spinner，文字分別為「正在預演撤銷」及
  「正在確認撤銷」，避免使用者誤以為沒有反應。

## 10. 預期版面

```text
Receipt Exclusion Governance
永久排除規則只影響精確 identity...

┌──────────────────────────────────────────────────────────────────┐
│ 選取 │ ID │ receiptNo │ sourceOrderNo │ exclusionKind │ 建立時間 │
│  □   │  1 │ ...       │ ...           │ ...           │ ...      │
│  ☑   │  2 │ ...       │ ...           │ ...           │ ...      │
│                         固定高度，可上下／左右滑動               │
└──────────────────────────────────────────────────────────────────┘

[預覽撤銷所選規則]

所選規則：#2 / receiptNo / sourceOrderNo / exclusionKind
預演結果：revocation_ready
[確認撤銷所選規則]

▶ 查看已撤銷規則
```

## 11. 影響檔案

預期最小修改範圍：

- `receipt_exclusion_rendering.py`
  - 建立治理表格 view rows。
  - 驗證單選狀態。
  - 管理 bounded preview session state。
  - 顯示 revoked rules expander。
- `tests/test_receipt_exclusion_rendering.py`
  - 補齊表格、單選、stale preview、確認資格及敏感欄位測試。

`app_pages.py` 的 callbacks 預期不需修改。若實作時發現 callback contract
缺少必要的 rule ID 或 registry revision，必須先返回 implementation plan 修正，
不能在 rendering layer 猜測正式狀態。

## 12. 驗證策略

### 12.1 Rendering tests

至少覆蓋：

1. Active 規則顯示完整 identity 與固定高度表格。
2. 0 條選擇時預覽 disabled。
3. 1 條選擇時只提交該 rule ID。
4. 多條選擇時預覽／確認 disabled 並顯示錯誤。
5. 選擇改變後舊 preview 不得啟用確認。
6. Registry revision 改變後舊 preview 不得啟用確認。
7. `revocation_ready` 且 fingerprint/rule/revision 一致時才允許確認。
8. Raw quarantine payload、evidence hash 與完整 proposal fingerprint 不渲染。
9. Revoked table 唯讀且沒有重新啟用按鈕。

### 12.2 Regression tests

實作完成後至少執行：

```bash
.venv/bin/python -m pytest tests/test_receipt_exclusion_rendering.py -q
.venv/bin/python -m pytest \
  tests/test_receipt_exclusion_read_model_service.py \
  tests/test_receipt_exclusion_governance_service.py -q
.venv/bin/python -m py_compile receipt_exclusion_rendering.py app_pages.py
```

因本次不修改 upload、SQLite service、baseline 或 rollback，預設不需要重跑正式
上傳資料；若實際 diff 超出 rendering/test 邊界，則升級為 receipt exclusion
完整 targeted suite、rollback suite 與 Hermes post-change check。

## 13. 非目標

- 批量撤銷多條規則。
- 修改 permanent exclusion proposal 的上傳確認介面。
- 修改 Registry／Quarantine／Events schema。
- 新增規則搜尋、排序持久化、CSV 匯出或分頁 API。
- 重新啟用 revoked 規則。
- 修改任何正式營收、baseline、分社歸屬、人數、交易數量或 export 規則。
