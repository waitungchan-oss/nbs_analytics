# NBS Governance Graph Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for sequential inline execution. Do not dispatch subagents for this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 Streamlit `Agent Operations` 中安全顯示已存在的 Governance Graph projection 摘要、lineage、evidence 與 stale/missing/blocked 狀態，而不新增任何 workflow control plane。

**Architecture:** `AgentOperationsService` 是唯一 runtime artifact reader，新增一個對既有 `governance-graph.json` 的嚴格、optional、read-only compact summary。`agent_operations_rendering.py` 只消費該 compact summary，將 Graph 顯示在 selected-run details；現有 `app_pages.py` snapshot cache 和 manual Refresh flow 不改變，Refresh 也不得 build 或 persist projection。

**Tech Stack:** Python 3、dataclasses / JSON / pathlib、既有 `GovernanceGraphSnapshot`、Streamlit、pytest、Hermes。

## Global Constraints

- 風險級別為 R1 standard engineering；不得觸及 `upload`、`sqlite`、`baseline`、`rollback`、`revenue`、`business_rules` 或 `export_schema`。
- `governance-graph.json` 是既有 canonical artifacts 的衍生 projection；UI/service 只能讀取，不得 `build()`、`persist()`、執行 Graph CLI 或建立 runtime directory。
- `AgentOperationsService` 仍是 UI 唯一 runtime-artifact reader；`app_pages.py` 與 rendering module 不得直接讀取 `.nbs_agent_runtime`。
- projection 不存在時，Graph `status` 必須是 `unavailable`；不得猜測 Graph、補建 projection 或把缺失表示為 PASS。
- Graph schema 必須以 `GovernanceGraphSnapshot.from_dict()` 嚴格驗證；unknown key、symlink、traversal、non-regular file、oversize 或 malformed JSON 必須 fail closed。
- Graph summary 只可暴露 node ID、status、reason code、artifact basename、SHA-256、evidence status、blocker code、freshness 與 bounded diagnostic；禁止 absolute path、raw JSON、runner command、prompt、stdout/stderr、完整 log、secret、原始資料列或內部推理。
- `allowedNextNodes` 只可作資訊提示；不得提供 approval、dispatch、repair、documentation apply、prune、Git 或 R2 decision UI。
- 只有既有 manual Refresh 可重建 `AGENT_OPERATIONS_SNAPSHOT`；一般 rerun 必須重用 snapshot 並保留 dashboard、AI、export、upload session caches。
- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 每個 Task 依序完成 TDD、focused tests、`git diff --check` 與 findings-first Review；Implementation Agent 不得 commit、merge 或自行進下一個 Task。Codex 只在使用者明確授權後處理 Git integration。

## Plan Reconciliation（2026-07-27）

狀態：Phase B implementation completed；Task 1 與 Task 2 均已完成並通過各自的 strict Review。

| Scope | Status | Evidence |
|---|---|---|
| Task 1 — compact Governance Graph read model | completed | `7032d81`；114 focused tests；strict Review PASS |
| Task 2 — Agent Operations Graph rendering | completed | `5d52c5c`；22 focused UI/boundary tests；strict Review PASS |
| Full verification | completed | `1079 passed`；compile PASS；system acceptance PASS |
| Hermes post-change acceptance | completed | `overallStatus: pass`；baseline matched；SQLite SHA-256 unchanged |
| Documentation dispatch | skipped | deterministic change scope is code/test only; no documentation proposal required |

Immutable implementation review scopes were:

- Task 1: `196d278..7032d81`
- Task 2: `ac5852f..5d52c5c`（working-tree review before commit）

No remaining implementation Task exists in this Phase B plan. Future graph query、version
comparison、dependency/impact analysis and risk summary belong to a separately approved
follow-up plan.

---

## File Structure

| Path | Responsibility |
|---|---|
| `backend/services/agent_operations_service.py` | 在既有 containment/cap/schema boundaries 中讀取並 compact Graph projection；不改變 workflow 或 Graph projection。 |
| `tests/test_agent_operations_service.py` | Service 的 valid/missing/invalid/unsafe/isolation/no-write Graph contract。 |
| `agent_operations_rendering.py` | 將 compact Graph summary 渲染為安全的 selected-run Graph section；不讀檔。 |
| `tests/test_agent_operations_rendering.py` | Graph summary、lineage、evidence、unavailable/invalid UI 與 refresh isolation tests。 |
| `docs/superpowers/specs/2026-07-27-nbs-governance-graph-phase-b-design.md` | 已批准的設計來源；本 plan 不改寫它。 |

