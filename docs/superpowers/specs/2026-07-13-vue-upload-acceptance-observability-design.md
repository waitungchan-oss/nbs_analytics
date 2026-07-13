# P2-4 Vue Upload Acceptance Observability Design

## Goal

讓 Vue 上傳入口直接展示既有 `/api/upload` 回傳的驗收、回滾、history、cache generation 與 operation 識別資訊，讓使用者能判斷資料是否正式寫入、是否回滾，以及後續是否需要處理 cache 或 history 異常。

## Scope

- 沿用既有 `POST /api/upload` response contract，不新增資料寫入流程。
- 將 upload public status、preflight、stability gate、rollback、cache、history、generation 轉成清楚的 UI 狀態。
- 上傳失敗時解析 FastAPI 的 JSON `detail`，特別清楚顯示跨 process lock 的 busy 狀態。
- 保留既有 Facts refresh：成功回應後由 `loadAll(false)` 重新讀取 Facts 與 acceptance history。
- 不修改 SQLite、upload orchestrator、rollback、正式口徑、baseline、Streamlit 或報表計算。

## UI Contract

本次上傳結果至少展示：

- Public status：已接受、已降級、已拒絕並回滾、回滾失敗、被阻擋。
- Operation ID、entry point、history record ID。
- Preflight / stability gate / monthly baseline 狀態。
- Rollback status 與 rollback error。
- Cache state、cache error、generation token。
- `writeCommitted` 明確表示是否已提交正式寫入。

所有數字與狀態直接來自 API response；Vue 不重算 revenue、baseline、ranking 或 reconciliation。

## Error Handling

- API 400/409/500 先解析 JSON `detail`，保留 status code 與可定位訊息。
- 409 busy 顯示「目前已有另一個上傳交易進行中」及 owner entry point，不把它誤顯示成一般失敗。
- API response 已回傳但 `loadAll(false)` refresh 失敗時，保留本次 upload result，另外顯示 refresh error。

## Verification

- 新增 API error parsing 與 Vue static contract assertions。
- `npm run verify`、`npm run build`。
- upload API、upload orchestrator、rollback/history targeted tests。
- 完整 pytest、system acceptance、Hermes read-only inspection。

## Alternatives Considered

1. **只補強現有 upload panel，採用此方案**：改動最小，直接利用已存在的 response 欄位。
2. 新增獨立 upload status endpoint：契約更細，但目前 response 已足夠，會增加不必要的 API surface。
3. 重寫 upload workflow：會放大正式寫入與 baseline 風險，不適合 P2-4。
