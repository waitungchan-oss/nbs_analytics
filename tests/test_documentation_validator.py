from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from backend.agents.documentation_models import DocumentationProposal, DocumentationProposalV2
from backend.agents.documentation_policy import load_documentation_target
from backend.agents.documentation_validator import (
    DocumentationPreviewV2,
    DocumentationProposalValidator,
    DocumentationValidationError,
)
from backend.agents.workflow_models import canonical_sha256


@pytest.fixture
def valid_proposal_payload():
    content = "# Task 1\n"
    evidence = {
        "schemaVersion": "documentation-evidence-v1",
        "taskId": "task-1",
        "generatedAt": "2026-07-18T12:00:00+08:00",
        "sources": [{"path": "docs/briefs/task-1.md", "sha256": "a" * 64}],
        "guardrails": {"revenueScope": "不含掛賬核銷與TT退款轉團款", "mayBaseline": "HKD 12,057,968"},
        "evidenceFingerprint": "a" * 64,
    }
    from backend.agents.workflow_models import canonical_sha256
    payload = {
        "schemaVersion": "documentation-proposal-v1", "taskId": "task-1",
        "generatedAt": "2026-07-18T12:00:00+08:00", "evidence": evidence,
        "evidenceFingerprint": "a" * 64, "status": "ready", "proposals": [{
            "targetKind": "brief_backfill", "targetIdentity": "docs/briefs/task-1.md",
            "operation": "update_managed_block", "content": content,
            "contentSha256": sha256(content.encode()).hexdigest(),
        }], "proposalFingerprint": "0" * 64,
    }
    payload["proposalFingerprint"] = canonical_sha256({k: v for k, v in payload.items() if k != "proposalFingerprint"})
    return payload


def _proposal(valid_proposal_payload, *, kind, identity, operation, content):
    payload = deepcopy(valid_proposal_payload)
    payload["proposals"] = [{
        "targetKind": kind,
        "targetIdentity": identity,
        "operation": operation,
        "content": content,
        "contentSha256": sha256(content.encode()).hexdigest(),
    }]
    from backend.agents.workflow_models import canonical_sha256
    payload["proposalFingerprint"] = canonical_sha256({k: v for k, v in payload.items() if k != "proposalFingerprint"})
    return DocumentationProposal.from_dict(payload)


def _v2_proposal(tmp_path: Path, *, target_id="handoff.current-conclusion", section=None, content=None):
    target = load_documentation_target(target_id)
    if section is None:
        section = f"{target.section_heading}\nOld handoff text.\n"
    if content is None:
        content = f"{target.section_heading}\n\nUpdated handoff text.\n"
    section_hash = sha256(section.encode("utf-8")).hexdigest()
    source_fingerprint = "b" * 64
    evidence_unsigned = {
        "schemaVersion": "documentation-evidence-v2",
        "taskId": "task-3",
        "generatedAt": "2026-09-28T09:00:00+08:00",
        "runId": "run-task-3",
        "commitSha": "c" * 40,
        "sourceFingerprint": source_fingerprint,
        "selectedTargetId": target_id,
        "sources": [{"path": target.repo_path, "sha256": "d" * 64}],
        "gateResults": [
            {
                "gate": gate,
                "status": "pass",
                "sourceFingerprint": source_fingerprint,
                "evidenceFingerprint": "e" * 64,
            }
            for gate in ("review", "full-verification", "hermes")
        ],
        "guardrails": {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        },
        "expectedSectionSha256": section_hash,
    }
    evidence = {
        **evidence_unsigned,
        "evidenceFingerprint": canonical_sha256(evidence_unsigned),
    }
    proposal_unsigned = {
        "schemaVersion": "documentation-proposal-v2",
        "taskId": "task-3",
        "generatedAt": "2026-09-28T09:01:00+08:00",
        "evidence": evidence,
        "evidenceFingerprint": evidence["evidenceFingerprint"],
        "status": "ready",
        "targetId": target.target_id,
        "targetKind": target.target_kind,
        "repoPath": target.repo_path,
        "sectionHeading": target.section_heading,
        "operation": target.operation,
        "expectedSectionSha256": section_hash,
        "content": content,
        "contentSha256": sha256(content.encode("utf-8")).hexdigest(),
    }
    return DocumentationProposalV2.from_dict({
        **proposal_unsigned,
        "proposalFingerprint": canonical_sha256(proposal_unsigned),
    })


