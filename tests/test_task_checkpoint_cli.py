from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.test_task_checkpoint_validator import initialized_repo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "scripts" / "task_checkpoint.py"


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "--project-root", str(repo), *args],
        text=True, capture_output=True, check=False,
    )


def git_state(repo: Path) -> tuple[str, str]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True).stdout
    status = subprocess.run(["git", "status", "--short"], cwd=repo, text=True, capture_output=True, check=True).stdout
    return head, status


def test_inspect_cli_emits_bounded_json_without_mutating_git(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    before = git_state(repo)

    completed = run_cli(repo, "inspect")

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["schemaVersion"] == "task-checkpoint-cli-v1"
    assert payload["status"] == "ready"
    assert git_state(repo) == before
    assert "commit" not in completed.stdout.lower()
    assert payload["git"]["stagedFiles"] == []


def test_validate_cli_returns_blocked_exit_without_git_mutation(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    task = tmp_path / "task.json"
    verification = tmp_path / "verification.json"
    task.write_text(json.dumps({"taskId": "task-03", "taskContractFingerprint": "b" * 64, "allowedFiles": ["allowed.py"]}), encoding="utf-8")
    verification.write_text(json.dumps({"sourceHead": "a" * 40}), encoding="utf-8")
    before = git_state(repo)

    completed = run_cli(repo, "validate", "--task-contract", str(task), "--verification", str(verification), "--expected-parent-head", "a" * 40)

    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "blocked"
    assert git_state(repo) == before


def test_cli_rejects_git_mutation_actions(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)

    completed = run_cli(repo, "commit")

    assert completed.returncode != 0
    assert "invalid choice" in completed.stderr
