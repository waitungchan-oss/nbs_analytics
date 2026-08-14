# NBS Memory Hub Streamlit UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在現有 Streamlit cockpit 增加只讀 Memory Hub tab，讓使用者可觀察 immutable catalog、query、ACL 與 source drill-down，而不改變 canonical authority、recall default 或任何正式資料。

**Architecture:** 建立 `MemoryHubUiService` 作為 C-0/C-1 `MemoryHubService` 的 bounded read model adapter；它只接受 deployment-owned、read-only catalog provider，沒有 provider 時回傳 `catalog_missing`。新增獨立 rendering module，再由 `app_pages.py` 將 tab 接入既有 cockpit；UI 不呼叫 catalog builder、不掃描 repository、不寫入任何狀態。

**Tech Stack:** Python 3、dataclasses、既有 Memory Hub models/catalog/service、Streamlit、pytest、py_compile；不新增 runtime dependency、資料庫或外部 API。

## Global Constraints

- 只允許 `governance_document`、`verified_evidence`、`approved_skill` 三種 source kind。
- Memory Hub UI、catalog、query result 與 projection 都是 read-only derived views；canonical artifacts 才是真相來源。
- 不修改 SQLite、baseline、revenue scope、business rules、export schema、Git、Graph authority、approval、dispatch 或 workflow state。
- 不呼叫 `build_catalog()`；沒有明確 catalog provider 時顯示 `catalog_missing`，不得自動建立 catalog／snapshot。
- 普通 workflow 的 recall default 維持 disabled；writer 維持 disabled；shadow mode 維持 enabled。
- 所有 UI payload 必須 bounded；不得顯示 secret、raw prompt、raw artifact content 或絕對 filesystem path。
- Query limits 固定為 `maxItems=3`、`maxBytes=6000`、`timeoutMs=800`。
- stale、unknown、tampered、scope mismatch、timeout、provider unavailable 與 invalid catalog 必須 fail-closed 或顯示明確 diagnostic。
- 每個 Task 僅修改其 allowlisted files；每個 Task 完成後必須 focused tests、py_compile、diff-check 與 findings-first Review。

---

### Task 1: Memory Hub UI read model adapter

**Files:**
- Create: `backend/agents/memory_hub_ui_service.py`
- Test: `tests/test_memory_hub_ui_service.py`

**Interfaces:**
- Consumes: `MemoryCatalog | None`, `MemoryHubService`, `MemoryQuery`, `RuntimeIdentity`, `SourceResolution`。
- Produces: frozen `MemoryHubUiReadModel`；`MemoryHubUiService(catalog_provider, *, project_id)`；`catalog_status() -> MemoryHubUiReadModel`；`query(*, query: str, consumer_id: str, scope: str, memory_kinds: tuple[str, ...], team_id: str | None) -> MemoryHubUiReadModel`；`resolve_source(source_id: str, *, consumer_id: str, team_id: str | None) -> MemoryHubUiReadModel`。
- `catalog_provider` 是 deployment-owned zero-argument callable，回傳 `MemoryCatalog | None`；沒有 provider 時傳 `None`。Adapter 不接受 builder、raw path scan 或 write callback。

- [x] **Step 1: Write failing tests**

```python
def test_missing_provider_is_explicitly_read_only_and_bounded():
    result = MemoryHubUiService(lambda: None, project_id="nbs").catalog_status()
    assert result.status == "catalog_missing"
    assert result.records == ()
    assert result.diagnostics == ("catalog_missing",)

def test_ready_query_preserves_record_and_acl_evidence(fake_catalog):
    service = MemoryHubUiService(lambda: fake_catalog, project_id="nbs")
    result = service.query(query="governance", consumer_id="review-agent", scope="project", memory_kinds=("governance",), team_id=None)
    assert result.status == "ready"
    assert result.records[0].memory_id == fake_catalog.records[0].memory_id
    assert result.records[0].source_count == 1
    assert result.decisions[0].decision in {"allow", "deny", "blocked"}

def test_invalid_or_stale_source_resolves_fail_closed(fake_catalog):
    result = MemoryHubUiService(lambda: fake_catalog, project_id="nbs").resolve_source("missing", consumer_id="review-agent", team_id=None)
    assert result.status in {"empty", "blocked"}
    assert result.artifact_ref is None
```

