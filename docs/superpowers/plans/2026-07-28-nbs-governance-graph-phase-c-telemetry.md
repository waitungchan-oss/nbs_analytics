# NBS Governance Graph Phase C Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 `Agent Operations` 中建立可驗證、read-only 的跨 run Governance Telemetry read model，並顯示 cycle time、gate failure、agent activity、evidence health 與 coverage／unknown 狀態。

**Architecture:** 新增 `GovernanceTelemetryService` 作為純 aggregation layer，沿用既有 `AgentOperationsService` 的 runtime containment 與 artifact safety boundary，讀取 canonical workflow artifacts 與 validated `governance-graph.json`。Service 產生不持久化的 `governance-telemetry-snapshot-v1`，由既有 `Agent Operations` renderer 顯示；不新增 database、API、daemon 或 workflow control path。

**Tech Stack:** Python 3、dataclasses／JSON／pathlib、既有 Workflow models、Governance Graph models、Streamlit、pytest。

## Global Constraints

- Telemetry 是衍生 read model，不是新的 workflow truth、approval、dispatch 或 routing input。
- Agent Operations service 是唯一 runtime artifact reader；Telemetry 不掃描任意檔名、不呼叫 Graph `build()`／`persist()`／CLI、不建立 runtime directory。
- evidence 不足時回報 `unknown`，不可補零、估算、由 artifact mtime 推算或把 retention 缺口當成沒有事件。
- 不修改 SQLite、baseline、revenue scope、business rules、upload、rollback、export schema、Git 或 canonical artifacts。
- 不新增 FastAPI、Vue、獨立 application、daemon、polling、mutable metrics store 或 background writer。
- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 baseline 固定為 `HKD 12,057,968`。
- `durationMs`、token values、counts 必須是 verified non-negative integers；拒絕 bool、負數、超 cap 與 invalid schema。
- UI 只使用既有 manual `Refresh`；不得新增 action button、runner control、approval 或 workflow mutation。

---

## File Structure

| Path | Responsibility |
|---|---|
| `backend/services/governance_telemetry_service.py` | 讀取 allowlisted run evidence，驗證並 aggregation 成 `governance-telemetry-snapshot-v1`；純 read-only。 |
| `backend/services/agent_operations_service.py` | 將 telemetry read model 以 top-level `governanceTelemetry` 納入既有 Agent Operations snapshot。 |
| `agent_operations_rendering.py` | 在既有 Governance Graph section 後渲染 Governance Telemetry；不讀檔、不執行 command。 |
| `tests/test_governance_telemetry_service.py` | Telemetry service 的 schema、metric、unknown、safety、isolation 與 no-write tests。 |
| `tests/test_agent_operations_service.py` | Agent Operations top-level telemetry integration 與敏感值隔離 regression tests。 |
| `tests/test_agent_operations_rendering.py` | Telemetry UI available／partial／unavailable／invalid／unknown／token tests。 |
| `tests/test_app_module_boundaries.py` | 確認 UI 與 telemetry 不新增 runtime／write／control-plane boundary。 |

### Shared interfaces

Task 1 必須提供：

```python
class GovernanceTelemetryService:
    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None: ...
    def build_snapshot(self) -> dict[str, Any]: ...
```

`build_snapshot()` 必須回傳 `schemaVersion == "governance-telemetry-snapshot-v1"`，並包含 `generatedAt`、`sourceGeneratedAt`、`coverage`、`cycleTimes`、`gateFailures`、`agentActivity`、`evidenceHealth`、`protectedIncidents`、`tokenUsage`、`runs`、`diagnostics`。

Task 1 完成後，`AgentOperationsService.build_snapshot()` 的 top-level result 必須包含：

```python
snapshot["governanceTelemetry"] == GovernanceTelemetryService(...).build_snapshot()
```

Task 2 只消費 `snapshot["governanceTelemetry"]`，不得讀取 runtime paths 或重新計算正式 metrics。

---

