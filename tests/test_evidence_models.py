from pathlib import Path

import pytest

from backend.agents.evidence_models import (
    AgentReportEnvelope,
    EvidenceBundle,
    EvidenceItem,
    canonical_fingerprint,
    estimate_tokens,
    load_json_config,
)


def test_canonical_fingerprint_is_order_independent():
    assert canonical_fingerprint({"b": 2, "a": 1}) == canonical_fingerprint({"a": 1, "b": 2})


def test_estimate_tokens_uses_conservative_character_ratio():
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("中旅分析") == 4


def test_evidence_bundle_serializes_with_schema_and_fingerprint():
    bundle = EvidenceBundle(
        schema_version="context-evidence-v1",
        task={"id": "P3-2", "objective": "Build context"},
        repository={"branch": "main", "head": "abc", "dirtyFiles": []},
        guardrails={"mayBaseline": "HKD 12,057,968"},
        evidence=(EvidenceItem(kind="document", source="docs/a.md", content="A"),),
    )
    payload = bundle.to_dict()
    assert payload["schemaVersion"] == "context-evidence-v1"
    assert payload["bundleFingerprint"] == bundle.fingerprint


def test_report_envelope_rejects_unknown_status():
    with pytest.raises(ValueError, match="Unsupported agent status"):
        AgentReportEnvelope(schema_version="context-summary-v1", status="invented", payload={})


def test_report_envelope_fields_cannot_be_overwritten_by_payload():
    envelope = AgentReportEnvelope(
        schema_version="context-summary-v1",
        status="pass",
        payload={"schemaVersion": "spoofed", "status": "blocked", "finding": "none"},
    )

    assert envelope.to_dict() == {
        "schemaVersion": "context-summary-v1",
        "status": "pass",
        "finding": "none",
    }


def test_configs_are_valid_json_and_runtime_is_ignored():
    root = Path(__file__).resolve().parents[1]
    budgets = load_json_config(root, "agent_config/token_budgets.json")
    assert budgets["context"]["inputTokens"] == 12000
    assert budgets["review"]["inputTokens"] > budgets["context"]["inputTokens"]
    assert budgets["review"]["outputTokens"] >= 2000
    assert budgets["review"]["maxCommandCharacters"] > budgets["excerpt"]["maxCommandCharacters"]
    assert ".nbs_agent_runtime/" in (root / ".gitignore").read_text(encoding="utf-8")


def test_review_token_contract_documents_configured_budget_authority():
    root = Path(__file__).resolve().parents[1]
    review = load_json_config(root, "agent_config/token_budgets.json")["review"]
    contract = (root / "docs/agents/REVIEW_AGENT_CONTRACT.md").read_text(encoding="utf-8")

    assert "`agent_config/token_budgets.json`" in contract
    assert f"{review['inputTokens']:,} estimated tokens" in contract
    assert f"{review['outputTokens']:,} tokens" in contract
    assert f"{review['maxCommandCharacters']:,} characters" in contract
