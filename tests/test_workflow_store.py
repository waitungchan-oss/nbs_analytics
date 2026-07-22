from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    EVENT_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowEvent,
    WorkflowManifest,
    WorkflowStatus,
)
from backend.agents.governance_graph_models import GRAPH_SCHEMA, GovernanceGraphSnapshot
from backend.agents.workflow_store import WorkflowLockedError, WorkflowStore


def _manifest(run_id: str = "run-123") -> WorkflowManifest:
    return WorkflowManifest(
        schema_version=MANIFEST_SCHEMA,
        run_id=run_id,
        brief_path="briefs/task.md",
        brief_sha256="a" * 64,
        git_branch="codex/agent-orchestrator-phase1",
        git_head="b" * 40,
        dirty_files=(),
        created_at="2026-07-15T10:00:00+00:00",
        context_fingerprint="c" * 64,
    )


def _status(run_id: str = "run-123", value: str = "created") -> WorkflowStatus:
    return WorkflowStatus(
        schema_version=STATUS_SCHEMA,
        run_id=run_id,
        stage="authorization",
        status=value,
        started_at="2026-07-15T10:00:00+00:00",
        updated_at="2026-07-15T10:00:00+00:00",
        completed_at=None,
        message="Run created",
        error_code=None,
        artifact_bytes=0,
    )


def _approval(run_id: str = "run-123") -> WorkflowApproval:
    return WorkflowApproval(
        schema_version=APPROVAL_SCHEMA,
        run_id=run_id,
        contract_path=".nbs_agent_runtime/contracts/task-2.json",
        contract_fingerprint="d" * 64,
        approved_base_sha="e" * 40,
        approved_at="2026-07-15T10:05:00+00:00",
        authorization_status="approved",
    )


def _event(run_id: str = "run-123", event_id: str = "event-1") -> WorkflowEvent:
    return WorkflowEvent(
        schema_version=EVENT_SCHEMA,
        run_id=run_id,
        event_id=event_id,
        event_type="warning",
        from_status=None,
        to_status=None,
        occurred_at="2026-07-15T10:01:00+00:00",
        message="A warning",
        metadata={"source": "test"},
    )


def _graph_payload() -> dict:
    payload = {
        "schemaVersion": GRAPH_SCHEMA,
        "runId": "run-123",
        "generatedAt": "2026-07-22T10:00:00+00:00",
        "graphFingerprint": "0" * 64,
        "risk": None,
        "authorizationMode": "per_task",
        "overallStatus": "awaiting_authorization",
        "nodes": [
            {
                "nodeId": "risk",
                "nodeType": "risk",
                "status": "not_started",
                "attempt": 0,
                "maxAttempts": 1,
                "evidenceRefs": [],
                "fingerprint": "0" * 64,
                "reasonCode": None,
            }
        ],
        "allowedNextNodes": ["risk"],
        "blockers": [],
        "freshness": {},
        "diagnostics": [],
    }
    canonical = dict(payload)
    canonical.pop("graphFingerprint")
    payload["graphFingerprint"] = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return payload


@pytest.fixture
def manifest() -> WorkflowManifest:
    return _manifest()


@pytest.fixture
def store(tmp_path: Path) -> WorkflowStore:
    return WorkflowStore(tmp_path)


def test_store_round_trips_manifest_status_approval_and_artifact(store, manifest):
    run_dir = store.create_run(manifest, _status())

    assert run_dir == store.runs_root / manifest.run_id
    assert store.load_manifest(manifest.run_id) == manifest
    assert store.load_status(manifest.run_id) == _status()

    store.write_approval(manifest.run_id, _approval())
    artifact = store.write_artifact(manifest.run_id, "context.json", {"ready": True})

    assert artifact == run_dir / "context.json"
    assert store.load_status(manifest.run_id).artifact_bytes == artifact.stat().st_size
    assert store.artifact_bytes(manifest.run_id) == artifact.stat().st_size
    assert (run_dir / "approval.json").is_file()


