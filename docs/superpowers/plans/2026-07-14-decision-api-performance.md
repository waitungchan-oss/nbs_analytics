# Decision API Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 generation-aware persistent cache 將 Decision API warm response 降至 300ms 內。

**Architecture:** 為 Data Quality 與 Dashboard Facts Read Model 建立版本化 JSON cache，key 綁定當前 data generation。Decision router 對所有上游使用同一 generation token，並以 profile script 驗證 warm latency。

**Tech Stack:** Python, FastAPI, pytest, JSON, atomic `os.replace`, existing `.nbs_runtime_cache`.

## Global Constraints

- 不修改正式口徑、baseline、SQLite、upload、rollback、Forecast 模型、目標或 Decision 規則。
- Cache 不使用固定 TTL；只按 generation token 與 service version 失效。
- Cache 損壞必須重建，不能回傳舊 generation payload。
- 實作遵守 TDD：每項 production behavior 先看到對應測試因功能缺失而失敗。

---

### Task 1: Generation-aware Data Quality cache

**Files:**
- Modify: `backend/services/data_quality_service.py`
- Create: `tests/test_data_quality_cache.py`

**Interfaces:**
- Produces: `build_data_quality_cached(*, db_path: str | Path, generation_token: str, cache_dir: str | Path | None = None) -> dict`
- Output includes `cacheStatus` (`rebuilt` or `hit`) and `generationToken`.

- [ ] Write a failing test using a temporary SQLite loader stub and cache directory; assert first call is `rebuilt`, second call is `hit`, and source frames are loaded once.
- [ ] Run `.venv/bin/python -m pytest tests/test_data_quality_cache.py -q` and confirm failure because `build_data_quality_cached` does not exist.
- [ ] Implement a versioned SHA-256 cache key, validated JSON wrapper, corruption-as-miss behavior, and atomic write.
- [ ] Add failing tests for changed generation and corrupted JSON; confirm each rebuilds and never returns stale payload.
- [ ] Run data-quality tests and commit `feat: cache data quality by generation`.

### Task 2: Dashboard Facts Read Model cache

**Files:**
- Modify: `backend/services/dashboard_facts_service.py`
- Modify: `tests/test_dashboard_facts_service.py`

**Interfaces:**
- Keeps: `build_dashboard_facts_read_model(...) -> dict`
- Output adds `readModelCacheStatus` while preserving all existing fields and calculations.

- [ ] Write a failing test that calls the read model twice and asserts analytics construction runs once with `rebuilt` then `hit`.
- [ ] Run `.venv/bin/python -m pytest tests/test_dashboard_facts_service.py -q` and confirm the new assertion fails.
- [ ] Implement a versioned read-model cache keyed by the existing Facts cache key, with validated JSON and atomic write.
- [ ] Add a failing generation-change test and implement automatic miss/rebuild for the new Facts cache key.
- [ ] Run dashboard Facts tests and commit `feat: cache dashboard read model`.

### Task 3: Decision API integration and performance contract

**Files:**
- Modify: `backend/routers/decisions.py`
- Modify: `backend/services/decision_service.py`
- Modify: `backend/schemas/decisions.py`
- Modify: `tests/test_decision_api.py`
- Create: `scripts/profile_decision_api.py`
- Create: `tests/test_decision_api_performance.py`

**Interfaces:**
- Decision router passes the same `generation_token` to Facts and Data Quality cached builders.
- Profile CLI: `.venv/bin/python scripts/profile_decision_api.py --warm-limit-ms 300 --runs 5`.

- [ ] Write a failing API test asserting `build_data_quality_cached` receives the current DB path and generation token.
- [ ] Run Decision API tests and confirm failure because the router still calls uncached `build_data_quality`.
- [ ] Switch the router to the cached service and expose both cache statuses in provenance.
- [ ] Write the profile CLI and tests for median calculation, JSON output, and nonzero exit when the warm limit is exceeded.
- [ ] Run focused tests, profile the real endpoint, and commit `perf: reuse generation read models in decision api`.

### Task 4: Full verification and handoff

**Files:**
- Modify: `docs/superpowers/plans/2026-07-14-decision-api-performance.md`

- [ ] Run complete pytest and confirm zero failures.
- [ ] Run Vue `npm run verify` and `npm run build`.
- [ ] Run the real Decision API profile and record cold/warm results.
- [ ] Run `scripts/system_manager.py acceptance` and Hermes post-change check.
- [ ] Confirm SQLite integrity ok, generation matched, 2026-05 baseline `HKD 12,057,968` matched, and clean worktree.
- [ ] Mark this plan complete and commit `docs: mark decision api performance verified`.