- [x] **Step 2: Run tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_hub_ui_service.py -q
```

Expected: collection failure because `backend.agents.memory_hub_ui_service` does not exist.

- [x] **Step 3: Implement the bounded adapter**

Use frozen dataclasses with explicit fields:

```python
@dataclass(frozen=True)
class MemoryHubUiReadModel:
    status: str
    catalog: dict[str, object]
    records: tuple[dict[str, object], ...]
    decisions: tuple[dict[str, object], ...]
    source: dict[str, object] | None
    diagnostics: tuple[str, ...]
    fingerprint: str
```

`catalog_status()` must expose only catalog fingerprint, built-from head, policy fingerprint, source count, record count and status. `query()` must build fixed-limit `MemoryQuery`, fixed `RuntimeIdentity`, call `MemoryHubService.query()`, and convert records／ACL decisions to bounded dictionaries. `resolve_source()` must call `MemoryHubService.resolve_source()` and never read the artifact body. Catch `MemoryHubCatalogError`, `MemoryHubSchemaError`, `ValueError`, `OSError` and map them to `invalid_catalog`, `blocked_identity`, `query_invalid` or `source_unavailable` without raising into Streamlit.

- [x] **Step 4: Run focused GREEN verification**

```bash
.venv/bin/python -m pytest tests/test_memory_hub_ui_service.py -q
.venv/bin/python -m py_compile backend/agents/memory_hub_ui_service.py tests/test_memory_hub_ui_service.py
git diff --check
```

- [x] **Step 5: Commit and request strict Review**

```bash
git add backend/agents/memory_hub_ui_service.py tests/test_memory_hub_ui_service.py
git commit -m "feat: add memory hub ui read model"
```

Review must confirm no builder call, no filesystem scan, no raw artifact content and bounded failure statuses.

### Task 2: Streamlit Memory Hub rendering contract

**Files:**
- Create: `memory_hub_rendering.py`
- Test: `tests/test_memory_hub_rendering.py`

**Interfaces:**
- Consumes: `MemoryHubUiReadModel` and query/source callbacks `Callable[..., MemoryHubUiReadModel]`。
- Produces: `render_memory_hub(model: MemoryHubUiReadModel, *, query_callback, source_callback, st_module=streamlit) -> None` plus pure row helpers `_catalog_status_rows`, `_record_rows`, `_decision_rows`, `_source_rows` for contract tests。

- [x] **Step 1: Write failing rendering tests**

Use a minimal fake Streamlit object and assert:

```python
def test_missing_catalog_copy_is_read_only(fake_streamlit, missing_model):
    render_memory_hub(missing_model, query_callback=None, source_callback=None, st_module=fake_streamlit)
    assert "尚無已建 Memory Hub catalog；此頁不會自行建立或更新 catalog。" in fake_streamlit.messages
    assert not fake_streamlit.buttons_with_write_intent

def test_ready_rows_hide_absolute_paths_and_raw_content(ready_model):
    rows = _record_rows(ready_model)
    assert rows[0]["Memory ID"]
    assert "artifact content" not in str(rows)
    assert all("/Users/" not in str(row) for row in rows)
