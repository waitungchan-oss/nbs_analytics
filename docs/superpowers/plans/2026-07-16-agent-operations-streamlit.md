# Agent Operations Streamlit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only fourth Streamlit tab that presents compact Agent Orchestrator run status, stage progress, findings, verification, Hermes, telemetry, and retention governance without changing workflow state.

**Architecture:** A new `AgentOperationsService` is the only reader of `.nbs_agent_runtime/runs`; it validates Phase 1 schemas, rejects unsafe paths, and returns `agent-operations-snapshot-v1`. A separate rendering module consumes only that compact snapshot, while `app_pages.py` owns session-scoped manual refresh and top-level tab integration.

**Tech Stack:** Python 3, Streamlit, dataclasses/standard-library JSON and pathlib, existing Phase 1 workflow models and retention policy, pytest.

## Global Constraints

- Formal scope remains `不含掛賬核銷與TT退款轉團款`.
- 2026-05 baseline remains `HKD 12,057,968`.
- The feature is read-only: no workflow approve, run, stop, delete, prune, Git, SQLite, baseline, service-management, or runtime write action.
- `.nbs_agent_runtime/runs/` remains the sole workflow truth source; no database or duplicate status store is added.
- Refresh is manual and session-scoped; no timer, polling, daemon, background worker, or unconditional `st.rerun()`.
- Missing token telemetry is represented as `None` and displayed as `未提供`; no quota or token estimation.
- Do not add a FastAPI endpoint or Vue page in this phase.
- `app.py` remains a thin entrypoint; workflow reading must not be placed in `app.py` or `app_pages.py`.
- Each Task follows RED -> GREEN -> focused tests -> findings-first review -> commit.
- Do not merge to `main` until all Tasks, full verification, Hermes, baseline, and DB hash checks pass.

---

## File Structure

- Create `backend/services/agent_operations_service.py`: safe artifact reader and compact snapshot builder.
- Create `agent_operations_rendering.py`: pure filtering/formatting helpers and Streamlit rendering for the compact snapshot.
- Create `tests/test_agent_operations_service.py`: service contract, aggregation, retention, and fail-closed tests.
- Create `tests/test_agent_operations_rendering.py`: filtering, status presentation, empty state, and refresh isolation tests.
- Modify `app_pages.py`: fourth tab, session snapshot lifecycle, and manual refresh callback.
- Modify `tests/test_app_module_boundaries.py`: preserve thin-module and fourth-tab boundaries.
- Modify `docs/agents/NBS_AGENT_ARCHITECTURE.md`: mark Agent Operations read-only UI as active and document its boundary.
- Modify `docs/agents/CODEX_AGENT_DISPATCH.md`: replace the stale statement that no Agent Orchestrator/UI exists.
- Modify `NBS_HERMES_MONITORING.md`: add read-only UI acceptance checks without giving Hermes write responsibility.

---

### Task 1: Safe Agent Operations Snapshot Foundation

**Files:**
- Create: `backend/services/agent_operations_service.py`
- Create: `tests/test_agent_operations_service.py`

**Interfaces:**
- Consumes: `WorkflowManifest.from_dict(dict)`, `WorkflowStatus.from_dict(dict)`, `RetentionPolicy.from_path(Path)`.
- Produces: `AgentOperationsService(project_root: Path, runtime_root: Path | None = None)` and `build_snapshot() -> dict[str, Any]` with schema `agent-operations-snapshot-v1`.

- [ ] **Step 1: Write failing tests for empty runtime and one valid run**

