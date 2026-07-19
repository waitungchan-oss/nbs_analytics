from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

import pytest

from backend.agents.documentation_models import (
    DOCUMENTATION_APPLICATION_SCHEMA,
    DOCUMENTATION_EVIDENCE_SCHEMA,
    DOCUMENTATION_POLICY_SCHEMA,
    DOCUMENTATION_PROPOSAL_SCHEMA,
    DocumentationApplication,
    DocumentationEvidence,
    DocumentationProposal,
    DocumentationSchemaError,
    DocumentationTargetPolicy,
)
from backend.agents.workflow_models import canonical_sha256


EVIDENCE_HASH = "a" * 64
TIMESTAMP = "2026-07-18T12:00:00+08:00"


@pytest.fixture
def valid_evidence_payload() -> dict:
    return {
        "schemaVersion": DOCUMENTATION_EVIDENCE_SCHEMA,
        "taskId": "task-1",
        "generatedAt": TIMESTAMP,
        "sources": [{"path": "docs/briefs/task-1.md", "sha256": EVIDENCE_HASH}],
        "guardrails": {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        },
        "evidenceFingerprint": EVIDENCE_HASH,
    }


@pytest.fixture
def valid_proposal_payload(valid_evidence_payload: dict) -> dict:
    payload = {
        "schemaVersion": DOCUMENTATION_PROPOSAL_SCHEMA,
        "taskId": "task-1",
        "generatedAt": TIMESTAMP,
        "evidence": valid_evidence_payload,
        "evidenceFingerprint": EVIDENCE_HASH,
        "status": "ready",
        "proposals": [
            {
                "targetKind": "brief_backfill",
                "targetIdentity": "docs/briefs/task-1.md",
                "operation": "update_managed_block",
                "content": "# Task 1\n",
                "contentSha256": sha256("# Task 1\n".encode("utf-8")).hexdigest(),
            }
        ],
        "proposalFingerprint": "0" * 64,
    }
    fingerprint_payload = deepcopy(payload)
    fingerprint_payload.pop("proposalFingerprint")
    payload["proposalFingerprint"] = canonical_sha256(fingerprint_payload)
    return payload


@pytest.fixture
def valid_application_payload(valid_proposal_payload: dict) -> dict:
    return {
        "schemaVersion": DOCUMENTATION_APPLICATION_SCHEMA,
        "taskId": "task-1",
        "generatedAt": TIMESTAMP,
        "proposalFingerprint": valid_proposal_payload["proposalFingerprint"],
        "status": "preview_ready",
        "applications": [
            {
                "targetKind": "brief_backfill",
                "targetIdentity": "docs/briefs/task-1.md",
                "operation": "update_managed_block",
                "result": "preview",
                "appliedSha256": None,
            }
        ],
    }


def test_documentation_evidence_round_trip(valid_evidence_payload: dict) -> None:
    model = DocumentationEvidence.from_dict(valid_evidence_payload)
    assert model.schema_version == DOCUMENTATION_EVIDENCE_SCHEMA
    assert model.to_dict() == valid_evidence_payload


def test_documentation_proposal_round_trip(valid_proposal_payload: dict) -> None:
    model = DocumentationProposal.from_dict(valid_proposal_payload)
    assert model.to_dict() == valid_proposal_payload


def test_documentation_application_round_trip(valid_application_payload: dict) -> None:
    model = DocumentationApplication.from_dict(valid_application_payload)
    assert model.to_dict() == valid_application_payload


def test_documentation_target_policy_round_trip() -> None:
    payload = {
        "schemaVersion": DOCUMENTATION_POLICY_SCHEMA,
        "targetKind": "system_map",
        "riskTier": "high",
        "operations": ["replace_section"],
        "repoRoots": [],
        "repoPaths": ["NBS_ANALYTICS_SYSTEM_MAP.md"],
        "obsidianSubdirectory": "10_System",
        "requiresExplicitTargetApproval": True,
    }
    assert DocumentationTargetPolicy.from_dict(payload).to_dict() == payload


@pytest.mark.parametrize(
    "key,value",
    [("revenueScope", "含掛賬核銷"), ("mayBaseline", "HKD 1")],
)
def test_evidence_rejects_non_governed_guardrails(
    valid_evidence_payload: dict, key: str, value: str,
) -> None:
    valid_evidence_payload["guardrails"][key] = value
    with pytest.raises(DocumentationSchemaError, match="guardrails"):
        DocumentationEvidence.from_dict(valid_evidence_payload)


def test_proposal_rejects_content_hash_mismatch(valid_proposal_payload: dict) -> None:
    valid_proposal_payload["proposals"][0]["contentSha256"] = "c" * 64
    with pytest.raises(DocumentationSchemaError, match="contentSha256"):
        DocumentationProposal.from_dict(valid_proposal_payload)


