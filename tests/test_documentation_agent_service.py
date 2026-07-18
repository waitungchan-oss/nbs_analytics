from __future__ import annotations

import json
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from backend.agents.documentation_agent_service import (
    DocumentationAgentService,
    DocumentationRunnerResult,
    _SubprocessDocumentationRunner,
)
from backend.agents.documentation_evidence import DocumentationEvidence
from backend.agents.documentation_models import (
    DOCUMENTATION_EVIDENCE_SCHEMA,
    DOCUMENTATION_PROPOSAL_SCHEMA,
)
from backend.agents.workflow_models import canonical_sha256


TIMESTAMP = "2026-07-18T12:00:00+08:00"


@pytest.fixture
def evidence() -> DocumentationEvidence:
    unsigned = {
        "schemaVersion": DOCUMENTATION_EVIDENCE_SCHEMA,
        "taskId": "run-task-3",
        "generatedAt": TIMESTAMP,
        "sources": [{"path": "docs/briefs/task-3.md", "sha256": "a" * 64}],
        "artifactHashes": {"implementation.json": "b" * 64},
        "changedPaths": ["backend/agents/documentation_agent_service.py"],
        "commandResults": [],
        "requirementCoverage": [],
        "summaries": {},
        "gateResults": {"review": "pass", "full-verification": "pass", "hermes": "pass"},
        "guardrails": {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        },
    }
    return DocumentationEvidence(
        unsigned["schemaVersion"], unsigned["taskId"], unsigned["generatedAt"],
        tuple(unsigned["sources"]), unsigned["artifactHashes"],
        tuple(unsigned["changedPaths"]), tuple(unsigned["commandResults"]),
        tuple(unsigned["requirementCoverage"]), unsigned["summaries"],
        unsigned["gateResults"], unsigned["guardrails"], canonical_sha256(unsigned),
    )


class FakeRunner:
    def __init__(self, result: DocumentationRunnerResult | None = None):
        self.result = result
        self.stdin_text = ""
        self.calls = 0

    def run(self, argv, *, input_text, timeout_seconds, max_output_bytes):
        self.calls += 1
        self.stdin_text = input_text
        if self.result is not None:
            return self.result
        payload = json.loads(input_text)
        content = "# Task 3\n"
        proposal = {
            "schemaVersion": DOCUMENTATION_PROPOSAL_SCHEMA,
            "taskId": payload["taskId"],
            "generatedAt": payload["generatedAt"],
            "evidence": payload,
            "evidenceFingerprint": payload["documentationFingerprint"],
            "status": "ready",
            "proposals": [{
                "targetKind": "brief_backfill",
                "targetIdentity": "docs/briefs/task-3.md",
                "operation": "update_managed_block",
                "content": content,
                "contentSha256": sha256(content.encode()).hexdigest(),
            }],
            "proposalFingerprint": "0" * 64,
        }
        proposal["proposalFingerprint"] = canonical_sha256({
            key: value for key, value in proposal.items() if key != "proposalFingerprint"
        })
        return DocumentationRunnerResult(0, json.dumps(proposal), "", 1)


@pytest.fixture
def service(tmp_path: Path):
    return lambda runner=None: DocumentationAgentService(tmp_path, runner=runner)


def test_missing_runner_is_blocked_without_main_llm_fallback(evidence, service):
    proposal = service().draft(evidence, agent_command=None)
    assert proposal.status == "blocked"
    assert proposal.warnings == ("blocked_missing_runner",)


def test_runner_receives_only_evidence_json(evidence, service):
    fake_runner = FakeRunner()
    service(fake_runner).draft(evidence, agent_command="codex")
    payload = json.loads(fake_runner.stdin_text)
    assert payload["schemaVersion"] == DOCUMENTATION_EVIDENCE_SCHEMA
    assert "prompt" not in payload
    assert "absoluteVaultPath" not in payload


def test_external_source_paths_are_redacted_from_runner_and_cache(evidence, service):
    evidence = replace(evidence, sources=(
        {"path": "/private/external/secret-brief.md", "sha256": "a" * 64},
        {"path": "docs/briefs/task-3.md", "sha256": "b" * 64},
    ))
    fake_runner = FakeRunner()
    instance = service(fake_runner)

    proposal = instance.draft(evidence, agent_command="codex")

    runner_payload = json.loads(fake_runner.stdin_text)
    assert runner_payload["sources"] == [
        {"path": "secret-brief.md", "sha256": "a" * 64},
        {"path": "docs/briefs/task-3.md", "sha256": "b" * 64},
    ]
    cache_path = instance.cache_root / f"{evidence.documentation_fingerprint}.json"
    cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    encoded = json.dumps({"runner": runner_payload, "cache": cache_payload})
    assert "/private/external/secret-brief.md" not in encoded
    assert proposal.evidence.sources[0]["path"] == "secret-brief.md"