```python
from __future__ import annotations

import json
from pathlib import Path

from backend.services.agent_operations_service import AgentOperationsService


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_run(root: Path, run_id: str = "run-123") -> Path:
    run = root / ".nbs_agent_runtime" / "runs" / run_id
    _write_json(run / "manifest.json", {
        "schemaVersion": "agent-workflow-manifest-v1",
        "runId": run_id,
        "briefPath": "docs/briefs/agent-operations.md",
        "briefSha256": "a" * 64,
        "gitBranch": "codex/agent-operations",
        "gitHead": "b" * 40,
        "dirtyFiles": [],
        "createdAt": "2026-07-16T09:00:00+08:00",
        "contextFingerprint": "c" * 64,
    })
    _write_json(run / "status.json", {
        "schemaVersion": "agent-workflow-status-v1",
        "runId": run_id,
        "stage": "authorization",
        "status": "awaiting_authorization",
        "startedAt": "2026-07-16T09:00:00+08:00",
        "updatedAt": "2026-07-16T09:03:00+08:00",
        "completedAt": None,
        "message": "Context ready",
        "errorCode": None,
        "artifactBytes": 128,
    })
    return run


def test_empty_runtime_returns_valid_snapshot(tmp_path):
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    assert snapshot["schemaVersion"] == "agent-operations-snapshot-v1"
    assert snapshot["summary"]["runCount"] == 0
    assert snapshot["runs"] == []
    assert snapshot["diagnostics"] == []


def test_valid_run_is_compacted_and_sorted(tmp_path):
    _valid_run(tmp_path)
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    run = snapshot["runs"][0]
    assert run["runId"] == "run-123"
    assert run["briefName"] == "agent-operations.md"
    assert run["gitHeadShort"] == "bbbbbbbb"
    assert run["status"] == "awaiting_authorization"
    assert run["durationMs"] == 180_000
    assert snapshot["summary"]["awaitingAuthorizationCount"] == 1
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Expected: collection fails because `backend.services.agent_operations_service` does not exist.

- [ ] **Step 3: Implement the minimal safe snapshot reader**

Create the service with these public constants and methods:

```python
SNAPSHOT_SCHEMA = "agent-operations-snapshot-v1"


class AgentOperationsService:
    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        candidate = Path(runtime_root) if runtime_root is not None else self.project_root / ".nbs_agent_runtime"
        self.runtime_root = self._safe_root(candidate)
        self.runs_root = self.runtime_root / "runs"
        self.retention_path = self.project_root / "agent_config" / "workflow_retention.json"

    def build_snapshot(self) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        diagnostics: list[dict[str, str]] = []
        runs = self._load_runs(diagnostics)
        runs.sort(key=lambda item: (item["updatedAt"], item["runId"]), reverse=True)
        return {
            "schemaVersion": SNAPSHOT_SCHEMA,
            "generatedAt": generated_at,
            "summary": self._summary(runs),
            "runs": runs,
            "retention": self._retention(diagnostics),
            "diagnostics": diagnostics,
        }
```

Use `WorkflowManifest.from_dict` and `WorkflowStatus.from_dict` for required metadata. Derive `briefName` with `Path(manifest.brief_path).name`, `gitHeadShort` with `manifest.git_head[:8]`, and duration from `startedAt` to `completedAt or updatedAt`. `_safe_root` must reject an existing symlink and any resolved path outside `project_root`; a missing runtime must return an empty snapshot without creating directories.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_workflow_models.py -q
```

Expected: all tests pass and no runtime directory is created under `tmp_path` by the empty-runtime test.

- [ ] **Step 5: Review and commit Task 1**

Review for schema reuse, read-only behavior, absolute-path leakage, and missing-runtime writes. Then run `git diff --check` and commit:

```bash
git add backend/services/agent_operations_service.py tests/test_agent_operations_service.py
git commit -m "feat: build agent operations snapshot"
```

---

### Task 2: Stage, Findings, Telemetry, Retention, and Failure Isolation

**Files:**
- Modify: `backend/services/agent_operations_service.py`
- Modify: `tests/test_agent_operations_service.py`

