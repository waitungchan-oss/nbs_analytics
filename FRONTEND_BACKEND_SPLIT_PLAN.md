# NBS Analytics 前後端拆分規劃：Approach A

更新日期：2026-06-30  
專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`  
目標方向：保留 Python 後端能力，使用 Vue.js 逐步接管前端，不破壞現有 Streamlit 交互邏輯與業務口徑。

---

## 1. 規劃目標

本文件採用 Approach A：漸進式 API 拆分。

核心目標：

- 後端保持 Python，沿用現有 `pipeline.py`、`database.py`、`forecasting.py`、`business_calendar.py`、`visuals.py` 的計算與資料邏輯。
- 前端新增 Vue.js 應用，逐步接管目前 `app.py` 中的 UI、導航、篩選、圖表與下載互動。
- 在 Vue 前端穩定前，保留現有 Streamlit `app.py` 作為 baseline、fallback 與驗收對照。
- 第一階段只做 read-only dashboard API 與 Vue cockpit，不先碰正式上傳、資料清洗、AI Forecast 寫入 cache、Excel export schema 或 SQLite schema。

此規劃不是一次性重寫，而是把現有 Streamlit monolith 安全拆成：

```text
Vue 3 frontend
→ Python API backend
→ Existing Python services
→ SQLite / runtime cache / Excel workbooks
```

目前落地狀態（2026-06-30）：

- Streamlit baseline：`http://127.0.0.1:8502/`，仍作為正式驗收對照與 fallback。
- FastAPI backend：`http://127.0.0.1:8601/docs`，提供 health、dashboard、upload / preflight、stability、history 等 API contract。
- Vue frontend：`http://127.0.0.1:5173/`，已逐步接入 read-only cockpit、狀態透明化、upload / report 入口。
- Phase 2I manager：`scripts/system_manager.py` 統一啟停與驗收三個服務。
- Phase 2R：Streamlit 已模組化，`app.py` 是 thin entrypoint，`app_pages.py` 管 UI page orchestration，`app_workflows.py` 管 workflow helpers；`app_pages.py` 對 `app_workflows.py` 已收斂為明確匯入清單。

---

## 2. 現有系統契約

以下契約來自現有 markdown 與目前 `app.py` 行為，前後端拆分時必須保留。

### 2.1 不可破壞的業務邊界

- 正式收入口徑仍是：`不含掛賬核銷與TT退款轉團款`。
- SQLite 只保存清洗後明細，不保存最終 dashboard 結果、AI 模型物件或 Vue 前端狀態。
- GMV 排除訂單看板維持 session-only 派生視角，不回寫 SQLite，不影響正式看板、AI Forecast、WAPE 或正式 export cache。
- Data Quality、Entity Resolution、Forecast Governance、Feature Store、Causal Analytics 都是只讀診斷層。
- AI-assisted Data Cleaning 只產生建議；只有人工確認後，才可寫入 `rules_config.json` 支援的既有規則欄位。
- Large Excel workbooks 維持 Lazy Export，不在首屏同步生成。

### 2.2 不可破壞的 UI 交互邏輯

- `Navigation` 是頁內目錄，只負責跳轉 section，不改分析視角。
- `Control Center` 是提交式篩選器，負責年份、月份、日期、分社、專職與主題。
- 點擊 navigation 不應重新計算 AI cache、不應重新生成 export、不應重設篩選條件。
- 深色 / 淺色主題切換只影響 UI token，不改 SQLite、AI Forecast、WAPE 或 Export。
- Forecast、Backtest、Data Quality、Export 區域要保留 `正式`、`只讀`、`診斷`、`治理`、`實驗` 等語義標籤。

---

## 3. 推薦架構

### 3.1 後端：Python API 層

建議新增 `backend/`，用 FastAPI 承接前端請求。

