# NBS Governance Graph Phase D-1 Query Read Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 deterministic、read-only 的 Governance Graph Query Service，供 CLI、Streamlit Graph tab 與後續版本比較／風險／變更影響分析共用。

**Architecture:** Query Service 只讀取既有且通過 `GovernanceGraphSnapshot` validation 的 per-run snapshot；CLI 與 Streamlit 都消費同一個 bounded query result，不自行解析 raw artifacts。Query 不建立、更新或修復 snapshot，並保留 `available`、`unavailable`、`unknown`、`invalid`、`blocked` 語意。

**Tech Stack:** Python 3、dataclasses、JSON canonical serialization、pathlib、既有 `GovernanceGraphSnapshot`／`WorkflowStore`、Streamlit、pytest、Hermes。

## Global Constraints

- Canonical artifacts 是唯一 truth；Query、Graph、Telemetry、UI 都是 read-only derived consumers。
- 初版只支援 exact-match filters：`runId`、`nodeType`、`nodeStatus`、`nodeId`、`edgeType`、`artifactKind`、`evidenceStatus`、`snapshotFingerprint`。
- Query output schema 固定為 `governance-graph-query-v1`，必須包含 `status`、`snapshotIdentity`、`queryFingerprint`、`matchedNodes`、`matchedEdges`、`evidenceRefs`、`unknownCount`、`invalidCount`、`blockedCount`、`diagnostics`。
- Status precedence 固定為：`invalid > blocked > unknown > available`。
- Snapshot missing 回傳 `unavailable`，不得 fallback 到其他 run，也不得由 UI／CLI 自動建立 snapshot。
- Query fingerprint 使用 normalized filters 與 snapshot identity 的 UTF-8 canonical JSON：`ensure_ascii=false`、`sort_keys=true`、`separators=(",", ":")`。
- 不新增 approval、dispatch、runner、background writer、SQLite、database、daemon、polling、runtime status writer、Git writer 或 export schema。
- 不修改 baseline、revenue scope、business rules、rollback；正式口徑固定為「不含掛賬核銷與TT退款轉團款」，2026-05 baseline 固定 `HKD 12,057,968`。
- 不實作自然語言查詢、模糊搜尋、dependency inference、risk scoring、change impact analysis 或跨 run 自動推測。
- 每個 Task 必須 TDD、focused tests、`git diff --check`、immutable strict Review；Review PASS 後才進下一 Task。

---

## File Structure

| Path | Responsibility |
|---|---|
| `backend/agents/governance_graph_query_models.py` | Query filter、snapshot identity、bounded result 與 deterministic fingerprint contract。 |
| `backend/agents/governance_graph_query_service.py` | 唯一 Graph query reader；safe snapshot loading、validation、filtering、ordering、status precedence。 |
| `scripts/governance_graph.py` | 新增 `query` CLI command，僅呼叫 Query Service 並輸出 deterministic JSON。 |
| `agent_operations_rendering.py` | Graph tab read-only filters、query result rendering、selected run／refresh state handling。 |
| `app_pages.py` | 只在需要時接線現有 Agent Operations snapshot refresh，不新增 writer。 |
| `tests/test_governance_graph_query_models.py` | Query contract、filter validation、canonical fingerprint。 |
| `tests/test_governance_graph_query_service.py` | Snapshot loading、filters、ordering、status precedence、no-write。 |
| `tests/test_governance_graph_cli.py` | `query` command、JSON envelope、invalid args、no-write。 |
| `tests/test_agent_operations_rendering.py` | Streamlit filters、refresh、selected run fallback、read-only boundary。 |
| `tests/test_app_module_boundaries.py` | UI 不直接讀 raw artifact、不呼叫 snapshot writer。 |

## Shared Interfaces

Task 1 產生：

```python
class GovernanceGraphQuery:
    def normalized(self) -> dict[str, str]: ...

class GovernanceGraphQueryResult:
    def to_dict(self) -> dict[str, Any]: ...
    @property
    def query_fingerprint(self) -> str: ...
```

Task 2 產生：