`app_pages.py` 不在預設修改範圍：它已透過 `_load_agent_operations_snapshot()` 及
`_render_agent_operations_tab()` 將同一 snapshot 傳給 renderer。只有 Task 2 證實
現有 renderer callback 無法承載 Graph section 時，才可將它加入明確 Task contract
的 allowlist，且不能改變 cache semantics。

---

### Task 1: Governance Graph Compact Read Model

**Files:**

- Modify: `backend/services/agent_operations_service.py`
- Modify: `tests/test_agent_operations_service.py`

**Consumes:** `WorkflowManifest`、`WorkflowStatus`、既有 `_inspect_regular_artifact()`、`_read_json()`、`_safe_message()`、`GovernanceGraphSnapshot.from_dict()`；已存在的 `<run>/governance-graph.json`。

**Produces:** 每個 `agent-operations-snapshot-v1.runs[]` item 的
`governanceGraph: dict[str, Any]`。其 `status` 為 `available`、`unavailable`、`invalid`
或 `blocked`；成功 payload 至少含 `overallStatus`、`freshness`、`nodes`、`blockers`、
`diagnostics`、`evidence`，且所有內容已裁剪。

- [ ] **Step 1: Write failing service tests for existing and missing projections.**

  在 `tests/test_agent_operations_service.py` 加入 Phase A fixture helpers；fixture 必須
  先以既有 `WorkflowStore` 建立 valid run，再以 `GovernanceGraphBuilder(...).persist()`
  在測試 setup 建立 projection。測試中的 service 呼叫只可用 `build_snapshot()`：

  ```python
  from backend.agents.governance_graph_service import GovernanceGraphBuilder
  from backend.agents.workflow_store import WorkflowStore

  def test_existing_graph_projection_is_compacted_without_rebuilding(tmp_path, monkeypatch):
      run = _valid_run(tmp_path, "graph-ready")
      _write_graph_projection(tmp_path, run.name, overall_status="awaiting_authorization")
      called = []
      monkeypatch.setattr(
          GovernanceGraphBuilder,
          "persist",
          lambda *args, **kwargs: called.append("persist"),
      )

      item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

      assert called == []
      assert item["governanceGraph"]["status"] == "available"
      assert item["governanceGraph"]["overallStatus"] == "awaiting_authorization"
      assert item["governanceGraph"]["nodes"][0] == {
          "nodeId": "risk", "status": "passed", "reasonCode": None,
      }

  def test_missing_graph_projection_is_unavailable_not_inferred(tmp_path):
      _valid_run(tmp_path, "graph-missing")

      item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]

      assert item["governanceGraph"] == {"status": "unavailable"}
  ```

  `_write_graph_projection()` must write a valid Phase A projection through
  `GovernanceGraphBuilder.persist()` during setup, not handcraft an unchecked JSON
  blob. The test must assert that `governance-graph.json` bytes are unchanged before
  and after `AgentOperationsService(...).build_snapshot()`.

