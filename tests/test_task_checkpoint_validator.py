from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.task_checkpoint_validator import (
    CheckpointValidationResult,
    inspect_git_state,
    validate_checkpoint,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "allowed.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "allowed.py")
    git(repo, "commit", "-qm", "initial")
    return repo


def staged_change(repo: Path, filename: str = "allowed.py") -> tuple[str, str]:
    path = repo / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("value = 2\n", encoding="utf-8")
    git(repo, "add", filename)
    return git(repo, "rev-parse", "HEAD"), git(repo, "diff", "--cached", "--binary")


def contract(allowed: tuple[str, ...] = ("allowed.py",)) -> dict:
    return {
        "taskId": "task-02",
        "taskContractFingerprint": "b" * 64,
        "allowedFiles": list(allowed),
        "allowNoOp": False,
    }


def verification(repo: Path, parent: str, diff: str, *, status: str = "pass") -> dict:
    return {
        "sourceHead": parent,
        "taskContractFingerprint": "b" * 64,
        "changedFiles": ["allowed.py"],
        "diffFingerprint": canonical_fingerprint(diff),
        "reviewFingerprint": "d" * 64,
        "focusedVerificationStatus": status,
        "gitDiffCheck": "pass",
        "generatedAt": "2026-09-01T00:00:00Z",
    }


def test_validator_accepts_only_allowlisted_staged_change(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    parent, diff = staged_change(repo)

    result = validate_checkpoint(repo, contract(), verification(repo, parent, diff), expected_parent_head=parent)

    assert isinstance(result, CheckpointValidationResult)
    assert result.status == "ready"
    assert result.evidence is not None
    assert result.evidence.parent_head == parent


@pytest.mark.parametrize("mutation", ["parent_drift", "unrelated_dirty", "allowlist_violation", "failed_review", "stale_verification"])
def test_validator_blocks_unsafe_checkpoint_mutation(tmp_path: Path, mutation: str) -> None:
    repo = initialized_repo(tmp_path)
    parent, diff = staged_change(repo)
    task = contract()
    check = verification(repo, parent, diff)
    expected_parent = parent
    if mutation == "parent_drift":
        expected_parent = "f" * 40
    elif mutation == "unrelated_dirty":
        (repo / "unrelated.txt").write_text("must remain dirty\n", encoding="utf-8")
    elif mutation == "allowlist_violation":
        staged_change(repo, "forbidden.py")
        check["changedFiles"] = ["forbidden.py"]
    elif mutation == "failed_review":
        check["reviewFingerprint"] = None
    elif mutation == "stale_verification":
        check["sourceHead"] = "e" * 40

    result = validate_checkpoint(repo, task, check, expected_parent_head=expected_parent)

    assert result.status == "blocked"
    assert result.reasons


def test_inspect_git_state_is_read_only_and_reports_staged_files(tmp_path: Path) -> None:
    repo = initialized_repo(tmp_path)
    staged_change(repo)
    before = git(repo, "status", "--short")

    state = inspect_git_state(repo)

    assert state.head == git(repo, "rev-parse", "HEAD")
    assert state.staged_files == ("allowed.py",)
    assert git(repo, "status", "--short") == before
