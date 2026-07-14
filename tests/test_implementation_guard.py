from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.agents.implementation_guard import (
    capture_worktree_state,
    validate_changes,
    validate_preconditions,
)
from backend.agents.implementation_models import ImplementationTaskContract


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "src").mkdir()
    (root / "src/allowed.py").write_text("value = 1\n", encoding="utf-8")
    (root / "tracked.py").write_text("tracked = True\n", encoding="utf-8")
    write_policy(root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-qb", "codex/test"], cwd=root, check=True)


def write_policy(root: Path) -> None:
    (root / "agent_config").mkdir(exist_ok=True)
    (root / "agent_config/implementation_policies.json").write_text(
        json.dumps(
            {
                "schemaVersion": "implementation-policy-v1",
                "requiredBranchPrefix": "codex/",
                "deniedWritePatterns": [".git/**", "*.db", ".nbs_runtime/**"],
                "limits": {"maxChangedFiles": 8, "maxDiffLines": 800, "maxRepairLoops": 2},
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    init_repo(tmp_path)
    return tmp_path


def contract_for(root: Path, **overrides: object) -> ImplementationTaskContract:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True,
    ).stdout.strip()
    payload: dict[str, object] = {
        "schemaVersion": "implementation-task-v1",
        "taskId": "task-2",
        "planPath": ".superpowers/sdd/task-2-brief.md",
        "planFingerprint": "a" * 64,
        "objective": "Guard implementation agent writes",
        "approvedBaseSha": head,
        "approvedWorktree": str(root),
        "allowedWritePaths": ["src/allowed.py"],
        "validationCommands": ["pytest_targeted"],
        "riskSurfaces": [],
        "maxChangedFiles": 8,
        "maxDiffLines": 800,
        "maxRepairLoops": 2,
    }
    payload.update(overrides)
    return ImplementationTaskContract.from_dict(payload)


def checkout_branch(root: Path, branch: str) -> None:
    if branch == "HEAD":
        subprocess.run(["git", "checkout", "--detach", "-q"], cwd=root, check=True)
        return
    subprocess.run(["git", "checkout", "-q", "-B", branch], cwd=root, check=True)


def write_three_line_change(path: Path) -> None:
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")


@pytest.mark.parametrize("branch", ["main", "feature/free-form", "HEAD"])
def test_guard_requires_codex_branch(tmp_git_repo, branch):
    checkout_branch(tmp_git_repo, branch)
    decision = validate_preconditions(tmp_git_repo, contract_for(tmp_git_repo))
    assert decision.status == "blocked_wrong_branch"


def test_guard_rejects_dirty_start(tmp_git_repo):
    (tmp_git_repo / "tracked.py").write_text("changed\n", encoding="utf-8")
    decision = validate_preconditions(tmp_git_repo, contract_for(tmp_git_repo))
    assert decision.status == "blocked_dirty_worktree"


def test_guard_rejects_approved_head_mismatch(tmp_git_repo):
    contract = contract_for(tmp_git_repo, approvedBaseSha="0" * 40)
    assert validate_preconditions(tmp_git_repo, contract).status == "blocked_head_mismatch"


def test_guard_rejects_path_escape_and_symlink(tmp_git_repo):
    contract = contract_for(tmp_git_repo, allowedWritePaths=["src/allowed.py", "link/out.py"])
    (tmp_git_repo / "link").symlink_to(tmp_git_repo.parent, target_is_directory=True)
    assert validate_preconditions(tmp_git_repo, contract).status == "blocked_scope"


def test_guard_rejects_denied_write_pattern(tmp_git_repo):
    contract = contract_for(tmp_git_repo, allowedWritePaths=["src/allowed.py", "nbs.db"])
    assert validate_preconditions(tmp_git_repo, contract).status == "blocked_scope"


def test_guard_rejects_unapproved_changed_file(tmp_git_repo):
    contract = contract_for(tmp_git_repo, allowedWritePaths=["src/allowed.py"])
    before = capture_worktree_state(tmp_git_repo)
    (tmp_git_repo / "README.md").write_text("outside scope\n", encoding="utf-8")
    assert validate_changes(tmp_git_repo, contract, before).status == "blocked_scope"


def test_guard_includes_deleted_paths_in_scope_check(tmp_git_repo):
    contract = contract_for(tmp_git_repo, allowedWritePaths=["src/allowed.py"])
    before = capture_worktree_state(tmp_git_repo)
    (tmp_git_repo / "tracked.py").unlink()
    assert validate_changes(tmp_git_repo, contract, before).status == "blocked_scope"


def test_guard_rejects_diff_limits(tmp_git_repo):
    contract = contract_for(tmp_git_repo, maxChangedFiles=1, maxDiffLines=2)
    before = capture_worktree_state(tmp_git_repo)
    write_three_line_change(tmp_git_repo / "src/allowed.py")
    assert validate_changes(tmp_git_repo, contract, before).status == "blocked_diff_limit"


def test_guard_rejects_staged_diff_limits_without_modifying_index(tmp_git_repo):
    contract = contract_for(tmp_git_repo, maxChangedFiles=1, maxDiffLines=2)
    before = capture_worktree_state(tmp_git_repo)
    write_three_line_change(tmp_git_repo / "src/allowed.py")
    subprocess.run(["git", "add", "src/allowed.py"], cwd=tmp_git_repo, check=True)
    staged_before = subprocess.run(
        ["git", "diff", "--cached", "--binary", "--"],
        cwd=tmp_git_repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    decision = validate_changes(tmp_git_repo, contract, before)

    staged_after = subprocess.run(
        ["git", "diff", "--cached", "--binary", "--"],
        cwd=tmp_git_repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert decision.status == "blocked_diff_limit"
    assert decision.diff_lines == 4
    assert staged_after == staged_before
