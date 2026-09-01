from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.agents.task_checkpoint_models import (
    CHECKPOINT_EVIDENCE_SCHEMA,
    TaskCheckpointCommitMetadata,
    TaskCheckpointEvidence,
    TaskCheckpointEvidenceError,
)


PARENT = "a" * 40
SHA = "b" * 64
EXPECTED_FIELDS = {
    "schemaVersion", "taskId", "taskContractFingerprint", "parentHead",
    "allowedFiles", "changedFiles", "diffFingerprint", "reviewFingerprint",
    "focusedVerification", "gitDiffCheck", "generatedAt", "evidenceFingerprint",
}


def valid_evidence() -> TaskCheckpointEvidence:
    return TaskCheckpointEvidence.build(
        task_id="task-03",
        task_contract_fingerprint=SHA,
        parent_head=PARENT,
        allowed_files=("backend/agents/task_checkpoint_models.py",),
        changed_files=("backend/agents/task_checkpoint_models.py",),
        diff_fingerprint="c" * 64,
        review_fingerprint="d" * 64,
        focused_verification={
            "status": "pass",
            "commandIds": ["checkpoint-model-tests"],
            "evidenceFingerprint": "e" * 64,
        },
        git_diff_check="pass",
        generated_at="2026-09-01T00:00:00Z",
    )


def valid_metadata() -> TaskCheckpointCommitMetadata:
    return TaskCheckpointCommitMetadata(
        task_id="task-03",
        task_contract_fingerprint=SHA,
        scope="add checkpoint validation cli",
        allowed_files=("scripts/task_checkpoint.py",),
        parent_head=PARENT,
        diff_fingerprint="c" * 64,
        review_fingerprint="d" * 64,
        focused_verification_status="pass",
    )


def test_valid_checkpoint_evidence_round_trips_with_exact_schema() -> None:
    evidence = valid_evidence()
    payload = evidence.to_dict()

    assert payload["schemaVersion"] == CHECKPOINT_EVIDENCE_SCHEMA
    assert set(payload) == EXPECTED_FIELDS
    assert TaskCheckpointEvidence.from_dict(payload).to_dict() == payload
    assert evidence.evidence_fingerprint == evidence.recompute_fingerprint()


def test_checkpoint_evidence_is_immutable_and_rejects_stale_parent() -> None:
    evidence = valid_evidence()

    with pytest.raises((FrozenInstanceError, AttributeError)):
        evidence.task_id = "task-04"
    with pytest.raises(TaskCheckpointEvidenceError, match="parent"):
        TaskCheckpointEvidence.from_dict(evidence.to_dict(), expected_parent_head="f" * 40)


@pytest.mark.parametrize("field, value", [
    ("parentHead", "not-a-git-sha"),
    ("taskContractFingerprint", "A" * 64),
    ("allowedFiles", ["/Users/private/secret.py"]),
    ("changedFiles", ["../outside.py"]),
    ("focusedVerification", {"status": "pass", "command": "cat token"}),
])
def test_checkpoint_evidence_rejects_unsafe_fields(field: str, value: object) -> None:
    payload = valid_evidence().to_dict()
    payload[field] = value

    with pytest.raises(TaskCheckpointEvidenceError):
        TaskCheckpointEvidence.from_dict(payload)


def test_commit_metadata_has_pending_final_acceptance_and_trailers() -> None:
    metadata = valid_metadata()

    assert metadata.subject() == "checkpoint(task-03): add checkpoint validation cli"
    assert "Checkpoint-Status: task-saved" in metadata.body()
    assert "Final-Acceptance: pending" in metadata.body()
    assert metadata.trailers() == {
        "NBS-Checkpoint-Version": "1",
        "NBS-Task-ID": "task-03",
        "NBS-Task-Contract": SHA,
        "NBS-Parent-HEAD": PARENT,
        "NBS-Diff-Fingerprint": "c" * 64,
        "NBS-Review-Fingerprint": "d" * 64,
        "NBS-Focused-Verification": "pass",
        "NBS-Final-Acceptance": "pending",
    }


def test_commit_metadata_rejects_empty_or_sensitive_scope() -> None:
    with pytest.raises(TaskCheckpointEvidenceError):
        TaskCheckpointCommitMetadata(
            task_id="task-03", task_contract_fingerprint=SHA, scope="",
            allowed_files=("scripts/task_checkpoint.py",), parent_head=PARENT,
            diff_fingerprint="c" * 64, review_fingerprint="d" * 64,
            focused_verification_status="pass",
        )
    with pytest.raises(TaskCheckpointEvidenceError):
        TaskCheckpointCommitMetadata(
            task_id="task-03", task_contract_fingerprint=SHA, scope="API_KEY=secret",
            allowed_files=("scripts/task_checkpoint.py",), parent_head=PARENT,
            diff_fingerprint="c" * 64, review_fingerprint="d" * 64,
            focused_verification_status="pass",
        )
