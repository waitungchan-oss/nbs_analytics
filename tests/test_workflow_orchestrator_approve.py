from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from backend.agents.workflow_notifications import NotificationResult
from backend.agents.implementation_models import ImplementationTaskContract
from backend.agents.workflow_orchestrator import StageResult, WorkflowOrchestrator
from backend.agents.workflow_store import WorkflowStore


ROOT = Path(__file__).resolve().parents[1]
BRIEF = Path("docs/agents/CODEX_AGENT_DISPATCH.md")
PLAN = Path("docs/superpowers/plans/2026-07-15-agent-orchestrator-phase1.md")
CONTEXT_PAYLOAD = {
    "status": "ready",
    "taskUnderstanding": [],
    "systemBoundaries": [],
    "relevantFiles": [],
    "dependencies": [],
    "recommendedTests": [],
    "risks": [],
    "unknowns": [],
    "contextFingerprint": "a" * 64,
}


@dataclass
class FakeExecutor:
    results: list[StageResult]
    calls: list[tuple[tuple[str, ...], int]]
    require_json_calls: list[bool] = field(default_factory=list)

    def run_json(self, argv: tuple[str, ...], *, timeout: int, require_json: bool = True) -> StageResult:
        self.calls.append((argv, timeout))
        self.require_json_calls.append(require_json)
        return self.results.pop(0)


@dataclass
class FakeNotifier:
    messages: list[tuple[str, str]]

    def send(self, title: str, message: str) -> NotificationResult:
        self.messages.append((title, message))
        return NotificationResult(False, None)


def result(
    payload: dict,
    exit_code: int = 0,
    *,
    stdout_tail: str = "implementation stdout",
    stderr_tail: str = "implementation stderr",
) -> StageResult:
    return StageResult(exit_code, payload, stdout_tail, stderr_tail, 4)


def implementation_payload(
    *,
    status: str = "completed",
    schema_version: str = "implementation-run-report-v1",
    task_id: str = "task-6",
    contract_fingerprint: str = "b" * 64,
    start_head: str = "c" * 40,
) -> dict:
    evidence = {
        "commandId": "pytest_targeted",
        "argv": [str(ROOT / ".venv/bin/python"), "-m", "pytest", "tests/test_workflow_orchestrator_approve.py", "-q"],
        "exitCode": 0,
        "stdout": "x" * 13000,
        "stderr": "",
        "durationMs": 8,
        "timedOut": False,
    }
    return {
        "schemaVersion": schema_version,
        "status": status,
        "taskId": task_id,
        "contractFingerprint": contract_fingerprint,
        "startHead": start_head,
        "endHead": "c" * 40,
        "changedFiles": ["backend/agents/workflow_orchestrator.py"],
        "diffStat": {"files": 1, "lines": 10},
        "redEvidence": [evidence],
        "greenEvidence": [evidence],
        "repairLoopsUsed": 0,
        "testFilesChanged": ["tests/test_workflow_orchestrator_approve.py"],
        "productionFilesChanged": ["backend/agents/workflow_orchestrator.py"],
        "findings": [],
    }


