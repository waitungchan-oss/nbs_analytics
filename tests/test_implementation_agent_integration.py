from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from backend.agents.implementation_agent_service import ImplementationAgentService
from backend.agents.implementation_guard import capture_worktree_state
from backend.agents.implementation_models import (
    ImplementationTaskContract,
    ValidationResult,
)


ROOT = Path(__file__).resolve().parents[1]


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _project_formal_state_hashes() -> dict[str, str | None]:
    return {
        "db": _digest(ROOT / "nbs_marketing_data.db"),
        "runtime": _digest(ROOT / ".nbs_runtime/data_generation.json"),
    }


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True,
    ).stdout.strip()


def _write_fixture_config(root: Path) -> None:
    config = root / "agent_config"
    config.mkdir()
    (config / "implementation_policies.json").write_text(
        json.dumps(
            {
                "schemaVersion": "implementation-policy-v1",
                "requiredBranchPrefix": "codex/",
                "deniedRiskSurfaces": ["baseline", "sqlite"],
                "deniedWritePatterns": [".git/**", "*.db", ".nbs_runtime/**", ".nbs_agent_runtime/**"],
                "limits": {"maxChangedFiles": 8, "maxDiffLines": 800, "maxRepairLoops": 2},
            }
        ),
        encoding="utf-8",
    )
    (config / "evidence_allowlist.json").write_text(
        json.dumps(
            {
                "schemaVersion": "evidence-allowlist-v1",
                "readRoots": ["agent_config", "data", "docs", "sandbox", "tests"],
                "rootFiles": ["AGENTS.md"],
                "defaultContextFiles": ["AGENTS.md"],
                "extensions": [".md", ".py", ".json"],
                "denyPatterns": [".nbs_agent_runtime/**", "*.db"],
                "agentExecutables": ["codex"],
            }
        ),
        encoding="utf-8",
    )
    (config / "token_budgets.json").write_text(
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


class FixtureValidationRunner:
    def __init__(self, exit_codes: list[int]) -> None:
        self._exit_codes = exit_codes

    def run(self, command_id: str, arguments: tuple[str, ...]) -> ValidationResult:
        exit_code = self._exit_codes.pop(0)
        return ValidationResult(
            command_id=command_id,
            argv=("fixture-python", command_id, *arguments),
            exit_code=exit_code,
            stdout="passed" if exit_code == 0 else "failing test proves RED",
            stderr="",
            duration_ms=1,
        )


@dataclass
class AgentFixture:
    worktree: Path
    formal_db: Path
    formal_runtime: Path
    initial_index_fingerprint: str

    def formal_state_hashes(self) -> dict[str, str]:
        return {
            "db": hashlib.sha256(self.formal_db.read_bytes()).hexdigest(),
            "runtime": hashlib.sha256(self.formal_runtime.read_bytes()).hexdigest(),
        }

    def git_index_unchanged(self) -> bool:
        return capture_worktree_state(self.worktree).index_fingerprint == self.initial_index_fingerprint

    def run_task(
        self,
        *,
        allowed_write_paths: tuple[str, ...],
        runner: str,
    ):
        assert runner == "tests/fixtures/implementation_runner.py"
        service = ImplementationAgentService(
            self.worktree,
            validation_runner=FixtureValidationRunner([1, 0, 0]),
        )
        report = service.execute(
            self._contract(allowed_write_paths, task_type="behavior", red_commands=("pytest_targeted",)),
            self._approved_runner,
        )
        return report

    def run_hostile_task(self, *, target: str):
        assert target == "data/nbs_analytics.db"
        service = ImplementationAgentService(
            self.worktree,
            validation_runner=FixtureValidationRunner([0]),
        )
        return service.execute(
            self._contract(("sandbox/example.py",), task_type="test", red_commands=()),
            self._hostile_runner,
        )

    def _contract(
        self,
        allowed_write_paths: tuple[str, ...],
        *,
        task_type: str,
        red_commands: tuple[str, ...],
    ) -> ImplementationTaskContract:
        return ImplementationTaskContract.from_dict(
            {
                "schemaVersion": "implementation-task-v1",
                "taskId": "Task-8",
                "planPath": "docs/task-8-brief.md",
                "planFingerprint": "a" * 64,
                "objective": "Verify isolated implementation-agent execution.",
                "approvedBaseSha": _git(self.worktree, "rev-parse", "HEAD"),
                "approvedWorktree": str(self.worktree),
                "allowedWritePaths": list(allowed_write_paths),
                "validationCommands": ["pytest_targeted", "py_compile"],
                "riskSurfaces": [],
                "maxChangedFiles": 8,
                "maxDiffLines": 800,
                "maxRepairLoops": 2,
                "taskType": task_type,
                "redCommands": list(red_commands),
                "greenCommands": ["pytest_targeted", "py_compile"],
            }
        )

    def _approved_runner(self, request: dict) -> dict:
        (self.worktree / "sandbox/example.py").write_text("value = 2\n", encoding="utf-8")
        (self.worktree / "tests/sandbox/test_example.py").write_text(
            "def test_example():\n    assert True\n", encoding="utf-8"
        )
        return {
            "schemaVersion": "implementation-response-v1",
            "status": "completed",
            "summary": "changed only the approved fixture files",
            "requestedValidationCommandIds": ["pytest_targeted", "py_compile"],
        }

    def _hostile_runner(self, request: dict) -> dict:
        self.formal_db.write_bytes(b"hostile fixture write")
        return {
            "schemaVersion": "implementation-response-v1",
            "status": "completed",
            "summary": "attempted a forbidden formal-state write",
            "requestedValidationCommandIds": ["pytest_targeted", "py_compile"],
        }


@pytest.fixture
def agent_fixture(tmp_path: Path) -> AgentFixture:
    source_root = tmp_path / "fixture-source"
    worktree = tmp_path / "isolated-worktree"
    source_root.mkdir()
    _git(source_root, "init", "-q")
    _git(source_root, "config", "user.email", "test@example.com")
    _git(source_root, "config", "user.name", "Task 8 Fixture")
    (source_root / "docs").mkdir()
    (source_root / "sandbox").mkdir()
    (source_root / "tests/sandbox").mkdir(parents=True)
    (source_root / "data").mkdir()
    (source_root / ".nbs_runtime").mkdir()
    (source_root / "AGENTS.md").write_text("fixture-only agent guardrails\n", encoding="utf-8")
    (source_root / "docs/task-8-brief.md").write_text("fixture-only Task 8 brief\n", encoding="utf-8")
    (source_root / "sandbox/example.py").write_text("value = 1\n", encoding="utf-8")
    (source_root / "tests/sandbox/test_example.py").write_text(
        "def test_example():\n    assert False\n", encoding="utf-8"
    )
    (source_root / "data/nbs_analytics.db").write_bytes(b"fixture database only")
    (source_root / ".nbs_runtime/data_generation.json").write_text(
        '{"fixture": true}\n', encoding="utf-8"
    )
    _write_fixture_config(source_root)
    _git(source_root, "add", ".")
    _git(source_root, "commit", "-qm", "fixture base")
    _git(source_root, "worktree", "add", "-q", "-b", "codex/task-8-fixture", str(worktree), "HEAD")

    initial_index_fingerprint = capture_worktree_state(worktree).index_fingerprint
    return AgentFixture(
        worktree=worktree,
        formal_db=worktree / "data/nbs_analytics.db",
        formal_runtime=worktree / ".nbs_runtime/data_generation.json",
        initial_index_fingerprint=initial_index_fingerprint,
    )


def test_agent_changes_only_approved_file_in_isolated_worktree(agent_fixture):
    before = agent_fixture.formal_state_hashes()

    report = agent_fixture.run_task(
        allowed_write_paths=("sandbox/example.py", "tests/sandbox/test_example.py"),
        runner="tests/fixtures/implementation_runner.py",
    )

    assert report.status == "completed"
    assert set(report.changed_files) == {"sandbox/example.py", "tests/sandbox/test_example.py"}
    assert agent_fixture.formal_state_hashes() == before
    assert agent_fixture.git_index_unchanged()


def test_hostile_runner_cannot_receive_pass_after_formal_state_write(agent_fixture):
    fixture_before = agent_fixture.formal_state_hashes()
    project_before = _project_formal_state_hashes()

    report = agent_fixture.run_hostile_task(target="data/nbs_analytics.db")

    fixture_after = agent_fixture.formal_state_hashes()
    assert report.status == "blocked_scope"
    assert report.changed_files == ("data/nbs_analytics.db",)
    assert report.findings[0]["code"] == "blocked_scope"
    assert report.findings[0]["paths"] == ["data/nbs_analytics.db"]
    assert "policy" in report.findings[0]["message"]
    assert fixture_after["db"] != fixture_before["db"]
    assert fixture_after["runtime"] == fixture_before["runtime"]
    assert _project_formal_state_hashes() == project_before
    assert agent_fixture.git_index_unchanged()
