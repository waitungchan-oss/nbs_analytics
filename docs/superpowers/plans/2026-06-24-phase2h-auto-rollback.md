# Phase 2H Auto Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject core-drift uploads and restore the last verified SQLite state with preserved audit evidence.

**Architecture:** Database restoration and rollback decisions live in tested backend services. Streamlit coordinates cache rebuild and history persistence after the restored database is active.

**Tech Stack:** Python 3.10, SQLite, Streamlit, FastAPI/Pydantic, Vue 3, pytest.

---

### Task 1: Lock Database Restore Behaviour

**Files:**
- Create: `tests/test_database_rollback.py`
- Modify: `database.py`

- [x] Write a failing test that creates a backup, mutates the live database, restores it, and verifies both quarantine and exact original rows.
- [x] Write a failing test that rejects a corrupt backup without replacing the live database.
- [x] Implement integrity validation, quarantine copy, temporary restore file, atomic replacement, and sidecar cleanup.
- [x] Run database rollback tests.

### Task 2: Lock Rollback State Machine

**Files:**
- Create: `tests/test_upload_rollback_service.py`
- Create: `backend/services/upload_rollback_service.py`

- [x] Test that matched core validation returns `accepted` without calling restore.
- [x] Test that drift triggers restore, cache rebuild, and second Gate verification.
- [x] Test that a drifting post-restore Gate returns `rollback_failed`.
- [x] Implement the minimal injectable orchestration service.
- [x] Run rollback service tests.

### Task 3: Extend Phase 2G Audit Contract

**Files:**
- Modify: `backend/services/stability_history_service.py`
- Modify: `backend/schemas/dashboard.py`
- Modify: `tests/test_stability_history_service.py`
- Modify: `tests/test_dashboard_api.py`

- [x] Add rollback metadata fields with lazy migration for the existing history table.
- [x] Preserve post-rollback Gate JSON and errors.
- [x] Extend API response contracts.
- [x] Run history and API tests.

### Task 4: Integrate Streamlit Upload Rollback

**Files:**
- Modify: `app.py`
- Modify: `tests/test_streamlit_upload_feedback_contract.py`

- [x] Serialize upload writes with a non-blocking process lock.
- [x] Run rollback only for core drift.
- [x] Reset and rebuild Streamlit cache after restoration.
- [x] Persist history after rollback completes.
- [x] Render accepted, rejected and rollback-failed feedback.
- [x] Run Streamlit contract tests.

### Task 5: Update Vue History Status

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Display rollback status and distinct rejected/failed badges.
- [x] Keep Vue read-only.
- [x] Run Vue contract verification and production build.

### Task 6: Full Verification

**Files:**
- No production file changes expected.

- [x] Run the complete Python regression suite.
- [x] Run Python compilation and Vue production build.
- [x] Verify current official baseline remains matched.
- [ ] Verify HTTP services and browser-visible history status. Blocked by managed environment `EPERM` on localhost port binding; FastAPI TestClient and direct data checks passed instead.