def test_projection_write_does_not_mutate_canonical_status(store, manifest):
    store.create_run(manifest, _status())
    before = store.load_status(manifest.run_id).to_dict()
    payload = _graph_payload()
    before_files = {
        path.relative_to(store.runs_root / manifest.run_id): path.read_bytes()
        for path in (store.runs_root / manifest.run_id).iterdir()
        if path.is_file()
    }

    path = store.write_projection(manifest.run_id, "governance-graph.json", payload)

    assert path.name == "governance-graph.json"
    assert store.load_status(manifest.run_id).to_dict() == before
    assert store.read_projection(manifest.run_id, "governance-graph.json") == payload
    assert (store.runs_root / manifest.run_id / ".lock").is_file()
    after_files = {
        path.relative_to(store.runs_root / manifest.run_id): path.read_bytes()
        for path in (store.runs_root / manifest.run_id).iterdir()
        if path.is_file() and path.name != "governance-graph.json"
    }
    assert after_files == before_files


def test_projection_storage_rejects_non_projection_names_and_escape(store, manifest):
    store.create_run(manifest, _status())

    with pytest.raises(ValueError, match="projection name"):
        store.write_projection(manifest.run_id, "context.json", {})
    with pytest.raises(PermissionError):
        store.read_projection(manifest.run_id, "../outside.json")
    with pytest.raises(PermissionError):
        store.write_projection(manifest.run_id, "/absolute/governance-graph.json", {})
    with pytest.raises(PermissionError):
        store.read_projection(manifest.run_id, "/absolute/governance-graph.json")


def test_projection_storage_rejects_cross_run_snapshot_on_write_and_read(store, manifest):
    store.create_run(manifest, _status())
    payload = _graph_payload()
    payload["runId"] = "run-999"
    canonical = dict(payload)
    canonical.pop("graphFingerprint")
    payload["graphFingerprint"] = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()

    with pytest.raises(ValueError, match="run ID"):
        store.write_projection(manifest.run_id, "governance-graph.json", payload)

    payload = _graph_payload()
    payload["runId"] = "run-999"
    canonical = dict(payload)
    canonical.pop("graphFingerprint")
    payload["graphFingerprint"] = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    store._atomic_json(store._run_file(manifest.run_id, "governance-graph.json"), payload)

    with pytest.raises(ValueError, match="run ID"):
        store.read_projection(manifest.run_id, "governance-graph.json")


def test_store_creates_lock_file_when_creating_run(store, manifest):
    run_dir = store.create_run(manifest, _status())

    assert (run_dir / ".lock").is_file()


def test_projection_storage_rejects_symlink_and_directory_targets(store, manifest):
    store.create_run(manifest, _status())
    run_dir = store.runs_root / manifest.run_id
    (run_dir / "governance-graph.json").symlink_to(run_dir / "outside.json")

    with pytest.raises(PermissionError):
        store.write_projection(manifest.run_id, "governance-graph.json", _graph_payload())

    second_run = _manifest("run-456")
    store.create_run(second_run, _status("run-456"))
    second_dir = store.runs_root / second_run.run_id
    (second_dir / "governance-graph.json").mkdir()

    with pytest.raises(PermissionError):
        store.read_projection(second_run.run_id, "governance-graph.json")


def test_write_approval_rejects_duplicate_approval(store, manifest):
    store.create_run(manifest, _status())
    store.write_approval(manifest.run_id, _approval())

    with pytest.raises(FileExistsError, match="approval"):
        store.write_approval(manifest.run_id, _approval())


def test_write_approval_rejects_existing_symlink(store, manifest):
    store.create_run(manifest, _status())
    approval_path = store.runs_root / manifest.run_id / "approval.json"
    approval_path.symlink_to(store.runs_root / manifest.run_id / "missing-approval.json")

    with pytest.raises(PermissionError, match="approval"):
        store.write_approval(manifest.run_id, _approval())