```python
class GovernanceGraphQueryService:
    def query(
        self,
        *,
        run_id: str | None = None,
        node_type: str | None = None,
        node_status: str | None = None,
        node_id: str | None = None,
        edge_type: str | None = None,
        artifact_kind: str | None = None,
        evidence_status: str | None = None,
        snapshot_fingerprint: str | None = None,
    ) -> GovernanceGraphQueryResult: ...
```

`query()` 的 public result 不得包含 raw payload、prompt、command、stdout/stderr、absolute path、secret 或未 bounded 的 metadata。

---

### Task 1: Query contract models and deterministic fingerprint

**Files:**

- Create: `backend/agents/governance_graph_query_models.py`
- Create: `tests/test_governance_graph_query_models.py`

**Consumes:** Approved D-1 spec §4；既有 `GovernanceGraphSnapshot` status、node／edge／evidence ref model 與 canonical JSON conventions。

**Produces:** 可由 Query Service、CLI、Streamlit 共用的 immutable query/result contract；本 Task 不讀取 runtime、不修改 snapshot。

- [ ] **Step 1: Write failing contract tests.**

  建立 tests 覆蓋：

  ```python
  def test_query_normalizes_exact_filters_and_rejects_unknown_keys():
      query = GovernanceGraphQuery.from_dict({"nodeType": "task_gate", "nodeStatus": "blocked"})
      assert query.normalized() == {"nodeStatus": "blocked", "nodeType": "task_gate"}
      with pytest.raises(GovernanceGraphQuerySchemaError):
          GovernanceGraphQuery.from_dict({"freeText": "risk"})

  def test_result_fingerprint_is_reproducible_and_changes_with_filters():
      first = _result(filters={"nodeType": "task_gate"})
      second = _result(filters={"nodeType": "task_gate"})
      changed = _result(filters={"nodeType": "plan_gate"})
      assert first.query_fingerprint == second.query_fingerprint
      assert first.query_fingerprint != changed.query_fingerprint

  def test_result_rejects_raw_or_absolute_path_metadata():
      with pytest.raises(GovernanceGraphQuerySchemaError):
          _result(diagnostics=[{"code": "bad", "path": "/private/raw.json"}])
  ```

  Fixtures must cover all eight exact filters, safe run/node identifiers, enum validation, string caps, empty result, all five statuses, bounded node/edge/evidence output, snapshot identity and diagnostics.

- [ ] **Step 2: Run model tests to verify RED.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_query_models.py -q
  ```

  Expected: FAIL because query model module does not exist.

- [ ] **Step 3: Implement immutable query/result models.**

  Implement `GovernanceGraphQuery.from_dict()`／`normalized()` with exact allowlist, safe identifier validation, lowercase SHA-256 validation for `snapshotFingerprint`, bounded strings and deterministic sorted keys. Implement `GovernanceGraphQueryResult` with fixed output keys, immutable internal tuples/mappings, bounded metadata validation and `query_fingerprint` over normalized filters plus snapshot identity and result status.

- [ ] **Step 4: Run GREEN verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_query_models.py -q
  .venv/bin/python -m py_compile backend/agents/governance_graph_query_models.py
  git diff --check
  ```

- [ ] **Step 5: Commit and strict-review Task 1.**

  ```bash
  git add backend/agents/governance_graph_query_models.py tests/test_governance_graph_query_models.py
  git commit -m "feat: define governance graph query contract"
  ```

  Review must confirm exact output schema, fingerprint reproducibility and no raw metadata leakage before Task 2 begins.

---

### Task 2: Safe snapshot reader and exact-match query service

**Files:**

- Create: `backend/agents/governance_graph_query_service.py`
- Create: `tests/test_governance_graph_query_service.py`

**Consumes:** Task 1 query/result models；既有 `GovernanceGraphSnapshot.from_dict()`、`WorkflowStore` containment conventions、`governance-graph.json` projection artifact。

**Produces:** 唯一 safe query reader，提供 Shared Interfaces 的 `GovernanceGraphQueryService.query()`。

