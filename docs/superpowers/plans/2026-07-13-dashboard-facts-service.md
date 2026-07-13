# Dashboard Facts Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 建立可被 Streamlit 使用的共用 Dashboard Facts read model，依 generation/cache token 重用結果，同時保留現有 DataFrame cache 與正式口徑。

**Architecture:** 新增 `backend/services/dashboard_facts_service.py`，以明確 `db_path` 載入 SQLite、套用既有 `build_revenue_scope_frames` 與 `build_dashboard_data_excluding_receipt_types`，產生包含 facts DataFrames、scope audit、metadata 的 payload，並以 version + generation token 做 pickle cache。`app_workflows._load_and_compute_cache()` 只負責呼叫 service 並把 facts metadata 放入既有 Streamlit session cache。

**Tech Stack:** Python 3.10+, pandas, SQLite, pickle, pytest, Streamlit session state。

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 全部分社 + 全部專職銷售組 baseline 必須維持 HKD 12,057,968。
- 不改 SQLite schema、upload、rollback、AI cache、Export cache 或 FastAPI/Vue contract。
- 所有讀取使用明確 `db_path`；Facts cache 只讀，不得寫入正式 DB。
- Facts cache miss 需明確重建；失敗不得以空資料或舊資料冒充成功。

---

### Task 1: 建立 Dashboard Facts Service 與 cache contract

**Files:**
- Create: `backend/services/dashboard_facts_service.py`
- Test: `tests/test_dashboard_facts_service.py`

**Interfaces:**
- Produces `FACTS_SERVICE_VERSION`, `facts_cache_path(cache_dir, cache_key)`, `build_dashboard_facts(*, db_path, generation_token, branch_mapping, target_branches_s3, cruise_depts, sales_rep_list, cache_dir=None) -> dict`.
- Payload keys: `serviceVersion`, `cacheKey`, `generationToken`, `dbPath`, `scopeAudit`, `rawTour`, `rawOthers`, `analysisTour`, `analysisOthers`, `branchFacts`, `specialistFacts`.

- [ ] **Step 1: Write failing tests**

```python
def test_build_dashboard_facts_returns_scope_and_summary_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "load_all_data_from_db", lambda *, db_path: (_tour_frame(), _others_frame()))
    payload = service.build_dashboard_facts(
        db_path=tmp_path / "facts.db",
        generation_token="1:test-sha",
        branch_mapping={"01": "銅鑼灣分社"},
        target_branches_s3=["銅鑼灣分社"],
        cruise_depts=[],
        sales_rep_list=["YTLAU 刘元太"],
        cache_dir=tmp_path / "cache",
    )
    assert payload["generationToken"] == "1:test-sha"
    assert payload["scopeAudit"]["scope_label"] == "不含掛賬核銷與TT退款轉團款"
    assert {"analysisTour", "analysisOthers", "branchFacts", "specialistFacts"} <= payload.keys()

def test_generation_token_changes_cache_key_and_forces_rebuild(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(service, "load_all_data_from_db", lambda *, db_path: (calls.append(db_path) or (_tour_frame(), _others_frame())))
    kwargs = {"db_path": tmp_path / "facts.db", "branch_mapping": {}, "target_branches_s3": [], "cruise_depts": [], "sales_rep_list": [], "cache_dir": tmp_path / "cache"}
    service.build_dashboard_facts(generation_token="1:a", **kwargs)
    service.build_dashboard_facts(generation_token="1:a", **kwargs)
    service.build_dashboard_facts(generation_token="2:b", **kwargs)
    assert len(calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_service.py -q`
Expected: FAIL because the service module and contract do not exist.

- [ ] **Step 3: Implement minimal service**

