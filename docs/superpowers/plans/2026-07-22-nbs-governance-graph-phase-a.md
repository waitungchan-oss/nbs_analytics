# NBS Governance Graph Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 NBS Governance Graph Phase A 的 strict contract、deterministic policy、由 canonical artifacts 衍生的 bounded snapshot，以及 projection-only CLI / Hermes integration；不建立自動執行狀態機。

**Architecture:** canonical workflow artifacts 維持唯一真相來源。Graph Builder 只讀 allowlisted artifacts，生成 `nbs-governance-graph-v1`；唯一允許寫入是同一 run 的可重建 `governance-graph.json` projection。Graph 不能成為 `agent_workflow.py`、Controller、Hermes 或 Agent Operations 的控制輸入。

**Tech Stack:** Python 3、dataclasses、JSON、SHA-256、pytest、既有 `WorkflowStore`、`scripts/hermes_post_change_check.py`。

## Plan Reconciliation (2026-07-27)

本 plan 已依 repository 實際狀態完成第一輪 reconciliation：

| Task | Status | Evidence |
|---|---|---|
| Task 1 — Strict Models and Projection Storage | completed | commit `176c7e6`; `backend/agents/governance_graph_models.py`、projection storage/retention changes；models、workflow store、workflow retention focused tests included in `96 passed` |
| Task 2 — Deterministic Risk, Gate, Transition, Retry and Freshness Policy | completed | commit `63564c6`; `backend/agents/governance_graph_policy.py`；policy/model/storage/retention focused tests included in `96 passed` |
| Task 3 — Canonical Artifact Reader and Derived Snapshot Builder | completed | implementation commit `1186993`; focused service/store suite 31 passed |
| Task 4 — JSON-Only Governance Graph CLI | completed | commit `34d6035`; focused Graph CLI suite 4 passed |
| Task 5 — Hermes Read-Only Coverage and Retention Regression | pending | Graph-specific Hermes coverage is not present |
| Task 6 — Governance Contract Documentation and Final Acceptance | pending | operator contract and documentation tests are not present |

Reconciliation boundary：Task 1–2 的 commit 與測試 evidence 已核對；Task 3–6 不因 plan 文字或預期架構而預設完成。Phase B Agent Operations Graph view 與 Phase C telemetry 仍不在 Phase A scope。正式口徑與 2026-05 frozen baseline 維持不變。

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」，2026-05 baseline 固定為 `HKD 12,057,968`。
- R2 surfaces：`upload`、`sqlite`、`baseline`、`rollback`、`revenue`、`business_rules`、`export_schema`；R2 不得路由至現有 Implementation Agent。
- R0 只適用無行為變更的說明、單行 typo、Markdown 拼字、格式或有效 cache reuse；行為性 `.py`、`.vue`、`.js`、`.mjs`、`.sql`、`.json` 變更至少是 R1。
- `build` 僅可原子寫入 `.nbs_agent_runtime/runs/<run-id>/governance-graph.json`；`validate`、`status` 零寫入。
- 不新增 runner、自動授權、自動 routing、UI、DB、SQLite、baseline、business-rule、export 或服務管理功能。
- runtime path 必須拒絕 symlink、traversal、absolute path、unknown artifact 和 non-regular file；snapshot 不含絕對路徑、secrets、runner command、prompt、raw rows、full logs 或內部推理。
- 每個 Task 要完成 TDD、focused tests、`git diff --check`、Review Agent；Task PASS 不取代 full verification 或 Hermes。

---

## File Structure

| 路徑 | 責任 |
|---|---|
| `backend/agents/governance_graph_models.py` | strict schema、fingerprint、risk/gate/node/snapshot dataclasses |
| `backend/agents/governance_graph_policy.py` | pure risk、transition、retry、freshness / invalidation policy |
| `backend/agents/governance_graph_service.py` | canonical artifact reader、snapshot builder、persist / validate / status |
| `backend/agents/workflow_store.py` | projection 的安全 atomic write/read；不改 canonical status |
| `backend/agents/workflow_retention.py` | old completed run projection compact policy |
| `scripts/governance_graph.py` | JSON-only `build`、`validate`、`status` CLI |
| `scripts/hermes_post_change_check.py` | Graph projection read-only report 與 focused coverage |
| `docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md` | operator-facing Graph contract |
| `tests/test_governance_graph_*.py` | models、policy、service、CLI、docs tests |

## Shared Interfaces

