# Stability Gate Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split upload acceptance into blocking core revenue-scope validation and non-blocking freshness updates.

**Architecture:** Keep the existing five checks as the audit source, then classify them by stable key sets inside `stability_service.py`. The overall status is derived only from core checks; freshness changes receive their own grouped status and presentation in Streamlit and Excel.

**Tech Stack:** Python 3.10, FastAPI/Pydantic, Streamlit, pandas/openpyxl, pytest.

---

### Task 1: Lock The Classification Contract

**Files:**
- Modify: `tests/test_dashboard_service.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_streamlit_upload_feedback_contract.py`

- [x] Add a test where revenue and scope match while date and row counts change; assert overall status is `matched`, `coreValidation.status` is `matched`, and `freshnessUpdate.status` is `updated`.
- [x] Add a test where revenue changes; assert overall status and `coreValidation.status` are `drift`.
- [x] Add API and Streamlit source contracts for the two grouped sections.
- [x] Run the focused tests and confirm they fail because grouped fields are absent.

### Task 2: Implement Service And Schema Classification

**Files:**
- Modify: `backend/services/stability_service.py`
- Modify: `backend/schemas/dashboard.py`

- [x] Classify `combinedRevenue` and `revenueScope` as core checks.
- [x] Classify `maxDate`, `analysisRows`, and `excludedRows` as freshness checks.
- [x] Add grouped summaries without removing the existing checks or summary.
- [x] Derive the upload gate status and message from core validation only.
- [x] Run focused service and API tests.

### Task 3: Update Upload Acceptance Presentation

**Files:**
- Modify: `app.py`

- [x] Show core validation as the primary acceptance result.
- [x] Show freshness changes as a separate informational section.
- [x] Export `Core Validation`, `Freshness Update`, and full `Gate Checks` worksheets.
- [x] Run Streamlit contract tests.

### Task 4: Verify Current Data And Regression Suite

**Files:**
- No production file changes expected.

- [x] Run the complete backend and upload-feedback test suite.
- [x] Run Python compile checks.
- [x] Run frontend contract verification and production build.
- [x] Execute the live gate and confirm the current result is core matched with freshness updated.
