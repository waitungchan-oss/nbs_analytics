from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from backend.agents.documentation_agent_service import DocumentationRunnerResult
from backend.agents.documentation_models import DOCUMENTATION_DRAFT_SCHEMA
from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
    canonical_sha256,
)
from backend.agents.workflow_store import WorkflowStore


def _manifest(run_id: str) -> WorkflowManifest:
    return WorkflowManifest(
        MANIFEST_SCHEMA, run_id, "docs/briefs/task-6.md", "a" * 64,
        "codex/task-6", "b" * 40, (), "2026-07-18T10:00:00+00:00", "c" * 64,
    )


def _status(run_id: str, value: str = "completed") -> WorkflowStatus:
    return WorkflowStatus(
        STATUS_SCHEMA, run_id, "hermes", value,
        "2026-07-18T10:00:00+00:00", "2026-07-18T10:01:00+00:00",
        "2026-07-18T10:01:00+00:00" if value == "completed" else None,
        "done", None, 0,
    )


class FakeRunner:
    command = "codex exec --json"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, argv, *, input_text, timeout_seconds, max_output_bytes):
        self.calls += 1
        evidence = json.loads(input_text)
        content = "implementation evidence\n"
        proposal = {
            "schemaVersion": DOCUMENTATION_DRAFT_SCHEMA,
            "evidenceFingerprint": evidence["evidenceFingerprint"],
            "status": "ready",
            "proposals": [{
                "targetKind": "brief_backfill",
                "content": content,
            }, {
                "targetKind": "system_map",
                "content": "workflow evidence\n",
            }],
        }
        return DocumentationRunnerResult(0, json.dumps(proposal), "", 1)


@pytest.fixture
def completed_run(tmp_path: Path):
    store = WorkflowStore(tmp_path)
    run_id = "run-task-6"
    store.create_run(_manifest(run_id), _status(run_id))
    store.write_approval(run_id, WorkflowApproval(
        APPROVAL_SCHEMA, run_id, "contract.json", "d" * 64, "e" * 40,
        "2026-07-18T10:00:30+00:00", "approved",
    ))
    payload = {
        "status": "pass",
        "changedPaths": ["backend/agents/documentation_workflow.py"],
    }
    for name in ("implementation.json", "targeted-verification.json", "review.json", "full-verification.json"):
        store.write_artifact(run_id, name, payload)
    store.write_artifact(run_id, "hermes.json", {"overallStatus": "pass"})
    brief = tmp_path / "docs/briefs/task-6.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Task 6\n", encoding="utf-8")
    system_map = tmp_path / "NBS_ANALYTICS_SYSTEM_MAP.md"
    system_map.write_text(
        "# System Map\n\n## 2A. Agent Evidence Pipeline\n\nExisting pipeline.\n",
        encoding="utf-8",
    )
    return type("CompletedRun", (), {
        "project_root": tmp_path, "run_id": run_id, "store": store,
        "brief_path": brief, "system_map_path": system_map,
    })


@pytest.fixture
def workflow(completed_run):
    from backend.agents.documentation_workflow import DocumentationWorkflow
    return DocumentationWorkflow(completed_run.project_root, runner=FakeRunner())


def test_document_workflow_preview_does_not_write(workflow, completed_run):
    before = completed_run.brief_path.read_bytes()
    before_map = completed_run.system_map_path.read_bytes()
    result = workflow.run(completed_run.run_id, agent_command=FakeRunner.command)
    assert result["status"] == "preview_ready"
    assert completed_run.brief_path.read_bytes() == before
    assert completed_run.system_map_path.read_bytes() == before_map
    assert {item["targetKind"] for item in result["items"]} == {"brief_backfill", "system_map"}
    assert (completed_run.store.runs_root / completed_run.run_id / "documentation-preview.json").is_file()


def test_document_workflow_never_persists_runner_command(workflow, completed_run):
    workflow.run(completed_run.run_id, agent_command=FakeRunner.command)
    artifacts = "".join(path.read_text(encoding="utf-8") for path in
                         (completed_run.store.runs_root / completed_run.run_id).glob("*.json"))
    assert FakeRunner.command not in artifacts


def test_document_workflow_applies_low_risk_brief(workflow, completed_run):
    before = completed_run.brief_path.read_bytes()
    result = workflow.run(completed_run.run_id, agent_command=FakeRunner.command, apply_brief=True)
    assert result["status"] == "awaiting_target_approval"
    assert completed_run.brief_path.read_bytes() == before


def test_document_workflow_requires_system_map_approval(workflow, completed_run):
    before = completed_run.system_map_path.read_bytes()
    result = workflow.run(completed_run.run_id, agent_command=FakeRunner.command, apply_brief=True)
    assert result["status"] == "awaiting_target_approval"
    assert completed_run.system_map_path.read_bytes() == before


def test_document_workflow_applies_system_map_only_with_explicit_approval(workflow, completed_run):
    result = workflow.run(
        completed_run.run_id,
        agent_command=FakeRunner.command,
        apply_brief=True,
        approved_targets=frozenset({"system_map"}),
    )
    assert result["status"] == "applied"
    assert "### Documentation Backfill: run-task-6" in completed_run.system_map_path.read_text(encoding="utf-8")


def test_document_workflow_preserves_core_terminal_status(workflow, completed_run):
    workflow.run(completed_run.run_id, agent_command=FakeRunner.command, apply_brief=True)
    assert completed_run.store.load_status(completed_run.run_id).status == "completed"


def test_document_workflow_missing_runner_is_blocked(workflow, completed_run):
    result = workflow.run(completed_run.run_id, agent_command=None)
    assert result["status"] == "blocked"


