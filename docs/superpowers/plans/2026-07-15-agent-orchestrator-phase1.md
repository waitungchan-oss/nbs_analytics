# Agent Orchestrator Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local CLI Orchestrator that records Agent workflow state, stops for explicit authorization, runs the approved single-Task pipeline, emits macOS notifications, and safely retains compact evidence.

**Architecture:** A non-daemon `scripts/agent_workflow.py` CLI delegates to focused workflow model, store, notification, retention, and orchestration modules. Existing Context, Implementation, Review, Validation, system acceptance, and Hermes contracts remain authoritative; the Orchestrator only sequences them and stores bounded JSON artifacts under `.nbs_agent_runtime/runs/`.

**Tech Stack:** Python 3.10, dataclasses, pathlib, hashlib, JSON/JSONL, fcntl, subprocess with `shell=False`, macOS `/usr/bin/osascript`, pytest.

## Global Constraints

- Formal revenue scope remains `不含掛賬核銷與TT退款轉團款`.
- 2026-05 frozen baseline remains `HKD 12,057,968`.
- `run` must stop at `awaiting_authorization`; only `approve` can invoke Implementation Agent.
- Do not add service start/stop, dependency installation, Git writes, SQLite writes, upload, rollback, baseline promotion, business-rule changes, or export changes.
- Context and Review remain read-only; Implementation remains limited to one approved Task, worktree, sandbox, allowlist, and diff limits.
- Runner commands are never persisted.
- Run retention manages only `.nbs_agent_runtime/runs/`.
- Streamlit Agent Operations is explicitly out of scope.

---

### Task 1: Workflow schemas and legal state transitions

**Files:**
- Create: `backend/agents/workflow_models.py`
- Create: `tests/test_workflow_models.py`

**Interfaces:**
- Produces: `WorkflowManifest`, `WorkflowApproval`, `WorkflowStatus`, `WorkflowEvent`, `WorkflowSchemaError`, `legal_transition(current, next_status)`, `canonical_sha256(payload)`.
- Consumed by: Tasks 2, 4, 5, 6, and 7.

- [ ] **Step 1: Write failing model tests**

Add tests proving schema versions, terminal states, valid transitions, illegal terminal re-entry, canonical fingerprints, ISO timestamps, and strict field validation:

```python
def test_run_must_stop_at_authorization():
    assert legal_transition("created", "context_running")
    assert legal_transition("context_running", "awaiting_authorization")
    assert not legal_transition("context_running", "implementation_running")


def test_terminal_run_cannot_restart():
    for status in ("completed", "changes_required", "blocked", "failed"):
        assert not legal_transition(status, "implementation_running")


def test_manifest_fingerprint_is_canonical():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_workflow_models.py -q
```

Expected: import failure because `backend.agents.workflow_models` does not exist.

- [ ] **Step 3: Implement strict models**

Implement these exact status sets and transition map:

```python
WORKFLOW_STATUSES = frozenset({
    "created", "context_running", "awaiting_authorization",
    "implementation_running", "targeted_verification_running",
    "review_running", "changes_required", "full_verification_running",
    "hermes_running", "completed", "blocked", "failed",
})
TERMINAL_STATUSES = frozenset({"completed", "changes_required", "blocked", "failed"})
TRANSITIONS = {
    "created": frozenset({"context_running", "blocked", "failed"}),
    "context_running": frozenset({"awaiting_authorization", "blocked", "failed"}),
    "awaiting_authorization": frozenset({"implementation_running", "blocked", "failed"}),
    "implementation_running": frozenset({"targeted_verification_running", "blocked", "failed"}),
    "targeted_verification_running": frozenset({"review_running", "blocked", "failed"}),
    "review_running": frozenset({"changes_required", "full_verification_running", "blocked", "failed"}),
    "full_verification_running": frozenset({"hermes_running", "blocked", "failed"}),
    "hermes_running": frozenset({"completed", "blocked", "failed"}),
}
```

Dataclasses must expose `to_dict()` and `from_dict()` with exact schema versions:

```python
MANIFEST_SCHEMA = "agent-workflow-manifest-v1"
APPROVAL_SCHEMA = "agent-workflow-approval-v1"
STATUS_SCHEMA = "agent-workflow-status-v1"
EVENT_SCHEMA = "agent-workflow-event-v1"
```

