import json
from pathlib import Path

import pytest

from backend.agents.documentation_evidence import (
    DocumentationEvidenceCollector,
    DocumentationEvidenceError,
    _MAX_TEXT,
    _bounded_text,
    _collect_commands,
    _collect_paths,
)
from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
)
from backend.agents.workflow_store import WorkflowStore


def _manifest(run_id: str) -> WorkflowManifest:
    return WorkflowManifest(
        MANIFEST_SCHEMA, run_id, "docs/briefs/task.md", "a" * 64,
        "codex/task-2", "b" * 40, (), "2026-07-18T10:00:00+00:00", "c" * 64,
    )


def _status(run_id: str, value: str) -> WorkflowStatus:
    return WorkflowStatus(
        STATUS_SCHEMA, run_id, "completed", value,
        "2026-07-18T10:00:00+00:00", "2026-07-18T10:01:00+00:00",
        "2026-07-18T10:01:00+00:00" if value == "completed" else None,
        "done", None, 0,
    )


@pytest.fixture
def completed_run_fixture(tmp_path: Path):
    store = WorkflowStore(tmp_path)
    run_id = "run-task-2"
    store.create_run(_manifest(run_id), _status(run_id, "completed"))
    store.write_approval(run_id, WorkflowApproval(
        APPROVAL_SCHEMA, run_id, "contract.json", "d" * 64, "e" * 40,
        "2026-07-18T10:00:30+00:00", "approved",
    ))
    payload = {"status": "pass", "commands": [{"command": "pytest", "exitCode": 0}],
               "changedPaths": ["backend/agents/documentation_evidence.py"],
               "stdoutTail": "runner command transactionRows"}
    for name in ("implementation.json", "targeted-verification.json", "full-verification.json"):
        store.write_artifact(run_id, name, payload)
    store.write_artifact(run_id, "review.json", {**payload, "verdict": "pass"})
    store.write_artifact(run_id, "hermes.json", {"overallStatus": "pass", "summary": "ok"})
    return type("Fixture", (), {"project_root": tmp_path, "run_id": run_id, "store": store})


def test_collector_requires_all_verified_gates(completed_run_fixture):
    completed_run_fixture.store.write_artifact(
        completed_run_fixture.run_id, "hermes.json", {"overallStatus": "fail"}
    )
    collector = DocumentationEvidenceCollector(completed_run_fixture.project_root)
    with pytest.raises(DocumentationEvidenceError, match="Hermes"):
        collector.collect(completed_run_fixture.run_id)


def test_collector_rejects_incomplete_status(completed_run_fixture, monkeypatch):
    monkeypatch.setattr(
        completed_run_fixture.store,
        "load_status",
        lambda run_id: _status(run_id, "awaiting_authorization"),
    )

    with pytest.raises(DocumentationEvidenceError, match="completed"):
        DocumentationEvidenceCollector(
            completed_run_fixture.project_root, store=completed_run_fixture.store
        ).collect(
            completed_run_fixture.run_id
        )


def test_collector_rejects_review_failure(completed_run_fixture):
    completed_run_fixture.store.write_artifact(
        completed_run_fixture.run_id, "review.json", {"status": "fail"}
    )

    with pytest.raises(DocumentationEvidenceError, match="review"):
        DocumentationEvidenceCollector(completed_run_fixture.project_root).collect(
            completed_run_fixture.run_id
        )


def test_collector_rejects_full_verification_failure(completed_run_fixture):
    completed_run_fixture.store.write_artifact(
        completed_run_fixture.run_id, "full-verification.json", {"status": "fail"}
    )

    with pytest.raises(DocumentationEvidenceError, match="full-verification"):
        DocumentationEvidenceCollector(completed_run_fixture.project_root).collect(
            completed_run_fixture.run_id
        )


def test_collector_never_exposes_raw_outputs(completed_run_fixture):
    evidence = DocumentationEvidenceCollector(
        completed_run_fixture.project_root
    ).collect(completed_run_fixture.run_id).to_dict()
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert "stdoutTail" not in encoded
    assert "runner command" not in encoded
    assert "transactionRows" not in encoded


def test_collector_fingerprint_is_stable_and_excludes_self(completed_run_fixture):
    collector = DocumentationEvidenceCollector(completed_run_fixture.project_root)
    first = collector.collect(completed_run_fixture.run_id).to_dict()
    second = collector.collect(completed_run_fixture.run_id).to_dict()
    assert first == second
    assert first["documentationFingerprint"]


def test_collector_exposes_manifest_brief_as_safe_source(completed_run_fixture):
    evidence = DocumentationEvidenceCollector(
        completed_run_fixture.project_root, store=completed_run_fixture.store,
    ).collect(completed_run_fixture.run_id).to_dict()

    assert {item["path"]: item["sha256"] for item in evidence["sources"]}["docs/briefs/task.md"] == "a" * 64


def test_bounded_text_truncates_long_strings():
    value = "x" * (_MAX_TEXT + 17)

    assert _bounded_text(value) == value[:_MAX_TEXT]


def test_collect_paths_rejects_non_string_dict_path():
    with pytest.raises(DocumentationEvidenceError, match="path"):
        _collect_paths({"implementation.json": {"changedPaths": [{"path": 42}]}})


def test_collector_bounds_artifact_strings(completed_run_fixture):
    long_value = "x" * (_MAX_TEXT + 17)
    completed_run_fixture.store.write_artifact(
        completed_run_fixture.run_id,
        "implementation.json",
        {"status": long_value, "summary": long_value, "changedPaths": [long_value]},
    )

    evidence = DocumentationEvidenceCollector(completed_run_fixture.project_root).collect(
        completed_run_fixture.run_id
    ).to_dict()

    assert len(evidence["taskId"]) <= _MAX_TEXT
    assert len(evidence["generatedAt"]) <= _MAX_TEXT
    assert all(len(value) <= _MAX_TEXT for value in evidence["changedPaths"])
    assert all(len(value) <= _MAX_TEXT for value in evidence["summaries"].values())
    assert all(len(value) <= _MAX_TEXT for value in evidence["gateResults"].values())


def test_collect_commands_redacts_command_and_argv():
    results = _collect_commands({
        "verification.json": {
            "commands": [{
                "command": "python -m pytest tests/test_secret.py",
                "argv": ["python", "-m", "pytest", "tests/test_secret.py"],
                "exitCode": 0,
                "summary": "passed safely",
            }],
        },
    })

    assert results[0]["commandId"]
    assert results[0]["exitCode"] == 0
    assert results[0]["summary"] == "passed safely"
    assert "command" not in results[0]
    assert "argv" not in results[0]
    encoded = json.dumps(results, ensure_ascii=False)
    assert "python -m pytest tests/test_secret.py" not in encoded
    assert "test_secret.py" not in encoded
