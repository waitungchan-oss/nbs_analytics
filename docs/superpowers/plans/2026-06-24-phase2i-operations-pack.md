# Phase 2I Operations Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start, verify, monitor, and stop all NBS services through one cross-platform operations entrypoint.

**Architecture:** A Python process manager provides common lifecycle behavior for macOS and Windows. A focused backend health service aggregates operational signals for FastAPI and Vue.

**Tech Stack:** Python 3.10, subprocess/socket/urllib, FastAPI, SQLite, Vue 3, pytest, Vite.

---

### Task 1: Lock Operations Contracts

**Files:**
- Create: `tests/test_system_manager.py`
- Create: `tests/test_system_health_service.py`
- Create: `tests/test_launchers_contract.py`
- Modify: `tests/test_backend_health.py`

- [x] Test the three service specifications and endpoints.
- [x] Test safe port-conflict detection and log rotation.
- [x] Test health status aggregation for healthy and failed SQLite states.
- [x] Test macOS and Windows launchers call the common manager.
- [x] Run focused tests and confirm Phase 2I interfaces are missing.

### Task 2: Implement Cross-platform Manager

**Files:**
- Create: `scripts/system_manager.py`

- [x] Implement runtime dependency and project-file preflight.
- [x] Implement service startup, PID state, rotating logs, and readiness polling.
- [x] Implement status reporting and stale-PID cleanup.
- [x] Implement graceful then forced shutdown for owned PIDs only.
- [x] Run manager unit tests.

### Task 3: Implement Operational Health API

**Files:**
- Create: `backend/services/system_health_service.py`
- Modify: `backend/routers/health.py`
- Modify: `tests/test_backend_health.py`

- [x] Aggregate SQLite integrity, history, rollback, backups, quarantine, and cache.
- [x] Treat deferred AI cache as a non-blocking runtime state that is only cleared by Streamlit `補算 AI`.
- [x] Derive `ok`, `degraded`, or `critical`.
- [x] Return actionable issue messages.
- [x] Run health tests.

### Task 4: Update Launchers

**Files:**
- Modify: `啟動NBS系統_mac.command`
- Modify: `啟動NBS系統_windows.bat`
- Create: `停止NBS系統_mac.command`
- Create: `停止NBS系統_windows.bat`

- [x] Route start launchers through `system_manager.py start`.
- [x] Add stop launchers through `system_manager.py stop`.
- [x] Preserve UTF-8 output and project-root resolution.
- [x] Run launcher contract tests.

### Task 5: Update Vue Health Monitoring

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Display SQLite integrity, latest acceptance/rollback, and backup/cache footprint.
- [x] Keep deferred AI cache visible without turning health into a startup failure.
- [x] Show degraded and critical states distinctly.
- [x] Run Vue contract verification and production build.

### Task 6: Full Verification

**Files:**
- No production changes expected.

- [x] Run complete Python regression tests.
- [x] Run Python compilation and Vue production build.
- [x] Run manager preflight/status.
- [x] Exercise startup, readiness failure cleanup, logs, and stopped state. HTTP
  readiness is pending a normal local shell because this managed sandbox rejects
  all three localhost binds with `EPERM`.