- [ ] **Step 4: Verify GREEN**

Run the Task 1 test file and `git diff --check`; expected all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/workflow_models.py tests/test_workflow_models.py
git commit -m "feat: define agent workflow schemas"
```

### Task 2: Safe run store, atomic artifacts, events, and locks

**Files:**
- Create: `backend/agents/workflow_store.py`
- Create: `tests/test_workflow_store.py`

**Interfaces:**
- Consumes: Task 1 models.
- Produces: `WorkflowStore(project_root)`, `create_run()`, `load_manifest()`, `load_status()`, `write_approval()`, `transition()`, `write_artifact()`, `append_event()`, `run_lock()`, `artifact_bytes()`.

- [ ] **Step 1: Write failing store tests**

Cover atomic round trips, append-only events, illegal transition rejection, duplicate run rejection, symlink root/parent/target rejection, path escape rejection, per-run lock contention, and artifact byte accounting:

```python
def test_store_rejects_escape(tmp_path):
    store = WorkflowStore(tmp_path)
    with pytest.raises(PermissionError):
        store.write_artifact("../outside", "context.json", {})


def test_run_lock_rejects_second_writer(store, manifest):
    with store.run_lock(manifest.run_id):
        with pytest.raises(WorkflowLockedError):
            with store.run_lock(manifest.run_id, blocking=False):
                pass
```

- [ ] **Step 2: Verify RED**

Expected: import failure for `workflow_store`.

- [ ] **Step 3: Implement the store**

Use lexical and resolved containment checks under `.nbs_agent_runtime/runs`, private temporary files, fsync, and `os.replace`. The public shape must be:

```python
class WorkflowStore:
    def __init__(self, project_root: Path) -> None: ...
    def create_run(self, manifest: WorkflowManifest, status: WorkflowStatus) -> Path: ...
    def load_manifest(self, run_id: str) -> WorkflowManifest: ...
    def load_status(self, run_id: str) -> WorkflowStatus: ...
    def write_approval(self, run_id: str, approval: WorkflowApproval) -> None: ...
    def transition(self, run_id: str, status: WorkflowStatus, event: WorkflowEvent) -> None: ...
    def write_artifact(self, run_id: str, name: str, payload: dict) -> Path: ...
    def append_event(self, run_id: str, event: WorkflowEvent) -> None: ...
    @contextmanager
    def run_lock(self, run_id: str, *, blocking: bool = False): ...
    def artifact_bytes(self, run_id: str) -> int: ...
```

Only these artifact names are accepted:

```python
ALLOWED_ARTIFACTS = frozenset({
    "approval.json", "context.json", "implementation.json",
    "targeted-verification.json", "review.json",
    "full-verification.json", "hermes.json", "archive-summary.json",
})
```

- [ ] **Step 4: Verify GREEN**

Run `tests/test_workflow_models.py tests/test_workflow_store.py`; expected all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/workflow_store.py tests/test_workflow_store.py
git commit -m "feat: persist safe agent workflow state"
```

### Task 3: macOS notification adapter with safe degradation

**Files:**
- Create: `backend/agents/workflow_notifications.py`
- Create: `tests/test_workflow_notifications.py`

**Interfaces:**
- Produces: `WorkflowNotifier` protocol, `MacOSWorkflowNotifier`, `NoOpWorkflowNotifier`, `build_notifier(enabled=True)`, `NotificationResult`.
- Consumed by: Task 5 and Task 6.

- [ ] **Step 1: Write failing notification tests**

Tests must prove exact `/usr/bin/osascript`, `shell=False`, bounded sanitized title/body, no environment/absolute path leakage, macOS success, command failure warning, non-macOS no-op, and disabled no-op.

```python
def test_macos_notifier_never_uses_shell(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)) or completed())
    result = MacOSWorkflowNotifier().send("Awaiting authorization", "run abc123")
    assert calls[0][0][0][0] == "/usr/bin/osascript"
    assert calls[0][1]["shell"] is False
    assert result.delivered is True
```

- [ ] **Step 2: Verify RED**

Expected: import failure for `workflow_notifications`.

- [ ] **Step 3: Implement notifier adapters**

Use this interface:

