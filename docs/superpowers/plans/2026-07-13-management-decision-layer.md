# Management Decision Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立唯讀的目標、預警與管理層決策 API，並在 Vue 展示其結果。

**Architecture:** 新增 decision service 聚合既有 Facts、Forecast、Data Quality、System Health 與可選 target config；FastAPI 提供 typed overview endpoint；Vue 只消費 read model，不自行計算。

**Tech Stack:** Python, FastAPI, Pydantic, Vue 3 Composition API, Vite, pytest。

## Global Constraints

- 不修改正式口徑、baseline、SQLite schema、upload、rollback、Forecast 模型、WAPE 或報表。
- `decision_targets.json` 是唯讀設定來源，不由 upload 或 pipeline 回寫。
- 沒有目標設定時回傳 `not_configured`，不得把歷史實際值當成目標。
- Vue 只展示 API 回傳的 decision facts，不做 revenue、attainment、gap 或 alert 計算。

---

### Task 1: Decision read model and rules

**Files:**
- Create: `backend/services/decision_service.py`
- Create: `backend/schemas/decisions.py`
- Create: `tests/test_decision_service.py`

- [x] Write failing unit tests for target matching, missing targets, forecast gap, quality/health/baseline alerts.
- [x] Implement target config loading with explicit `not_configured` fallback.
- [x] Implement monthly target evaluation and deterministic alert severity.
- [x] Implement decision cards with evidence references and no write actions.
- [x] Run focused tests and commit.

### Task 2: FastAPI decision overview endpoint

**Files:**
- Create: `backend/routers/decisions.py`
- Modify: `backend/main.py`
- Create: `tests/test_decision_api.py`

- [x] Add `GET /api/decisions/overview` with typed response model.
- [x] Reuse existing service outputs and expose provenance/generation/cache status.
- [x] Add API contract and OpenAPI tests.
- [x] Run focused API tests and commit.

### Task 3: Vue management decision panel

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Add `getDecisionOverview()` and non-blocking load handling.
- [x] Add Management Decisions navigation and panel for target status, alerts and decision cards.
- [x] Bind values directly to API response; do not calculate in Vue.
- [x] Run `npm run verify` and `npm run build`; commit.

### Task 4: Full verification and Hermes

**Files:**
- Modify: none unless verification exposes a P2-5 regression.

- [x] Run decision targeted tests, full pytest, Vue checks and system acceptance.
- [x] Smoke test `/api/decisions/overview` and confirm scope/baseline provenance.
- [x] Run Hermes read-only inspection; require pass, SQLite integrity ok, baseline matched, generation matched, and issues empty.
- [x] Confirm clean worktree and report commit before merge options.