def test_document_workflow_no_doc_fast_path_skips_runner(workflow, completed_run):
    for name in ("implementation.json", "targeted-verification.json", "review.json", "full-verification.json"):
        completed_run.store.write_artifact(
            completed_run.run_id, name,
            {"status": "pass", "changedPaths": ["docs/notes.md"]},
        )
    result = workflow.run(completed_run.run_id, agent_command=None)
    assert result["status"] == "no_documentation_needed"
    assert workflow.service.runner.calls == 0


def test_document_workflow_reuses_same_fingerprint_cache(workflow, completed_run):
    first = workflow.run(completed_run.run_id, agent_command=FakeRunner.command)
    second = workflow.run(completed_run.run_id, agent_command=FakeRunner.command)
    assert first["status"] == second["status"] == "preview_ready"
    assert workflow.service.runner.calls == 1


def test_document_workflow_incomplete_run_is_blocked(tmp_path):
    store = WorkflowStore(tmp_path)
    run_id = "run-incomplete"
    store.create_run(_manifest(run_id), _status(run_id, "awaiting_authorization"))
    from backend.agents.documentation_workflow import DocumentationWorkflow
    result = DocumentationWorkflow(tmp_path).run(run_id, agent_command=None)
    assert result["status"] == "blocked"


def test_v2_workflow_separates_preview_from_target_approved_apply(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import backend.agents.documentation_workflow as module
    from backend.agents.documentation_workflow import DocumentationWorkflow

    calls = {"draft": 0, "apply": 0}
    artifacts = {}
    evidence_payload = {"schemaVersion": "documentation-evidence-v2", "evidenceFingerprint": "e" * 64}
    evidence = SimpleNamespace(
        run_id="run-v2", selected_target_id="handoff.current-conclusion",
        commit_sha="c" * 40, source_fingerprint="b" * 64,
        expected_section_sha256="d" * 64,
        evidence_fingerprint="e" * 64, to_dict=lambda: evidence_payload,
    )
    proposal = SimpleNamespace(
        status="ready", evidence=evidence, target_id="handoff.current-conclusion",
        to_dict=lambda: {"schemaVersion": "documentation-proposal-v2"},
    )
    preview = SimpleNamespace(
        proposal=proposal, run_id="run-v2", target_id="handoff.current-conclusion",
        to_dict=lambda: {"schemaVersion": "documentation-preview-v2"},
    )

    class FakeStore:
        def load_status(self, _run_id):
            return SimpleNamespace(status="completed")

        def write_artifact(self, *_args):
            pass

    class FakeController:
        def __init__(self, _project_root):
            pass

        def write_target_artifact(self, run_id, target_id, name, payload):
            artifacts[(run_id, target_id, name)] = payload

        def read_target_artifact(self, run_id, target_id, name):
            return artifacts[(run_id, target_id, name)]

        def apply_target(self, received_preview, *, approved_target_ids):
            calls["apply"] += 1
            assert received_preview is preview
            assert approved_target_ids == frozenset({"handoff.current-conclusion"})
            return SimpleNamespace(to_dict=lambda: {"status": "applied"})

    workflow = DocumentationWorkflow(tmp_path, runner=FakeRunner(), store=FakeStore())
    monkeypatch.setattr(workflow.collector, "collect_target", lambda *_args: evidence)

    def draft_target(_evidence, *, agent_command):
        assert agent_command == FakeRunner.command
        calls["draft"] += 1
        return proposal

    monkeypatch.setattr(workflow.service, "draft_target", draft_target)
    monkeypatch.setattr(workflow.validator, "build_target_preview", lambda _proposal: preview)
    monkeypatch.setattr(module, "DocumentationController", FakeController)
    monkeypatch.setattr(
        module.DocumentationPreviewV2, "from_dict",
        classmethod(lambda _cls, _payload: preview),
    )

    shown = workflow.run(
        "run-v2", agent_command=FakeRunner.command,
        target_id="handoff.current-conclusion",
    )
    stale_evidence_payload = {
        "schemaVersion": "documentation-evidence-v2", "evidenceFingerprint": "f" * 64,
    }
    stale_evidence = SimpleNamespace(
        run_id=evidence.run_id, selected_target_id=evidence.selected_target_id,
        commit_sha=evidence.commit_sha, source_fingerprint=evidence.source_fingerprint,
        expected_section_sha256=evidence.expected_section_sha256,
        evidence_fingerprint="f" * 64, to_dict=lambda: stale_evidence_payload,
    )
    monkeypatch.setattr(workflow.collector, "collect_target", lambda *_args: stale_evidence)
    blocked = workflow.run(
        "run-v2", agent_command=None, target_id="handoff.current-conclusion",
        approve_target_id="handoff.current-conclusion",
    )
    monkeypatch.setattr(workflow.collector, "collect_target", lambda *_args: evidence)
    applied = workflow.run(
        "run-v2", agent_command=None, target_id="handoff.current-conclusion",
        approve_target_id="handoff.current-conclusion",
    )
    reapplied = workflow.run(
        "run-v2", agent_command=None, target_id="handoff.current-conclusion",
        approve_target_id="handoff.current-conclusion",
    )

    assert shown["status"] == "preview_ready"
    assert blocked["status"] == "blocked"
    assert applied["status"] == reapplied["status"] == "applied"
    assert calls == {"draft": 1, "apply": 2}
    assert set(artifacts) == {
        ("run-v2", "handoff.current-conclusion", "documentation-evidence-v2.json"),
        ("run-v2", "handoff.current-conclusion", "documentation-proposal-v2.json"),
        ("run-v2", "handoff.current-conclusion", "documentation-preview-v2.json"),
        ("run-v2", "handoff.current-conclusion", "documentation-application-v2.json"),
    }
