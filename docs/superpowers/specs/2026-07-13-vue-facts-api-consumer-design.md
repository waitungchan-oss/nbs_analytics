# P2-3 Vue Facts API Consumer Design

## Goal

讓 Vue read-only cockpit 開始消費 `GET /api/dashboard/facts`，顯示共同 Facts 的 provenance 與全域守恆狀態，同時保留既有篩選用 `/summary`、`/analytics`，避免改變目前使用習慣與畫面口徑。

## Scope

- `frontend/src/lib/api.js` 新增 `getDashboardFacts()`。
- `frontend/src/App.vue` 在首次載入及 Vue upload 成功後取得 Facts。
- API Status 區新增 Facts source status：service version、generation token、cache state、reconciliation、全域合計。
- Facts API 失敗時顯示可定位錯誤，但不吞掉既有 dashboard 載入錯誤。
- 不讓 Vue 自行重算正式營收；不修改 `/summary`、`/analytics`、Streamlit、FastAPI write path、SQLite 或 baseline。

## Data Flow

`Vue loadAll()` / `submitVueUpload()` → `getDashboardFacts()` → `facts` reactive state → API Status Facts source panel。

篩選變動仍走 `getDashboardSummary(applied)` 與 `getDashboardAnalytics(applied)`；Facts endpoint 只提供全域 read model 與 generation/provenance。upload 成功後重新載入 Facts，讓 Vue 顯示最新 generation。

## Error Handling

- Facts API 失敗時保留 `facts` 為 null，顯示 `Facts source unavailable` 與錯誤訊息。
- Facts 失敗不以 summary/analytics 的數字冒充 Facts 成功。
- Vue 不在 client 端補算 combined revenue、ranking 或 baseline。

## Verification

- API client contract test 確認使用 `/api/dashboard/facts`。
- Vue static cockpit contract 確認 Facts source、generation、cache、reconciliation UI 存在，且沒有 client-side revenue recomputation。
- `npm run verify`、`npm run build`。
- FastAPI/Python targeted tests、完整 pytest、system acceptance、Hermes read-only inspection。

## Alternatives Considered

1. 立即把所有 Vue KPI/ranking 改成 Facts：一致性高，但會同時改變篩選行為，風險較大。
2. 只新增 API client，不顯示 Facts 狀態：無法驗證 Vue 實際消費成功。
3. 先接入 Facts provenance/status，保留 filtered API：可觀測、可回退、最符合目前使用習慣，採用此方案。