- [ ] **Step 2: Run the focused tests and verify RED.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_service.py -q
  ```

  Expected: FAIL because `governanceGraph` is not present on compact run items.

- [ ] **Step 3: Add a strict optional Graph projection reader.**

  In `backend/services/agent_operations_service.py`:

  1. Import `GovernanceGraphSnapshot` and `GovernanceGraphSchemaError` from
     `backend.agents.governance_graph_models`.
  2. Add `GOVERNANCE_GRAPH_FILE = "governance-graph.json"` beside the existing
     artifact allowlists.
  3. Add `_governance_graph(run_dir: Path, hard_cap: int) -> dict[str, Any]` that:
     - calls existing `_read_json(run_dir / GOVERNANCE_GRAPH_FILE, run_dir, hard_cap, optional=True)`;
     - returns `{\"status\": \"unavailable\"}` only when the optional file is absent;
     - calls `GovernanceGraphSnapshot.from_dict(payload)` when present;
     - returns an `available` compact value created only from the validated model;
     - catches `_UnsafeArtifactError`, `GovernanceGraphSchemaError`, JSON and value
       failures locally and returns `{\"status\": \"invalid\", \"diagnostics\":
       [{\"code\": \"unsafe_projection\"}]}` or
       `{\"status\": \"invalid\", \"diagnostics\": [{\"code\":
       \"invalid_projection\"}]}`; it must not leak the exception text or hide the
       otherwise valid run;
     - never imports or calls `GovernanceGraphBuilder`, `scripts.governance_graph`,
       `build`, `persist`, `WorkflowStore.write_projection`, or any writer.
  4. Add `_compact_governance_graph(snapshot: GovernanceGraphSnapshot) -> dict[str, Any]`
     with the exact shape below. Cap evidence at one item per node and cap node,
     blocker and diagnostic lists at their model-provided bounded size; shorten a
     SHA-256 only in rendering, not in the service value.

  ```python
  {
      "status": "available",
      "overallStatus": snapshot.overall_status,
      "freshness": snapshot.freshness["status"],
      "nodes": [
          {"nodeId": node.node_id, "status": node.status, "reasonCode": node.reason_code}
          for node in snapshot.nodes
      ],
      "blockers": [dict(item) for item in snapshot.blockers],
      "diagnostics": [dict(item) for item in snapshot.diagnostics],
      "evidence": [
          {
              "nodeId": node.node_id,
              "artifact": Path(evidence.path).name,
              "sha256": evidence.sha256,
              "status": evidence.status,
          }
          for node in snapshot.nodes
          for evidence in node.evidence_refs[:1]
      ],
  }
  ```

  5. In `_compact_run()`, assign `"governanceGraph": self._governance_graph(...)`
     after the existing stage/documentation reads. Do not alter `status`,
     `verification`, `hermes`, documentation, retention or token aggregation.

- [ ] **Step 4: Add failing safety and isolation tests.**

  Add the following tests, using existing safe fixture helpers and temporary paths:

  ```python
  @pytest.mark.parametrize("mode", ["malformed_json", "unknown_schema", "symlink", "oversize"])
  def test_invalid_graph_projection_is_isolated_and_never_leaks_paths(tmp_path, mode):
      good = _valid_run(tmp_path, "good")
      bad = _valid_run(tmp_path, "bad")
      _write_graph_projection(tmp_path, good.name, overall_status="completed")
      graph_path = bad / "governance-graph.json"
      _make_invalid_graph_artifact(graph_path, tmp_path, mode)

      snapshot = AgentOperationsService(tmp_path).build_snapshot()

      by_id = {item["runId"]: item for item in snapshot["runs"]}
      assert set(by_id) == {"good", "bad"}
      assert by_id["good"]["governanceGraph"]["status"] == "available"
      assert by_id["bad"]["governanceGraph"]["status"] == "invalid"
      assert by_id["bad"]["governanceGraph"]["diagnostics"] in (
          [{"code": "invalid_projection"}], [{"code": "unsafe_projection"}],
      )
      assert str(tmp_path) not in json.dumps(snapshot)

  def test_persisted_stale_graph_is_exposed_as_non_pass_state(tmp_path):
      run = _valid_run(tmp_path, "graph-stale")
      _write_graph_projection(tmp_path, run.name, overall_status="blocked", freshness="stale")

      graph = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]["governanceGraph"]

      assert graph["status"] == "available"
      assert graph["freshness"] == "stale"
      assert graph["overallStatus"] == "blocked"
  ```

  The malformed/unknown-schema cases must use an existing file; symlink must point
  outside the run; oversize must exceed `DEFAULT_STAGE_ARTIFACT_MAX_BYTES`. Ensure
  no Graph raw payload, command, prompt, stdout/stderr or absolute path appears in
  the serialized snapshot.

- [ ] **Step 5: Implement only the safety behavior required by the tests.**

  Keep missing projection optional. Do not add a generic artifact filename argument,
  timestamp-based freshness inference, a Graph rebuild fallback, a new runtime cache,
  or a change to Phase A Graph schema. Unsafe and malformed optional projections must
  fail closed inside that run's Graph field without hiding the valid non-Graph run
  metadata; a valid stale projection must remain visible as the stored non-PASS state.

- [ ] **Step 6: Verify focused service coverage and inspect the diff.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_governance_graph_service.py -q
  git diff --check
  git diff -- backend/services/agent_operations_service.py tests/test_agent_operations_service.py
  ```

  Expected: all selected tests PASS; diff contains only the strict optional Graph
  reader, compact summary, and corresponding tests. Submit the Task report and diff
  to the findings-first Review Agent. Do not commit, merge or start Task 2 without
  explicit user authorization.

### Task 2: Governance Graph Rendering in Agent Operations

**Files:**

- Modify: `agent_operations_rendering.py`
- Modify: `tests/test_agent_operations_rendering.py`

**Consumes:** `runs[].governanceGraph` produced by Task 1; existing
`render_agent_operations()` and `_render_run_details()` flow.

**Produces:** A selected-run `Governance Graph` section that renders safe summary,
lineage, blockers/diagnostics, capped evidence, and unavailable/invalid state without
file reads or controls that mutate workflow state.

- [ ] **Step 1: Write failing renderer tests for valid and unavailable Graph states.**

  Add focused tests that call rendering with `FakeStreamlit` and compact fixture data:

  ```python
  def test_render_run_details_includes_compact_governance_graph(monkeypatch):
      calls = []
      monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))
      agent_operations_rendering._render_run_details({
          "runId": "graph-ready",
          "briefName": "phase-b.md",
          "status": "completed",
          "stage": "hermes",
          "updatedAt": "2026-07-27T10:00:00+00:00",
          "stages": {}, "findings": {}, "verification": {}, "hermes": {},
          "tokenUsage": None, "documentation": {"status": "not_requested"},
          "governanceGraph": {
              "status": "available", "overallStatus": "blocked", "freshness": "stale",
              "nodes": [{"nodeId": "hermes", "status": "blocked", "reasonCode": "stale_artifact"}],
              "blockers": [{"code": "stale_artifact", "nodeId": "hermes"}],
              "diagnostics": [],
              "evidence": [{"nodeId": "hermes", "artifact": "hermes.json", "sha256": "a" * 64, "status": "blocked"}],
          },
      })

      text = " ".join(str(args) for name, args, _ in calls if name in {"subheader", "caption", "write", "warning"})
      assert "Governance Graph" in text
      assert "stale" in text and "stale_artifact" in text
      assert "hermes.json" in text and "aaaaaaaa" in text
      assert not any(name in {"button", "download_button"} and "Graph" in str(args) for name, args, _ in calls)

  def test_render_run_details_marks_missing_graph_as_unavailable(monkeypatch):
      calls = []
      monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))
      _render_details_with_graph({"status": "unavailable"})

      assert any(name == "info" and "尚無已建 Graph snapshot" in args[0] for name, args, _ in calls)
  ```

  Define `_render_details_with_graph()` in the test file as a local fixture helper
  that supplies the required existing run-detail keys. Do not add it to production.

- [ ] **Step 2: Run renderer tests and verify RED.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py -q
  ```

  Expected: FAIL because the current renderer has no Governance Graph section.

