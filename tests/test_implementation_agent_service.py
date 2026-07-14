from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agents.implementation_agent_service import ImplementationAgentService
from backend.agents.implementation_models import (
    ImplementationTaskContract,
    ValidationResult,
)


def write_config(root: Path) -> None:
    (root / "agent_config").mkdir()
    (root / "agent_config/implementation_policies.json").write_text(
        json.dumps(
            {
                "schemaVersion": "implementation-policy-v1",
                "requiredBranchPrefix": "codex/",
                "deniedRiskSurfaces": ["baseline", "sqlite"],
                "deniedWritePatterns": [".git/**", "*.db", ".nbs_agent_runtime/**"],
                "limits": {"maxChangedFiles": 8, "maxDiffLines": 800, "maxRepairLoops": 2},
            }
        ),
        encoding="utf-8",
    )
    (root / "agent_config/evidence_allowlist.json").write_text(
        json.dumps(
            {
                "schemaVersion": "evidence-allowlist-v1",
                "readRoots": ["agent_config", "docs", "src", "tests"],
                "rootFiles": ["AGENTS.md"],
                "defaultContextFiles": ["AGENTS.md"],
                "extensions": [".md", ".py", ".json"],
                "denyPatterns": [".nbs_agent_runtime/**", "*.db"],
                "agentExecutables": ["codex"],
            }
        ),
        encoding="utf-8",
    )
    (root / "agent_config/token_budgets.json").write_text(
        json.dumps(
            {
                "schemaVersion": "token-budgets-v1",
                "context": {"inputTokens": 12000, "outputTokens": 1500},
                "review": {"inputTokens": 16000, "outputTokens": 2000},
                "implementation": {"inputTokens": 12000, "outputTokens": 2000, "maxRepairLoops": 2},
                "excerpt": {"maxFileLines": 120, "symbolContextLines": 20, "maxCommandCharacters": 12000},
            }
        ),
        encoding="utf-8",
    )


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "docs").mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "AGENTS.md").write_text("project guardrails\n", encoding="utf-8")
    (root / "docs/task-4-brief.md").write_text("Implement one approved task.\n", encoding="utf-8")
    (root / "src/allowed.py").write_text("value = 1\n", encoding="utf-8")
    (root / "tests/test_allowed.py").write_text("def test_allowed():\n    assert True\n", encoding="utf-8")
    write_config(root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-qb", "codex/test"], cwd=root, check=True)


def head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True,
    ).stdout.strip()


def make_contract(root: Path, **overrides: object) -> ImplementationTaskContract:
    payload: dict[str, object] = {
        "schemaVersion": "implementation-task-v1",
        "taskId": "Task-4",
        "planPath": "docs/task-4-brief.md",
        "planFingerprint": "a" * 64,
        "objective": "Orchestrate one approved implementation task",
        "approvedBaseSha": head(root),
        "approvedWorktree": str(root),
        "allowedWritePaths": ["src/allowed.py", "tests/test_allowed.py"],
        "validationCommands": ["pytest_targeted", "py_compile"],
        "riskSurfaces": [],
        "maxChangedFiles": 8,
        "maxDiffLines": 800,
        "maxRepairLoops": 2,
    }
    payload.update(overrides)
    return ImplementationTaskContract.from_dict(payload)


class RunnerSpy:
    def __init__(self, root: Path, *, write_path: str = "src/allowed.py", response: object | None = None) -> None:
        self.root = root
        self.write_path = write_path
        self.response = response
        self.calls = 0
        self.last_request = None

    def command(self, request: dict) -> object:
        self.calls += 1
        self.last_request = request
        (self.root / self.write_path).write_text(f"value = {self.calls + 1}\n", encoding="utf-8")
        return self.response or {
            "schemaVersion": "implementation-response-v1",
            "status": "completed",
            "summary": "implemented approved task",
            "requestedValidationCommandIds": ["pytest_targeted", "py_compile"],
        }


class FakeValidationRunner:
    def __init__(self, exit_codes: list[int] | None = None) -> None:
        self.exit_codes = list(exit_codes or [0])
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, command_id: str, arguments: tuple[str, ...]) -> ValidationResult:
        self.calls.append((command_id, arguments))
        exit_code = self.exit_codes.pop(0) if self.exit_codes else 0
        return ValidationResult(
            command_id=command_id,
            argv=(".venv/bin/python", command_id, *arguments),
            exit_code=exit_code,
            stdout="passed" if exit_code == 0 else "failed",
            stderr="",
            duration_ms=1,
        )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