```python
GRAPH_SCHEMA = "nbs-governance-graph-v1"
RISK_SCHEMA = "nbs-governance-risk-v1"
GATE_SCHEMA = "nbs-governance-gate-v1"

@dataclass(frozen=True)
class GovernanceGraphSnapshot:
    schema_version: str
    run_id: str
    generated_at: str
    graph_fingerprint: str
    risk: GovernanceRisk | None
    authorization_mode: str
    overall_status: str
    nodes: tuple[GovernanceGraphNode, ...]
    allowed_next_nodes: tuple[str, ...]
    blockers: tuple[dict[str, str], ...]
    freshness: dict[str, object]
    diagnostics: tuple[dict[str, str], ...]

class GovernanceGraphBuilder:
    def build(self, run_id: str) -> GovernanceGraphSnapshot: ...
    def persist(self, run_id: str) -> GovernanceGraphSnapshot: ...
    def validate(self, run_id: str) -> GovernanceGraphSnapshot: ...
    def status(self, run_id: str) -> dict[str, object]: ...
```

Unknown keys, missing fields, invalid SHA-256 / timestamps, unsafe evidence paths, duplicate node IDs and illegal enums raise `GovernanceGraphSchemaError`. Missing `risk-classification.json` means `risk is None` and the risk node is `not_started`; it never defaults to R1.

### Task 1: Strict Models and Projection Storage

**Files:**
- Create: `backend/agents/governance_graph_models.py`
- Create: `tests/test_governance_graph_models.py`
- Modify: `backend/agents/workflow_store.py`
- Modify: `tests/test_workflow_store.py`
- Modify: `backend/agents/workflow_retention.py`
- Modify: `tests/test_workflow_retention.py`

**Consumes:** validation / canonical hashing patterns in `workflow_models.py` and path / lock / atomic-write patterns in `workflow_store.py`.

**Produces:** strict Graph models plus `WorkflowStore.write_projection()` and `read_projection()`.

- [x] **Step 1: Write failing model tests.**

```python
def test_graph_snapshot_round_trips_with_stable_fingerprint():
    snapshot = GovernanceGraphSnapshot.from_dict(_valid_snapshot())
    assert snapshot.to_dict()["schemaVersion"] == "nbs-governance-graph-v1"
    assert snapshot.graph_fingerprint == snapshot.canonical_fingerprint

@pytest.mark.parametrize("field,value", [
    ("schemaVersion", "unknown-v1"), ("runId", "../escape"),
    ("authorizationMode", "automatic"),
])
def test_graph_snapshot_rejects_invalid_contract(field, value):
    payload = _valid_snapshot()
    payload[field] = value
    with pytest.raises(GovernanceGraphSchemaError):
        GovernanceGraphSnapshot.from_dict(payload)
```

- [x] **Step 2: Prove RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_models.py -q`
Expected: import / collection failure before the module exists.

- [x] **Step 3: Implement strict model helpers and enums.**

Use exact enums:

```python
RISK_LEVELS = frozenset({"R0", "R1", "R2"})
NODE_STATUSES = frozenset({"not_started", "ready", "passed", "failed", "blocked", "skipped"})
AUTHORIZATION_MODES = frozenset({"per_task", "approved_batch"})
OVERALL_STATUSES = frozenset({
    "not_started", "awaiting_authorization", "blocked_user_decision",
    "diagnosis_required", "protected_incident", "blocked_missing_runner",
    "awaiting_documentation", "ready_for_integration", "completed", "blocked",
})
```

Require `graphFingerprint` to equal canonical SHA-256 with the fingerprint field removed. Require all `allowedNextNodes` IDs to exist.

- [x] **Step 4: Write failing projection isolation tests.**

```python
def test_projection_write_does_not_mutate_canonical_status(store, manifest):
    store.create_run(manifest, _status())
    before = store.load_status(manifest.run_id).to_dict()
    path = store.write_projection(manifest.run_id, "governance-graph.json", _graph_payload())
    assert path.name == "governance-graph.json"
    assert store.load_status(manifest.run_id).to_dict() == before
    assert store.read_projection(manifest.run_id, "governance-graph.json") == _graph_payload()