**Interfaces:**
- Consumes: Phase 1 artifact names `context.json`, `implementation.json`, `targeted-verification.json`, `review.json`, `full-verification.json`, `hermes.json`, `archive-summary.json`, and `events.jsonl`.
- Produces: each run's `stages`, `findings`, `verification`, `hermes`, `tokenUsage`, `retentionState`; snapshot `retention` and bounded `diagnostics`.

- [ ] **Step 1: Add RED tests for governance aggregation**

Add a completed run with bounded stage JSON:

```python
def test_completed_run_aggregates_review_verification_hermes_and_tokens(tmp_path):
    run = _valid_run(tmp_path, "run-complete")
    status = json.loads((run / "status.json").read_text())
    status.update({
        "stage": "hermes",
        "status": "completed",
        "completedAt": "2026-07-16T09:10:00+08:00",
        "updatedAt": "2026-07-16T09:10:00+08:00",
        "message": "Workflow completed",
    })
    _write_json(run / "status.json", status)
    _write_json(run / "implementation.json", {"durationMs": 1200, "usage": {"inputTokens": 80, "outputTokens": 20}})
    _write_json(run / "review.json", {
        "verdict": "pass",
        "durationMs": 400,
        "findings": [{"severity": "medium", "code": "R1", "message": "Add coverage"}],
        "usage": {"inputTokens": 40, "outputTokens": 10},
    })
    _write_json(run / "full-verification.json", {"status": "pass", "commands": [{"exitCode": 0}]})
    _write_json(run / "hermes.json", {"overallStatus": "pass"})
    item = AgentOperationsService(tmp_path).build_snapshot()["runs"][0]
    assert item["findings"] == {"count": 1, "highestSeverity": "medium", "items": [{"severity": "medium", "code": "R1", "message": "Add coverage"}]}
    assert item["verification"]["status"] == "pass"
    assert item["hermes"]["status"] == "pass"
    assert item["tokenUsage"] == {"inputTokens": 120, "outputTokens": 30, "totalTokens": 150}


def test_missing_usage_is_not_estimated(tmp_path):
    _valid_run(tmp_path)
    assert AgentOperationsService(tmp_path).build_snapshot()["runs"][0]["tokenUsage"] is None
```

- [ ] **Step 2: Add RED tests for fail-closed isolation**

```python
def test_bad_run_is_isolated_without_leaking_absolute_path(tmp_path):
    _valid_run(tmp_path, "good")
    bad = tmp_path / ".nbs_agent_runtime" / "runs" / "bad"
    bad.mkdir(parents=True)
    (bad / "manifest.json").write_text("{bad json", encoding="utf-8")
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    assert [item["runId"] for item in snapshot["runs"]] == ["good"]
    assert snapshot["diagnostics"][0]["runId"] == "bad"
    assert str(tmp_path) not in json.dumps(snapshot["diagnostics"])


def test_symlink_and_oversize_artifact_are_rejected(tmp_path):
    run = _valid_run(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text('{"verdict":"pass"}', encoding="utf-8")
    (run / "review.json").symlink_to(outside)
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    assert snapshot["runs"] == []
    assert snapshot["diagnostics"][0]["code"] == "unsafe_artifact"
```

- [ ] **Step 3: Run Task 2 tests and verify RED**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py -q
```

Expected: aggregation and isolation assertions fail because Task 1 only reads manifest/status.

- [ ] **Step 4: Implement bounded artifact aggregation**

Add fixed names and no caller-controlled path:

```python
STAGE_FILES = {
    "context": "context.json",
    "implementation": "implementation.json",
    "targeted_verification": "targeted-verification.json",
    "review": "review.json",
    "full_verification": "full-verification.json",
    "hermes": "hermes.json",
}
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
MAX_FINDINGS = 50
MAX_DIAGNOSTICS = 100
MAX_SAFE_MESSAGE_CHARS = 500
```

Implement `_read_stage(run_dir, filename, hard_cap)` so it checks containment, symlink, regular-file type, size, JSON object shape, then returns a dict. Sanitize messages to a bounded single line and never include exception text containing paths. Token aggregation must accept only non-negative integer `inputTokens`, `outputTokens`, and `totalTokens`; if no valid usage object exists, return `None`.

Load `RetentionPolicy` once per snapshot. Unknown or invalid config yields `retention={"status":"unavailable"}` plus `retention_config_invalid`, while valid config exposes only its five numeric limits. `archive-summary.json` sets `retentionState="archived_summary"`; otherwise use `complete`.

Read at most the final 500 valid event lines within `stageArtifactMaxBytes`; validate each with `WorkflowEvent.from_dict` before deriving stage timing. Do not return raw events.

- [ ] **Step 5: Run Task 2 focused and Phase 1 regression tests**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_retention.py -q
```

