from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

import pytest

from backend.agents.documentation_models import (
    DOCUMENTATION_APPLICATION_SCHEMA,
    DOCUMENTATION_DRAFT_SCHEMA,
    DOCUMENTATION_EVIDENCE_SCHEMA,
    DOCUMENTATION_POLICY_SCHEMA,
    DOCUMENTATION_PROPOSAL_SCHEMA,
    DocumentationApplication,
    DocumentationDraft,
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


def test_documentation_draft_round_trip() -> None:
    payload = {
        "schemaVersion": DOCUMENTATION_DRAFT_SCHEMA,
        "evidenceFingerprint": EVIDENCE_HASH,
        "status": "ready",
        "proposals": [{"targetKind": "brief_backfill", "content": "Summary."}],
    }
    model = DocumentationDraft.from_dict(payload)
    assert model.to_dict() == payload


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schemaVersion": DOCUMENTATION_DRAFT_SCHEMA,
            "evidenceFingerprint": EVIDENCE_HASH,
            "status": "ready",
            "proposals": [],
            "unexpected": True,
        },
        {
            "schemaVersion": DOCUMENTATION_DRAFT_SCHEMA,
            "evidenceFingerprint": "A" * 64,
            "status": "ready",
            "proposals": [],
        },
    ],
)
def test_documentation_draft_rejects_unknown_or_invalid_fields(payload: dict) -> None:
    with pytest.raises(DocumentationSchemaError):
        DocumentationDraft.from_dict(payload)


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