```

- [x] **Step 5: Implement storage and retention.**

Add `PROJECTION_ARTIFACTS = frozenset({"governance-graph.json"})`. Implement `write_projection()` with exact projection allowlist, stage cap, safe contained path, run lock and same-directory atomic replacement. Do not call `write_artifact()`, because it mutates canonical `status.json.artifactBytes`. Add projection to retention `STAGE_ARTIFACTS`, never permanent artifacts; only eligible old completed runs may compact it.

- [x] **Step 6: Verify and commit.**

```bash
.venv/bin/python -m pytest tests/test_governance_graph_models.py tests/test_workflow_store.py tests/test_workflow_retention.py -q
git diff --check
git add backend/agents/governance_graph_models.py backend/agents/workflow_store.py backend/agents/workflow_retention.py tests/test_governance_graph_models.py tests/test_workflow_store.py tests/test_workflow_retention.py
git commit -m "feat: add governance graph contracts"
```

### Task 2: Deterministic Risk, Gate, Transition, Retry and Freshness Policy

**Files:**
- Create: `backend/agents/governance_graph_policy.py`
- Create: `tests/test_governance_graph_policy.py`

**Consumes:** Task 1 models.
**Produces:** `classify_risk()`, `validate_gate()`, `allowed_next_nodes()`, `resolve_retry()`, `invalidate_downstream()`.

- [x] **Step 1: Write failing risk and retry tests.**

```python
def test_behavioral_python_change_is_r1_even_with_cache_hit():
    result = classify_risk(
        changed_paths=("backend/services/decision_service.py",),
        declared_surfaces=(), behavior_change=True, fingerprint_cache_hit=True,
    )
    assert result.level == "R1"
    assert result.reason_code == "behavioral_code_change"

@pytest.mark.parametrize("surface", ["upload", "sqlite", "baseline", "rollback", "revenue", "business_rules", "export_schema"])
def test_protected_surface_is_r2(surface):
    assert classify_risk((), (surface,), False, False).level == "R2"

def test_luna_repair_is_available_once_then_requires_diagnosis():
    assert resolve_retry("task_validation", repair_loops_used=0).next_status == "luna_repair"
    assert resolve_retry("task_validation", repair_loops_used=1).next_status == "diagnosis_required"
```

- [x] **Step 2: Prove RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_policy.py -q`
Expected: missing policy module / symbols.

- [x] **Step 3: Implement pure tables only.**

```python
R2_SURFACES = frozenset({"upload", "sqlite", "baseline", "rollback", "revenue", "business_rules", "export_schema"})
R1_CODE_SUFFIXES = frozenset({".py", ".vue", ".js", ".mjs", ".sql", ".json"})
R0_DOCUMENT_SUFFIXES = frozenset({".md", ".txt"})
```

Risk precedence is `R2 > R1 > R0`. Unknown surface / unsafe path is R2 with `unknown_or_ambiguous_surface`. A cache hit is R0 only for non-behavioral document paths.

Implement only design-spec transitions. No allowed next node for `protected_incident`, `blocked_user_decision` or `blocked_missing_runner`. Environment recovery consumes no repair budget; baseline drift, revenue scope conflict and unsafe DB path route immediately to `protected_incident`; design conflict routes to `plan_gate`.

- [x] **Step 4: Implement deterministic freshness mapping.**

```python
INVALIDATION = {
    "brief": ("risk", "spec_gate", "plan_gate", "task", "targeted_verification", "review", "full_verification", "hermes", "documentation", "git_integration"),
    "risk": ("spec_gate", "plan_gate", "task", "targeted_verification", "review", "full_verification", "hermes", "documentation", "git_integration"),
    "spec": ("plan_gate", "task", "targeted_verification", "review", "full_verification", "hermes", "documentation", "git_integration"),
    "plan_or_contract": ("task", "targeted_verification", "review", "full_verification", "hermes", "documentation", "git_integration"),
    "git_identity": ("targeted_verification", "review", "full_verification", "hermes", "documentation", "git_integration"),
    "hermes": ("documentation", "git_integration"),
}
```

`invalidate_downstream()` returns node IDs only. It never deletes artifacts or updates workflow state.

- [x] **Step 5: Verify and commit.**

```bash
.venv/bin/python -m pytest tests/test_governance_graph_models.py tests/test_governance_graph_policy.py -q
git diff --check
git add backend/agents/governance_graph_policy.py tests/test_governance_graph_policy.py
git commit -m "feat: add governance graph policy"
```

### Task 3: Canonical Artifact Reader and Derived Snapshot Builder

**Files:**
- Create: `backend/agents/governance_graph_service.py`
- Create: `tests/test_governance_graph_service.py`
- Modify: `backend/agents/workflow_store.py`
- Modify: `tests/test_workflow_store.py`