Expected: all pass, including symlink, unknown schema, archive, token-missing, and one-bad-run isolation cases.

- [ ] **Step 6: Review and commit Task 2**

Review findings-first for path leakage, artifact caps, permissive schema parsing, token invention, and retention writes. Run `git diff --check`, then commit:

```bash
git add backend/services/agent_operations_service.py tests/test_agent_operations_service.py
git commit -m "feat: summarize agent workflow governance"
```

---

### Task 3: Read-Only Streamlit Rendering Module

**Files:**
- Create: `agent_operations_rendering.py`
- Create: `tests/test_agent_operations_rendering.py`

**Interfaces:**
- Consumes: `agent-operations-snapshot-v1` only.
- Produces: `filter_agent_runs(runs, statuses, date_from, date_to, brief_query) -> list[dict]` and `render_agent_operations(snapshot: dict, *, on_refresh: Callable[[], None]) -> None`.

- [ ] **Step 1: Write RED tests for pure filtering and labels**

```python
from datetime import date

from agent_operations_rendering import filter_agent_runs, token_usage_label


RUNS = [
    {"runId": "new", "briefName": "Upload lock", "status": "completed", "updatedAt": "2026-07-16T10:00:00+08:00"},
    {"runId": "old", "briefName": "Forecast", "status": "blocked", "updatedAt": "2026-07-01T10:00:00+08:00"},
]


def test_filter_agent_runs_is_local_and_deterministic():
    result = filter_agent_runs(RUNS, {"completed"}, date(2026, 7, 10), date(2026, 7, 20), "upload")
    assert [item["runId"] for item in result] == ["new"]
    assert RUNS[0]["runId"] == "new"


def test_token_usage_label_never_estimates_missing_usage():
    assert token_usage_label(None) == "未提供"
    assert token_usage_label({"totalTokens": 150}) == "150 tokens"
```

- [ ] **Step 2: Run Task 3 tests and verify RED**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_rendering.py -q
```

Expected: collection fails because `agent_operations_rendering.py` does not exist.

- [ ] **Step 3: Implement pure helpers and the four UI bands**

Use these stable public signatures:

```python
def filter_agent_runs(
    runs: list[dict],
    statuses: set[str],
    date_from: date | None,
    date_to: date | None,
    brief_query: str,
) -> list[dict]:
    query = brief_query.strip().casefold()
    result = []
    for item in runs:
        updated = datetime.fromisoformat(item["updatedAt"]).date()
        if statuses and item["status"] not in statuses:
            continue
        if date_from and updated < date_from:
            continue
        if date_to and updated > date_to:
            continue
        if query and query not in item["briefName"].casefold():
            continue
        result.append(item)
    return result


def token_usage_label(value: dict | None) -> str:
    if not value or not isinstance(value.get("totalTokens"), int):
        return "未提供"
    return f"{value['totalTokens']:,} tokens"