def test_documentation_v2_models_round_trip_and_reject_mixed_version(
    valid_evidence_payload: dict,
) -> None:
    from backend.agents.documentation_models import (
        DOCUMENTATION_APPLICATION_V2_SCHEMA,
        DOCUMENTATION_DRAFT_V2_SCHEMA,
        DOCUMENTATION_EVIDENCE_V2_SCHEMA,
        DOCUMENTATION_PROPOSAL_V2_SCHEMA,
        DocumentationApplicationV2,
        DocumentationDraftV2,
        DocumentationEvidenceV2,
        DocumentationProposalV2,
    )

    evidence = {
        "schemaVersion": DOCUMENTATION_EVIDENCE_V2_SCHEMA,
        "taskId": "task-1",
        "generatedAt": TIMESTAMP,
        "runId": "run-123",
        "commitSha": "b" * 40,
        "sourceFingerprint": "c" * 64,
        "selectedTargetId": "handoff.current-conclusion",
        "sources": [{"path": "NBS_ANALYTICS_HANDOFF.md", "sha256": "d" * 64}],
        "gateResults": [{
            "gate": "strict_review",
            "status": "pass",
            "sourceFingerprint": "c" * 64,
            "evidenceFingerprint": "e" * 64,
        }],
        "guardrails": {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        },
        "expectedSectionSha256": "f" * 64,
        "evidenceFingerprint": "0" * 64,
    }
    evidence["evidenceFingerprint"] = canonical_sha256(
        {key: value for key, value in evidence.items() if key != "evidenceFingerprint"}
    )
    evidence_model = DocumentationEvidenceV2.from_dict(evidence)
    assert evidence_model.to_dict() == evidence
    assert evidence_model.canonical_fingerprint == evidence["evidenceFingerprint"]

    draft = {
        "schemaVersion": DOCUMENTATION_DRAFT_V2_SCHEMA,
        "evidenceFingerprint": evidence["evidenceFingerprint"],
        "status": "ready",
        "proposals": [{"targetId": "handoff.current-conclusion", "content": "Verified conclusion.\n"}],
        "draftFingerprint": "0" * 64,
    }
    draft["draftFingerprint"] = canonical_sha256(
        {key: value for key, value in draft.items() if key != "draftFingerprint"}
    )
    assert DocumentationDraftV2.from_dict(draft).to_dict() == draft

    proposal = {
        "schemaVersion": DOCUMENTATION_PROPOSAL_V2_SCHEMA,
        "taskId": "task-1",
        "generatedAt": TIMESTAMP,
        "evidence": evidence,
        "evidenceFingerprint": evidence["evidenceFingerprint"],
        "status": "ready",
        "targetId": "handoff.current-conclusion",
        "targetKind": "handoff",
        "repoPath": "NBS_ANALYTICS_HANDOFF.md",
        "sectionHeading": "## 1. 本輪交接結論",
        "operation": "replace_section",
        "expectedSectionSha256": "f" * 64,
        "content": "Verified conclusion.\n",
        "contentSha256": sha256("Verified conclusion.\n".encode("utf-8")).hexdigest(),
        "proposalFingerprint": "0" * 64,
    }
    proposal["proposalFingerprint"] = canonical_sha256(
        {key: value for key, value in proposal.items() if key != "proposalFingerprint"}
    )
    proposal_model = DocumentationProposalV2.from_dict(proposal)
    assert proposal_model.to_dict() == proposal
    assert proposal_model.canonical_fingerprint == proposal["proposalFingerprint"]

    utc_z_proposal = deepcopy(proposal)
    utc_z_proposal["generatedAt"] = "2026-07-18T04:00:00Z"
    canonical_utc_proposal = deepcopy(utc_z_proposal)
    canonical_utc_proposal["generatedAt"] = "2026-07-18T04:00:00+00:00"
    utc_z_proposal["proposalFingerprint"] = canonical_sha256(
        {key: value for key, value in canonical_utc_proposal.items() if key != "proposalFingerprint"}
    )
    normalized_proposal = DocumentationProposalV2.from_dict(utc_z_proposal)
    assert normalized_proposal.to_dict()["generatedAt"] == "2026-07-18T04:00:00+00:00"
    assert normalized_proposal.canonical_fingerprint == utc_z_proposal["proposalFingerprint"]

    application = {
        "schemaVersion": DOCUMENTATION_APPLICATION_V2_SCHEMA,
        "taskId": "task-1",
        "generatedAt": TIMESTAMP,
        "runId": "run-123",
        "sourceFingerprint": "c" * 64,
        "proposalFingerprint": proposal["proposalFingerprint"],
        "status": "applied",
        "targetId": "handoff.current-conclusion",
        "repoPath": "NBS_ANALYTICS_HANDOFF.md",
        "approvalId": "handoff.current-conclusion",
        "beforeSectionSha256": "f" * 64,
        "afterSectionSha256": sha256("Verified conclusion.\n".encode("utf-8")).hexdigest(),
        "result": "applied",
        "applicationFingerprint": "0" * 64,
    }
    application["applicationFingerprint"] = canonical_sha256(
        {key: value for key, value in application.items() if key != "applicationFingerprint"}
    )
    assert DocumentationApplicationV2.from_dict(application).to_dict() == application

    with pytest.raises(DocumentationSchemaError, match="schemaVersion"):
        DocumentationEvidenceV2.from_dict(valid_evidence_payload)

    mixed_proposal = deepcopy(proposal)
    mixed_proposal["evidence"] = valid_evidence_payload
    with pytest.raises(DocumentationSchemaError, match="schemaVersion"):
        DocumentationProposalV2.from_dict(mixed_proposal)

    invalid_draft = deepcopy(draft)
    invalid_draft["proposals"].append(deepcopy(invalid_draft["proposals"][0]))
    with pytest.raises(DocumentationSchemaError, match="exactly one"):
        DocumentationDraftV2.from_dict(invalid_draft)
    empty_blocked_draft = deepcopy(draft)
    empty_blocked_draft["status"] = "blocked"
    empty_blocked_draft["proposals"] = []
    with pytest.raises(DocumentationSchemaError, match="exactly one"):
        DocumentationDraftV2.from_dict(empty_blocked_draft)

    invalid_draft_fingerprint = deepcopy(draft)
    invalid_draft_fingerprint["draftFingerprint"] = "9" * 64
    with pytest.raises(DocumentationSchemaError, match="draft fingerprint"):
        DocumentationDraftV2.from_dict(invalid_draft_fingerprint)

    wrong_task_proposal = deepcopy(proposal)
    wrong_task_proposal["taskId"] = "task-other"
    wrong_task_proposal["proposalFingerprint"] = canonical_sha256(
        {key: value for key, value in wrong_task_proposal.items() if key != "proposalFingerprint"}
    )
    with pytest.raises(DocumentationSchemaError, match="taskId"):
        DocumentationProposalV2.from_dict(wrong_task_proposal)

    stale_time_proposal = deepcopy(proposal)
    stale_time_proposal["generatedAt"] = "2026-07-18T11:59:59+08:00"
    stale_time_proposal["proposalFingerprint"] = canonical_sha256(
        {key: value for key, value in stale_time_proposal.items() if key != "proposalFingerprint"}
    )
    with pytest.raises(DocumentationSchemaError, match="predates"):
        DocumentationProposalV2.from_dict(stale_time_proposal)

    invalid_evidence = deepcopy(evidence)
    invalid_evidence["absolutePath"] = "/Users/private/vault"
    with pytest.raises(DocumentationSchemaError, match="unknown fields"):
        DocumentationEvidenceV2.from_dict(invalid_evidence)
