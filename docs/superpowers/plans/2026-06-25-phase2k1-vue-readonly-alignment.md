# Phase 2K-1 Vue Read-only Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reconciled yearly, monthly, ranking, and product views to the Vue read-only cockpit.

**Architecture:** A dedicated analytics service creates all views from the same official `s1` and `s2` facts. FastAPI fixes the response contract, while Vue only formats and displays server-provided values.

**Tech Stack:** Python 3.10, Pandas, FastAPI/Pydantic, Vue 3, pytest, Vite.

---

### Task 1: Lock Analytics Contracts

**Files:**
- Create: `tests/test_dashboard_analytics_service.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Test annual and monthly totals.
- [x] Test full ranking and product reconciliation.
- [x] Test fixed `/api/dashboard/analytics` response fields.
- [x] Test Vue contract tokens.
- [x] Run tests and confirm the new interfaces are absent.

### Task 2: Implement Analytics Read Model

**Files:**
- Create: `backend/services/dashboard_analytics_service.py`

- [x] Build filtered annual and monthly channel summaries.
- [x] Build full branch and specialist rankings.
- [x] Build channel product composition.
- [x] Return explicit reconciliation checks.
- [x] Run service tests.

### Task 3: Add FastAPI Contract

**Files:**
- Modify: `backend/schemas/dashboard.py`
- Modify: `backend/routers/dashboard.py`

- [x] Add response models for every analytics section.
- [x] Add `POST /api/dashboard/analytics`.
- [x] Run API contract tests.

### Task 4: Add Vue Views

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles.css`

- [x] Load analytics with the existing filters.
- [x] Render annual cards and monthly trend.
- [x] Render expandable full ranking tables.
- [x] Render branch and specialist product composition.
- [x] Display reconciliation status.
- [x] Keep deferred AI cache rebuilds inside Streamlit's explicit `補算 AI` action.
- [x] Run Vue contract and production build.

### Task 5: Full Acceptance

**Files:**
- No production changes expected.

- [x] Run complete Python tests and compilation.
- [x] Verify the May 2026 `HKD 12,057,968` baseline.
- [x] Restart services and verify the analytics API over HTTP.
- [x] Verify Vue rendering in the browser.
- [x] Reconfirm that Vue read-only views never trigger AI/cache recompute by themselves.