```

`render_agent_operations` must render, in order: header + refresh button, five overview metrics, local filters + workflow dataframe, selected run timeline/details, retention + diagnostics. Use Streamlit status elements and existing styling conventions; do not add nested cards, downloads, action buttons, raw JSON, or hidden writes.

The refresh button calls only `on_refresh`. Run selection uses a selectbox keyed `AGENT_OPERATIONS_SELECTED_RUN_ID`. Empty `runs` renders an informational message and retention/diagnostics without error.

- [ ] **Step 4: Add rendering contract tests with a fake Streamlit surface**

Monkeypatch the module's `st` with a fake object that records `button`, `metric`, `dataframe`, `selectbox`, `info`, and `warning` calls. Assert:

```python
def test_render_empty_snapshot_and_refresh_callback(monkeypatch):
    calls = []
    fake = FakeStreamlit(button_result=True, calls=calls)
    monkeypatch.setattr("agent_operations_rendering.st", fake)
    refreshed = []
    render_agent_operations(_empty_snapshot(), on_refresh=lambda: refreshed.append(True))
    assert refreshed == [True]
    assert any(call[0] == "info" and "尚無 Agent" in call[1] for call in calls)
    assert not any(call[0] == "json" for call in calls)
```

- [ ] **Step 5: Run Task 3 tests and review**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_rendering.py -q
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile agent_operations_rendering.py
```

Review for accidental writes, raw artifact display, unstable widget keys, text overflow, dashboard imports, and implicit reruns.

- [ ] **Step 6: Commit Task 3**

```bash
git add agent_operations_rendering.py tests/test_agent_operations_rendering.py
git commit -m "feat: render agent operations governance"
```

---

### Task 4: Fourth Tab and Manual Snapshot Lifecycle

**Files:**
- Modify: `app_pages.py`
- Modify: `tests/test_app_module_boundaries.py`
- Modify: `tests/test_agent_operations_rendering.py`

**Interfaces:**
- Consumes: `AgentOperationsService.build_snapshot()` and `render_agent_operations(snapshot, on_refresh=...)`.
- Produces: `_render_agent_operations_tab() -> None`, fourth top-level tab, and session key `AGENT_OPERATIONS_SNAPSHOT`.

- [ ] **Step 1: Write RED module-boundary tests**

```python
def test_agent_operations_is_the_fourth_top_level_tab():
    source = PAGES_PATH.read_text(encoding="utf-8")
    expected = '["經營分析大盤", "業務規則配置", "GMV 排除訂單看板", "Agent Operations"]'
    assert expected in source
    assert "def _render_agent_operations_tab" in source
    assert "AgentOperationsService" in source
    assert "render_agent_operations" in source


def test_agent_operations_reader_does_not_leak_into_thin_entrypoint():
    source = APP_PATH.read_text(encoding="utf-8")
    assert "AgentOperationsService" not in source
    assert ".nbs_agent_runtime" not in source
```

- [ ] **Step 2: Add RED session-cache isolation test**

Extract `_load_agent_operations_snapshot(force: bool = False) -> dict` and monkeypatch the service constructor. Assert two ordinary calls invoke `build_snapshot` once, while `force=True` invokes it again. Seed these unrelated keys and assert they remain unchanged:

```python
unrelated = {
    "PROCESSED_DATA_CACHE": object(),
    "AI_FORECAST_CACHE": object(),
    "EXPORT_WORKBOOKS": object(),
    "UPLOAD_LAST_RESULT": object(),
}
```

- [ ] **Step 3: Run Task 4 tests and verify RED**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_app_module_boundaries.py tests/test_agent_operations_rendering.py -q
```

Expected: fourth-tab and lifecycle assertions fail.

- [ ] **Step 4: Integrate the service without loading dashboard data**

Add imports to `app_pages.py`:

```python
from pathlib import Path

from agent_operations_rendering import render_agent_operations
from backend.services.agent_operations_service import AgentOperationsService
```

Add focused helpers:

```python
PROJECT_ROOT = Path(__file__).resolve().parent