```text
backend/
  main.py
  routers/
    health.py
    dashboard.py
    upload.py
    forecast.py
    export.py
    rules.py
    gmv.py
  services/
    dashboard_service.py
    upload_service.py
    forecast_service.py
    export_service.py
    rules_service.py
    gmv_service.py
  schemas/
    dashboard.py
    forecast.py
    export.py
    rules.py
    gmv.py
```

後端原則：

- API 層只負責 request / response、錯誤轉譯、序列化與權限邊界。
- Service 層包裝現有 Python 函式，初期不得複製大量業務邏輯。
- `pipeline.py`、`database.py`、`forecasting.py` 優先視為 system of record，不在 Vue 或 API router 中重寫計算。
- `app.py` 初期保留，並逐步把可重用的純計算函式抽入 service。

### 3.2 前端：Vue.js cockpit

建議新增 `frontend/`，用 Vue 3 + Vite。

```text
frontend/
  src/
    app/
      router.ts
      apiClient.ts
    stores/
      dashboardStore.ts
      filterStore.ts
      themeStore.ts
      gmvStore.ts
    views/
      DashboardView.vue
      RulesView.vue
      GmvExclusionView.vue
    components/
      shell/
        AppShell.vue
        SideNavigation.vue
        ControlCenter.vue
      dashboard/
        KpiStrip.vue
        RankingTable.vue
        ProductDrilldown.vue
        ForecastPanel.vue
        ExportCenter.vue
      shared/
        StatusBadge.vue
        LoadingStage.vue
        EmptyState.vue
        ErrorDetails.vue
```

前端原則：

- Vue 只負責展示、互動狀態、表格/圖表渲染與下載觸發。
- 前端不得計算正式收入口徑、WAPE、Forecast consensus、GMV 排除財務結果。
- 即使 Vue 已接入 upload / report 入口，正式口徑、drift gate、rollback、preflight diagnosis 與 workbook schema 仍由 Python backend 決定。
- Navigation 使用 hash anchor 或 Vue Router hash mode 保留「頁內跳轉」語義。
- Control Center 使用 explicit apply，不因 navigation click 改變 store 中的分析視角。

---

## 4. API Contract 初稿

第一階段以 read-only dashboard 為主。

### 4.1 Health

```text
GET /api/health
```

用途：

- 確認 Python API server 正常。
- 回傳版本、SQLite 是否可讀、runtime cache 目錄是否可用。

### 4.2 Dashboard Context

```text
GET /api/dashboard/context
```

用途：

- 回傳目前資料庫狀態、最大日期、可選年份、月份、分社、專職清單。
- 對應目前 `app.py` 的 database status 與 Control Center options。

### 4.3 Dashboard Summary

```text
POST /api/dashboard/summary
```

Request：

```json
{
  "years": [2026],
  "months": ["06"],
  "dateRange": ["2026-06-01", "2026-06-15"],
  "branch": "全部",
  "salesGroup": "全部"
}
```

Response 應包含：

- applied filters
- revenue scope caption
- KPI cards
- annual overview
- branch ranking
- product drill-down data
- data quality summary
- entity audit summary
- export readiness status

### 4.4 Forecast Read Model

```text
GET /api/forecast/summary
```

用途：

- 只讀取或觸發現有 Python forecast cache 行為。
- 回傳 Daily / 7-Day / Month-End forecast 的表格資料、健康燈號與圖表序列。
- 不允許 Vue 前端自行重建 Forecast ensemble。

### 4.5 Lazy Export

```text
GET /api/exports/status
POST /api/exports/prepare
GET /api/exports/{export_id}/download
```

用途：

- 保留 Lazy Export。
- `status` 回傳是否已命中 export cache。
- `prepare` 才生成或載入 workbook bytes。
- `download` 回傳既有檔名與 bytes。

### 4.6 GMV Session View

```text
POST /api/gmv/exclusion/preview
POST /api/gmv/exclusion/workbooks
GET /api/gmv/exclusion/workbooks/{workbook_id}/download
```

用途：