Implement deterministic cache key from `FACTS_SERVICE_VERSION` and `generation_token`; on hit load a dict from `cache_dir`; on miss call `load_all_data_from_db(db_path=Path(db_path))`, `build_revenue_scope_frames`, and `build_dashboard_data_excluding_receipt_types(..., make_workbook=False)`. Store DataFrames and metadata with an atomic temporary pickle replacement. Validate required payload keys before returning.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_service.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/dashboard_facts_service.py tests/test_dashboard_facts_service.py
git commit -m "feat: add dashboard facts service"
```

### Task 2: 接入 Streamlit cache 並保留 fallback

**Files:**
- Modify: `app_workflows.py:30-75,1135-1210`
- Test: `tests/test_streamlit_upload_feedback_contract.py`

**Interfaces:**
- Consumes `build_dashboard_facts` from Task 1.
- Existing `PROCESSED_DATA_CACHE` keys `t`, `o`, `raw_t`, `raw_o`, `s1`, `s2`, `scope` remain available to all existing renderers.
- Adds `facts_service_version`, `facts_cache_key`, and `facts_cache_status` metadata.

- [ ] **Step 1: Write failing tests**

```python
def test_streamlit_cache_load_uses_dashboard_facts_service():
    source = _workflows_function_source("_load_and_compute_cache")
    assert "build_dashboard_facts(" in source
    assert '"facts_cache_status"' in source
    assert '"facts_cache_key"' in source

def test_streamlit_facts_service_uses_current_generation_token():
    source = _workflows_function_source("_load_and_compute_cache")
    assert "load_cache_generation(db_path=database_module.DB_FILE)" in source
    assert "generation_token" in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_streamlit_upload_feedback_contract.py -q`
Expected: FAIL because `_load_and_compute_cache()` does not call the new service or expose its metadata.

- [ ] **Step 3: Implement minimal integration**

Import `build_dashboard_facts`; load generation before the facts call; pass the existing current rules and explicit `database_module.DB_FILE`; replace the duplicated raw/scope/summary build in `_load_and_compute_cache()` with payload values. Keep AI and Export cache-key computation unchanged and keep the existing empty-database branch. If facts construction raises, clear the session cache and re-raise the error with the DB path and generation token in the message.

- [ ] **Step 4: Run targeted tests**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_service.py tests/test_streamlit_upload_feedback_contract.py -q`
Expected: all targeted tests pass.

- [ ] **Step 5: Commit**

```bash
git add app_workflows.py tests/test_streamlit_upload_feedback_contract.py
git commit -m "feat: use dashboard facts in streamlit cache"
```

### Task 3: Baseline、compile、服務與 Hermes 驗收

**Files:**
- Modify: none unless verification exposes a P2 regression.
- Test: existing acceptance suites and Hermes checks.

**Interfaces:**
- Verifies Task 1/2 without changing formal calculation behavior.

- [ ] **Step 1: Run compile and targeted regression tests**

Run:
`/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py forecasting.py pipeline.py database.py backend/services/dashboard_facts_service.py backend/services/dashboard_service.py backend/services/dashboard_analytics_service.py`

`/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_service.py tests/test_dashboard_service.py tests/test_phase2_precheck_acceptance.py tests/test_streamlit_upload_feedback_contract.py -q`

- [ ] **Step 2: Run full suite**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest -q`
Expected: zero failures.

- [ ] **Step 3: Run system acceptance and baseline governance**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/system_manager.py acceptance`

Verify 2026-01 through 2026-06 are blocking and matched; verify May is HKD 12,057,968; verify SQLite integrity and generation signature are matched.

- [ ] **Step 4: Run Hermes read-only post-change inspection**

Use the existing Hermes monitoring contract to inspect Git diff, DB integrity, generation/history, acceptance, and test evidence. Hermes must report PASS with no baseline drift, no upload/rollback mutation, and no unexpected files.

- [ ] **Step 5: Commit verification record**

```bash
git status --short --branch
git log -3 --oneline
```

Keep the worktree clean and report the commit hash, test counts, acceptance result, and any runtime caveat.
