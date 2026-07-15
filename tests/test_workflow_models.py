from datetime import datetime, timezone

import pytest

from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    EVENT_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    TERMINAL_STATUSES,
    WORKFLOW_STATUSES,
    WorkflowApproval,
    WorkflowEvent,
    WorkflowManifest,
    WorkflowSchemaError,
    WorkflowStatus,
    canonical_sha256,
    legal_transition,
)


def manifest_payload() -> dict:
    return {
        "schemaVersion": MANIFEST_SCHEMA,
        "runId": "run-123",
        "briefPath": "briefs/task.md",
        "briefSha256": "a" * 64,
        "gitBranch": "codex/agent-orchestrator-phase1",
        "gitHead": "b" * 40,
        "dirtyFiles": [{"path": "README.md", "sha256": "c" * 64}],
        "createdAt": "2026-07-15T10:00:00+00:00",
        "contextFingerprint": "d" * 64,
    }


def approval_payload() -> dict:
    return {
        "schemaVersion": APPROVAL_SCHEMA,
        "runId": "run-123",
        "contractPath": ".nbs_agent_runtime/contracts/task-1.json",
        "contractFingerprint": "a" * 64,
        "approvedBaseSha": "b" * 40,
        "approvedAt": "2026-07-15T10:05:00+00:00",
        "authorizationStatus": "approved",
    }


def status_payload() -> dict:
    return {
        "schemaVersion": STATUS_SCHEMA,
        "runId": "run-123",
        "stage": "authorization",
        "status": "awaiting_authorization",
        "startedAt": "2026-07-15T10:00:00+00:00",
        "updatedAt": "2026-07-15T10:05:00+00:00",
        "completedAt": None,
        "message": "Context ready; explicit authorization required",
        "errorCode": None,
        "artifactBytes": 0,
    }


def event_payload() -> dict:
    return {
        "schemaVersion": EVENT_SCHEMA,
        "runId": "run-123",
        "eventId": "event-1",
        "eventType": "status_transition",
        "fromStatus": "created",
        "toStatus": "context_running",
        "occurredAt": "2026-07-15T10:00:01+00:00",
        "message": "Context started",
        "metadata": {"source": "test"},
    }


def test_run_must_stop_at_authorization():
    assert legal_transition("created", "context_running")
    assert legal_transition("context_running", "awaiting_authorization")
    assert not legal_transition("context_running", "implementation_running")


def test_terminal_run_cannot_restart():
    for status in TERMINAL_STATUSES:
        assert not legal_transition(status, "implementation_running")


def test_status_sets_and_terminal_states_are_exact():
    assert WORKFLOW_STATUSES == {
        "created", "context_running", "awaiting_authorization",
        "implementation_running", "targeted_verification_running",
        "review_running", "changes_required", "full_verification_running",
        "hermes_running", "completed", "blocked", "failed",
    }
    assert TERMINAL_STATUSES == {"completed", "changes_required", "blocked", "failed"}


def test_manifest_fingerprint_is_canonical():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_models_round_trip_with_exact_schema_versions():
    manifest = WorkflowManifest.from_dict(manifest_payload())
    approval = WorkflowApproval.from_dict(approval_payload())
    status = WorkflowStatus.from_dict(status_payload())
    event = WorkflowEvent.from_dict(event_payload())

    assert manifest.to_dict()["schemaVersion"] == MANIFEST_SCHEMA
    assert WorkflowManifest.from_dict(manifest.to_dict()) == manifest
    assert WorkflowApproval.from_dict(approval.to_dict()) == approval
    assert WorkflowStatus.from_dict(status.to_dict()) == status
    assert WorkflowEvent.from_dict(event.to_dict()) == event


def test_timestamps_must_be_iso_8601():
    status = WorkflowStatus.from_dict(status_payload())
    assert datetime.fromisoformat(status.started_at).tzinfo == timezone.utc
    assert datetime.fromisoformat(status.updated_at).tzinfo == timezone.utc

    payload = status_payload()
    payload["updatedAt"] = "not-a-timestamp"
    with pytest.raises(WorkflowSchemaError, match="updatedAt"):
        WorkflowStatus.from_dict(payload)


