# Phase 2Q Vue Analytics Cockpit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Vue cockpit up to the Streamlit analytics standard by adding read-only charts, forecast visualizations, and downloadable reports while keeping all business logic in Python.

**Architecture:** The Vue app will stay a thin presentation layer. It will consume existing read-only backend endpoints for dashboard summary, analytics, health, quality, and forecast data, then render those payloads through small self-contained SVG chart components so we do not introduce a heavyweight chart dependency. Reporting will move to dedicated Python export endpoints that assemble workbook bytes from the same backend read models already used by Streamlit, keeping the report口徑 and the UI口徑 aligned.

**Tech Stack:** Vue 3, Vite, Python FastAPI, pandas/openpyxl, existing backend dashboard and forecast services, native SVG/CSS for charts, pytest, FastAPI TestClient.

---

### Task 1: Add backend report export endpoints

**Files:**
- Create: `backend/services/report_export_service.py`
- Create: `backend/routers/exports.py`
- Modify: `backend/main.py`
- Modify: `backend/schemas/dashboard.py`
- Test: `tests/test_report_export_api.py`

- [ ] **Step 1: Write the failing test**

```python
from fastapi.testclient import TestClient
from backend.main import create_app

def test_dashboard_report_export_returns_xlsx(monkeypatch):
    client = TestClient(create_app())
    response = client.get("/api/exports/dashboard.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_report_export_api.py -v`
Expected: `404 Not Found` until the router exists.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/services/report_export_service.py
from io import BytesIO
import pandas as pd

def build_dashboard_export_workbook() -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame([{"Section": "Overview"}]).to_excel(writer, sheet_name="Overview", index=False)
    buf.seek(0)
    return buf.getvalue()
```

```python
# backend/routers/exports.py
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from backend.services.report_export_service import build_dashboard_export_workbook

router = APIRouter(prefix="/api/exports", tags=["exports"])

@router.get("/dashboard.xlsx")
def dashboard_export():
    payload = build_dashboard_export_workbook()
    return StreamingResponse(
        iter([payload]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="NBS_Dashboard_Report.xlsx"'},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/test_report_export_api.py -v`
Expected: `PASS` and the response should be a downloadable XLSX.

- [ ] **Step 5: Commit**

```bash
git add backend/services/report_export_service.py backend/routers/exports.py backend/main.py backend/schemas/dashboard.py tests/test_report_export_api.py
git commit -m "feat: add vue analytics report export endpoints"
```

### Task 2: Build reusable SVG chart components for the cockpit

**Files:**
- Create: `frontend/src/components/SvgBarChart.vue`
- Create: `frontend/src/components/SvgLineChart.vue`
- Create: `frontend/src/components/SvgDonutChart.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles.css`
- Test: `frontend/scripts/verify-cockpit-contract.mjs`

- [ ] **Step 1: Write the failing test**

```js
const app = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
if (!app.includes('SvgBarChart') || !app.includes('SvgLineChart') || !app.includes('SvgDonutChart')) {
  throw new Error('Vue cockpit is missing reusable chart components.')
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node scripts/verify-cockpit-contract.mjs`
Expected: fail until the chart components are wired.

- [ ] **Step 3: Write minimal implementation**

```vue
<!-- frontend/src/components/SvgBarChart.vue -->
<script setup>
const props = defineProps({
  rows: { type: Array, default: () => [] },
  valueKey: { type: String, required: true },
  labelKey: { type: String, required: true }
})
</script>
<template>
  <svg viewBox="0 0 640 280" class="chart-surface">
    <g v-for="(row, index) in rows" :key="index">
      <rect :x="40 + index * 48" :y="220 - Number(row[valueKey])" width="28" :height="Number(row[valueKey])" />
      <text :x="40 + index * 48" y="250">{{ row[labelKey] }}</text>
    </g>
  </svg>
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run build`
Expected: Vue build succeeds with the new chart components.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SvgBarChart.vue frontend/src/components/SvgLineChart.vue frontend/src/components/SvgDonutChart.vue frontend/src/App.vue frontend/src/styles.css frontend/scripts/verify-cockpit-contract.mjs
git commit -m "feat: add svg chart primitives for analytics cockpit"
```

### Task 3: Render Streamlit-equivalent analytics and forecast sections in Vue

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/styles.css`
- Test: `frontend/scripts/verify-cockpit-contract.mjs`

- [ ] **Step 1: Write the failing test**

```js
const requiredTokens = [
  'Monthly Trend Chart',
  'Branch Ranking Chart',
  'Specialist Ranking Chart',
  'Forecast Chart',
  'Product Mix Chart',
  'Data Quality Dimensions',
  'Official Forecast',
  'getDashboardAnalytics',
  'getForecastInsights'
]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node scripts/verify-cockpit-contract.mjs`
Expected: fail until the new sections and labels are present.

- [ ] **Step 3: Write minimal implementation**

```vue
<SvgLineChart :rows="analytics?.monthlyTrend || []" x-key="month" y-key="combinedRevenue" />
<SvgBarChart :rows="branchRows" label-key="branch" value-key="totalRevenue" />
<SvgBarChart :rows="specialistRows" label-key="specialist" value-key="totalRevenue" />
<SvgDonutChart :rows="productMixRows" label-key="product" value-key="revenue" />
<SvgLineChart :rows="forecastInsights?.daily || []" x-key="date" y-key="consensus" />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run build && node scripts/verify-cockpit-contract.mjs`
Expected: build passes and cockpit contract verifies.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.vue frontend/src/lib/api.js frontend/src/styles.css frontend/scripts/verify-cockpit-contract.mjs
git commit -m "feat: add vue analytics and forecast visualizations"
```

### Task 4: Add report export actions to the cockpit

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/styles.css`
- Test: `tests/test_report_export_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_forecast_export_contract(monkeypatch):
    client = TestClient(create_app())
    response = client.get("/api/exports/forecast.xlsx")
    assert response.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_report_export_api.py -v`
Expected: fail until the export route exists.

- [ ] **Step 3: Write minimal implementation**

```vue
<button @click="downloadReport('dashboard')">下載 Dashboard 報表</button>
<button @click="downloadReport('forecast')">下載 Forecast 報表</button>
```

```js
async function downloadReport(kind) {
  const response = await fetch(`/api/exports/${kind}.xlsx`)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${kind}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/test_report_export_api.py -v && npm run build`
Expected: API tests and frontend build pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.vue frontend/src/lib/api.js frontend/src/styles.css tests/test_report_export_api.py
git commit -m "feat: add cockpit report export actions"
```

### Task 5: Verify end-to-end cockpit behavior in the browser

**Files:**
- Modify: `frontend/src/App.vue` if any layout fixes are needed
- Test: manual browser verification on `http://127.0.0.1:5174/`

- [ ] **Step 1: Write the failing test**

```text
Open the cockpit in Chrome and confirm:
1. Charts render non-empty.
2. Forecast chart shows the daily forecast series.
3. Download buttons trigger XLSX downloads.
4. No layout overlaps on the analytics sections.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run dev -- --host 127.0.0.1 --port 5174`
Expected: browser can load the app, but charts may still be empty until data wiring is done.

- [ ] **Step 3: Write minimal implementation**

```text
If a chart is blank, first check the API payload in DevTools, then verify the SVG rows are bound from the backend read model.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run build` and visually inspect `http://127.0.0.1:5174/`
Expected: analytics sections are populated and download actions work.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.vue frontend/src/styles.css
git commit -m "feat: verify vue analytics cockpit end to end"
```