def test_write_artifact_rejects_approval_json(store, manifest):
    store.create_run(manifest, _status())
    store.write_approval(manifest.run_id, _approval())

    with pytest.raises(ValueError, match="approval.json"):
        store.write_artifact(manifest.run_id, "approval.json", {"authorizationStatus": "tampered"})


def test_store_appends_events_without_replacing_previous_events(store, manifest):
    store.create_run(manifest, _status())

    store.append_event(manifest.run_id, _event(event_id="event-1"))
    store.append_event(manifest.run_id, _event(event_id="event-2"))

    lines = (store.runs_root / manifest.run_id / "events.jsonl").read_text().splitlines()
    assert [line for line in lines if '"eventId":"event-1"' in line]
    assert [line for line in lines if '"eventId":"event-2"' in line]
    assert len(lines) == 2


def test_transition_rejects_illegal_state_and_writes_legal_state_event(store, manifest):
    store.create_run(manifest, _status())
    illegal = _event(event_id="illegal")

    with pytest.raises(ValueError, match="illegal workflow transition"):
        store.transition(manifest.run_id, _status(value="awaiting_authorization"), illegal)

    event = WorkflowEvent(
        schema_version=EVENT_SCHEMA,
        run_id=manifest.run_id,
        event_id="event-legal",
        event_type="status_transition",
        from_status="created",
        to_status="context_running",
        occurred_at="2026-07-15T10:02:00+00:00",
        message="Context started",
        metadata={},
    )
    next_status = WorkflowStatus(
        schema_version=STATUS_SCHEMA,
        run_id=manifest.run_id,
        stage="context",
        status="context_running",
        started_at="2026-07-15T10:00:00+00:00",
        updated_at="2026-07-15T10:02:00+00:00",
        completed_at=None,
        message="Context started",
        error_code=None,
        artifact_bytes=0,
    )
    store.transition(manifest.run_id, next_status, event)

    assert store.load_status(manifest.run_id) == next_status
    assert '"eventId":"event-legal"' in (store.runs_root / manifest.run_id / "events.jsonl").read_text()


def test_store_rejects_duplicate_run(store, manifest):
    store.create_run(manifest, _status())

    with pytest.raises(FileExistsError):
        store.create_run(manifest, _status())


def test_store_rejects_escape_and_unknown_artifacts(store, manifest):
    store.create_run(manifest, _status())

    with pytest.raises(PermissionError):
        store.write_artifact(manifest.run_id, "../outside", {})
    with pytest.raises(PermissionError):
        store.write_artifact(manifest.run_id, "context.json/child", {})
    with pytest.raises(ValueError, match="artifact name"):
        store.write_artifact(manifest.run_id, "secret.json", {})


def test_store_accepts_documentation_sidecar_artifacts(store, manifest):
    store.create_run(manifest, _status())
    for name in (
        "documentation-evidence.json", "documentation-proposal.json",
        "documentation-preview.json", "documentation-application.json",
        "documentation-telemetry.json",
    ):
        assert store.write_artifact(manifest.run_id, name, {"status": "ok"}).is_file()


def test_store_accepts_verified_backfill_manifest_artifact(store, manifest):
    from backend.agents.verified_backfill_models import VerifiedBackfillManifest

    store.create_run(manifest, _status())
    verified = VerifiedBackfillManifest.from_dict(
        {
            "sourceCommit": "a" * 40,
            "sourceBranch": "main",
            "dirtyFiles": [],
            "gateHashes": {
                "pytest": "b" * 64,
                "systemAcceptance": "c" * 64,
                "hermes": "d" * 64,
            },
            "reviewHash": "e" * 64,
        }
    )

    store.write_artifact(manifest.run_id, "verified-backfill.json", verified.to_dict())

    assert store._read_json(store._run_file(manifest.run_id, "verified-backfill.json"))["sourceBranch"] == "main"


def test_store_rejects_symlink_project_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    link = tmp_path / "project-link"
    link.symlink_to(project, target_is_directory=True)

    with pytest.raises(PermissionError):
        WorkflowStore(link)