- 上傳交易號碼清單後，在後端 session 或 request-scoped cache 中產生派生視角。
- 不回寫 SQLite。
- 不使用正式 export cache。
- workbook sheets 與正式報表一致，但檔名維持 GMV 排除版。

---

## 5. 遷移階段

### Phase 0：文件與契約凍結

目標：

- 以本文件作為拆分起點。
- 明確列出不可破壞交互與資料邊界。
- 保留 Streamlit `app.py` 為現行 baseline。

交付：

- `FRONTEND_BACKEND_SPLIT_PLAN.md`
- 後續實作前再新增正式 implementation plan。

驗證：

- 不修改 Python 邏輯。
- 不修改 SQLite。
- 不修改 `.nbs_runtime_cache`。

### Phase 1：Read-only API Baseline

目標：

- 建立 FastAPI skeleton。
- 提供 `health`、`dashboard/context`、`dashboard/summary`。
- 抽出 dashboard read service，但不改現有 Streamlit flow。

最小可行改動：

- 新增 `backend/`。
- 新增 `services/dashboard_service.py`，包裝現有 `load_all_data_from_db()`、`_build_revenue_scope_frames()`、`build_dashboard_data()` 類能力。
- 若 `app.py` 內函式需要共用，優先小心抽出純計算函式，不移動 Streamlit rendering。

驗證：

- Python import / compile。
- API health HTTP 200。
- Dashboard summary 和 Streamlit baseline 的 KPI / ranking 主要數字一致。

### Phase 2：Vue Read-only Cockpit

目標：

- 建立 Vue 3 + Vite frontend。
- 實作 App Shell、Side Navigation、Control Center、KPI、Ranking、Product Drill-down。
- 不接上傳、不接 export prepare、不接 rules 寫入。

最小可行改動：

- 新增 `frontend/`。
- Vue 使用 API client 讀取 Phase 1 endpoints。
- Navigation 使用 hash anchors；Control Center 使用 explicit apply。

驗證：

- 本地 HTTP 開啟 Vue。
- 點擊 Navigation 不發送 dashboard summary POST。
- 點擊 Apply 才更新 dashboard summary。
- Vue 顯示數字與 Streamlit baseline 一致。

### Phase 3：Forecast / Diagnostics Read Models

目標：

- Vue 顯示 Daily / 7-Day / Month-End Forecast、Backtest、Forecast Governance、Feature Store、Causal Analytics。
- 後端仍沿用現有 forecast/cache 計算。
- Streamlit 保留「補算 AI」手動入口；Vue 只讀展示，不自動觸發 AI/backtest 補算。

驗證：

- 不改 WAPE 計算。
- 不讓 Normal-Day 或 Two-Lane 實驗覆蓋正式 Daily Forecast。
- API 回傳包含 role labels：production / diagnostic / experimental。

### Phase 4：Lazy Export API

目標：

- Vue Export Center 接入 export status、prepare、download。
- 保留 lazy export，不在首屏生成 workbook。

驗證：

- 未點擊 prepare 前不生成正式三份 workbook bytes。
- 下載檔名、sheet 結構、正式報表 schema 與現有 Streamlit 一致。
- Export cache key 不因 Vue 引入而改變。

### Phase 5：GMV Exclusion Vue View

目標：

- Vue 接管 GMV 排除訂單看板。
- 後端維持 session-only 或 request-scoped 派生，不寫 SQLite。

驗證：

- 正式 dashboard 數字不變。
- AI Forecast / WAPE 不因 GMV 排除而重算。
- 正式 export cache 不被污染。
- GMV 排除版 workbook sheets 與正式 workbook 結構一致。

### Phase 6：Rules Config UI

目標：

- Vue 接管業務規則配置。
- 僅寫入 `rules_config.json` 既有規則欄位。

驗證：

- 不改 SQLite schema。
- 不自動重寫已入庫明細。
- AI-assisted Data Cleaning 建議仍需人工確認才可套用。

---

## 6. 主要風險與防護

### 6.1 風險：把 Streamlit session 行為直接搬到 Vue