def test_nonzero_runner_exit_is_blocked(evidence, service):
    fake_runner = FakeRunner(DocumentationRunnerResult(7, "", "secret stderr", 2))
    proposal = service(fake_runner).draft(evidence, agent_command="codex")
    assert proposal.status == "blocked"
    assert proposal.warnings == ("runner_nonzero_exit",)
    assert "secret stderr" not in json.dumps(proposal.to_dict())


def test_timeout_is_blocked(evidence, service):
    fake_runner = FakeRunner(DocumentationRunnerResult(-9, "", "", 120001))
    proposal = service(fake_runner).draft(evidence, agent_command="codex")
    assert proposal.status == "blocked"
    assert proposal.warnings == ("runner_timeout",)


def test_invalid_schema_is_rejected(evidence, service):
    fake_runner = FakeRunner(DocumentationRunnerResult(0, json.dumps({"schemaVersion": "wrong"}), "", 1))
    proposal = service(fake_runner).draft(evidence, agent_command="codex")
    assert proposal.status == "invalid_agent_output"
    assert proposal.warnings == ("invalid_agent_output",)


def test_fingerprint_mismatch_is_rejected(evidence, service):
    fake_runner = FakeRunner()
    original = fake_runner.run

    def mismatch(*args, **kwargs):
        result = original(*args, **kwargs)
        payload = json.loads(result.stdout)
        payload["evidenceFingerprint"] = "b" * 64
        return replace(result, stdout=json.dumps(payload))

    fake_runner.run = mismatch
    proposal = service(fake_runner).draft(evidence, agent_command="codex")
    assert proposal.status == "invalid_agent_output"
    assert proposal.warnings == ("fingerprint_mismatch",)


def test_unapproved_target_is_rejected(evidence, service):
    fake_runner = FakeRunner()
    original = fake_runner.run

    def unauthorized(*args, **kwargs):
        result = original(*args, **kwargs)
        payload = json.loads(result.stdout)
        payload["proposals"][0]["targetKind"] = "adr"
        payload["proposalFingerprint"] = canonical_sha256({
            key: value for key, value in payload.items() if key != "proposalFingerprint"
        })
        return replace(result, stdout=json.dumps(payload))

    fake_runner.run = unauthorized
    proposal = service(fake_runner).draft(evidence, agent_command="codex")
    assert proposal.status == "invalid_agent_output"
    assert proposal.warnings == ("unapproved_target",)


def test_required_targets_ignore_caller_classification(evidence, service):
    instance = service(FakeRunner())
    assert instance._required_targets({
        **evidence.to_dict(), "classification": {"requiredTargets": ["adr"]},
    }) == ("brief_backfill", "system_map")


def test_subprocess_runner_caps_stdout_and_stderr_at_boundary(tmp_path):
    runner = _SubprocessDocumentationRunner(tmp_path)
    result = runner.run(
        (sys.executable, "-c", "import sys; sys.stdout.write('x'*70000); sys.stderr.write('e'*10000)"),
        input_text="{}", timeout_seconds=5, max_output_bytes=64 * 1024,
    )
    assert len(result.stdout.encode()) == 64 * 1024 + 1
    assert len(result.stderr_tail.encode()) <= 4 * 1024


def test_output_over_budget_is_context_overflow(evidence, service):
    fake_runner = FakeRunner(DocumentationRunnerResult(0, "x" * 70000, "", 1))
    proposal = service(fake_runner).draft(evidence, agent_command="codex")
    assert proposal.status == "context_overflow"
    assert proposal.warnings == ("output_over_budget",)


def test_cache_hit_does_not_call_runner_twice(evidence, service):
    fake_runner = FakeRunner()
    instance = service(fake_runner)
    first = instance.draft(evidence, agent_command="codex")
    second = instance.draft(evidence, agent_command="codex")
    assert first.to_dict() == second.to_dict()
    assert fake_runner.calls == 1
    telemetry = (Path(instance.project_root) / ".nbs_agent_runtime/telemetry/documentation.jsonl")
    records = [json.loads(line) for line in telemetry.read_text().splitlines()]
    assert records[-1]["cacheHit"] is True
    assert set(records[-1]) == {
        "schemaVersion", "runId", "documentationFingerprint", "inputCharacters",
        "estimatedInputTokens", "outputTokens", "proposalCount", "cacheHit",
        "durationMs", "result",
    }
