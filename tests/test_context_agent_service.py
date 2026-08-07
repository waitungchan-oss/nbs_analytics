import json

import pytest

from backend.agents.context_agent_service import (
    build_context_evidence_payload,
    build_context_report,
    context_bundle_from_payload,
    context_summary_from_evidence_payload,
)
from backend.agents.evidence_models import CommandEvidence, EvidenceBundle, EvidenceItem, canonical_fingerprint


class FakeRunner:
    last_payload = None

    def run(self, payload):
        self.last_payload = payload
        return {
            "schemaVersion": "context-summary-v1",
            "status": "ready",
            "taskUnderstanding": ["approved objective"],
            "systemBoundaries": ["baseline unchanged"],
            "relevantFiles": [],
            "dependencies": [],
            "recommendedTests": [],
            "risks": [],
            "unknowns": [],
            "contextFingerprint": payload["bundleFingerprint"],
        }


def make_bundle(content="short"):
    return EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "x", "objective": "approved objective", "scope": [], "forbidden": []},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="document", source="docs/x.md", content=content),),
    )


def test_context_report_accepts_valid_runner_output(tmp_path):
    runner = FakeRunner()
    report = build_context_report(
        make_bundle(), runner=runner, project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime",
        instructions="context-contract-v1",
    )
    assert report["status"] == "ready"
    assert report["contextFingerprint"]
    assert set(runner.last_payload["evidence"]) >= {
        "schemaVersion", "task", "repository", "guardrails", "documents",
        "symbols", "relatedTests", "recentChanges", "bundleFingerprint",
    }


def test_context_report_returns_overflow_before_runner(tmp_path):
    report = build_context_report(
        make_bundle("x" * 60000), runner=FakeRunner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime",
        instructions="context-contract-v1", input_token_limit=10,
    )
    assert report["status"] == "context_overflow"


def test_collect_only_returns_bundle_without_runner(tmp_path):
    report = build_context_report(
        make_bundle(), runner=None, project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime",
        instructions="context-contract-v1", collect_only=True,
    )
    assert report["schemaVersion"] == "context-evidence-v1"
    assert report["bundleFingerprint"]


def test_collect_only_context_evidence_converts_to_strict_review_summary():
    payload = build_context_evidence_payload(make_bundle())

    summary = context_summary_from_evidence_payload(payload)

    assert set(summary) == {
        "schemaVersion", "status", "taskUnderstanding", "systemBoundaries",
        "relevantFiles", "dependencies", "recommendedTests", "risks", "unknowns",
        "contextFingerprint",
    }
    assert summary["schemaVersion"] == "context-summary-v1"
    assert summary["status"] == "ready"
    assert summary["contextFingerprint"] == payload["bundleFingerprint"]
    assert all(set(item) == {"path", "reason", "symbols"} for item in summary["relevantFiles"])


def test_runtime_root_must_be_named_agent_runtime(tmp_path):
    with pytest.raises(PermissionError, match="runtime root"):
        build_context_report(make_bundle(), runner=FakeRunner(), project_root=tmp_path, runtime_root=tmp_path, instructions="contract")


def test_runtime_root_must_bind_to_project_and_reject_same_basename_escape(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    valid_runtime = project_root / ".nbs_agent_runtime"
    build_context_report(
        make_bundle(), runner=FakeRunner(), project_root=project_root,
        runtime_root=valid_runtime, instructions="contract",
    )
    external_runtime = tmp_path / ".nbs_agent_runtime"
    with pytest.raises(PermissionError, match="symlink|project"):
        build_context_report(
            make_bundle(), runner=FakeRunner(), project_root=project_root,
            runtime_root=external_runtime, instructions="contract",
        )


def test_runtime_symlink_cannot_bypass_project_binding(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    external_runtime = tmp_path / "external-runtime"
    external_runtime.mkdir()
    symlink_runtime = project_root / ".nbs_agent_runtime"
    symlink_runtime.symlink_to(external_runtime, target_is_directory=True)
    with pytest.raises(PermissionError, match="symlink|project"):
        build_context_report(
            make_bundle(), runner=FakeRunner(), project_root=project_root,
            runtime_root=symlink_runtime, instructions="contract",
        )
    with pytest.raises(PermissionError, match="project"):
        build_context_report(
            make_bundle(), runner=FakeRunner(), project_root=project_root,
            runtime_root=external_runtime, instructions="contract",
        )


def test_context_payload_roundtrip_preserves_semantic_evidence_without_duplicates():
    bundle = EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "x", "objective": "approved objective", "scope": [], "forbidden": []},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(
            EvidenceItem(kind="document", source="docs/x.md", content="short"),
            EvidenceItem(kind="symbol", source="rg-query-0", content=json.dumps({"queryId": "rg-query-0", "paths": ["backend/x.py"]})),
            EvidenceItem(kind="recent_change", source="git-log", content="commit one"),
        ),
        commands=(
            CommandEvidence(label="rg-query-0", argv=("rg",), exit_code=0, stdout="backend/x.py\n", stderr=""),
            CommandEvidence(label="git-log", argv=("git",), exit_code=0, stdout="commit one\n", stderr=""),
        ),
    )
    payload = build_context_evidence_payload(bundle)
    rebuilt = context_bundle_from_payload(payload)
    assert build_context_evidence_payload(rebuilt) == payload
    assert payload["symbols"] == [{"queryId": "rg-query-0", "paths": ["backend/x.py"]}]
    assert payload["recentChanges"] == [{"summary": "commit one"}]