```python
@dataclass(frozen=True)
class NotificationResult:
    delivered: bool
    warning: str | None = None


class WorkflowNotifier(Protocol):
    def send(self, title: str, message: str) -> NotificationResult: ...
```

Use `osascript -e 'display notification ... with title ...'` as an argv list. Sanitize control characters, environment values, absolute paths, and cap title/message at 80/240 characters. A notifier exception must be returned as a warning, never raised into the workflow.

- [ ] **Step 4: Verify GREEN and commit**

Run notification tests, then commit:

```bash
git add backend/agents/workflow_notifications.py tests/test_workflow_notifications.py
git commit -m "feat: notify agent workflow milestones"
```

### Task 4: Retention planning, dry-run, and compact metadata preservation

**Files:**
- Create: `backend/agents/workflow_retention.py`
- Create: `agent_config/workflow_retention.json`
- Create: `tests/test_workflow_retention.py`

**Interfaces:**
- Consumes: Task 1 models and Task 2 store.
- Produces: `RetentionPolicy`, `RetentionCandidate`, `RetentionReport`, `WorkflowRetention.plan(now)`, `WorkflowRetention.apply(report)`.

- [ ] **Step 1: Write failing retention tests**

Use fixed timestamps and at least 35 terminal runs to prove:

- nonterminal run never pruned;
- all runs younger than 90 days remain complete;
- newest 30 terminal runs remain complete;
- old completed stage reports are compacted;
- old blocked/failed/changes-required/risk metadata remains;
- unknown schema, held lock, symlink, and external path are skipped;
- dry-run does not change bytes;
- retention never traverses outside `.nbs_agent_runtime/runs`.

- [ ] **Step 2: Verify RED**

Expected: retention module/config missing.

- [ ] **Step 3: Add strict policy config**

Create:

```json
{
  "schemaVersion": "agent-workflow-retention-v1",
  "retainDays": 90,
  "retainLatestTerminalRuns": 30,
  "stageArtifactMaxBytes": 5242880,
  "runArtifactSoftCapBytes": 26214400,
  "commandOutputTailCharacters": 12000
}
```

- [ ] **Step 4: Implement deterministic retention**

`plan()` returns candidates and reasons without writes. `apply()` may delete only stage JSON files listed by the report and must write `archive-summary.json` before removal. Preserve `manifest.json`, `status.json`, `approval.json`, and compact event/error metadata according to the design.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/test_workflow_retention.py -q
git add backend/agents/workflow_retention.py agent_config/workflow_retention.json tests/test_workflow_retention.py
git commit -m "feat: retain compact agent workflow evidence"
```

### Task 5: Start run, collect Context, and stop at authorization

**Files:**
- Create: `backend/agents/workflow_orchestrator.py`
- Create: `tests/test_workflow_orchestrator_start.py`

**Interfaces:**
- Consumes: Tasks 1-4, existing Context CLI, Git read-only identity.
- Produces: `WorkflowOrchestrator.start(brief_path, context_agent_command=None, notify=True) -> WorkflowStatus`.

- [ ] **Step 1: Write failing start-flow tests**

Tests use an injected `StageExecutor` and notifier; prove:

- run ID and manifest are created;
- Brief, HEAD, branch, dirty identities and Context fingerprint are saved;
- missing/denied Brief blocks before Context;
- collect-only is used when no Context runner is supplied;
- supplied Context runner is forwarded but not persisted;
- Context failure becomes blocked/failed;
- successful Context always stops at `awaiting_authorization`;
- awaiting-authorization notification and event are emitted;
- automatic housekeeping failure only emits warning.

- [ ] **Step 2: Verify RED**

Expected: `WorkflowOrchestrator` missing.

- [ ] **Step 3: Implement read-only identity and stage executor**

Add these interfaces in `workflow_orchestrator.py`:

```python
@dataclass(frozen=True)
class StageResult:
    exit_code: int
    payload: dict
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


class StageExecutor(Protocol):
    def run_json(self, argv: tuple[str, ...], *, timeout: int) -> StageResult: ...


class WorkflowOrchestrator:
    def start(
        self,
        brief_path: Path,
        *,
        context_agent_command: str | None = None,
        notify: bool = True,
    ) -> WorkflowStatus: ...
