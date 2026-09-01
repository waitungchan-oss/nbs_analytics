"""Read-only validation for one Task checkpoint boundary."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.task_checkpoint_models import (
    TaskCheckpointEvidence,
    TaskCheckpointEvidenceError,
)


class CheckpointValidationError(ValueError):
    """Raised when live Git state cannot be inspected safely."""


@dataclass(frozen=True)
class GitCheckpointState:
    head: str
    changed_files: tuple[str, ...]
    staged_files: tuple[str, ...]
    status_lines: tuple[str, ...]
    staged_diff: str


@dataclass(frozen=True)
class CheckpointValidationResult:
    status: Literal["ready", "blocked", "no_op"]
    reasons: tuple[str, ...]
    evidence: TaskCheckpointEvidence | None


def _run_git(project_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=project_root, text=True, capture_output=True,
            check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckpointValidationError(f"git inspection failed: {type(exc).__name__}") from exc
    if result.returncode != 0:
        raise CheckpointValidationError("git inspection returned a non-zero status")
    if len(result.stdout.encode("utf-8")) > 5 * 1024 * 1024:
        raise CheckpointValidationError("git inspection output exceeded cap")
    return result.stdout


def _files(output: str) -> tuple[str, ...]:
    return tuple(item for item in output.splitlines() if item)


def _diff_fingerprint(diff: str) -> str:
    return canonical_fingerprint(diff.rstrip("\n"))


def inspect_git_state(project_root: Path) -> GitCheckpointState:
    root = project_root.resolve(strict=True)
    head = _run_git(root, "rev-parse", "HEAD").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise CheckpointValidationError("live HEAD is not a Git SHA")
    status_lines = tuple(line for line in _run_git(root, "status", "--short").splitlines() if line)
    changed = _files(_run_git(root, "diff", "--name-only", "HEAD"))
    staged = _files(_run_git(root, "diff", "--cached", "--name-only"))
    staged_diff = _run_git(root, "diff", "--cached", "--binary", "--no-ext-diff")
    return GitCheckpointState(head, changed, staged, status_lines, staged_diff)


def _blocked(reasons: list[str]) -> CheckpointValidationResult:
    return CheckpointValidationResult("blocked", tuple(dict.fromkeys(reasons)), None)


def _required_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CheckpointValidationError(f"{name} must be an object")
    return value


def validate_checkpoint(
    project_root: Path,
    task_contract: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    expected_parent_head: str,
) -> CheckpointValidationResult:
    try:
        state = inspect_git_state(project_root)
    except CheckpointValidationError as exc:
        return _blocked(["git_inspection_failed", str(exc)])

    contract = _required_mapping(task_contract, "task contract")
    check = _required_mapping(verification, "verification")
    reasons: list[str] = []
    if state.head != expected_parent_head:
        reasons.append("parent_head_drift")
    if any(line.startswith("??") for line in state.status_lines):
        reasons.append("untracked_dirty_worktree")
    allowed = tuple(contract.get("allowedFiles", ()))
    changed = state.changed_files
    if not changed:
        if contract.get("allowNoOp") is True:
            return CheckpointValidationResult("no_op", (), None)
        return _blocked(["no_changes"])
    if tuple(check.get("changedFiles", ())) != changed:
        reasons.append("changed_files_mismatch")
    if tuple(state.staged_files) != tuple(changed):
        reasons.append("staged_files_mismatch")
    if not isinstance(allowed, (tuple, list)) or not set(changed).issubset(set(allowed)):
        reasons.append("allowlist_violation")
    if check.get("sourceHead") != state.head:
        reasons.append("verification_source_head_mismatch")
    if check.get("taskContractFingerprint") != contract.get("taskContractFingerprint"):
        reasons.append("task_contract_fingerprint_mismatch")
    if check.get("diffFingerprint") != _diff_fingerprint(state.staged_diff):
        reasons.append("diff_fingerprint_mismatch")
    if not isinstance(check.get("reviewFingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", check["reviewFingerprint"]):
        reasons.append("review_evidence_missing")
    if check.get("focusedVerificationStatus") != "pass":
        reasons.append("focused_verification_failed")
    if check.get("gitDiffCheck") != "pass":
        reasons.append("git_diff_check_failed")
    if reasons:
        return _blocked(reasons)
    try:
        evidence = TaskCheckpointEvidence.build(
            task_id=contract["taskId"],
            task_contract_fingerprint=contract["taskContractFingerprint"],
            parent_head=state.head,
            allowed_files=tuple(allowed),
            changed_files=changed,
            diff_fingerprint=check["diffFingerprint"],
            review_fingerprint=check["reviewFingerprint"],
            focused_verification={
                "status": "pass",
                "commandIds": check.get("commandIds", ["focused-verification"]),
                "evidenceFingerprint": check.get("focusedEvidenceFingerprint", "0" * 64),
            },
            git_diff_check="pass",
            generated_at=check["generatedAt"],
        )
    except (KeyError, TaskCheckpointEvidenceError, TypeError) as exc:
        return _blocked(["checkpoint_evidence_invalid", str(exc)])
    return CheckpointValidationResult("ready", (), evidence)
