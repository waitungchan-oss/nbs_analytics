# P2-2 Dashboard Facts API Design

## Goal

新增唯讀 `GET /api/dashboard/facts`，讓 FastAPI 使用 P2-1 的 Dashboard Facts Service，提供與 Streamlit 相同 generation、正式口徑與彙總結果。

## Scope

- 新增 Facts API endpoint 與 typed response schema。
- API 只回傳 Facts 派生的 KPI totals、monthly totals、branch ranking、specialist ranking、product totals 與 reconciliation，不回傳原始 DataFrame。
- 保留既有 `/api/dashboard/summary`、`/api/dashboard/analytics`，不改 Vue。
- 不修改 SQLite schema、upload、rollback、revenue scope、baseline、AI cache 或 Export cache。

## Data Flow

`GET /api/dashboard/facts` → 讀取明確 `DB_FILE` 與 data generation → 讀取/重建 `dashboard_facts_service` cache → 以既有 `build_analytics_from_facts` 派生 API read model → Pydantic response。

API consumer 不接觸 SQLite，也不自行計算正式收入。generation token、facts cache key、service version 與 scope audit 會隨 response 回傳，方便 Vue/Hermes 判斷資料新鮮度與一致性。

## Response Contract

- `status`: `ready` 或 `error`。
- `serviceVersion`, `generationToken`, `cacheKey`, `factsCacheStatus`。
- `revenueScope` 與 `scopeAudit`。
- `kpiTotals`: 分社、專職、合計及三種產品收入。
- `monthlyTotals`: 月份分社/專職/合計。
- `branchRanking`、`specialistRanking`、`productTotals`。
- `reconciliation`: 對年、月、排行榜、產品彙總的守恆檢查。

## Error Handling

- Facts cache 或正式 DB 讀取失敗時，API 回傳 HTTP 500 與可定位錯誤訊息，不回傳空資料冒充成功。
- 不在 endpoint 內執行任何 DB write、upload、rollback 或 baseline promotion。

## Verification

- API contract test 驗證 response keys、OpenAPI schema 與 service invocation。
- API 與 Facts Service 使用同一 generation token 時，response 的 combined revenue 必須一致。
- 2026-05 baseline 維持 HKD 12,057,968；2026-01 至 2026-06 monthly governance 全部 matched。
- 執行 compile、targeted pytest、完整 pytest、system acceptance 與 Hermes read-only inspection。

## Alternatives Considered

1. 修改現有 `/summary`：改動表面較小，但會影響現有 Vue 與既有 contract。
2. 新增 `/facts`：契約獨立、可逐步遷移，風險最低，採用此方案。
3. 只在 FastAPI 內部呼叫 Facts、不公開 endpoint：無法支援後續 Vue 或其他 consumer，暫不採用。
