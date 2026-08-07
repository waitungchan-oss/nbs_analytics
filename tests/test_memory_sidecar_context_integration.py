import copy

from backend.agents.context_agent_service import (
    build_context_evidence_payload,
    build_context_report,
    context_summary_from_evidence_payload,
)
from backend.agents.evidence_models import EvidenceBundle, EvidenceItem
from backend.agents.memory_sidecar_hint_models import MemoryHint, MemoryHints


def _bundle() -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "memory-task", "objective": "preserve evidence authority", "scope": [], "forbidden": []},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="document", source="docs/context.md", content="canonical context"),),
    )


def _ready_hints(*, freshness: str = "fresh") -> MemoryHints:
    hint = MemoryHint(
        memory_id="a" * 64,
        summary="Use the focused context tests before dispatch.",
        source_refs=("docs/context.md",),
        freshness=freshness,
        confidence="high",
        source_fingerprints=("b" * 64,),
    )
    return MemoryHints(query_fingerprint="c" * 64, status="ready", hints=(hint,))


def test_memory_hints_are_separate_non_authoritative_and_do_not_change_canonical_fingerprint():
    canonical = build_context_evidence_payload(_bundle())
    with_hints = build_context_evidence_payload(_bundle(), memory_hints=_ready_hints())

    assert with_hints["bundleFingerprint"] == canonical["bundleFingerprint"]
    assert with_hints["memoryHints"]["authority"] == "non_authoritative_memory"
    assert with_hints["memoryHints"]["status"] == "ready"
    assert with_hints["memoryHints"]["hints"]
    assert "memoryHints" not in canonical

    summary = context_summary_from_evidence_payload(with_hints)
    assert summary["memoryHints"]["authority"] == "non_authoritative_memory"
    assert summary["contextFingerprint"] == canonical["bundleFingerprint"]


def test_stale_hint_is_ignored_without_injecting_summary():
    payload = build_context_evidence_payload(_bundle(), memory_hints=_ready_hints(freshness="stale"))
    assert payload["memoryHints"]["status"] == "ignored"
    assert payload["memoryHints"]["hints"] == []
    assert payload["memoryHints"]["reason"] == "stale"


def test_non_ready_status_is_ignored_without_injecting_summary():
    hints = MemoryHints.empty(query_fingerprint="c" * 64, status="timeout")
    payload = build_context_evidence_payload(_bundle(), memory_hints=hints)
    assert payload["memoryHints"]["status"] == "ignored"
    assert payload["memoryHints"]["hints"] == []
    assert payload["memoryHints"]["reason"] == "timeout"


def test_malformed_or_conflicting_hint_payload_fails_closed():
    hints = _ready_hints()
    malformed = copy.deepcopy(hints.to_dict())
    malformed["hintsFingerprint"] = "d" * 64
    payload = build_context_evidence_payload(_bundle(), memory_hints=malformed)
    assert payload["memoryHints"]["status"] == "ignored"
    assert payload["memoryHints"]["hints"] == []
    assert payload["memoryHints"]["reason"] == "invalid"


def test_ignored_memory_hints_reject_unexpected_fields():
    payload = build_context_evidence_payload(_bundle(), memory_hints=MemoryHints.empty(query_fingerprint="c" * 64, status="timeout"))
    payload["memoryHints"]["rawMemory"] = "must not pass"
    try:
        context_summary_from_evidence_payload(payload)
    except ValueError as exc:
        assert "memory hints" in str(exc)
    else:
        raise AssertionError("unexpected ignored memory hint fields were accepted")


def test_memory_hints_are_bounded_by_existing_input_token_limit(tmp_path):
    class Runner:
        def run(self, payload):
            return {
                "schemaVersion": "context-summary-v1", "status": "ready",
                "taskUnderstanding": [], "systemBoundaries": [], "relevantFiles": [],
                "dependencies": [], "recommendedTests": [], "risks": [], "unknowns": [],
                "contextFingerprint": payload["bundleFingerprint"],
            }

    report = build_context_report(
        _bundle(), runner=Runner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime",
        instructions="context-contract-v1", memory_hints=_ready_hints(), input_token_limit=1,
    )
    assert report["status"] == "context_overflow"


def test_memory_hints_cannot_push_final_report_over_output_token_limit(tmp_path):
    class Runner:
        def run(self, payload):
            return {
                "schemaVersion": "context-summary-v1", "status": "ready",
                "taskUnderstanding": [], "systemBoundaries": [], "relevantFiles": [],
                "dependencies": [], "recommendedTests": [], "risks": [], "unknowns": [],
                "contextFingerprint": payload["bundleFingerprint"],
            }

    report = build_context_report(
        _bundle(), runner=Runner(), project_root=tmp_path, runtime_root=tmp_path / ".nbs_agent_runtime",
        instructions="context-contract-v1", memory_hints=_ready_hints(),
        input_token_limit=12000, output_token_limit=100,
    )
    assert report["status"] == "context_overflow"
    assert "memoryHints" not in report