def test_store_rejects_symlink_runtime_parent_and_run_target(tmp_path, manifest):
    project = tmp_path / "project"
    project.mkdir()
    runtime_target = tmp_path / "runtime-target"
    runtime_target.mkdir()
    (project / ".nbs_agent_runtime").symlink_to(runtime_target, target_is_directory=True)

    with pytest.raises(PermissionError):
        WorkflowStore(project)

    project2 = tmp_path / "project2"
    project2.mkdir()
    store = WorkflowStore(project2)
    target = tmp_path / "run-target"
    target.mkdir()
    (store.runs_root / manifest.run_id).symlink_to(target, target_is_directory=True)
    with pytest.raises(PermissionError):
        store.create_run(manifest, _status())


def test_run_lock_rejects_second_writer(store, manifest):
    store.create_run(manifest, _status())

    with store.run_lock(manifest.run_id):
        with pytest.raises(WorkflowLockedError):
            with store.run_lock(manifest.run_id, blocking=False):
                pass


def test_run_lock_can_be_released_and_reacquired(store, manifest):
    store.create_run(manifest, _status())

    with store.run_lock(manifest.run_id):
        pass
    with store.run_lock(manifest.run_id, blocking=False):
        pass


def test_artifact_bytes_counts_only_allowed_stage_artifacts(store, manifest):
    store.create_run(manifest, _status())
    store.write_artifact(manifest.run_id, "context.json", {"x": "1"})
    store.write_artifact(manifest.run_id, "review.json", {"x": "2"})

    run_dir = store.runs_root / manifest.run_id
    expected = (run_dir / "context.json").stat().st_size + (run_dir / "review.json").stat().st_size
    assert store.artifact_bytes(manifest.run_id) == expected


def test_artifact_bytes_rejects_dangling_symlink(store, manifest):
    store.create_run(manifest, _status())
    artifact = store.runs_root / manifest.run_id / "context.json"
    artifact.symlink_to(store.runs_root / manifest.run_id / "missing.json")

    with pytest.raises(PermissionError, match="artifact"):
        store.artifact_bytes(manifest.run_id)


def test_write_artifact_rejects_stage_payload_over_hard_cap(tmp_path, manifest):
    limited = WorkflowStore(
        tmp_path,
        stage_artifact_max_bytes=128,
        run_artifact_soft_cap_bytes=512,
    )
    limited.create_run(manifest, _status())

    with pytest.raises(ValueError, match="stage artifact exceeds"):
        limited.write_artifact(manifest.run_id, "context.json", {"content": "x" * 256})

    assert not (limited.runs_root / manifest.run_id / "context.json").exists()


def test_write_artifact_emits_warning_when_run_crosses_soft_cap(tmp_path, manifest):
    limited = WorkflowStore(
        tmp_path,
        stage_artifact_max_bytes=512,
        run_artifact_soft_cap_bytes=180,
    )
    limited.create_run(manifest, _status())

    limited.write_artifact(manifest.run_id, "context.json", {"content": "x" * 80})
    limited.write_artifact(manifest.run_id, "review.json", {"content": "y" * 80})

    status = limited.load_status(manifest.run_id)
    events = [
        __import__("json").loads(line)
        for line in (limited.runs_root / manifest.run_id / "events.jsonl").read_text().splitlines()
    ]
    assert status.artifact_bytes > 180
    assert events[-1]["eventType"] == "artifact_size_warning"
    assert events[-1]["metadata"] == {
        "artifactBytes": status.artifact_bytes,
        "runArtifactSoftCapBytes": 180,
    }


def test_create_run_cleans_up_when_status_write_fails(store, manifest, monkeypatch):
    original = store._atomic_json
    calls = 0

    def fail_on_status(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated status write failure")
        return original(path, payload)

    monkeypatch.setattr(store, "_atomic_json", fail_on_status)

    with pytest.raises(OSError, match="simulated status write failure"):
        store.create_run(manifest, _status())

    assert not (store.runs_root / manifest.run_id).exists()