防護：

- 明確區分 browser UI state、API request state、backend runtime cache。
- Vue store 不等於 SQLite，不等於 Python cache。

### 6.2 風險：前端重算業務口徑

防護：

- Vue 僅做展示，不計算正式收入口徑、Forecast、WAPE、GMV 排除財務結果。
- 所有金額、排名、Forecast consensus 由 Python API 回傳。
- 若 Streamlit 將 AI cache 標示為 deferred，仍需由 Streamlit 顯式按鈕補算，不由 Vue 路由或刷新代替。

### 6.3 風險：Navigation click 觸發重算

防護：

- Side Navigation 僅改 hash / scroll target。
- Dashboard summary API 只在 Control Center apply、初次載入或明確 refresh 時呼叫。

### 6.4 風險：Lazy Export 退化成首屏同步生成

防護：

- Export status 和 prepare 分成兩個 endpoint。
- Vue 初次載入只查 status，不呼叫 prepare。

### 6.5 風險：GMV 排除污染正式資料

防護：

- GMV endpoints 不呼叫 `upsert_to_db()`。
- GMV workbooks 不寫入正式 export cache。
- GMV response 明確標 `sessionOnly: true`。

---

## 7. 驗收標準

每個階段完成後，至少驗證：

```bash
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py forecasting.py pipeline.py database.py business_calendar.py visuals.py backend/services/upload_preflight_service.py scripts/system_manager.py scripts/validate_business_calendar.py scripts/inspect_sqlite_latest.py scripts/prewarm_ai_cache.py
.venv/bin/python scripts/validate_business_calendar.py
.venv/bin/python scripts/inspect_sqlite_latest.py
.venv/bin/python scripts/prewarm_ai_cache.py --status
.venv/bin/python scripts/system_manager.py acceptance
```

Streamlit baseline：

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
```

Baseline URL：

```text
http://127.0.0.1:8502/
http://127.0.0.1:8502/_stcore/health
```

Vue / API 階段新增驗收：

- API `/api/health` HTTP 200。
- Vue dev server HTTP 200。
- Control Center apply 前後 request 數可觀察。
- Navigation hash change 不觸發 dashboard recompute。
- KPI、ranking、forecast 表格與 Streamlit baseline 主要數字一致。
- Export workbook 檔名與 sheet 結構一致。
- Upload preflight `batchSummary` 必須包含最早/最晚收款時間、金額合計與指定日期覆蓋狀態，避免 false warning。

---

## 8. 歷史實作計劃與目前後續

以下是 Approach A 啟動時的第一個 implementation plan，已作為早期拆分基礎完成，不再代表目前下一步：

```text
Phase 1 Implementation Plan:
1. 新增 backend FastAPI skeleton。
2. 新增 health endpoint。
3. 新增 dashboard context endpoint。
4. 新增 dashboard summary endpoint。
5. 抽 dashboard read-only service。
6. 用 Streamlit baseline 驗證主要 KPI / ranking。
```

當時第一階段暫不處理：

- Vue frontend scaffold。
- file upload。
- rules config 寫入。
- forecast 模型調整。
- Excel export schema 調整。
- GMV 排除 workbook。
- SQLite migration。

目前後續重點已轉為：

- 保持 Streamlit baseline 與 Vue/API 數值對照。
- 補強 Vue upload / report 的錯誤透明度與回滾可視化。
- 持續把 Streamlit rendering 與 workflow 邊界收斂成明確 imports。
- 在不改正式口徑的前提下，逐步把可重用 API contract 固定成測試。

---

## 9. 最重要的決策

Approach A 的核心不是「把 Streamlit 立刻換成 Vue」，而是先把現在可靠的 Python 商業邏輯變成穩定 API contract，再讓 Vue 逐步接管展示層。

只要每一步都保留 Streamlit baseline 並做數值對照，系統可以在不破壞現有交互邏輯的情況下，逐步演進為真正的前後端分離架構。