@pytest.fixture
def contract(project_root: Path) -> ImplementationTaskContract:
    return make_contract(project_root)


@pytest.fixture
def validation_runner() -> FakeValidationRunner:
    return FakeValidationRunner()


@pytest.fixture
def service(project_root: Path, validation_runner: FakeValidationRunner) -> ImplementationAgentService:
    return ImplementationAgentService(project_root, validation_runner=validation_runner)


def test_service_rejects_more_than_one_plan_task(service, contract, project_root):
    object.__setattr__(contract, "task_id", "Task 2, Task 3")
    runner = RunnerSpy(project_root)

    report = service.execute(contract, runner.command)

    assert report.status == "blocked_invalid_contract"
    assert runner.calls == 0


def test_service_sends_bundle_and_contract_as_json(service, contract, project_root):
    runner_spy = RunnerSpy(project_root)

    report = service.execute(contract, runner_spy.command)

    request = runner_spy.last_request
    assert request["schemaVersion"] == "implementation-request-v1"
    assert request["contractFingerprint"] == contract.fingerprint
    assert request["task"]["taskId"] == contract.task_id
    assert request["context"]["schemaVersion"] == "context-evidence-v1"
    assert report.status == "completed"


def test_service_rejects_invalid_agent_json(service, contract, project_root):
    invalid_json_runner = RunnerSpy(project_root, response="not-json")

    report = service.execute(contract, invalid_json_runner.command)

    assert report.status == "invalid_agent_output"


def test_service_stops_before_runner_when_high_risk(service, contract, project_root):
    high_risk_contract = replace(contract, risk_surfaces=("baseline",))
    runner_spy = RunnerSpy(project_root)

    report = service.execute(high_risk_contract, runner_spy.command)

    assert report.status == "blocked_high_risk"
    assert runner_spy.calls == 0


def test_service_stops_before_runner_when_request_exceeds_token_limit(
    service, contract, project_root, monkeypatch,
):
    monkeypatch.setattr(service, "_implementation_budget", lambda key: 1)
    runner_spy = RunnerSpy(project_root)

    report = service.execute(contract, runner_spy.command)

    assert report.status == "context_overflow"
    assert runner_spy.calls == 0


def test_service_stops_after_out_of_scope_write(service, contract, project_root):
    writes_outside_scope = RunnerSpy(project_root, write_path="README.md")

    report = service.execute(contract, writes_outside_scope.command)

    assert report.status == "blocked_scope"
    assert "README.md" in report.findings[0]["paths"]


def test_service_blocks_allowed_source_write_that_mutates_git_index(
    service, contract, project_root,
):
    def runner(request: dict) -> object:
        (project_root / "src/allowed.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/allowed.py"], cwd=project_root, check=True)
        return {
            "schemaVersion": "implementation-response-v1",
            "status": "completed",
            "summary": "implemented approved task",
            "requestedValidationCommandIds": ["pytest_targeted", "py_compile"],
        }

    report = service.execute(contract, runner)

    assert report.status == "blocked_scope"
    assert report.findings[0]["indexFingerprintChanged"] is True
    assert "src/allowed.py" in report.findings[0]["paths"]


def test_service_collects_context_for_the_contract(service, contract):
    bundle = service.collect(contract)

    assert bundle.schema_version == "context-evidence-v1"
    assert bundle.task["id"] == contract.task_id
    assert bundle.task["objective"] == contract.objective


def test_service_collects_approved_plan_as_context_evidence(service, contract):
    bundle = service.collect(contract)

    assert any(item.source == contract.plan_path for item in bundle.evidence)


def test_service_limits_repair_calls_and_writes_telemetry(
    service, contract, project_root, validation_runner,
):
    validation_runner.exit_codes = [1, 1, 1, 1, 1, 1]
    runner_spy = RunnerSpy(project_root)

    report = service.execute(contract, runner_spy.command)

    assert report.status == "validation_failed"
    assert runner_spy.calls == 1 + contract.max_repair_loops
    assert (project_root / ".nbs_agent_runtime/implementation/reports").is_dir()
    assert (project_root / ".nbs_agent_runtime/implementation/telemetry.jsonl").is_file()
