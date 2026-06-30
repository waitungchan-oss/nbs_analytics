# Phase 2G Stability History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist upload Gate snapshots and expose a read-only acceptance history in the Vue cockpit.

**Architecture:** A focused backend service owns the audit table and JSON serialization. Streamlit records one snapshot after each completed rebuild, FastAPI exposes bounded recent history, and Vue renders a compact table.

**Tech Stack:** Python 3.10, SQLite, FastAPI/Pydantic, Streamlit, Vue 3, pytest, Vite.

---

### Task 1: Define Storage And API Contracts

**Files:**
- Create: `tests/test_stability_history_service.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_streamlit_upload_feedback_contract.py`

- [x] Add a temporary-SQLite test that records and reloads a Gate snapshot.
- [x] Add an API test for `GET /api/stability/history?limit=10`.
- [x] Add a Streamlit source contract requiring history persistence after Gate construction.
- [x] Run focused tests and confirm failure because Phase 2G interfaces do not exist.

### Task 2: Implement Persistence And Read API

**Files:**
- Create: `backend/services/stability_history_service.py`
- Create: `backend/routers/stability.py`
- Modify: `backend/schemas/dashboard.py`
- Modify: `backend/main.py`

- [x] Create the audit table lazily with indexed creation timestamp.
- [x] Serialize lists and Gate payloads as UTF-8 JSON.
- [x] Return newest-first bounded history records.
- [x] Register the read-only stability router.
- [x] Run service and API tests.

### Task 3: Persist Streamlit Upload Results

**Files:**
- Modify: `app.py`

- [x] Capture uploaded filenames and the completed upload audit context.
- [x] Record the Gate snapshot after cache rebuild and Gate construction.
- [x] Catch persistence errors and expose a precise warning without failing the upload.
- [x] Run Streamlit contract tests.

### Task 4: Add Vue Acceptance History

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Fetch stability history with health and dashboard context.
- [x] Add an Acceptance History navigation item and recent-record table.
- [x] Distinguish matched, drift, updated, and stable statuses.
- [x] Run Vue contract verification and production build.

### Task 5: Full Verification

**Files:**
- No production file changes expected.

- [x] Run the full Python regression suite.
- [x] Run Python compilation.
- [x] Restart API and Vue development servers.
- [x] Verify endpoint output and browser-visible history panel.
