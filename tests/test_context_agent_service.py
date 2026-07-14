import pytest

from backend.agents.context_agent_service import build_context_report
from backend.agents.evidence_models import EvidenceBundle, EvidenceItem


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
        make_bundle(), runner=runner, runtime_root=tmp_path,
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
        make_bundle("x" * 60000), runner=FakeRunner(), runtime_root=tmp_path,
        instructions="context-contract-v1", input_token_limit=10,
    )
    assert report["status"] == "context_overflow"


def test_collect_only_returns_bundle_without_runner(tmp_path):
    report = build_context_report(
        make_bundle(), runner=None, runtime_root=tmp_path,
        instructions="context-contract-v1", collect_only=True,
    )
    assert report["schemaVersion"] == "context-evidence-v1"
    assert report["bundleFingerprint"]


def test_runtime_parent_is_normalized_to_agent_runtime(tmp_path):
    runner = FakeRunner()
    build_context_report(make_bundle(), runner=runner, runtime_root=tmp_path, instructions="contract")
    assert (tmp_path / ".nbs_agent_runtime").is_dir()


def test_context_report_rejects_output_over_budget(tmp_path):
    class VerboseRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["taskUnderstanding"] = ["x" * 1000]
            return report

    with pytest.raises(ValueError, match="output token budget"):
        build_context_report(
            make_bundle(), runner=VerboseRunner(), runtime_root=tmp_path,
            instructions="context-contract-v1", output_token_limit=10,
        )


def test_context_report_rejects_fingerprint_mismatch(tmp_path):
    class BadRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["contextFingerprint"] = "wrong"
            return report

    with pytest.raises(ValueError, match="fingerprint"):
        build_context_report(make_bundle(), runner=BadRunner(), runtime_root=tmp_path, instructions="contract")


def test_context_report_rejects_unknown_schema(tmp_path):
    class BadRunner(FakeRunner):
        def run(self, payload):
            report = super().run(payload)
            report["schemaVersion"] = "other"
            return report

    with pytest.raises(ValueError, match="schema"):
        build_context_report(make_bundle(), runner=BadRunner(), runtime_root=tmp_path, instructions="contract")