```

The production executor must use `shell=False`, UTF-8, bounded output, exact repository Python, and JSON object validation. Use existing `EvidencePolicy` to validate the Brief.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/test_workflow_orchestrator_start.py -q
git add backend/agents/workflow_orchestrator.py tests/test_workflow_orchestrator_start.py
git commit -m "feat: prepare authorized agent workflow runs"
```

### Task 6: Approve and execute Implementation, Review, verification, and Hermes

**Files:**
- Modify: `backend/agents/workflow_orchestrator.py`
- Create: `tests/test_workflow_orchestrator_approve.py`

**Interfaces:**
- Consumes: Task 5 run state, Task contract, existing Implementation/Review CLIs, system acceptance, Hermes.
- Produces: `WorkflowOrchestrator.approve(...) -> WorkflowStatus` and terminal evidence.

- [ ] **Step 1: Write failing authorization and pipeline tests**

Cover:

- status must be `awaiting_authorization`;
- per-run lock prevents concurrent approve;
- Brief/branch/HEAD/dirty identity drift blocks authorization;
- contract worktree/base/plan fingerprint mismatch blocks;
- runner commands are required and never persisted;
- Implementation failure mapping;
- RED/GREEN evidence normalization to exact Review `commands` schema;
- Review changes-required terminal path;
- Review pass triggers exact full pytest and system acceptance commands;
- Orchestrator never calls service start/stop;
- full verification failure blocks before Hermes;
- Hermes fail blocks and Hermes pass completes;
- notifications at Implementation completion, changes required, blocked/failed, and completed.

- [ ] **Step 2: Verify RED**

Expected: `approve` missing.

- [ ] **Step 3: Implement authorization binding**

Use this signature:

```python
def approve(
    self,
    run_id: str,
    contract_path: Path,
    *,
    implementation_agent_command: str,
    review_agent_command: str,
    notify: bool = True,
) -> WorkflowStatus: ...
```

Create immutable `approval.json` only after all run and contract identities match. Do not store either runner command.

- [ ] **Step 4: Normalize targeted evidence**

Convert `redEvidence` + `greenEvidence` items to:

```python
{
    "label": item["commandId"],
    "argv": item["argv"],
    "exitCode": item["exitCode"],
    "stdoutTail": item.get("stdout", "")[-tail_limit:],
    "stderrTail": item.get("stderr", "")[-tail_limit:],
}
```

Write only `{"commands": [...]}` to `targeted-verification.json`.

- [ ] **Step 5: Implement fixed final gates**

Only exact argv are allowed:

```python
full_pytest = (python, "-m", "pytest", "-q")
acceptance = (python, "scripts/system_manager.py", "acceptance")
hermes = (python, "scripts/hermes_post_change_check.py", "--skip-monitor", "--json")
```

Parse acceptance `status == "passed"` and Hermes `overallStatus == "pass"`. Do not start or stop services.

- [ ] **Step 6: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/test_workflow_orchestrator_start.py tests/test_workflow_orchestrator_approve.py -q
git add backend/agents/workflow_orchestrator.py tests/test_workflow_orchestrator_approve.py
git commit -m "feat: orchestrate approved agent workflow gates"
```

### Task 7: CLI, documentation, Hermes coverage, and end-to-end acceptance

**Files:**
- Create: `scripts/agent_workflow.py`
- Create: `tests/test_agent_workflow_cli.py`
- Create: `tests/test_agent_workflow_integration.py`
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: `run`, `approve`, `status`, `list`, and `prune` CLI commands with JSON output and documented governance.

- [ ] **Step 1: Write failing CLI and integration tests**

Prove argparse shape, JSON-only stdout, exit-code mapping, safe path errors, status/list ordering, prune dry-run/apply, `--no-notify`, missing runner blocking, command redaction, and a fake-executor end-to-end flow through `completed`.

CLI parser shape:

```python
run = subparsers.add_parser("run")
run.add_argument("--brief", required=True)
run.add_argument("--context-agent-command")
run.add_argument("--no-notify", action="store_true")

