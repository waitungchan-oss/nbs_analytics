# Phase 2K-2 Data Quality and Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stable read-only Data Quality and cached Forecast views to FastAPI and Vue.

**Architecture:** Data Quality is calculated from SQLite by a pure service. Forecast is cache-only and never trains models during API requests. A dedicated insights router exposes both contracts.

**Tech Stack:** Python 3.10, Pandas, pickle, FastAPI/Pydantic, Vue 3, pytest, Vite.

---

### Task 1: Lock Contracts

- [x] Add Data Quality service tests.
- [x] Add Forecast cache-reader tests.
- [x] Add API response-contract tests.
- [x] Add Vue contract tokens.
- [x] Confirm tests fail because interfaces are absent.

### Task 2: Implement Pure Services

- [x] Implement five Data Quality dimensions and summary.
- [x] Implement newest valid AI-cache selection.
- [x] Implement horizon-aware official consensus.
- [x] Build Daily, 7-Day Macro, Month-End, and WAPE summaries.
- [x] Run focused service tests.

### Task 3: Add Insights API

- [x] Add Pydantic response contracts.
- [x] Add `/api/insights/data-quality`.
- [x] Add `/api/insights/forecast`.
- [x] Run API tests.

### Task 4: Add Vue Views

- [x] Add API client methods.
- [x] Add Data Quality scorecard and dimension table.
- [x] Add Forecast KPI cards and Daily table.
- [x] Add 7-Day and Month-End summaries.
- [x] Show cache readiness and source timestamp.
- [x] Confirm deferred AI cache still requires the Streamlit `補算 AI` action.
- [x] Run Vue verify and build.

### Task 5: Full Acceptance

- [x] Run complete Python tests and compilation.
- [x] Restart services and verify endpoints over HTTP.
- [x] Verify Vue rendering in browser.
- [x] Reconfirm `HKD 12,057,968` baseline.
- [x] Confirm deferred cache behavior does not change the read-only API contract.