def test_proposal_rejects_fingerprint_mismatch(valid_proposal_payload: dict) -> None:
    valid_proposal_payload["proposalFingerprint"] = "c" * 64
    with pytest.raises(DocumentationSchemaError, match="proposal fingerprint"):
        DocumentationProposal.from_dict(valid_proposal_payload)


@pytest.mark.parametrize(
    "target_kind,expected",
    [
        (
            "brief_backfill",
            {
                "riskTier": "low",
                "operations": ["update_managed_block"],
                "repoRoots": ["docs/briefs"],
                "repoPaths": [],
                "requiresExplicitTargetApproval": False,
            },
        ),
        (
            "system_map",
            {
                "riskTier": "high",
                "operations": ["replace_section"],
                "repoRoots": [],
                "repoPaths": ["NBS_ANALYTICS_SYSTEM_MAP.md"],
                "requiresExplicitTargetApproval": True,
            },
        ),
        (
            "adr",
            {
                "riskTier": "high",
                "operations": ["create_file"],
                "repoRoots": ["Summay"],
                "repoPaths": [],
                "requiresExplicitTargetApproval": True,
            },
        ),
    ],
)
def test_target_policy_requires_exact_governed_mapping(target_kind: str, expected: dict) -> None:
    payload = {
        "schemaVersion": DOCUMENTATION_POLICY_SCHEMA,
        "targetKind": target_kind,
        **expected,
        "obsidianSubdirectory": {
            "brief_backfill": "70_Codex_Briefs",
            "system_map": "10_System",
            "adr": "20_Decisions",
        }[target_kind],
    }
    assert DocumentationTargetPolicy.from_dict(payload).to_dict() == payload


def test_target_policy_rejects_wrong_governed_mapping() -> None:
    payload = {
        "schemaVersion": DOCUMENTATION_POLICY_SCHEMA,
        "targetKind": "brief_backfill",
        "riskTier": "high",
        "operations": ["replace_section"],
        "repoRoots": [],
        "repoPaths": ["NBS_ANALYTICS_SYSTEM_MAP.md"],
        "obsidianSubdirectory": "10_System",
        "requiresExplicitTargetApproval": True,
    }
    with pytest.raises(DocumentationSchemaError, match="policy"):
        DocumentationTargetPolicy.from_dict(payload)


def test_application_rejects_duplicate_target_identities(valid_application_payload: dict) -> None:
    valid_application_payload["applications"].append(
        deepcopy(valid_application_payload["applications"][0])
    )
    with pytest.raises(DocumentationSchemaError, match="duplicate targetIdentity"):
        DocumentationApplication.from_dict(valid_application_payload)


@pytest.mark.parametrize(
    "factory,payload_key",
    [
        (DocumentationEvidence.from_dict, "evidence"),
        (DocumentationProposal.from_dict, "proposal"),
        (DocumentationApplication.from_dict, "application"),
    ],
)
def test_models_reject_unknown_fields(
    factory, payload_key: str, valid_evidence_payload: dict,
    valid_proposal_payload: dict, valid_application_payload: dict,
) -> None:
    payloads = {
        "evidence": valid_evidence_payload,
        "proposal": valid_proposal_payload,
        "application": valid_application_payload,
    }
    invalid = deepcopy(payloads[payload_key])
    invalid["unexpected"] = True
    with pytest.raises(DocumentationSchemaError, match="unknown fields"):
        factory(invalid)


def test_evidence_rejects_bad_hash(valid_evidence_payload: dict) -> None:
    valid_evidence_payload["evidenceFingerprint"] = "A" * 64
    with pytest.raises(DocumentationSchemaError, match="SHA-256"):
        DocumentationEvidence.from_dict(valid_evidence_payload)


def test_proposal_rejects_unknown_target_kind(valid_proposal_payload: dict) -> None:
    valid_proposal_payload["proposals"][0]["targetKind"] = "sqlite"
    with pytest.raises(DocumentationSchemaError, match="targetKind"):
        DocumentationProposal.from_dict(valid_proposal_payload)


def test_proposal_rejects_invalid_operation(valid_proposal_payload: dict) -> None:
    valid_proposal_payload["proposals"][0]["operation"] = "delete_file"
    with pytest.raises(DocumentationSchemaError, match="operation"):
        DocumentationProposal.from_dict(valid_proposal_payload)


def test_proposal_rejects_duplicate_target_identities(valid_proposal_payload: dict) -> None:
    valid_proposal_payload["proposals"].append(deepcopy(valid_proposal_payload["proposals"][0]))
    with pytest.raises(DocumentationSchemaError, match="duplicate targetIdentity"):
        DocumentationProposal.from_dict(valid_proposal_payload)


def test_proposal_rejects_fingerprint_different_from_evidence(valid_proposal_payload: dict) -> None:
    valid_proposal_payload["evidenceFingerprint"] = "c" * 64
    with pytest.raises(DocumentationSchemaError, match="evidence fingerprint"):
        DocumentationProposal.from_dict(valid_proposal_payload)