def _write_target(tmp_path: Path, proposal: DocumentationProposalV2, text: str) -> Path:
    target_path = tmp_path / proposal.repo_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(text.encode("utf-8"))
    return target_path


def test_brief_preview_replaces_managed_block_without_writing(tmp_path, valid_proposal_payload):
    target = tmp_path / "docs/briefs/task.md"
    target.parent.mkdir(parents=True)
    original = "# Brief\n\n<!-- documentation-agent:implementation-evidence:start -->\nold\n<!-- documentation-agent:implementation-evidence:end -->\n"
    target.write_text(original, encoding="utf-8")
    content = "<!-- documentation-agent:implementation-evidence:start -->\nnew\n<!-- documentation-agent:implementation-evidence:end -->"
    proposal = _proposal(valid_proposal_payload, kind="brief_backfill", identity="docs/briefs/task.md", operation="update_managed_block", content=content)

    preview = DocumentationProposalValidator(tmp_path).build_preview(proposal)
    assert target.read_text(encoding="utf-8") == original
    assert preview.items[0].before_sha256 == sha256(original.encode()).hexdigest()
    assert "new" in preview.items[0].unified_diff


def test_system_map_rejects_stale_hash_and_duplicate_heading(tmp_path, valid_proposal_payload):
    target = tmp_path / "NBS_ANALYTICS_SYSTEM_MAP.md"
    target.write_text("# Root\n\n## Agents\none\n\n## Agents\ntwo\n", encoding="utf-8")
    content = "## Agents\nreplacement"
    proposal = _proposal(valid_proposal_payload, kind="system_map", identity="NBS_ANALYTICS_SYSTEM_MAP.md#Agents|sha256=" + "0" * 64, operation="replace_section", content=content)
    with pytest.raises(DocumentationValidationError, match="stale_target|duplicate"):
        DocumentationProposalValidator(tmp_path).build_preview(proposal)


def test_validator_rejects_protected_mutation_secret_and_raw_rows(tmp_path, valid_proposal_payload):
    target = tmp_path / "docs/briefs/task.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Brief\n", encoding="utf-8")
    for content in ("<!-- documentation-agent:implementation-evidence:start -->\nHKD 1\n<!-- documentation-agent:implementation-evidence:end -->", "<!-- documentation-agent:implementation-evidence:start -->\npassword=secret\n<!-- documentation-agent:implementation-evidence:end -->", "<!-- documentation-agent:implementation-evidence:start -->\ntransaction_id,amount\n1,20\n<!-- documentation-agent:implementation-evidence:end -->"):
        proposal = _proposal(valid_proposal_payload, kind="brief_backfill", identity="docs/briefs/task.md", operation="update_managed_block", content=content)
        with pytest.raises(DocumentationValidationError):
            DocumentationProposalValidator(tmp_path).build_preview(proposal)


def test_adr_preview_is_create_only(tmp_path, valid_proposal_payload):
    target = tmp_path / "Summay/ADR-003-new.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing", encoding="utf-8")
    proposal = _proposal(valid_proposal_payload, kind="adr", identity="Summay/ADR-003-new.md", operation="create_file", content="# ADR\n")
    with pytest.raises(DocumentationValidationError, match="create-only|exists"):
        DocumentationProposalValidator(tmp_path).build_preview(proposal)