approve = subparsers.add_parser("approve")
approve.add_argument("--run-id", required=True)
approve.add_argument("--contract", required=True)
approve.add_argument("--implementation-agent-command", required=True)
approve.add_argument("--review-agent-command", required=True)
approve.add_argument("--no-notify", action="store_true")
```

- [ ] **Step 2: Verify RED**

Expected: script missing.

- [ ] **Step 3: Implement CLI**

`stdout` must contain one JSON document only. Human diagnostics go to redacted `stderr`. Map completed/awaiting authorization/status/list/prune success to 0, changes-required to 1, blocked to 2, overflow/invalid Agent output to 4, and runtime/schema failure to 5.

- [ ] **Step 4: Extend Hermes targeted coverage**

Add these files to the implementation/Agent checks:

```text
tests/test_workflow_models.py
tests/test_workflow_store.py
tests/test_workflow_notifications.py
tests/test_workflow_retention.py
tests/test_workflow_orchestrator_start.py
tests/test_workflow_orchestrator_approve.py
tests/test_agent_workflow_cli.py
tests/test_agent_workflow_integration.py
```

- [ ] **Step 5: Update governance docs**

Document active Phase 1 CLI, authorization stop, notification behavior, run artifacts, retention rules, non-goals, and the fact that Streamlit Agent Operations remains Phase 2 read-only work.

- [ ] **Step 6: Run focused Agent pack**

```bash
.venv/bin/python -m pytest \
  tests/test_workflow_models.py \
  tests/test_workflow_store.py \
  tests/test_workflow_notifications.py \
  tests/test_workflow_retention.py \
  tests/test_workflow_orchestrator_start.py \
  tests/test_workflow_orchestrator_approve.py \
  tests/test_agent_workflow_cli.py \
  tests/test_agent_workflow_integration.py -q
```

Expected: all pass.

- [ ] **Step 7: Run existing Agent regressions**

```bash
.venv/bin/python -m pytest \
  tests/test_agent_runtime.py \
  tests/test_context_agent_service.py \
  tests/test_review_agent_service.py \
  tests/test_agent_cli.py \
  tests/test_agent_dispatch_contract.py \
  tests/test_agent_read_only_contract.py \
  tests/test_implementation_models.py \
  tests/test_implementation_guard.py \
  tests/test_validation_runner.py \
  tests/test_implementation_agent_service.py \
  tests/test_implementation_agent_cli.py \
  tests/test_implementation_agent_integration.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add scripts/agent_workflow.py tests/test_agent_workflow_cli.py tests/test_agent_workflow_integration.py scripts/hermes_post_change_check.py docs/agents/NBS_AGENT_ARCHITECTURE.md docs/agents/CODEX_AGENT_DISPATCH.md AGENTS.md
git commit -m "feat: expose agent workflow orchestrator"
```

### Task 8: Final verification and real dry-run evidence

**Files:**
- Verify only; runtime artifacts remain ignored.

**Interfaces:**
- Consumes: completed Tasks 1-7.
- Produces: fresh test, service, Hermes, baseline, notification, retention, and telemetry evidence.

- [ ] **Step 1: Run compile and full tests**

```bash
.venv/bin/python -m py_compile \
  backend/agents/workflow_models.py \
  backend/agents/workflow_store.py \
  backend/agents/workflow_notifications.py \
  backend/agents/workflow_retention.py \
  backend/agents/workflow_orchestrator.py \
  scripts/agent_workflow.py
.venv/bin/python -m pytest -q
```

Expected: compile succeeds and full pytest has zero failures.

- [ ] **Step 2: Run a non-modifying workflow preparation trial**

Use this design spec as the Brief and no Context runner:

```bash
.venv/bin/python scripts/agent_workflow.py run \
  --brief docs/superpowers/specs/2026-07-15-agent-orchestrator-phase1-design.md \
  --no-notify
```

Expected: exit 0, status `awaiting_authorization`, no tracked file/DB/runtime-generation change, and bounded run artifacts.

- [ ] **Step 3: Verify retention dry-run**

```bash
.venv/bin/python scripts/agent_workflow.py prune --dry-run
```

Expected: JSON report only, no run artifact changes.

- [ ] **Step 4: Run services and formal acceptance**

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

Expected: all services ready, system acceptance passed, Hermes `overallStatus=pass`, formal scope matched, and 2026-05 baseline matched at `HKD 12,057,968`.

- [ ] **Step 5: Verify final state**

```bash
git diff --check
git status --short --branch
shasum -a 256 nbs_marketing_data.db
```

Expected: only intended tracked changes before final commit/integration; formal DB hash unchanged.