- [ ] **Step 1: Write failing service tests.**

  建立 temporary runtime fixtures，覆蓋：

  ```python
  def test_query_returns_deterministically_filtered_nodes_edges_and_refs(tmp_path):
      _write_valid_snapshot(tmp_path, "run-1")
      result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1", node_type="task_gate")
      assert result.status == "available"
      assert [node["nodeId"] for node in result.matched_nodes] == ["task_gate"]
      assert result.query_fingerprint == GovernanceGraphQueryService(tmp_path).query(
          run_id="run-1", node_type="task_gate"
      ).query_fingerprint

  def test_missing_snapshot_is_unavailable_without_fallback_or_write(tmp_path):
      _write_run_without_snapshot(tmp_path, "run-1")
      before = _tree_bytes(tmp_path)
      result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1")
      assert result.status == "unavailable"
      assert _tree_bytes(tmp_path) == before

  def test_invalid_blocked_unknown_precedence_is_preserved(tmp_path):
      _write_snapshot_with_states(tmp_path, "run-1")
      result = GovernanceGraphQueryService(tmp_path).query(run_id="run-1")
      assert result.invalid_count == 1
      assert result.blocked_count == 1
      assert result.unknown_count == 1
  ```

  Also cover duplicate-key JSON, symlink／non-regular snapshot, traversal, fingerprint mismatch, exact edge／artifact／evidence filters, stable ordering and no-write tree bytes.

- [ ] **Step 2: Run service tests to verify RED.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_query_service.py -q
  ```

  Expected: FAIL because Query Service does not exist.

- [ ] **Step 3: Implement safe snapshot query.**

  Resolve only a safe single-component `run_id` inside the validated runtime runs root; reject symlink/non-regular roots and snapshot files. Read and validate `governance-graph.json` through `GovernanceGraphSnapshot.from_dict()`, verify its `runId` and graph fingerprint, then normalize nodes, edges and evidence refs into bounded dictionaries. Apply exact filters using AND semantics across supplied dimensions, sort output by `nodeId`, edge `(source, target, type)` and evidence path, compute status counts with precedence `invalid > blocked > unknown > available`, and never call `GovernanceGraphBuilder.persist()` or any writer.

- [ ] **Step 4: Run GREEN and boundary verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_query_service.py -q
  .venv/bin/python -m py_compile backend/agents/governance_graph_query_service.py
  git diff --check
  ```

- [ ] **Step 5: Commit and strict-review Task 2.**

  ```bash
  git add backend/agents/governance_graph_query_service.py tests/test_governance_graph_query_service.py
  git commit -m "feat: add read-only governance graph query service"
  ```

  Review must confirm no fallback across runs, no snapshot creation, no raw leakage and deterministic ordering.

---

### Task 3: Deterministic CLI query command

**Files:**

- Modify: `scripts/governance_graph.py`
- Modify: `tests/test_governance_graph_cli.py`

**Consumes:** Task 1 query/result models and Task 2 `GovernanceGraphQueryService`.

**Produces:** `scripts/governance_graph.py query --run-id ...` with the same exact-match filters and `governance-graph-query-v1` JSON envelope.

- [ ] **Step 1: Write failing CLI tests.**

  Add tests that assert:

  ```python
  def test_parser_exposes_query_with_exact_filters():
      args = cli._parser().parse_args([
          "query", "--run-id", "run-123", "--node-type", "task_gate",
          "--node-status", "invalid",
      ])
      assert args.command == "query"
      assert args.node_type == "task_gate"
      assert args.node_status == "invalid"

  def test_query_emits_read_only_query_envelope(tmp_path, monkeypatch, capsys):
      _write_valid_snapshot(tmp_path, "run-123")
      monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
      before = _tree_bytes(tmp_path)
      assert cli.main(["query", "--run-id", "run-123", "--node-type", "task_gate"]) == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["schemaVersion"] == "nbs-governance-graph-cli-v1"
      assert payload["command"] == "query"
      assert payload["result"]["schemaVersion"] == "governance-graph-query-v1"
      assert _tree_bytes(tmp_path) == before
  ```

  Cover all optional flags, invalid flag values, missing snapshot and forbidden build/approve/dispatch/prune/delete flags.

