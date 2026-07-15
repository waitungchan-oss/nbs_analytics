import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeExecutor:
    results: list
    calls: list = field(default_factory=list)

    def run_json(self, argv, *, timeout, require_json=True):
        self.calls.append((argv, timeout, require_json))
        return self.results.pop(0)


def _result(payload, exit_code=0, stdout_tail="", stderr_tail=""):
    from backend.agents.workflow_orchestrator import StageResult

    return StageResult(exit_code, payload, stdout_tail, stderr_tail, 1)


def test_cli_fake_executor_runs_start_then_explicit_approval_to_completed_without_command_persistence(tmp_path, monkeypatch, capsys):
    import scripts.agent_workflow as cli
    from backend.agents.workflow_notifications import NoOpWorkflowNotifier
    from backend.agents.workflow_orchestrator import WorkflowOrchestrator
    from backend.agents.workflow_store import WorkflowStore
    from backend.agents.implementation_models import ImplementationTaskContract

    for name in ["evidence_allowlist.json", "token_budgets.json", "workflow_retention.json"]:
        source = ROOT / "agent_config" / name
        target = tmp_path / "agent_config" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    brief = tmp_path / "docs" / "brief.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("# approved brief\n", encoding="utf-8")
    plan = tmp_path / "docs" / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    from backend.agents.evidence_models import canonical_fingerprint

    context_unsigned = {
        "schemaVersion": "context-evidence-v1",
        "task": {"id": "task-7", "objective": "complete fake workflow", "scope": [], "forbidden": []},
        "repository": {"head": "b" * 40, "dirtyFiles": []},
        "guardrails": {"revenueScope": "read-only"},
        "documents": [], "symbols": [], "relatedTests": [], "recentChanges": [],
    }
    context = {**context_unsigned, "bundleFingerprint": canonical_fingerprint(context_unsigned)}
    executor = FakeExecutor([_result(context)])

    def build_flow(*, notify, housekeeping=None):
        flow = WorkflowOrchestrator(
            tmp_path, store=WorkflowStore(tmp_path), stage_executor=executor,
            notifier=NoOpWorkflowNotifier(), housekeeping=housekeeping,
        )
        flow._git_identity = lambda: {"branch": "codex/test", "head": "b" * 40, "dirtyFiles": []}
        return flow

    monkeypatch.setattr(cli, "_orchestrator", build_flow)
    monkeypatch.setattr(cli, "_run_housekeeping", lambda: None)

    assert cli.main(["run", "--brief", "docs/brief.md", "--no-notify"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["status"] == "awaiting_authorization"
    assert "--collect-only" in executor.calls[0][0]

    manifest = WorkflowStore(tmp_path).load_manifest(started["runId"])
    contract_payload = {
        "schemaVersion": "implementation-task-v1", "taskId": "task-7", "planPath": "docs/plan.md",
        "planFingerprint": __import__("hashlib").sha256(plan.read_bytes()).hexdigest(),
        "objective": "complete fake workflow", "approvedBaseSha": manifest.git_head,
        "approvedWorktree": str(tmp_path), "allowedWritePaths": ["scripts/agent_workflow.py"],
        "validationCommands": ["pytest_targeted"], "riskSurfaces": [], "maxChangedFiles": 1,
        "maxDiffLines": 10, "maxRepairLoops": 1, "taskType": "test", "redCommands": ["pytest_targeted"],
        "greenCommands": ["pytest_targeted"],
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract_payload), encoding="utf-8")
    contract = ImplementationTaskContract.from_dict(contract_payload)
    implementation = {
        "schemaVersion": "implementation-run-report-v1", "status": "completed", "taskId": "task-7",
        "contractFingerprint": contract.fingerprint, "startHead": manifest.git_head, "endHead": manifest.git_head,
        "changedFiles": [], "diffStat": {"files": 0, "lines": 0}, "redEvidence": [], "greenEvidence": [],
        "repairLoopsUsed": 0, "testFilesChanged": [], "productionFilesChanged": [], "findings": [],
    }
    executor.results.extend([
        _result(implementation),
        _result({"schemaVersion": "review-report-v1", "verdict": "pass", "findings": []}),
        _result({}, stdout_tail="1 passed"), _result({"status": "passed"}), _result({"overallStatus": "pass"}),
    ])

    assert cli.main([
        "approve", "--run-id", started["runId"], "--contract", str(contract_path),
        "--implementation-agent-command", "codex fake-implementation",
        "--review-agent-command", "claude fake-review", "--no-notify",
    ]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "completed"
    review_argv = executor.calls[2][0]
    assert "--task-contract" in review_argv
    task_contract_path = Path(review_argv[review_argv.index("--task-contract") + 1])
    implementation_argv = executor.calls[1][0]
    implementation_contract_path = Path(implementation_argv[implementation_argv.index("--contract") + 1])
    assert task_contract_path == implementation_contract_path
    assert task_contract_path != contract_path
    assert not task_contract_path.exists()
    run_dir = tmp_path / ".nbs_agent_runtime" / "runs" / started["runId"]
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.iterdir() if path.is_file())
    assert "codex fake-implementation" not in persisted
    assert "claude fake-review" not in persisted
