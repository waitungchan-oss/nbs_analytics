# Vue Upload Acceptance Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Vue 透明展示既有 upload response 的驗收、回滾、cache generation 與錯誤狀態。

**Architecture:** 保持 FastAPI `/api/upload` 與 upload orchestrator 不變；在前端 API client 集中解析錯誤，在 `App.vue` 以 computed display model 直接讀取 upload response，並補上 static contract checks。

**Tech Stack:** Vue 3 Composition API, native fetch, Vite, FastAPI TestClient, pytest。

## Global Constraints

- 不修改正式口徑、baseline、SQLite、upload write path、rollback 或 Streamlit。
- Vue 不自行計算正式 revenue、ranking、baseline 或 reconciliation。
- Facts refresh 維持由 upload success 後的既有 `loadAll(false)` 執行。
- 所有新增狀態必須直接映射 API response 欄位。

---

### Task 1: API upload error contract

**Files:**
- Modify: `frontend/src/lib/api.js`
- Test: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Add static assertions requiring JSON detail parsing and busy owner handling.
- [x] Run `npm run verify` and confirm the new contract fails.
- [x] Add a shared `readApiError(response)` helper and use it in `uploadMonthlyData`.
- [x] Preserve status code, HTTP status text, JSON `detail`, and busy owner entry point in the thrown message.
- [x] Run `npm run verify` and commit the upload observability implementation.

### Task 2: Upload acceptance display model

**Files:**
- Modify: `frontend/src/App.vue`
- Test: `frontend/scripts/verify-cockpit-contract.mjs`

- [x] Add computed display values for public status, preflight, stability gate, rollback, cache, history, operation and generation.
- [x] Add a result summary panel that binds directly to `uploadResult` fields and shows `writeCommitted` explicitly.
- [x] Keep existing preflight table and upload form behavior unchanged.
- [x] Show refresh failure separately without clearing a successful upload result.
- [x] Run `npm run verify` and `npm run build`; commit `feat: expose vue upload acceptance state`.

### Task 3: Cross-layer verification and Hermes

**Files:**
- Modify: none unless verification exposes a P2-4 regression.

- [x] Run upload API, orchestrator, rollback, and history targeted tests.
- [x] Run full pytest and system acceptance.
- [x] Verify Vue contract/build and `GET /api/dashboard/facts` remains healthy.
- [x] Run Hermes read-only inspection and require pass, SQLite integrity ok, baseline matched, generation matched, and no issues.
- [x] Confirm clean worktree and report commit before offering merge back to `main`.