def _load_agent_operations_snapshot(*, force: bool = False) -> dict:
    key = "AGENT_OPERATIONS_SNAPSHOT"
    if force or key not in st.session_state:
        st.session_state[key] = AgentOperationsService(PROJECT_ROOT).build_snapshot()
    return st.session_state[key]


def _render_agent_operations_tab() -> None:
    snapshot = _load_agent_operations_snapshot()

    def refresh() -> None:
        _load_agent_operations_snapshot(force=True)

    render_agent_operations(snapshot, on_refresh=refresh)
```

Change `main()` to create four tabs and call `_render_agent_operations_tab()` only inside the fourth context. Do not call `_render_dashboard_tab()` or `_load_and_compute_cache()` from Agent Operations.

- [ ] **Step 5: Run focused Streamlit/module tests**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_app_module_boundaries.py tests/test_agent_operations_rendering.py tests/test_streamlit_upload_feedback_contract.py -q
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile app.py app_pages.py agent_operations_rendering.py backend/services/agent_operations_service.py
```

Expected: all pass; the test service counter is `1`, then `2` after forced refresh, and unrelated session keys retain object identity.

- [ ] **Step 6: Review and commit Task 4**

Review the diff for eager snapshot reads on non-Agent tabs, unconditional rerun, dashboard cache invalidation, large rendering blocks in `app_pages.py`, and tab-order regression. Then commit:

```bash
git add app_pages.py tests/test_app_module_boundaries.py tests/test_agent_operations_rendering.py
git commit -m "feat: add Agent Operations Streamlit tab"
```

---

### Task 5: Governance Documentation and Hermes Boundary

**Files:**
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Modify: `NBS_HERMES_MONITORING.md`

**Interfaces:**
- Consumes: implemented `agent-operations-snapshot-v1` and manual-refresh UI behavior.
- Produces: current operational documentation without changing executable policy.

- [ ] **Step 1: Write a RED documentation contract test**

Add `tests/test_agent_operations_docs.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_agent_operations_docs_define_read_only_boundary():
    architecture = (ROOT / "docs/agents/NBS_AGENT_ARCHITECTURE.md").read_text(encoding="utf-8")
    dispatch = (ROOT / "docs/agents/CODEX_AGENT_DISPATCH.md").read_text(encoding="utf-8")
    hermes = (ROOT / "NBS_HERMES_MONITORING.md").read_text(encoding="utf-8")
    combined = "\n".join((architecture, dispatch, hermes))
    assert "agent-operations-snapshot-v1" in combined
    assert "手動重新整理" in combined
    assert "read-only" in combined
    assert "不得批准" in combined
    assert "Token" in combined and "未提供" in combined
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_docs.py -q
```

Expected: assertions fail because current docs still describe Agent Operations as future work.

- [ ] **Step 3: Update governance docs**

Document these exact boundaries:

- Agent Operations is an active read-only Streamlit view over `agent-operations-snapshot-v1`.
- The service reads Phase 1 artifacts; it does not become a second source of truth.
- Refresh is manual and does not clear dashboard caches.
- The UI不得批准、執行、停止、刪除或 prune workflow。
- Hermes may verify snapshot/read-only behavior but must not write UI artifacts or operate retention.
- Token usage is shown only when supplied; otherwise `未提供`.

Remove only stale statements that say Agent Orchestrator/Agent Operations do not exist. Preserve all existing Context/Review/Hermes responsibility boundaries.