**Consumes:** Tasks 1-2, `WorkflowStore.load_manifest()`, `load_status()` and safe artifact reads.
**Produces:** `GovernanceGraphBuilder.build()`, `persist()`, `validate()`, `status()`.

- [x] **Step 1: Write failing mapping and zero-write tests.**

```python
def test_existing_run_without_risk_is_not_auto_classified(tmp_path):
    run_id = _completed_context_run(tmp_path)
    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)
    assert snapshot.risk is None
    assert _node(snapshot, "risk").status == "not_started"
    assert "implementation" not in snapshot.allowed_next_nodes

def test_persist_writes_only_projection_and_validate_is_zero_write(tmp_path):
    run_id = _fully_verified_run(tmp_path)
    builder = GovernanceGraphBuilder(tmp_path)
    before = _canonical_bytes(tmp_path, run_id)
    persisted = builder.persist(run_id)
    assert _projection_path(tmp_path, run_id).is_file()
    assert _canonical_bytes(tmp_path, run_id) == before
    projection_before = _projection_path(tmp_path, run_id).read_bytes()
    assert builder.validate(run_id).graph_fingerprint == persisted.graph_fingerprint
    assert _projection_path(tmp_path, run_id).read_bytes() == projection_before
```

- [x] **Step 2: Prove RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_service.py -q`
Expected: missing service module.

- [x] **Step 3: Implement allowlisted artifact mapping.**

```python
CANONICAL_GRAPH_ARTIFACTS = {
    "risk": "risk-classification.json",
    "spec_gate": "design-spec-gate.json",
    "plan_gate": "plan-gate.json",
    "implementation": "implementation.json",
    "targeted_verification": "targeted-verification.json",
    "review": "review.json",
    "full_verification": "full-verification.json",
    "hermes": "hermes.json",
    "documentation": "documentation-application.json",
    "git_integration": "git-integration.json",
}
```

Extend `WorkflowStore.ALLOWED_ARTIFACTS` only with future canonical Graph inputs `risk-classification.json`, `design-spec-gate.json`, `plan-gate.json`, `git-integration.json`. Phase A must not write them.

Builder rules:
- safe store reads plus strict `from_dict()`;
- absent optional artifact => `not_started`;
- malformed / symlinked / oversize / schema mismatch => `blocked` with bounded reason;
- only relative evidence refs with schema, hash and status;
- Task 2 freshness invalidates stale descendants;
- no orchestrator, notifier, subprocess, runner, Controller or service manager invocation.

- [x] **Step 4: Implement completion semantics.**

A full verification / Hermes failure prevents documentation and completion. Explicit canonical `blocked_missing_runner` maps only to that status; a valid Hermes pass without documentation outcome is `awaiting_documentation`. Git outcome is only `committed`, `merged` or `kept_branch_by_user`; absent outcome is `ready_for_integration`. `completed` requires final Gate, documentation application or deterministic no-doc artifact, and Git evidence.

- [x] **Step 5: Add stale / security tests, verify and commit.**

Task 3 evidence: `1186993` (`feat: build governance graph snapshots`). Focused
Governance Graph / WorkflowStore tests: 31 passed; Hermes post-change check:
`overallStatus=PASS`, system acceptance passed, baseline matched. Full pytest
仍有兩項既有 unrelated failures：runtime health 回傳 `degraded` 與 verified
backfill 回傳 `partially_applied`；本 Task 未修改其相關路徑。

```python
def test_changed_git_identity_invalidates_review_and_hermes(tmp_path):
    run_id = _fully_verified_run(tmp_path)
    _change_manifest_head(tmp_path, run_id)
    snapshot = GovernanceGraphBuilder(tmp_path).build(run_id)
    assert _node(snapshot, "review").status != "passed"
    assert _node(snapshot, "hermes").status != "passed"