- [ ] **Step 3: Add pure Graph presentation helpers and rendering.**

  In `agent_operations_rendering.py`, add:

  ```python
  def _graph_evidence_rows(graph: dict[str, Any]) -> list[dict[str, str]]:
      rows = []
      for item in graph.get("evidence", []):
          if not isinstance(item, dict):
              continue
          artifact = item.get("artifact")
          sha256 = item.get("sha256")
          status = item.get("status")
          node_id = item.get("nodeId")
          if all(isinstance(value, str) and value for value in (artifact, sha256, status, node_id)):
              rows.append({
                  "Node": node_id,
                  "Artifact": artifact,
                  "SHA-256": sha256[:8],
                  "Status": status,
              })
      return rows
  ```

  Add `_render_governance_graph(graph: Any) -> None` and call it once from
  `_render_run_details()` after the existing Documentation summary. The renderer
  must implement these exact branches:

  - non-dict or `{\"status\": \"unavailable\"}`: `st.subheader("Governance Graph")`
    then `st.info("尚無已建 Graph snapshot；此頁不會自行建立或更新 snapshot。")`;
  - `status` not equal to `available`: render a bounded warning containing only the
    safe status value, with no raw object dump;
  - `available`: render `overallStatus` and `freshness`; render one dataframe for
    valid node rows (`Node`, `Status`, `Reason`), one dataframe for non-empty
    blockers/diagnostics, and one dataframe for `_graph_evidence_rows()` when non-empty.

  Do not call `st.json`, `st.code`, `st.download_button`, any file API, any CLI, or
  add a button. Never render `allowedNextNodes` as an action. Treat malformed compact
  collections as empty and show a safe warning rather than raising.

