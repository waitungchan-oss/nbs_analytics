from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256

import pytest

from backend.agents.documentation_models import DocumentationProposal
from backend.agents.documentation_validator import (
    DocumentationProposalValidator,
    DocumentationValidationError,
)


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
