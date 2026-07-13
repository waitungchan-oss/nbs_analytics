# Dashboard Facts API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 新增 `/api/dashboard/facts`，讓 FastAPI 使用 P2-1 Facts Service 回傳一致、唯讀、可驗證的 Dashboard read model。

**Architecture:** Facts Service 保持資料建構與 cache 生命週期；新增 read-model helper 將 Facts DataFrame 派生為 API payload。Dashboard router 只負責讀取 generation/rules、呼叫 helper 與套用 Pydantic response model。既有 `/summary`、`/analytics` 不變。

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, pandas, pytest。

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 全部分社 + 全部專職銷售組 baseline 必須維持 HKD 12,057,968。
- 不改 SQLite schema、upload、rollback、Vue、既有 `/summary` 或 `/analytics` contract。
- API 必須使用明確 `DB_FILE` 與 current generation token；不得在 endpoint 寫入 DB。
- 不回傳原始 DataFrame；只回傳標準化 read model 與 provenance metadata。

---

### Task 1: 建立 Facts API read model 與 response schema

**Files:**
- Modify: `backend/services/dashboard_facts_service.py`
- Modify: `backend/schemas/dashboard.py`
- Create: `tests/test_dashboard_facts_api_service.py`

**Interfaces:**
- Add `build_dashboard_facts_read_model(*, db_path, generation_token, branch_mapping, target_branches_s3, cruise_depts, sales_rep_list, cache_dir=None) -> dict`.
- Add `DashboardFactsResponse` with metadata, scope, `kpiTotals`, `monthlyTotals`, rankings, `productTotals`, and `reconciliation`.

- [ ] **Step 1: Write failing tests**

```python
def test_read_model_contains_summary_without_raw_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "build_dashboard_facts", lambda **kwargs: _facts_payload())
    result = service.build_dashboard_facts_read_model(
        db_path=tmp_path / "facts.db",
        generation_token="1:test",
        branch_mapping={}, target_branches_s3=[], cruise_depts=[], sales_rep_list=[], cache_dir=tmp_path / "cache",
    )
    assert result["status"] == "ready"
    assert result["generationToken"] == "1:test"
    assert "kpiTotals" in result
    assert "rawTour" not in result
    assert result["reconciliation"]["status"] == "matched"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_api_service.py -q`
Expected: FAIL because the read-model helper and response contract do not exist.

- [ ] **Step 3: Implement minimal read model and schema**

Call `build_dashboard_facts`, pass `branchFacts` and `specialistFacts` to `build_analytics_from_facts` with empty filters, derive `kpiTotals` from product drilldown/rank totals, copy `monthlyTrend` to `monthlyTotals`, and include only provenance/scope/summary fields. Add a Pydantic model using existing ranking, monthly, product, and reconciliation row models.

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_api_service.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/services/dashboard_facts_service.py backend/schemas/dashboard.py tests/test_dashboard_facts_api_service.py
git commit -m "feat: add dashboard facts api read model"
```

### Task 2: 新增 FastAPI endpoint 與 API contract tests

**Files:**
- Modify: `backend/routers/dashboard.py`
- Create: `tests/test_dashboard_facts_api.py`

**Interfaces:**
- Add `GET /api/dashboard/facts` with `response_model=DashboardFactsResponse`.
- Endpoint uses `Path(DB_FILE)`, `load_cache_generation(db_path=...)`, and `_current_rules()`.
- Existing dashboard endpoints and response keys remain unchanged.

- [ ] **Step 1: Write failing tests**

```python
def test_dashboard_facts_api_returns_read_model(monkeypatch):
    monkeypatch.setattr("backend.routers.dashboard.build_dashboard_facts_read_model", lambda **kwargs: _read_model())
    response = TestClient(create_app()).get("/api/dashboard/facts")
    assert response.status_code == 200
    assert response.json()["generationToken"] == "1:test"
    assert "rawTour" not in response.json()

def test_dashboard_facts_openapi_has_named_response_contract():
    schema = create_app().openapi()
    ref = schema["paths"]["/api/dashboard/facts"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/DashboardFactsResponse")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_api.py -q`
Expected: FAIL because the route and named schema do not exist.

- [ ] **Step 3: Implement minimal endpoint**

Import the response model, facts read-model helper, `DB_FILE`, `Path`, `load_cache_generation`, and `_current_rules`; build the explicit current generation token and call the helper. Let errors surface as FastAPI 500 with the service's diagnostic message; do not catch and replace with empty data.

- [ ] **Step 4: Run targeted API tests**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_api_service.py tests/test_dashboard_facts_api.py tests/test_dashboard_api.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/routers/dashboard.py tests/test_dashboard_facts_api.py
git commit -m "feat: expose dashboard facts api"
```

### Task 3: Full regression、baseline、Hermes 驗收

**Files:**
- Modify: none unless verification exposes a P2-2 regression.

- [ ] **Step 1: Compile and API regression**

Run:
`/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/main.py backend/routers/dashboard.py backend/schemas/dashboard.py backend/services/dashboard_facts_service.py backend/services/dashboard_analytics_service.py`

`/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_dashboard_facts_api_service.py tests/test_dashboard_facts_api.py tests/test_dashboard_api.py tests/test_dashboard_service.py -q`

- [ ] **Step 2: Full test suite**

Run: `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest -q`
Expected: zero failures.

- [ ] **Step 3: Live endpoint and acceptance**

Run `scripts/system_manager.py acceptance`, then verify `GET http://127.0.0.1:8601/api/dashboard/facts` returns HTTP 200, `revenueScope` is the formal scope, and generation metadata is present. Verify May baseline remains HKD 12,057,968.

- [ ] **Step 4: Hermes read-only inspection**

Run the existing `scripts/hermes_post_change_check.py --json`; require PASS, matched SQLite signature, matched upload evidence, no issues, and unchanged monthly baseline governance.

- [ ] **Step 5: Commit verification state**

Run `git status --short --branch` and `git log -3 --oneline`; keep the branch clean and report evidence before offering merge back to `main`.