- [ ] **Step 4: Add failure-safe renderer tests.**

  Add tests for `invalid` Graph state and malformed collection items:

  ```python
  def test_invalid_graph_state_is_bounded_and_does_not_render_raw_payload(monkeypatch):
      calls = []
      monkeypatch.setattr(agent_operations_rendering, "st", FakeStreamlit(calls=calls))
      _render_details_with_graph({
          "status": "invalid",
          "unexpected": {"prompt": "secret", "absolutePath": "/private/tmp/x"},
      })

      rendered = " ".join(str(args) for _, args, _ in calls)
      assert "invalid" in rendered
      assert "secret" not in rendered and "/private/tmp/x" not in rendered
      assert not any(name == "json" for name, _, _ in calls)
  ```

- [ ] **Step 5: Implement only bounded defensive rendering required by the tests.**

  Keep renderer inputs typed as `dict[str, Any]`/`Any` where existing code uses
  compact dict snapshots. Do not modify `app_pages.py`: its existing manual refresh
  callback must continue to rebuild only `AGENT_OPERATIONS_SNAPSHOT`, so Task 1's
  read-only service behavior is sufficient. Do not change top-level tabs, session
  keys, dashboard cache keys or Agent Operations filters.

- [ ] **Step 6: Verify focused UI coverage and inspect the diff.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py -q
  git diff --check
  git diff -- agent_operations_rendering.py tests/test_agent_operations_rendering.py
  ```

  Expected: all selected tests PASS; the diff adds only Graph presentation and tests.
  Submit the Task report and diff to the findings-first Review Agent. Do not commit,
  merge or claim final completion until Task 2 review and the final gates below pass.

---

## Final Integration, Documentation and Acceptance

After both Task Reviews PASS, Codex performs the following in order:

1. Review the combined diff against this plan and verify Task 1/Task 2 interface
   consistency (`governanceGraph`, `available`, `unavailable`, `invalid`, `blocked`).
2. Run:

   ```bash
   .venv/bin/python -m py_compile backend/services/agent_operations_service.py agent_operations_rendering.py app_pages.py
   .venv/bin/python -m pytest tests/test_governance_graph_service.py tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py -q
   .venv/bin/python -m pytest -q
   .venv/bin/python scripts/system_manager.py acceptance
   .venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
   ```

3. Record baseline evidence and formal SQLite SHA-256 before/after. The expected
   2026-05 baseline is exactly `HKD 12,057,968`; SQLite hash must be byte-identical.
4. If Review PASS, full verification PASS and Hermes PASS, invoke the approved
   Documentation workflow only when its deterministic classifier requires it. If no
   approved Documentation runner exists, report `blocked_missing_runner`; do not
   replace it with a main-Codex documentation write.
5. Before any commit or PR, provide the user with modified files, all test results,
   Review result, Hermes result and the proposed Git scope. Do not merge to `main`
   without explicit user authorization.

## Plan Self-Review

- Spec coverage: Task 1 covers projection-only read, strict validation, missing/unsafe
  behavior, compact evidence and no-write semantics. Task 2 covers Graph display,
  unavailable/invalid states, no controls and session-cache preservation. Final gates
  cover Review, full verification, Hermes, baseline and SQLite integrity.
- Scope: no new API, database, application process, Graph build path or control-plane
  surface is included. `app_pages.py` remains unchanged unless a later approved
  contract explicitly proves it necessary.
- Interface consistency: Task 1 produces `governanceGraph`; Task 2 consumes
  `status`, `overallStatus`, `freshness`, `nodeStatuses` (with a compatibility fallback
  for the existing `nodes` projection), `blockers`, `diagnostics` and `evidence`.
  `available`, `unavailable`, `invalid` and `blocked` have explicit rendering semantics.
- Placeholder scan: no unresolved marker, deferred implementation instruction or
  unspecified test behavior remains.