- [ ] **Step 4: Run docs and Agent governance tests**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_agent_operations_docs.py tests/test_agent_dispatch_contract.py tests/test_agent_read_only_contract.py -q
git diff --check
```

Expected: all pass and no contract grants write capability.

- [ ] **Step 5: Review and commit Task 5**

```bash
git add docs/agents/NBS_AGENT_ARCHITECTURE.md docs/agents/CODEX_AGENT_DISPATCH.md NBS_HERMES_MONITORING.md tests/test_agent_operations_docs.py
git commit -m "docs: govern Agent Operations read model"
```

---

### Task 6: Full Verification, UI Acceptance, and Evidence

**Files:**
- Create: `docs/agents/AGENT_OPERATIONS_ACCEPTANCE.md`
- Modify only if a verified defect is found: files owned by Tasks 1–5 and their tests.

**Interfaces:**
- Consumes: completed Tasks 1–5.
- Produces: formal verification evidence and a clean, reviewable branch; no merge.

- [ ] **Step 1: Record pre-verification DB identity and Git state**

Run:

```bash
git status --short --branch
shasum -a 256 /Users/chanwaitung2025/Downloads/nbs_analytics/nbs_marketing_data.db
```

Expected: no unexpected tracked changes; save the DB SHA-256 in the acceptance document.

- [ ] **Step 2: Run focused Agent Operations and Agent Orchestrator regression tests**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest \
  tests/test_agent_operations_service.py \
  tests/test_agent_operations_rendering.py \
  tests/test_agent_operations_docs.py \
  tests/test_app_module_boundaries.py \
  tests/test_agent_workflow_cli.py \
  tests/test_agent_workflow_integration.py \
  tests/test_workflow_models.py \
  tests/test_workflow_store.py \
  tests/test_workflow_notifications.py \
  tests/test_workflow_retention.py \
  tests/test_workflow_orchestrator_start.py \
  tests/test_workflow_orchestrator_approve.py -q
```

Expected: all pass.

- [ ] **Step 3: Run compile and Streamlit dashboard regression tests**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile \
  app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py \
  agent_operations_rendering.py backend/services/agent_operations_service.py \
  backend/agents/workflow_models.py backend/agents/workflow_store.py
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest \
  tests/test_streamlit_upload_feedback_contract.py \
  tests/test_dashboard_service.py tests/test_dashboard_api.py -q
```

Expected: compile and all tests pass.

- [ ] **Step 4: Run full pytest and formal service gates**

Run from the feature worktree using the configured project interpreter and formal DB path without copying or writing the DB:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest -q
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/system_manager.py acceptance
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

Expected: full pytest passes, system acceptance reports `passed`, and Hermes reports `overallStatus=pass`.

- [ ] **Step 5: Verify frozen baseline and DB immutability**

Run:

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -c "from pathlib import Path; from scripts.phase2j_baseline_check import check_phase2_baseline; import json; print(json.dumps(check_phase2_baseline(Path('/Users/chanwaitung2025/Downloads/nbs_analytics/nbs_marketing_data.db')), ensure_ascii=False, indent=2))"
shasum -a 256 /Users/chanwaitung2025/Downloads/nbs_analytics/nbs_marketing_data.db
git diff --check
git status --short --branch
```

Expected: baseline status `matched`, formatted actual `HKD 12,057,968`, DB SHA-256 equals Step 1, and worktree contains only the planned acceptance document before commit.

- [ ] **Step 6: Perform final findings-first review**

Review the complete merge-base-to-HEAD diff for:

- Any workflow, retention, Git, SQLite, baseline, or service write path.
- Any symlink/path traversal or oversized artifact gap.
- Raw prompt/stdout/path/secret exposure.
- Token estimation.
- Automatic polling or dashboard cache invalidation.
- Missing tests for corrupted or archived runs.

Expected: PASS or all findings fixed with focused RED/GREEN tests and a separate fix commit before repeating Steps 2–5.

- [ ] **Step 7: Write and commit acceptance evidence**

`docs/agents/AGENT_OPERATIONS_ACCEPTANCE.md` must record date, branch/HEAD, focused/full test counts, system acceptance, Hermes result, baseline result, DB SHA before/after, manual UI checks, and residual risks. Then commit:

```bash
git add docs/agents/AGENT_OPERATIONS_ACCEPTANCE.md
git commit -m "docs: record Agent Operations acceptance"
```

Do not merge to `main`; present the verified branch and commits to the user for the integration decision.