### Task 1: Governance Telemetry read model 與 safe aggregation

**Files:**

- Create: `backend/services/governance_telemetry_service.py`
- Modify: `backend/services/agent_operations_service.py`
- Create: `tests/test_governance_telemetry_service.py`
- Modify: `tests/test_agent_operations_service.py`

**Consumes:** `manifest.json`、`status.json`、bounded `events.jsonl`、existing stage artifacts、validated `governance-graph.json`；沿用 `AgentOperationsService` 的 project-root／symlink／hard-cap safety semantics。

**Produces:** `GovernanceTelemetryService.build_snapshot() -> dict[str, Any]` 與 Agent Operations top-level `governanceTelemetry` field。

- [ ] **Step 1: Write failing tests for the public snapshot contract.**

  在 `tests/test_governance_telemetry_service.py` 建立 isolated `tmp_path` run fixtures，只透過既有 `WorkflowStore`／approved artifact writers 建立 canonical inputs。先覆蓋：

  ```python
  def test_empty_runtime_is_unavailable_with_bounded_coverage(tmp_path):
      snapshot = GovernanceTelemetryService(tmp_path).build_snapshot()
      assert snapshot["schemaVersion"] == "governance-telemetry-snapshot-v1"
      assert snapshot["coverage"]["eligibleRunCount"] == 0
      assert snapshot["tokenUsage"] is None

  def test_supplied_usage_and_repair_loops_are_aggregated(tmp_path):
      _write_valid_run(tmp_path, usage={"inputTokens": 120, "outputTokens": 30, "totalTokens": 150}, repair_loops=2)
      telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()
      assert telemetry["agentActivity"]["lunaRepair"]["observedCount"] == 1
      assert telemetry["agentActivity"]["lunaRepair"]["total"] == 2
      assert telemetry["tokenUsage"]["totalTokens"] == 150

  def test_missing_task_gate_terra_and_protected_evidence_is_unknown(tmp_path):
      _write_valid_run(tmp_path)
      telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()
      assert telemetry["gateFailures"]["taskGate"]["status"] == "unknown"
      assert telemetry["agentActivity"]["terraDiagnosis"]["status"] == "unknown"
      assert telemetry["protectedIncidents"]["status"] == "unknown"
  ```

  Fixtures must not handcraft unchecked runtime payloads where an existing model／writer is available. Tests must assert no absolute path, prompt, command, stdout／stderr, raw rows or secret appears in `json.dumps(snapshot)`.

- [ ] **Step 2: Run the focused tests and verify RED.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_governance_telemetry_service.py -q
  ```

  Expected: FAIL because `GovernanceTelemetryService` and `governance-telemetry-snapshot-v1` do not yet exist.

- [ ] **Step 3: Implement safe run evidence loading and deterministic aggregation.**

  Implement a bounded allowlist reader with these exact rules:

  - Validate project/runtime/run containment, reject symlink and non-regular files, enforce existing hard caps, parse JSON objects only, and return bounded diagnostics.
  - Read valid stage `durationMs` first; use `events.jsonl` only when a stage has two valid lifecycle timestamps; never use file mtime.
  - Read Spec／Plan Gate failure／blocked evidence from valid gate artifacts or validated Graph nodes. Set Task Gate, Terra diagnosis, and protected incident to `unknown` when their canonical evidence is absent or insufficient.
  - Count stale evidence only from validated Graph freshness／bounded stale diagnostics; missing Graph contributes to unknown coverage, not stale count.
  - Sum only valid non-negative `implementation.json.repairLoopsUsed`; aggregate supplied token usage only when token fields are valid; missing usage remains `None`／unknown.
  - Isolate one malformed or unsafe run／metric; do not hide healthy runs and do not convert invalid／blocked evidence to success.

  Use bounded output shapes such as:

  ```python
  {
      "status": "available" | "partial" | "unavailable" | "invalid",
      "observedCount": 1,
      "unknownCount": 0,
      "total": 1200,
  }
  ```

  Keep `GovernanceTelemetryService` read-only: no `build()`, `persist()`, JSONL append, runtime directory creation, SQLite access, Git command, or external provider call.

- [ ] **Step 4: Add Agent Operations integration tests and wire the top-level read model.**

  Add a test proving `AgentOperationsService(tmp_path).build_snapshot()["governanceTelemetry"]` equals the service snapshot contract, and that malformed telemetry inputs only produce bounded diagnostics without leaking paths. Add a no-write assertion that Graph projection bytes and existing workflow artifacts remain byte-identical after snapshot build.

  In `backend/services/agent_operations_service.py`, instantiate the telemetry service using the already validated project/runtime roots and add the top-level field without changing run item shape, session cache semantics, or existing Graph projection behavior.

- [ ] **Step 5: Run Task 1 GREEN verification and inspect scope.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_governance_telemetry_service.py tests/test_agent_operations_service.py -q
  .venv/bin/python -m py_compile backend/services/governance_telemetry_service.py backend/services/agent_operations_service.py
  git diff --check
  ```

  Expected: all selected tests pass; only the four Task 1 files are changed. Submit the immutable Task 1 diff and verification evidence to strict Review before starting Task 2.

