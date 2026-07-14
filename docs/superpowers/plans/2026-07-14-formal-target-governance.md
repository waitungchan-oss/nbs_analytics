# Formal Target Configuration and Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 建立可驗證、可版本化、可審計的正式目標設定，並接入 Vue Management Decisions。

**Architecture:** 新增獨立 target governance service 管理 `decision_targets.json` 與 history JSONL；FastAPI 提供 typed GET/PUT；P2-5 decision service 只採用 approved config；Vue 表單直接使用 API contract。

**Tech Stack:** Python, FastAPI, Pydantic, atomic `os.replace`, Vue 3 Composition API, Vite, pytest。

## Global Constraints

- 不修改 revenue scope、baseline、Facts、Forecast、WAPE、SQLite 或 upload。
- 目標設定與 `rules_config.json` 分離。
- 只有 `approved` 設定才進入 P2-5 decision evaluation。
- 所有保存都必須留下 revision、修改人、原因與時間。

---

### Task 1: Target governance service and validation

**Files:**
- Create: `backend/services/target_governance_service.py`
- Modify: `backend/services/decision_service.py`
- Create: `tests/test_target_governance_service.py`
- Modify: `.gitignore`

- [x] Write failing tests for valid config, invalid scope/month/amount, duplicate month, approved without approver, revision and history.
- [x] Implement schema validation and `not_configured` fallback.
- [x] Implement atomic save and append-only history.
- [x] Make decision loader expose approval status and make only approved targets active.
- [x] Run focused tests and commit.

### Task 2: Target configuration API

**Files:**
- Create: `backend/schemas/target_governance.py`
- Create: `backend/routers/target_governance.py`
- Modify: `backend/main.py`
- Create: `tests/test_target_governance_api.py`

- [x] Add typed `GET /api/decisions/targets` and `PUT /api/decisions/targets`.
- [x] Return 422 with field-level validation messages; do not partially write invalid config.
- [x] Include current revision and recent history in GET response.
- [x] Add OpenAPI and API behavior tests.
- [x] Run focused API tests and commit.

### Task 3: Vue target governance form

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Add target config load/save clients.
- [x] Add editable month target rows, draft/approved selector, updatedBy and changeReason fields.
- [x] Show current scope/population/revision and recent history.
- [x] After save, reload target config and decision overview; display validation errors without clearing existing data.
- [x] Run `npm run verify` and `npm run build`; commit.

### Task 4: Full verification and Hermes

**Files:**
- Modify: none unless verification exposes a P2-6 regression.

- [x] Run target governance focused tests and complete pytest.
- [x] Run Vue checks and system acceptance.
- [x] Smoke test GET/PUT using temporary config paths and confirm invalid payload does not write.
- [x] Run Hermes and require SQLite integrity ok, baseline matched, generation matched, and issues empty.
- [x] Confirm clean worktree and report commit before merge options.