```

- [x] **Step 2: Run tests to verify RED**

```bash
.venv/bin/python -m pytest tests/test_memory_hub_rendering.py -q
```

Expected: missing-module failure.

- [x] **Step 3: Implement read-only rendering**

Render a title, authority notice, catalog status card, bounded query controls, record dataframe, ACL／failure panel and source drill-down. Do not add `st.button` actions that call a builder, writer, approval or refresh mutation. A query submit may call only the injected read-only query callback. Use existing theme tokens and existing table conventions; do not render raw source bodies or absolute paths.

- [x] **Step 4: Run GREEN verification**

```bash
.venv/bin/python -m pytest tests/test_memory_hub_rendering.py -q
.venv/bin/python -m py_compile memory_hub_rendering.py tests/test_memory_hub_rendering.py
git diff --check
```

- [x] **Step 5: Commit and request strict Review**

```bash
git add memory_hub_rendering.py tests/test_memory_hub_rendering.py
git commit -m "feat: render read-only memory hub tab"
```

Review must verify copy, bounded display, no write controls and no authority leakage.

### Task 3: Existing Streamlit cockpit integration

**Files:**
- Modify: `app_pages.py: imports, _render_agent_operations_tab adjacency, main tab declarations`
- Test: `tests/test_app_pages_memory_hub.py`

**Interfaces:**
- Consumes: `MemoryHubUiService`, `MemoryHubUiReadModel`, `render_memory_hub`。
- Produces: fifth top-level tab labelled `Memory Hub` and an explicit provider injection point. When no deployment-owned provider is configured, pass `lambda: None` and render `catalog_missing`; never auto-discover source roots or call `build_catalog()`.

- [x] **Step 1: Write failing app integration tests**

Assert the tab list contains `Memory Hub`, the missing-provider path renders the prescribed copy, and importing／rendering the tab does not call `build_catalog`, mutate SQLite, or change `MemorySidecarProviderMetadata` defaults.

- [x] **Step 2: Run tests to verify RED**

```bash
.venv/bin/python -m pytest tests/test_app_pages_memory_hub.py -q
```

Expected: missing integration symbol or missing tab assertion.

- [x] **Step 3: Add the tab with explicit provider boundary**

Keep `main()`’s existing four tabs and append `Memory Hub`. Add a small `_render_memory_hub_tab()` that constructs `MemoryHubUiService` from an explicit provider function and project ID, then calls `render_memory_hub`. The default provider returns `None` until a deployment-owned catalog provider is configured; it must not inspect arbitrary paths or create artifacts. Keep Agent Operations and Governance Graph callbacks unchanged.

- [x] **Step 4: Run focused integration regression**

```bash
.venv/bin/python -m pytest tests/test_app_pages_memory_hub.py tests/test_app_pages_governance_graph.py tests/test_agent_operations_rendering.py tests/test_memory_sidecar_models.py -q
.venv/bin/python -m py_compile app_pages.py memory_hub_rendering.py backend/agents/memory_hub_ui_service.py
git diff --check
```

- [x] **Step 5: Commit and request strict Review**

```bash
git add app_pages.py tests/test_app_pages_memory_hub.py
git commit -m "feat: add memory hub streamlit tab"
```

Review must confirm the new tab is observation-only and existing tabs／defaults remain unchanged.

### Task 4: Full UI acceptance and Hermes read-only validation

**Files:**
- Modify: `docs/superpowers/plans/2026-08-14-nbs-memory-hub-streamlit-ui.md` (task status only, after implementation)
- Create: runtime-only acceptance evidence under `.nbs_agent_runtime/` (never commit)

**Interfaces:**
- Consumes: approved Task 1–3 commits, existing Streamlit runtime, Context／Review artifacts and Hermes read-only checks。
- Produces: browser smoke evidence for missing catalog／ready fixture／blocked query states and final acceptance report.

- [x] **Step 1: Run focused and full verification**

```bash
.venv/bin/python -m pytest tests/test_memory_hub_ui_service.py tests/test_memory_hub_rendering.py tests/test_app_pages_memory_hub.py tests/test_app_pages_governance_graph.py tests/test_agent_operations_rendering.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

- [x] **Step 2: Run browser smoke checks**

At `http://127.0.0.1:8502/`, verify the `Memory Hub` tab, missing-catalog message, query controls, authority notice and no-write behavior. Use a controlled test provider only for ready／drill-down coverage; never create production catalog data from the UI.

- [x] **Step 3: Confirm immutable boundaries**

Verify `git diff --check`, SQLite signature, frozen baseline value, sidecar default flags, and absence of new catalog／snapshot writes caused by page load or query.

- [x] **Step 4: Record final acceptance and stop**

Write runtime evidence with test counts, browser states, Hermes result and blocked cases. Do not claim Memory Hub production catalog readiness unless an independently approved catalog provider exists.