- [ ] **Step 6: Commit Task 1 after explicit authorization.**

  ```bash
  git add backend/services/governance_telemetry_service.py backend/services/agent_operations_service.py tests/test_governance_telemetry_service.py tests/test_agent_operations_service.py
  git commit -m "feat: add governance telemetry read model"
  ```

### Task 2: Agent Operations Governance Telemetry rendering

**Files:**

- Modify: `agent_operations_rendering.py`
- Modify: `tests/test_agent_operations_rendering.py`
- Modify: `tests/test_app_module_boundaries.py` only if a boundary regression test needs an explicit source assertion

**Consumes:** `snapshot["governanceTelemetry"]` from Task 1; existing `render_agent_operations(snapshot, on_refresh=...)` and `_render_run_details()`／Governance Graph rendering flow。

**Produces:** Read-only `Governance Telemetry` section after the Governance Graph section。

- [ ] **Step 1: Write failing renderer tests for available and unavailable states.**

  Extend the existing `FakeStreamlit` fixtures with compact telemetry payloads and assert:

  ```python
  def test_render_governance_telemetry_available(monkeypatch):
      calls = _render_snapshot_with_telemetry({
          "status": "partial",
          "coverage": {"eligibleRunCount": 3, "includedRunCount": 2, "unknownRunCount": 1, "diagnosticCount": 0},
          "cycleTimes": {"implementation": {"observedCount": 2, "averageMs": 1200}},
          "gateFailures": {"specGate": {"failed": 1, "blocked": 0, "unknownCount": 0}},
          "agentActivity": {"lunaRepair": {"total": 2, "unknownCount": 0}, "terraDiagnosis": {"status": "unknown"}},
          "evidenceHealth": {"stale": {"observedCount": 1, "unknownCount": 1}},
          "protectedIncidents": {"status": "unknown"},
          "tokenUsage": None,
          "runs": [], "diagnostics": [],
      }, monkeypatch)
      rendered = " ".join(str(args) for name, args, _ in calls if name in {"subheader", "caption", "write", "warning", "info"})
      assert "Governance Telemetry" in rendered
      assert "partial" in rendered and "unknown" in rendered
      assert not any(name in {"button", "download_button"} and "Telemetry" in str(args) for name, args, _ in calls)
  ```

  Add a missing／unavailable test asserting an explicit coverage message rather than blank KPI output, and a token-missing assertion that renders `未提供` without estimation.