def test_v2_validator_replaces_only_allowlisted_heading_section(tmp_path):
    target = load_documentation_target("handoff.current-conclusion")
    before_section = f"{target.section_heading}\nOld handoff text.\n### Nested detail\nKeep nested scope.\n"
    # Bind the proposal hash to the complete current section, including nested headings.
    proposal = _v2_proposal(tmp_path, section=before_section)
    original = (
        "# Handoff\n\nStable introduction.\n"
        + before_section
        + "## 2. Next section\nPreserve this section.\n"
    )
    target = _write_target(tmp_path, proposal, original)

    preview = DocumentationProposalValidator(tmp_path).build_target_preview(proposal)

    assert preview.status == "preview_ready"
    assert preview.target_id == "handoff.current-conclusion"
    assert preview.repo_path == "NBS_ANALYTICS_HANDOFF.md"
    assert preview.section_heading == "## 1. 本輪交接結論"
    assert preview.risk_tier == "high"
    assert preview.required_approval_id == proposal.target_id
    assert preview.evidence_fingerprint == proposal.evidence_fingerprint
    assert preview.before_section_sha256 == sha256(before_section.encode()).hexdigest()
    assert preview.after_section_sha256 == sha256(proposal.content.encode()).hexdigest()
    assert "-Stable introduction." not in preview.unified_diff
    assert "+Updated handoff text." in preview.unified_diff
    assert target.read_text(encoding="utf-8") == original
    assert DocumentationPreviewV2.from_dict(preview.to_dict()) == preview
    tampered = preview.to_dict()
    tampered["repoPath"] = "Summay/forged.md"
    with pytest.raises(ValueError, match="identity differs"):
        DocumentationPreviewV2.from_dict(tampered)


def test_v2_validator_rejects_missing_or_duplicate_heading(tmp_path):
    proposal = _v2_proposal(tmp_path)
    target = _write_target(tmp_path, proposal, "# Handoff\n\nNo selected section.\n")
    validator = DocumentationProposalValidator(tmp_path)

    with pytest.raises(DocumentationValidationError, match="missing|duplicated"):
        validator.build_target_preview(proposal)

    duplicated = (
        f"{proposal.section_heading}\none\n"
        f"{proposal.section_heading}\ntwo\n"
    )
    target.write_text(duplicated, encoding="utf-8")
    with pytest.raises(DocumentationValidationError, match="missing|duplicated"):
        validator.build_target_preview(proposal)


def test_v2_validator_rejects_stale_section_hash_and_unknown_target(tmp_path):
    expected_section = "## 1. 本輪交接結論\nAccepted version.\n"
    proposal = _v2_proposal(tmp_path, section=expected_section)
    _write_target(tmp_path, proposal, "## 1. 本輪交接結論\nChanged after evidence.\n")

    with pytest.raises(DocumentationValidationError, match="stale_target"):
        DocumentationProposalValidator(tmp_path).build_target_preview(proposal)

    unknown = replace(proposal, target_id="handoff.arbitrary-path")
    with pytest.raises(DocumentationValidationError, match="target|catalog|fingerprint"):
        DocumentationProposalValidator(tmp_path).build_target_preview(unknown)


def test_v2_validator_blocks_protected_governance_mutation(tmp_path):
    protected_section = (
        "## 1. 本輪交接結論\n"
        "正式口徑：不含掛賬核銷與TT退款轉團款。\n"
        "基線：HKD 12,057,968。\n"
    )
    proposal = _v2_proposal(
        tmp_path,
        section=protected_section,
        content="## 1. 本輪交接結論\n\n改寫後遺漏受保護口徑。\n",
    )
    _write_target(tmp_path, proposal, protected_section)

    with pytest.raises(DocumentationValidationError, match="protected|baseline"):
        DocumentationProposalValidator(tmp_path).build_target_preview(proposal)


def test_v2_preview_never_changes_target_bytes(tmp_path):
    target = load_documentation_target("handoff.current-conclusion")
    original_section = f"{target.section_heading}\r\nOriginal CRLF text.\r\n"
    proposal = _v2_proposal(tmp_path, section=original_section)
    original_bytes = (
        f"# Handoff\r\n\r\n{original_section}"
        "## 2. Next section\r\nKeep CRLF too.\r\n"
    ).encode("utf-8")
    target = tmp_path / proposal.repo_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(original_bytes)

    DocumentationProposalValidator(tmp_path).build_target_preview(proposal)

    assert target.read_bytes() == original_bytes
