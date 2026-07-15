from datetime import datetime, timezone
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