def test_public_constructors_reject_invalid_states():
    with pytest.raises(WorkflowSchemaError, match="briefSha256"):
        WorkflowManifest(
            schema_version=MANIFEST_SCHEMA,
            run_id="run-123",
            brief_path="briefs/task.md",
            brief_sha256="invalid",
            git_branch="codex/agent-orchestrator-phase1",
            git_head="b" * 40,
            dirty_files=(),
            created_at="2026-07-15T10:00:00+00:00",
            context_fingerprint="d" * 64,
        )

    with pytest.raises(WorkflowSchemaError, match="authorizationStatus"):
        WorkflowApproval(
            schema_version=APPROVAL_SCHEMA,
            run_id="run-123",
            contract_path=".nbs_agent_runtime/contracts/task-1.json",
            contract_fingerprint="a" * 64,
            approved_base_sha="b" * 40,
            approved_at="2026-07-15T10:05:00+00:00",
            authorization_status="pending",
        )

    with pytest.raises(WorkflowSchemaError, match="status"):
        WorkflowStatus(
            schema_version=STATUS_SCHEMA,
            run_id="run-123",
            stage="authorization",
            status="not-a-status",
            started_at="2026-07-15T10:00:00+00:00",
            updated_at="2026-07-15T10:05:00+00:00",
            completed_at=None,
            message="invalid status",
            error_code=None,
            artifact_bytes=0,
        )

    with pytest.raises(WorkflowSchemaError, match="transition"):
        WorkflowEvent(
            schema_version=EVENT_SCHEMA,
            run_id="run-123",
            event_id="event-1",
            event_type="status_transition",
            from_status="completed",
            to_status="implementation_running",
            occurred_at="2026-07-15T10:00:01+00:00",
            message="invalid transition",
            metadata={},
        )


def test_z_timestamps_are_normalized_for_python_39_and_round_trip():
    payload = status_payload()
    payload["startedAt"] = "2026-07-15T10:00:00Z"
    payload["updatedAt"] = "2026-07-15T10:05:00Z"
    status = WorkflowStatus.from_dict(payload)

    assert status.started_at == "2026-07-15T10:00:00+00:00"
    assert status.updated_at == "2026-07-15T10:05:00+00:00"
    assert WorkflowStatus.from_dict(status.to_dict()) == status


def test_models_reject_unknown_or_missing_fields():
    payload = manifest_payload()
    payload["unexpected"] = True
    with pytest.raises(WorkflowSchemaError, match="keys"):
        WorkflowManifest.from_dict(payload)

    payload = approval_payload()
    del payload["contractFingerprint"]
    with pytest.raises(WorkflowSchemaError, match="contractFingerprint"):
        WorkflowApproval.from_dict(payload)


def test_manifest_rejects_tuple_dirty_files_from_json_payload():
    payload = manifest_payload()
    payload["dirtyFiles"] = tuple(payload["dirtyFiles"])

    with pytest.raises(WorkflowSchemaError, match="dirtyFiles must be a list"):
        WorkflowManifest.from_dict(payload)


def test_status_constructor_rejects_empty_message_and_malformed_status():
    with pytest.raises(WorkflowSchemaError, match="message"):
        WorkflowStatus(
            schema_version=STATUS_SCHEMA,
            run_id="run-123",
            stage="authorization",
            status="awaiting_authorization",
            started_at="2026-07-15T10:00:00+00:00",
            updated_at="2026-07-15T10:05:00+00:00",
            completed_at=None,
            message="",
            error_code=None,
            artifact_bytes=0,
        )

    with pytest.raises(WorkflowSchemaError, match="status"):
        WorkflowStatus(
            schema_version=STATUS_SCHEMA,
            run_id="run-123",
            stage="authorization",
            status=[],
            started_at="2026-07-15T10:00:00+00:00",
            updated_at="2026-07-15T10:05:00+00:00",
            completed_at=None,
            message="malformed status",
            error_code=None,
            artifact_bytes=0,
        )


def test_event_rejects_malformed_status_values_as_schema_errors():
    with pytest.raises(WorkflowSchemaError, match="fromStatus"):
        WorkflowEvent(
            schema_version=EVENT_SCHEMA,
            run_id="run-123",
            event_id="event-1",
            event_type="status_transition",
            from_status=[],
            to_status="context_running",
            occurred_at="2026-07-15T10:00:01+00:00",
            message="malformed transition",
            metadata={},
        )

    payload = event_payload()
    payload["fromStatus"] = "created"
    payload["toStatus"] = []
    with pytest.raises(WorkflowSchemaError, match="toStatus"):
        WorkflowEvent.from_dict(payload)


def test_status_rejects_unknown_status_and_illegal_event_transition():
    payload = status_payload()
    payload["status"] = "not-a-status"
    with pytest.raises(WorkflowSchemaError, match="status"):
        WorkflowStatus.from_dict(payload)

    payload = event_payload()
    payload["fromStatus"] = "completed"
    payload["toStatus"] = "implementation_running"
    with pytest.raises(WorkflowSchemaError, match="transition"):
        WorkflowEvent.from_dict(payload)