```

```bash
.venv/bin/python -m pytest tests/test_governance_graph_models.py tests/test_governance_graph_policy.py tests/test_governance_graph_service.py tests/test_workflow_store.py -q
git diff --check
git add backend/agents/governance_graph_service.py backend/agents/workflow_store.py tests/test_governance_graph_service.py tests/test_workflow_store.py
git commit -m "feat: build governance graph snapshots"
```

### Task 4: JSON-Only Governance Graph CLI

**Files:**
- Create: `scripts/governance_graph.py`
- Create: `tests/test_governance_graph_cli.py`

**Consumes:** Task 3 builder.
**Produces:** JSON-only `build` / `validate` / `status`.

- [x] **Step 1: Write failing parser and zero-write tests.**

```python
def test_parser_exposes_only_projection_commands():
    parser = _parser()
    assert parser.parse_args(["build", "--run-id", "run-123"]).command == "build"
    assert parser.parse_args(["validate", "--run-id", "run-123"]).command == "validate"
    assert parser.parse_args(["status", "--run-id", "run-123"]).command == "status"

def test_validate_and_status_do_not_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    before = _tree_bytes(tmp_path)
    cli.main(["status", "--run-id", "run-123"])
    assert _tree_bytes(tmp_path) == before
    assert json.loads(capsys.readouterr().out)["schemaVersion"] == "nbs-governance-graph-cli-v1"
```

- [x] **Step 2: Prove RED.**

Run: `.venv/bin/python -m pytest tests/test_governance_graph_cli.py -q`
Expected: missing CLI module.

- [x] **Step 3: Implement the separate narrow CLI.**

Commands:

```bash
.venv/bin/python scripts/governance_graph.py build --run-id <run-id>
.venv/bin/python scripts/governance_graph.py validate --run-id <run-id>
.venv/bin/python scripts/governance_graph.py status --run-id <run-id>
```

`build` calls only `persist()`; `validate` calls only `validate()`; `status` calls only `status()`. Reuse redaction style from `scripts/agent_workflow.py`. Do not add `approve`, `dispatch`, `repair`, `apply`, `prune`, `delete`, runner command or model flags.

Exit status rules: `completed`, `ready_for_integration`, `awaiting_authorization` return 0; `awaiting_documentation`, `blocked_user_decision`, `diagnosis_required` return 1; `blocked_missing_runner`, `protected_incident`, `blocked` return 2; runtime error returns 5.

- [x] **Step 4: Verify and commit.**

Task 4 evidence: `34d6035` (`feat: add governance graph cli`). Focused
Graph models/policy/service/CLI tests: 59 passed; Hermes post-change check:
`overallStatus=PASS`, system acceptance passed, baseline matched. Full pytest
結果為 1060 passed、2 個既有 unrelated failures：runtime health 回傳
`degraded` 與 verified backfill 回傳 `partially_applied`；本 Task 未修改
其相關路徑。

```bash
.venv/bin/python -m pytest tests/test_governance_graph_models.py tests/test_governance_graph_policy.py tests/test_governance_graph_service.py tests/test_governance_graph_cli.py -q
git diff --check
git add scripts/governance_graph.py tests/test_governance_graph_cli.py
git commit -m "feat: add governance graph cli"
```

### Task 5: Hermes Read-Only Coverage and Retention Regression

**Files:**
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `tests/test_hermes_post_change_check.py`
- Modify: `tests/test_workflow_retention.py`
- Modify: `tests/test_governance_graph_service.py`

**Consumes:** persisted Graph projection and existing Hermes check plan.
**Produces:** `governance-graph-hermes-report-v1` and focused coverage.

- [ ] **Step 1: Write failing Hermes report tests.**

```python
def test_governance_graph_report_is_read_only_and_bounded(tmp_path):
    report = governance_graph_artifact_report(tmp_path)
    assert report["schemaVersion"] == "governance-graph-hermes-report-v1"
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0

def test_hermes_targeted_tests_include_graph_pack():
    targeted = next(step for step in build_check_plan(include_monitor=False) if step.label == "targeted-tests")
    for name in ("tests/test_governance_graph_models.py", "tests/test_governance_graph_policy.py", "tests/test_governance_graph_service.py", "tests/test_governance_graph_cli.py"):
        assert name in targeted.command
```

- [ ] **Step 2: Implement only a bounded read-only report.**

`governance_graph_artifact_report(project_root=PROJECT_ROOT)` scans only safe run dirs and `governance-graph.json`; checks regular file, cap, JSON object and schema. It returns counts, invalid runs, cap warnings, `policy: "read-only"`, `invocations: 0`, `writes: 0`. It must not instantiate the builder, rebuild snapshots, call CLI, execute a runner or invoke Documentation.

Add the Graph focused tests to `TARGETED_TESTS`. Keep Agent Operations unchanged; Graph UI belongs to Phase B.

- [ ] **Step 3: Prove projection retention behavior, verify and commit.**

```python
def test_old_completed_projection_is_compacted_but_blocked_projection_is_preserved(tmp_path):
    old = _old_completed_run_with_graph(tmp_path)
    blocked = _blocked_run_with_graph(tmp_path)
    report = WorkflowRetention(tmp_path, policy=_policy()).plan(NOW)
    assert "governance-graph.json" in _candidate(report, old.name).delete_paths
    assert all(item.run_id != blocked.name for item in report.candidates)