- [ ] **Step 2: Run CLI tests to verify RED.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_cli.py -q
  ```

  Expected: FAIL because the parser and command do not yet expose `query`.

- [ ] **Step 3: Implement CLI wiring.**

  Add `query` subparser flags with explicit `None` defaults, construct `GovernanceGraphQueryService(PROJECT_ROOT)`, call `query()`, render the existing CLI envelope with `result.to_dict()`, and map result status to the existing safe exit-code policy. Do not call `GovernanceGraphBuilder.persist()` for query.

- [ ] **Step 4: Run GREEN verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_cli.py -q
  .venv/bin/python -m py_compile scripts/governance_graph.py
  git diff --check
  ```

- [ ] **Step 5: Commit and strict-review Task 3.**

  ```bash
  git add scripts/governance_graph.py tests/test_governance_graph_cli.py
  git commit -m "feat: expose governance graph query CLI"
  ```

---

### Task 4: Streamlit Graph filters and refresh/session state boundary

**Files:**

- Modify: `agent_operations_rendering.py`
- Modify: `app_pages.py` only if the existing refresh callback requires explicit query state invalidation
- Modify: `tests/test_agent_operations_rendering.py`
- Modify: `tests/test_app_module_boundaries.py`

**Consumes:** Task 2 Query Service result contract；existing Agent Operations snapshot and selected run UI.

**Produces:** Read-only Graph filters for node type/status/id, edge type, artifact kind and evidence status; a refresh path that invalidates stale Agent Operations snapshot／selected-run state without building a Graph snapshot.

- [ ] **Step 1: Write failing rendering and session tests.**

  Add tests that assert:

  ```python
  def test_graph_filters_render_compact_query_result_without_writer(monkeypatch):
      calls = []
      snapshot = _snapshot_with_graph_query_result()
      fake = FakeStreamlit(calls=calls, selected_filters={"nodeType": "task_gate"})
      monkeypatch.setattr(agent_operations_rendering, "st", fake)
      agent_operations_rendering.render_agent_operations(snapshot, on_refresh=lambda: calls.append("refresh"))
      assert any(call[0] == "dataframe" for call in calls)
      assert not any(call[0] == "writer" for call in calls)

  def test_refresh_does_not_build_snapshot_and_invalidates_stale_selection(monkeypatch):
      state = {"AGENT_OPERATIONS_SELECTED_RUN_ID": "missing-run"}
      _render_with_refresh(state, on_refresh=lambda: state.clear())
      assert state == {}
  ```

  Cover unavailable/invalid query result display, empty result, blocked/unknown counts, no raw path display, and selected run fallback to the first filtered run when the prior selection is no longer present.

- [ ] **Step 2: Run rendering tests to verify RED.**

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py -q
  ```

  Expected: FAIL because Graph filters and explicit stale-selection handling do not yet exist.

- [ ] **Step 3: Implement read-only UI integration.**

  Render exact-match controls from bounded option sets, pass filters to the shared Query Service result already present in the Agent Operations snapshot, display snapshot identity and status counts, preserve `unknown`／`invalid`／`blocked`, and reset selected run only when it is not in the filtered list. The Refresh callback may clear session state and reload Agent Operations, but must not invoke `GovernanceGraphBuilder.persist()` or any canonical writer.

- [ ] **Step 4: Run GREEN and UI boundary verification.**

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py -q
  git diff --check
  ```

- [ ] **Step 5: Commit and strict-review Task 4.**

  ```bash
  git add agent_operations_rendering.py app_pages.py tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py
  git commit -m "feat: add governance graph query filters"
  ```

---

## Final Integration and Acceptance

- [ ] Run affected compile checks for query models/service/CLI.
- [ ] Run focused query, CLI, rendering and boundary tests.
- [ ] Run `.venv/bin/python -m pytest -q`.
- [ ] Run `.venv/bin/python scripts/system_manager.py acceptance`.
- [ ] Run `.venv/bin/python scripts/hermes_post_change_check.py`.
- [ ] Confirm Hermes PASS, no query-triggered writes, SQLite SHA unchanged, baseline exactly `HKD 12,057,968`, and formal revenue scope unchanged.
- [ ] Run final whole-branch strict Review over the merge-base diff; resolve all Critical/Important findings before integration.
- [ ] Do not push, merge, delete branches or create a PR until separately authorized.
