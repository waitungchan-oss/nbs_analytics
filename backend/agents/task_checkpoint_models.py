"""Bounded, source-bound models for Task checkpoint commits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from backend.agents.evidence_models import canonical_fingerprint


CHECKPOINT_EVIDENCE_SCHEMA = "task-checkpoint-evidence-v1"
CHECKPOINT_CLI_SCHEMA = "task-checkpoint-cli-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_TASK_ID = re.compile(r"^task-[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")
_SENSITIVE = re.compile(r"(?:api[_-]?key|secret|token|password|credential|private[_-]?key)", re.IGNORECASE)


class TaskCheckpointEvidenceError(ValueError):
    """Raised when checkpoint metadata is not bounded or source-bound."""


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise TaskCheckpointEvidenceError(f"{name} must be lowercase SHA-256")
    return value


def _git_sha(value: object, name: str) -> str:
    if not isinstance(value, str) or not _GIT_SHA.fullmatch(value):
        raise TaskCheckpointEvidenceError(f"{name} must be a 40-character Git SHA")
    return value


def _task_id(value: object) -> str:
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
        raise TaskCheckpointEvidenceError("taskId is invalid")
    return value


def _paths(values: object, name: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 0 < len(values) <= 100:
        raise TaskCheckpointEvidenceError(f"{name} must be a bounded non-empty list")
    result = []
    for value in values:
        if not isinstance(value, str) or len(value) > 240 or not _SAFE_PATH.fullmatch(value):
            raise TaskCheckpointEvidenceError(f"{name} contains an unsafe path")
        if value.startswith("/") or value.startswith("-") or ".." in value.split("/"):
            raise TaskCheckpointEvidenceError(f"{name} contains an unsafe path")
        result.append(value)
    return tuple(result)


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise TaskCheckpointEvidenceError("generatedAt must be an ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TaskCheckpointEvidenceError("generatedAt must be an ISO timestamp") from exc
    return value


def _focused(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"status", "commandIds", "evidenceFingerprint"}:
        raise TaskCheckpointEvidenceError("focusedVerification has an invalid schema")
    if value["status"] != "pass":
        raise TaskCheckpointEvidenceError("focusedVerification must be pass")
    command_ids = value["commandIds"]
    if not isinstance(command_ids, list) or not 0 < len(command_ids) <= 20:
        raise TaskCheckpointEvidenceError("commandIds must be bounded")
    if any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", item) for item in command_ids):
        raise TaskCheckpointEvidenceError("commandIds are invalid")
    return {"status": "pass", "commandIds": list(command_ids), "evidenceFingerprint": _sha(value["evidenceFingerprint"], "focused evidence")}


@dataclass(frozen=True)
class TaskCheckpointEvidence:
    task_id: str
    task_contract_fingerprint: str
    parent_head: str
    allowed_files: tuple[str, ...]
    changed_files: tuple[str, ...]
    diff_fingerprint: str
    review_fingerprint: str
    focused_verification: Mapping[str, object]
    git_diff_check: str
    generated_at: str
    evidence_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        _task_id(self.task_id)
        _sha(self.task_contract_fingerprint, "taskContractFingerprint")
        _git_sha(self.parent_head, "parentHead")
        allowed = _paths(self.allowed_files, "allowedFiles")
        changed = _paths(self.changed_files, "changedFiles")
        if not set(changed).issubset(allowed):
            raise TaskCheckpointEvidenceError("changedFiles must be within allowedFiles")
        _sha(self.diff_fingerprint, "diffFingerprint")
        _sha(self.review_fingerprint, "reviewFingerprint")
        _focused(self.focused_verification)
        if self.git_diff_check != "pass":
            raise TaskCheckpointEvidenceError("gitDiffCheck must be pass")
        _timestamp(self.generated_at)
        object.__setattr__(self, "allowed_files", allowed)
        object.__setattr__(self, "changed_files", changed)
        object.__setattr__(self, "focused_verification", _focused(self.focused_verification))
        object.__setattr__(self, "evidence_fingerprint", canonical_fingerprint(self._unsigned_dict()))

    @classmethod
    def build(cls, **kwargs: object) -> "TaskCheckpointEvidence":
        return cls(**kwargs)

    def _unsigned_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": CHECKPOINT_EVIDENCE_SCHEMA,
            "taskId": self.task_id,
            "taskContractFingerprint": self.task_contract_fingerprint,
            "parentHead": self.parent_head,
            "allowedFiles": list(self.allowed_files),
            "changedFiles": list(self.changed_files),
            "diffFingerprint": self.diff_fingerprint,
            "reviewFingerprint": self.review_fingerprint,
            "focusedVerification": dict(self.focused_verification),
            "gitDiffCheck": self.git_diff_check,
            "generatedAt": self.generated_at,
        }

    def recompute_fingerprint(self) -> str:
        return canonical_fingerprint(self._unsigned_dict())

    def to_dict(self) -> dict[str, object]:
        return {**self._unsigned_dict(), "evidenceFingerprint": self.evidence_fingerprint}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, expected_parent_head: str | None = None) -> "TaskCheckpointEvidence":
        expected = {
            "schemaVersion", "taskId", "taskContractFingerprint", "parentHead", "allowedFiles", "changedFiles",
            "diffFingerprint", "reviewFingerprint", "focusedVerification", "gitDiffCheck", "generatedAt", "evidenceFingerprint",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise TaskCheckpointEvidenceError("checkpoint evidence has unknown or missing fields")
        if payload["schemaVersion"] != CHECKPOINT_EVIDENCE_SCHEMA:
            raise TaskCheckpointEvidenceError("checkpoint evidence schema is invalid")
        if _SENSITIVE.search(str(payload)):
            raise TaskCheckpointEvidenceError("checkpoint evidence contains sensitive content")
        evidence = cls(
            task_id=payload["taskId"], task_contract_fingerprint=payload["taskContractFingerprint"],
            parent_head=payload["parentHead"], allowed_files=payload["allowedFiles"], changed_files=payload["changedFiles"],
            diff_fingerprint=payload["diffFingerprint"], review_fingerprint=payload["reviewFingerprint"],
            focused_verification=payload["focusedVerification"], git_diff_check=payload["gitDiffCheck"], generated_at=payload["generatedAt"],
        )
        if expected_parent_head is not None and evidence.parent_head != expected_parent_head:
            raise TaskCheckpointEvidenceError("parent HEAD mismatch")
        if payload["evidenceFingerprint"] != evidence.evidence_fingerprint:
            raise TaskCheckpointEvidenceError("evidence fingerprint mismatch")
        return evidence


@dataclass(frozen=True)
class TaskCheckpointCommitMetadata:
    task_id: str
    task_contract_fingerprint: str
    scope: str
    allowed_files: tuple[str, ...]
    parent_head: str
    diff_fingerprint: str
    review_fingerprint: str
    focused_verification_status: str

    def __post_init__(self) -> None:
        _task_id(self.task_id)
        _sha(self.task_contract_fingerprint, "task contract")
        if not isinstance(self.scope, str) or not 1 <= len(self.scope) <= 120 or "\n" in self.scope or _SENSITIVE.search(self.scope):
            raise TaskCheckpointEvidenceError("scope is invalid")
        object.__setattr__(self, "allowed_files", _paths(self.allowed_files, "allowedFiles"))
        _git_sha(self.parent_head, "parent HEAD")
        _sha(self.diff_fingerprint, "diff fingerprint")
        _sha(self.review_fingerprint, "review fingerprint")
        if self.focused_verification_status != "pass":
            raise TaskCheckpointEvidenceError("focused verification must be pass")

    def subject(self) -> str:
        subject = f"checkpoint({self.task_id}): {self.scope}"
        if len(subject) > 72:
            raise TaskCheckpointEvidenceError("commit subject exceeds 72 characters")
        return subject

    def body(self) -> str:
        files = ", ".join(self.allowed_files)
        return "\n".join((
            f"Task-ID: {self.task_id}", f"Task-Contract: {self.task_contract_fingerprint}",
            f"Scope: {self.scope}", f"Allowed-Files: {files}", f"Parent-HEAD: {self.parent_head}",
            f"Diff-Fingerprint: {self.diff_fingerprint}", f"Review-Fingerprint: {self.review_fingerprint}",
            f"Focused-Verification: {self.focused_verification_status}", "Checkpoint-Status: task-saved",
            "Final-Acceptance: pending", "Rollback: git revert <commit-sha>",
        ))

    def trailers(self) -> dict[str, str]:
        return {
            "NBS-Checkpoint-Version": "1", "NBS-Task-ID": self.task_id,
            "NBS-Task-Contract": self.task_contract_fingerprint, "NBS-Parent-HEAD": self.parent_head,
            "NBS-Diff-Fingerprint": self.diff_fingerprint, "NBS-Review-Fingerprint": self.review_fingerprint,
            "NBS-Focused-Verification": self.focused_verification_status, "NBS-Final-Acceptance": "pending",
        }