```

```bash
.venv/bin/python -m pytest tests/test_governance_graph_models.py tests/test_governance_graph_policy.py tests/test_governance_graph_service.py tests/test_governance_graph_cli.py tests/test_workflow_store.py tests/test_workflow_retention.py tests/test_hermes_post_change_check.py -q
git diff --check
git add scripts/hermes_post_change_check.py tests/test_hermes_post_change_check.py tests/test_workflow_retention.py tests/test_governance_graph_service.py
git commit -m "test: cover governance graph acceptance"
```

### Task 6: Governance Contract Documentation and Final Acceptance

**Files:**
- Create: `docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md`
- Create: `tests/test_governance_graph_docs.py`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Modify: `NBS_HERMES_MONITORING.md`

**Consumes:** schema names, CLI and policy from Tasks 1-5.
**Produces:** stable operator contract without an execution or approval interface.

- [ ] **Step 1: Write failing documentation invariants.**

```python
def test_graph_contract_preserves_required_boundaries():
    text = (ROOT / "docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md").read_text(encoding="utf-8")
    for value in ("nbs-governance-graph-v1", "canonical artifacts", "not a control input", "R0", "R1", "R2", "blocked_missing_runner", "protected_incident", "不含掛賬核銷與TT退款轉團款", "HKD 12,057,968"):
        assert value in text
```

- [ ] **Step 2: Write the contract and concise cross-references.**

The contract must cover: purpose/non-goals; canonical versus projection boundary; three CLI commands; R0/R1/R2; Spec/Plan/Task/Final ownership; `per_task` / `approved_batch`; retry / Terra diagnostic-only boundary; freshness; terminal statuses including `blocked_missing_runner` versus `awaiting_documentation`; retention; Hermes read-only report; Agent Operations Phase B. Update architecture, dispatch and Hermes docs with concise links only. Do not invoke Documentation Agent or write Obsidian in this Task.

- [ ] **Step 3: Run focused regression and final acceptance.**

```bash
.venv/bin/python -m pytest tests/test_governance_graph_docs.py tests/test_governance_graph_models.py tests/test_governance_graph_policy.py tests/test_governance_graph_service.py tests/test_governance_graph_cli.py tests/test_workflow_models.py tests/test_workflow_store.py tests/test_workflow_retention.py tests/test_agent_workflow_cli.py tests/test_agent_workflow_integration.py tests/test_hermes_post_change_check.py -q
git diff --check
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

Before and after final acceptance record only official SQLite SHA-256 evidence. It must be unchanged; May baseline stays `HKD 12,057,968`; revenue scope stays「不含掛賬核銷與TT退款轉團款」. Any protected failure is `protected_incident`, not a Luna / Terra retry.

- [ ] **Step 4: Request final Review Agent then complete normal documentation flow.**

After Review PASS, full pytest PASS, acceptance PASS and Hermes PASS, use the existing `agent_workflow.py document` path with an approved Documentation runner. Do not let main Codex silently write Obsidian, System Map or ADR.

- [ ] **Step 5: Commit Task 6.**

```bash
git add docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md docs/agents/NBS_AGENT_ARCHITECTURE.md docs/agents/CODEX_AGENT_DISPATCH.md NBS_HERMES_MONITORING.md tests/test_governance_graph_docs.py
git commit -m "docs: govern NBS graph workflow"
```

## Final Completion Checklist

- [ ] Six Task commits, focused test evidence and Review Agent PASS exist.
- [ ] No Graph code path mutates canonical workflow state; only rebuildable projection may be written.
- [ ] `validate` and `status` prove zero writes.
- [ ] Existing Agent workflow, Implementation Agent, Documentation Controller, Hermes and Agent Operations retain their authority boundaries.
- [ ] Full pytest, acceptance and Hermes pass with unchanged official SQLite hash, `HKD 12,057,968` baseline and fixed revenue scope.
- [ ] Documentation uses the existing proposal / Controller path with no silent LLM fallback.