def test_context_payload_without_memory_hints_is_byte_for_byte_compatible():
    payload = build_context_evidence_payload(make_bundle())
    assert build_context_evidence_payload(make_bundle(), memory_hints=None) == payload


def test_context_report_rejects_output_over_budget(tmp_path):
    class VerboseRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["taskUnderstanding"] = ["x" * 1000]
            return report

    with pytest.raises(ValueError, match="output token budget"):
        build_context_report(
            make_bundle(), runner=VerboseRunner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime",
            instructions="context-contract-v1", output_token_limit=10,
        )


def test_context_report_rejects_fingerprint_mismatch(tmp_path):
    class BadRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["contextFingerprint"] = "wrong"
            return report

    with pytest.raises(ValueError, match="fingerprint"):
        build_context_report(make_bundle(), runner=BadRunner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime", instructions="contract")


def test_context_report_rejects_unknown_schema(tmp_path):
    class BadRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["schemaVersion"] = "other"
            return report

    with pytest.raises(ValueError, match="schema|field"):
        build_context_report(make_bundle(), runner=BadRunner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime", instructions="contract")


@pytest.mark.parametrize("mutate", [
    lambda payload: payload.update({"documents": "wrong"}),
    lambda payload: payload.update({"symbols": ["wrong"]}),
    lambda payload: payload.update({"recentChanges": None}),
    lambda payload: payload.update({"documents": [{"kind": "document", "source": [], "content": "x", "metadata": {}}]}),
    lambda payload: payload.update({"symbols": [{"queryId": "q", "paths": [False]}]}),
    lambda payload: payload.update({"recentChanges": [{"summary": {}}]}),
])
def test_context_bundle_rejects_fingerprint_valid_malformed_payloads(mutate):
    payload = build_context_evidence_payload(make_bundle())
    mutate(payload)
    unsigned = {key: value for key, value in payload.items() if key != "bundleFingerprint"}
    payload["bundleFingerprint"] = canonical_fingerprint(unsigned)
    with pytest.raises(ValueError):
        context_bundle_from_payload(payload)


def test_runtime_overflow_is_public_context_report_shape(tmp_path, monkeypatch):
    class OverflowRuntime:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            return {"schemaVersion": "context-summary-v1", "status": "context_overflow", "requestFingerprint": "secret"}

    monkeypatch.setattr("backend.agents.context_agent_service.AgentRuntime", OverflowRuntime)
    report = build_context_report(
        make_bundle(), runner=FakeRunner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime", instructions="contract",
    )
    assert report["status"] == "context_overflow"
    assert report["contextFingerprint"]
    assert "requestFingerprint" not in report
    assert set(report) == {
        "schemaVersion", "status", "taskUnderstanding", "systemBoundaries", "relevantFiles",
        "dependencies", "recommendedTests", "risks", "unknowns", "contextFingerprint",
    }


@pytest.mark.parametrize("field,bad_value", [
    ("taskUnderstanding", [False]),
    ("systemBoundaries", [{"bad": "value"}]),
    ("dependencies", [None]),
    ("recommendedTests", [["nested"]]),
    ("risks", [1]),
    ("unknowns", [{"bad": "value"}]),
])
def test_context_report_rejects_non_string_list_items(tmp_path, field, bad_value):
    class BadRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report[field] = bad_value
            return report

    with pytest.raises(ValueError, match="schema|field"):
        build_context_report(make_bundle(), runner=BadRunner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime", instructions="contract")


def test_context_report_rejects_non_string_relevant_file_symbols(tmp_path):
    class BadRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["relevantFiles"] = [{"path": "x.py", "reason": "why", "symbols": [False]}]
            return report

    with pytest.raises(ValueError, match="schema"):
        build_context_report(make_bundle(), runner=BadRunner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime", instructions="contract")
