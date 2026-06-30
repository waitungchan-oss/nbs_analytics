# Phase 2J Operational Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable health history, 3 GB backup governance, isolated recovery drills, diagnostic bundles, and real HTTP acceptance to the Phase 2I operations layer.

**Architecture:** Focused backend services own monitoring, retention, restore drills, and diagnostics. `scripts/system_manager.py` exposes those services as cross-platform commands while keeping business data and Streamlit session state out of the operations layer.

**Tech Stack:** Python 3.10, SQLite backup API, JSONL, ZIP, urllib, FastAPI, pytest, Vue 3, Vite.

---

### Task 1: Lock Phase 2J Contracts

**Files:**
- Create: `tests/test_operational_monitor_service.py`
- Create: `tests/test_backup_retention_service.py`
- Create: `tests/test_restore_drill_service.py`
- Create: `tests/test_diagnostics_service.py`
- Modify: `tests/test_system_manager.py`

- [x] Test compact bounded JSONL health history and malformed-line tolerance.
- [x] Test 3 GB warning threshold and backup retention tiers.
- [x] Test protected-backup preservation and quarantine exclusion.
- [x] Test restore drill uses an isolated target and validates baseline callbacks.
- [x] Test diagnostic ZIP contents and forbidden-file exclusions.
- [x] Test new manager command parser and HTTP acceptance behavior.
- [x] Run focused tests and confirm failure because Phase 2J modules are absent.

### Task 2: Implement Monitoring History

**Files:**
- Create: `backend/services/operational_monitor_service.py`
- Modify: `backend/services/system_health_service.py`
- Modify: `backend/routers/health.py`

- [x] Build compact snapshots from health and endpoint probes.
- [x] Append snapshots atomically to bounded UTF-8 JSONL.
- [x] Expose recent operational history from the health API.
- [x] Report backup-capacity warning at 3 GB.
- [x] Run monitoring and health tests.

### Task 3: Implement Backup Governance

**Files:**
- Create: `backend/services/backup_retention_service.py`

- [x] Parse only valid timestamped backup filenames.
- [x] Select daily, weekly, monthly, and protected retention sets.
- [x] Return dry-run plan with kept/deleted bytes and 3 GB warning.
- [x] Delete only planned eligible backups when apply is explicitly requested.
- [x] Run retention tests.

### Task 4: Implement Isolated Restore Drill

**Files:**
- Create: `backend/services/restore_drill_service.py`
- Create: `scripts/phase2j_baseline_check.py`

- [x] Select the newest valid backup unless a path is supplied.
- [x] Restore into a temporary SQLite target using SQLite backup semantics.
- [x] Validate integrity and run the established Phase 2 baseline checks.
- [x] Persist a compact drill report without modifying the live database.
- [x] Run restore-drill tests and baseline script tests.

### Task 5: Implement Diagnostic Package

**Files:**
- Create: `backend/services/diagnostics_service.py`

- [x] Collect compact status, health, history, acceptance, retention, and drill reports.
- [x] Include bounded tails for Streamlit, API, and Vue logs.
- [x] Include environment versions without secrets or business records.
- [x] Write a timestamped ZIP and manifest under `.nbs_runtime/diagnostics`.
- [x] Run diagnostic-package tests.

### Task 6: Extend Operations Commands

**Files:**
- Modify: `scripts/system_manager.py`
- Modify: `PHASE2I_OPERATIONS.md`

- [x] Add `monitor`, `retention`, `drill`, `diagnose`, and `acceptance`.
- [x] Keep start/status/stop contracts unchanged.
- [x] Print concise actionable results and output paths.
- [x] Document dry-run versus apply behavior and the 3 GB threshold.
- [x] Keep deferred AI cache visible as a non-blocking state and leave full rebuilds to Streamlit `補算 AI`.
- [x] Run manager and launcher tests.

### Task 7: Full Verification

**Files:**
- No production changes expected.

- [x] Run the complete Python regression suite.
- [x] Run Python compilation and Vue contract/build.
- [x] Start all services in the unrestricted local environment.
- [x] Verify Streamlit, API Docs, Health, and Vue over HTTP.
- [x] Verify a repeated start does not duplicate processes.
- [x] Run monitor, retention dry-run/apply, restore drill, and diagnose.
- [x] Stop all services and confirm no managed PID remains, then restart for handoff.
- [x] Confirm deferred AI cache does not alter the operations acceptance path.