def contract(path: Path, *, base: str, worktree: Path = ROOT, plan_fingerprint: str | None = None) -> Path:
    payload = {
        "schemaVersion": "implementation-task-v1",
        "taskId": "task-6",
        "planPath": PLAN.as_posix(),
        "planFingerprint": plan_fingerprint or hashlib.sha256((ROOT / PLAN).read_bytes()).hexdigest(),
        "objective": "Approve exactly one workflow task.",
        "approvedBaseSha": base,
        "approvedWorktree": str(worktree),
        "allowedWritePaths": ["backend/agents/workflow_orchestrator.py", "tests/test_workflow_orchestrator_approve.py"],
        "validationCommands": ["pytest_targeted"],
        "riskSurfaces": [],
        "maxChangedFiles": 2,
        "maxDiffLines": 400,
        "maxRepairLoops": 1,
        "taskType": "behavior",
        "redCommands": ["pytest_targeted"],
        "greenCommands": ["pytest_targeted"],
        "approvedTestBehaviorChanges": ["approve pipeline"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def orchestrator(tmp_path: Path, results: list[StageResult]) -> tuple[WorkflowOrchestrator, FakeExecutor, FakeNotifier]:
    executor = FakeExecutor(results, [])
    notifier = FakeNotifier([])
    return WorkflowOrchestrator(ROOT, store=WorkflowStore(tmp_path), stage_executor=executor, notifier=notifier), executor, notifier


def started_run(tmp_path: Path, *stage_results: StageResult):
    flow, executor, notifier = orchestrator(tmp_path, [result(CONTEXT_PAYLOAD), *stage_results])
    status = flow.start(BRIEF)
    assert status.status == "awaiting_authorization"
    return flow, executor, notifier, status


def add_successful_approval_stages(
    flow: WorkflowOrchestrator,
    executor: FakeExecutor,
    started,
    contract_path: Path,
    review_payload: dict | None = None,
    *,
    implementation_overrides: dict | None = None,
) -> None:
    manifest = flow.store.load_manifest(started.run_id)
    task = ImplementationTaskContract.from_dict(json.loads(contract_path.read_text(encoding="utf-8")))
    implementation = implementation_payload(
        contract_fingerprint=task.fingerprint,
        start_head=manifest.git_head,
    )
    implementation.update(implementation_overrides or {})
    executor.results.extend([
        result(implementation),
        result(review_payload or {"schemaVersion": "review-report-v1", "verdict": "pass", "findings": []}),
    ])


def add_passing_final_gates(executor: FakeExecutor) -> None:
    executor.results.extend([
        result({"tests": "passed"}),
        result({"status": "passed"}),
        result({"overallStatus": "pass"}),
    ])


def test_approve_runs_fixed_final_gates_and_persists_terminal_evidence_without_persisting_runners(tmp_path):
    flow, executor, notifier, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    add_successful_approval_stages(flow, executor, started, contract_path)
    add_passing_final_gates(executor)

    status = flow.approve(
        started.run_id, contract_path,
        implementation_agent_command="codex exec --json",
        review_agent_command="claude --json",
    )

    assert status.status == "completed"
    assert status.completed_at == status.updated_at
    python = str(ROOT / ".venv/bin/python")
    approved_snapshot = executor.calls[1][0][3]
    assert [call[0] for call in executor.calls[1:]] == [
        (python, "scripts/implementation_agent.py", "--contract", approved_snapshot, "--agent-command", "codex", "exec", "--json"),
        (python, "scripts/review_agent.py", "--brief", BRIEF.as_posix(), "--base", flow.store.load_manifest(started.run_id).git_head, "--head", "WORKTREE", "--context", str(tmp_path / ".nbs_agent_runtime" / "runs" / started.run_id / "context.json"), "--task-contract", approved_snapshot, "--verification", str(tmp_path / ".nbs_agent_runtime" / "runs" / started.run_id / "targeted-verification.json"), "--agent-command", "claude --json", "--strict"),
        (python, "-m", "pytest", "-q"),
        (python, "scripts/system_manager.py", "acceptance"),
        (python, "scripts/hermes_post_change_check.py", "--skip-monitor", "--json"),
    ]
    implementation_argv = executor.calls[1][0]
    assert implementation_argv[-4:] == ("--agent-command", "codex", "exec", "--json")
    assert not Path(approved_snapshot).exists()
    review_argv = executor.calls[2][0]
    assert review_argv[-3:] == ("--agent-command", "claude --json", "--strict")
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / started.run_id
    approval = json.loads((run_dir / "approval.json").read_text())
    assert approval["approvedBaseSha"] == flow.store.load_manifest(started.run_id).git_head
    persisted = "\n".join(item.read_text() for item in run_dir.iterdir() if item.is_file())
    assert "codex exec --json" not in persisted
    assert "claude --json" not in persisted
    verification = json.loads((run_dir / "targeted-verification.json").read_text())
    assert set(verification) == {"commands"}
    assert verification["commands"][0] == {
        "label": "pytest_targeted",
        "argv": implementation_payload()["redEvidence"][0]["argv"],
        "exitCode": 0,
        "stdoutTail": "x" * 12000,
        "stderrTail": "",
    }
    assert json.loads((run_dir / "full-verification.json").read_text()) == {
        "fullPytest": {
            "exitCode": 0,
            "stdoutTail": "implementation stdout",
            "stderrTail": "implementation stderr",
            "payload": {"tests": "passed"},
        },
        "acceptance": {"status": "passed"},
    }
    assert json.loads((run_dir / "hermes.json").read_text()) == {"overallStatus": "pass"}
    assert any(title == "Implementation completed" for title, _ in notifier.messages)
    assert any(title == "Workflow completed" for title, _ in notifier.messages)
    assert all(" start" not in " ".join(argv) and " stop" not in " ".join(argv) for argv, _ in executor.calls)


def test_approve_dispatches_fingerprint_bound_snapshot_after_original_contract_is_replaced(tmp_path):
    flow, executor, _, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    approved_payload = json.loads(contract_path.read_text(encoding="utf-8"))
    add_successful_approval_stages(flow, executor, started, contract_path)
    add_passing_final_gates(executor)

    original_run_json = executor.run_json

    def replace_before_dispatch(argv, *, timeout, require_json=True):
        if "scripts/implementation_agent.py" in argv:
            replaced = {**approved_payload, "objective": "replacement must not execute"}
            contract_path.write_text(json.dumps(replaced), encoding="utf-8")
            dispatched_path = Path(argv[argv.index("--contract") + 1])
            assert dispatched_path != contract_path
            assert json.loads(dispatched_path.read_text(encoding="utf-8")) == approved_payload
        return original_run_json(argv, timeout=timeout, require_json=require_json)

    executor.run_json = replace_before_dispatch

    status = flow.approve(
        started.run_id,
        contract_path,
        implementation_agent_command="codex",
        review_agent_command="claude",
    )

    assert status.status == "completed"
    dispatched_path = Path(executor.calls[1][0][executor.calls[1][0].index("--contract") + 1])
    assert not dispatched_path.exists()


def test_approve_persists_bounded_text_evidence_for_full_pytest(tmp_path):
    flow, executor, _, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    add_successful_approval_stages(flow, executor, started, contract_path)
    full_pytest_stdout = "35 passed in 1.58s\n"
    executor.results.extend([
        result({}, stdout_tail=full_pytest_stdout, stderr_tail="pytest warning\n"),
        result({"status": "passed"}),
        result({"overallStatus": "pass"}),
    ])

    status = flow.approve(
        started.run_id,
        contract_path,
        implementation_agent_command="codex",
        review_agent_command="claude",
    )

    assert status.status == "completed"
    assert executor.require_json_calls == [True, True, True, False, True, True]
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / started.run_id
    assert json.loads((run_dir / "full-verification.json").read_text()) == {
        "fullPytest": {
            "exitCode": 0,
            "stdoutTail": full_pytest_stdout,
            "stderrTail": "pytest warning\n",
            "payload": {},
        },
        "acceptance": {
            "status": "passed",
        },
    }


@pytest.mark.parametrize(
    ("final_results", "artifact", "expected_error"),
    [
        (
            [result({}, exit_code=1)],
            {
                "fullPytest": {
                    "exitCode": 1,
                    "stdoutTail": "implementation stdout",
                    "stderrTail": "implementation stderr",
                    "payload": {},
                }
            },
            "full_verification_blocked",
        ),
        (
            [result({"tests": "passed"}), result({"status": "failed"})],
            {
                "fullPytest": {
                    "exitCode": 0,
                    "stdoutTail": "implementation stdout",
                    "stderrTail": "implementation stderr",
                    "payload": {"tests": "passed"},
                },
                "acceptance": {"status": "failed"},
            },
            "full_verification_blocked",
        ),
    ],
)
def test_approve_blocks_full_verification_before_hermes(tmp_path, final_results, artifact, expected_error):
    flow, executor, notifier, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    add_successful_approval_stages(flow, executor, started, contract_path)
    executor.results.extend(final_results)

    status = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")

    assert status.status == "blocked"
    assert status.error_code == expected_error
    assert status.completed_at == status.updated_at
    assert len(executor.calls) == 3 + len(final_results)
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / started.run_id
    assert json.loads((run_dir / "full-verification.json").read_text()) == artifact
    assert not (run_dir / "hermes.json").exists()
    assert any(title == "Workflow blocked" for title, _ in notifier.messages)


@pytest.mark.parametrize("hermes_result", [result({"overallStatus": "fail"}), result({}, exit_code=1)])
def test_approve_blocks_when_hermes_does_not_pass(tmp_path, hermes_result):
    flow, executor, notifier, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    add_successful_approval_stages(flow, executor, started, contract_path)
    executor.results.extend([result({"tests": "passed"}), result({"status": "passed"}), hermes_result])

    status = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")

    assert status.status == "blocked"
    assert status.error_code == "hermes_blocked"
    assert status.completed_at == status.updated_at
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / started.run_id
    assert json.loads((run_dir / "hermes.json").read_text()) == hermes_result.payload
    assert any(title == "Workflow blocked" for title, _ in notifier.messages)


def test_approve_blocks_when_brief_identity_drifts(tmp_path):
    flow, executor, _, started = started_run(tmp_path)
    manifest = flow.store.load_manifest(started.run_id)
    contract_path = contract(tmp_path / "task.json", base=manifest.git_head)
    brief = ROOT / BRIEF
    original = brief.read_bytes()
    try:
        brief.write_bytes(original + b"\n")
        status = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")
    finally:
        brief.write_bytes(original)
    assert status.status == "blocked"
    assert executor.calls[1:] == []


@pytest.mark.parametrize("field, value", [
    ("branch", "codex/identity-drift"),
    ("head", "0" * 40),
    ("dirtyFiles", []),
])
def test_approve_blocks_when_current_git_identity_drifts(tmp_path, monkeypatch, field, value):
    flow, executor, _, started = started_run(tmp_path)
    manifest = flow.store.load_manifest(started.run_id)
    identity = flow._git_identity()
    identity[field] = (
        [{"path": "identity-drift.txt", "sha256": "0" * 64}]
        if field == "dirtyFiles" else value
    )
    monkeypatch.setattr(flow, "_git_identity", lambda: identity)
    contract_path = contract(tmp_path / "task.json", base=manifest.git_head)

    status = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")

    assert status.status == "blocked"
    assert executor.calls[1:] == []


@pytest.mark.parametrize("kwargs", [
    {"worktree": ROOT.parent},
    {"base": "0" * 40},
    {"plan_fingerprint": "0" * 64},
])
def test_approve_blocks_contract_worktree_or_plan_fingerprint_mismatch(tmp_path, kwargs):
    flow, executor, _, started = started_run(tmp_path)
    manifest = flow.store.load_manifest(started.run_id)
    contract_path = contract(tmp_path / "task.json", base=kwargs.pop("base", manifest.git_head), **kwargs)

    status = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")

    assert status.status == "blocked"
    assert executor.calls[1:] == []


def test_approve_requires_awaiting_status_allowed_runners_and_immutable_approval(tmp_path):
    flow, executor, _, started = started_run(tmp_path)
    manifest = flow.store.load_manifest(started.run_id)
    contract_path = contract(tmp_path / "task.json", base=manifest.git_head)

    rejected = flow.approve(started.run_id, contract_path, implementation_agent_command="python", review_agent_command="claude")
    assert rejected.status == "blocked"
    assert executor.calls[1:] == []

    assert flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude") == rejected

    failing, _, _, failing_run = started_run(tmp_path, result(implementation_payload(status="failed"), exit_code=1))
    failing_contract = contract(tmp_path / "failing-task.json", base=failing.store.load_manifest(failing_run.run_id).git_head)
    second = failing.approve(failing_run.run_id, failing_contract, implementation_agent_command="codex", review_agent_command="claude")
    assert second.status == "failed"
    assert failing.store.load_status(failing_run.run_id).status == "failed"


@pytest.mark.parametrize("implementation_command, review_command", [
    ("", "claude"),
    ("codex", ""),
])
def test_approve_requires_both_runner_commands(tmp_path, implementation_command, review_command):
    flow, executor, _, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)

    status = flow.approve(
        started.run_id,
        contract_path,
        implementation_agent_command=implementation_command,
        review_agent_command=review_command,
    )

    assert status.status == "blocked"
    assert executor.calls[1:] == []


def test_approve_changes_required_is_terminal_and_does_not_start_later_gates(tmp_path):
    flow, executor, notifier, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    add_successful_approval_stages(
        flow,
        executor,
        started,
        contract_path,
        {"schemaVersion": "review-report-v1", "verdict": "changes_required", "findings": [{"severity": "high"}]},
    )

    status = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")

    assert status.status == "changes_required"
    assert status.completed_at == status.updated_at
    assert len(executor.calls) == 3
    assert any("changes required" in title.lower() for title, _ in notifier.messages)


def test_approve_lock_contention_returns_blocked_exit_semantics_without_mutating_run(tmp_path):
    flow, executor, _, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)

    with flow.store.run_lock(started.run_id):
        status = flow.approve(
            started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude"
        )

    from scripts.agent_workflow import _exit_code

    assert status.status == "blocked"
    assert status.error_code == "blocked_run_locked"
    assert status.message == "Workflow approval is already running"
    assert _exit_code(status.status) == 2
    assert flow.store.load_status(started.run_id).status == "awaiting_authorization"
    assert executor.calls[1:] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", "implementation-run-report-v0"),
        ("taskId", "task-7"),
        ("contractFingerprint", "0" * 64),
        ("startHead", "0" * 40),
    ],
)
def test_approve_fails_closed_when_implementation_report_identity_mismatches(tmp_path, field, value):
    flow, executor, _, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    add_successful_approval_stages(flow, executor, started, contract_path, implementation_overrides={field: value})

    status = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")

    assert status.status == "failed"
    assert status.error_code == "failed_implementation_report"
    assert status.completed_at == status.updated_at
    assert len(executor.calls) == 2


def test_approve_duplicate_returns_running_status_without_second_approval_or_execution(tmp_path):
    flow, executor, _, started = started_run(tmp_path)
    contract_path = contract(tmp_path / "task.json", base=flow.store.load_manifest(started.run_id).git_head)
    add_successful_approval_stages(flow, executor, started, contract_path)
    add_passing_final_gates(executor)

    first = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")
    second = flow.approve(started.run_id, contract_path, implementation_agent_command="codex", review_agent_command="claude")

    assert first.status == "completed"
    assert second == first
    assert len(executor.calls) == 6