- [ ] **Step 2: Run renderer tests and verify RED.**

  Run:

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py -q
  ```

  Expected: FAIL because no Governance Telemetry section exists.

- [ ] **Step 3: Implement bounded telemetry presentation.**

  Add pure helpers in `agent_operations_rendering.py` that accept only the compact telemetry dict, validate list／dict shapes, and render:

  - `st.subheader("Governance Telemetry")`;
  - coverage summary and state (`available`／`partial`／`unavailable`／`invalid`);
  - table rows for cycle times and gate failures;
  - agent activity and evidence health summaries;
  - token usage only when supplied, otherwise `未提供`;
  - bounded diagnostics／unknown caveat without raw object dump.

  Call the renderer once after `_render_governance_graph()` from `_render_run_details()` or the existing selected-run flow. Do not call `st.json`／`st.code`／`st.download_button`, read files, execute commands, add controls, or recompute metrics in the UI.

- [ ] **Step 4: Add malformed／invalid／unknown rendering tests.**

  Assert malformed collections are treated as empty with safe warning, invalid state does not expose prompt／absolute path／raw payload, unknown Task Gate／Terra／protected incident is visibly marked `unknown`, and existing Refresh／dashboard cache behavior remains unchanged.

- [ ] **Step 5: Run Task 2 verification and module-boundary tests.**

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py -q
  .venv/bin/python -m py_compile agent_operations_rendering.py app_pages.py
  git diff --check
  ```

  Expected: only renderer／test files are changed after Task 1 base; submit immutable Task 2 diff to strict Review.

- [ ] **Step 6: Commit Task 2 after explicit authorization.**

  ```bash
  git add agent_operations_rendering.py tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py
  git commit -m "feat: render governance telemetry in agent operations"
  ```

## Final Integration and Acceptance

After Task 1 and Task 2 strict Review PASS:

1. Verify the top-level interface remains `governanceTelemetry` and the renderer consumes only the compact snapshot.
2. Run:

   ```bash
   .venv/bin/python -m py_compile backend/services/governance_telemetry_service.py backend/services/agent_operations_service.py agent_operations_rendering.py app_pages.py
   .venv/bin/python -m pytest tests/test_governance_telemetry_service.py tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_app_module_boundaries.py -q
   .venv/bin/python -m pytest -q
   .venv/bin/python scripts/system_manager.py acceptance
   .venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
   ```

3. Record frozen baseline evidence and formal SQLite SHA-256 before／after; expected baseline is exactly `HKD 12,057,968` and the database hash must be byte-identical.
4. Confirm no Graph projection, workflow artifact, runtime, Git, SQLite or baseline write occurred.
5. Documentation dispatch is skipped unless the deterministic classifier identifies a real documentation target; no main-Codex fallback is allowed.
6. Before commit／PR integration, report modified files, tests, strict Review results, Hermes result, baseline／SQLite evidence and exact Git scope. Do not merge to `main` without explicit authorization.

## Plan Self-Review

- Spec coverage: all Phase C metrics, read-model states, unknown semantics, UI placement, no-write boundaries, security constraints and acceptance gates map to Task 1, Task 2 or the explicit deferred Task 3.
- Scope: Task 3 canonical evidence gaps are intentionally excluded from this plan and require a separate approved spec／plan.
- Type consistency: Task 1 produces `governanceTelemetry` and `governance-telemetry-snapshot-v1`; Task 2 consumes exactly that field and does not re-read artifacts.
- Placeholder scan: no unresolved placeholder or unspecified implementation marker remains.

## Plan Reconciliation — 2026-07-28

- Task 1 `GovernanceTelemetryService` read model and `AgentOperationsService` integration: **completed**.
- Task 2 Agent Operations Governance Telemetry rendering: **completed**.
- Task 1 strict immutable Review: **PASS** (`phase-c-task1-review.json`).
- Task 2 strict immutable Review: **PASS** (`phase-c-task2-review.json`).
- Full verification: **PASS** (`1093 passed`), `system_manager.py acceptance`: **PASS**.
- Hermes post-change check: **PASS** (`overallStatus=pass`, read-only policy preserved).
- Task 3 canonical evidence gap work remains explicitly deferred to a separate approved spec／plan.
