import json
from pathlib import Path

import pytest

from backend.agents.documentation_evidence import (
    DocumentationEvidenceCollector,
    DocumentationEvidenceError,
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
        MANIFEST_SCHEMA, run_id, "briefs/task.md", "a" * 64,
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
    for name in ("implementation.json", "targeted-verification.json", "review.json", "full-verification.json"):
        store.write_artifact(run_id, name, payload)
    store.write_artifact(run_id, "hermes.json", {"overallStatus": "pass", "summary": "ok"})
    return type("Fixture", (), {"project_root": tmp_path, "run_id": run_id, "store": store})


def test_collector_requires_all_verified_gates(completed_run_fixture):
    completed_run_fixture.store.write_artifact(
        completed_run_fixture.run_id, "hermes.json", {"overallStatus": "fail"}
    )
    collector = DocumentationEvidenceCollector(completed_run_fixture.project_root)
    with pytest.raises(DocumentationEvidenceError, match="Hermes"):
        collector.collect(completed_run_fixture.run_id)


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
