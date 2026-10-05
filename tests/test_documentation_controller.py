from __future__ import annotations

import json
import difflib
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from backend.agents.documentation_validator import DocumentationPreview, DocumentationPreviewItem


def _preview_item(
    target_kind: str,
    path_identity: str,
    before: str | None,
    after: str,
    *,
    vault_relative_path: str | None = None,
    required_approval: str | None = None,
) -> DocumentationPreviewItem:
    digest = lambda value: sha256(value.encode("utf-8")).hexdigest()
    unified_diff = "".join(difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=path_identity,
        tofile=path_identity,
    ))
    return DocumentationPreviewItem(
        target_kind=target_kind,
        path_identity=path_identity,
        vault_relative_path=vault_relative_path,
        before_sha256=digest(before) if before is not None else None,
        after_sha256=digest(after),
        unified_diff=unified_diff,
        risk_tier="low" if target_kind == "brief_backfill" else "high",
        required_approval=required_approval,
    )


@pytest.fixture
def controller(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    return DocumentationController(tmp_path)


@pytest.fixture
def brief_preview():
    before = "# Brief\n"
    after = before + "\n<!-- documentation-agent:implementation-evidence:start -->\nnew\n<!-- documentation-agent:implementation-evidence:end -->\n"
    return DocumentationPreview("preview_ready", (_preview_item("brief_backfill", "docs/briefs/task.md", before, after),))


@pytest.fixture
def system_map_preview():
    before = "# Root\n\n## Agents\none\n\n## Other\ntwo\n"
    after = "# Root\n\n## Agents\nreplacement\n\n## Other\ntwo\n"
    return DocumentationPreview("preview_ready", (_preview_item("system_map", "NBS_ANALYTICS_SYSTEM_MAP.md#Agents", before, after, required_approval="system_map"),))


def test_low_risk_brief_apply_is_atomic_and_backed_up(controller, brief_preview, tmp_path):
    target = tmp_path / "docs/briefs/task.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Brief\n", encoding="utf-8")

    result = controller.apply(brief_preview, apply_brief=True, approved_targets=frozenset())

    assert result.status == "applied"
    assert target.read_text(encoding="utf-8").endswith("<!-- documentation-agent:implementation-evidence:end -->\n")
    backups = list((tmp_path / ".nbs_agent_runtime/documentation-backups").rglob("*.md"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "# Brief\n"
    assert "beforeSha256=" in result.applications[0]["result"]
    assert result.applications[0]["appliedSha256"] == sha256(target.read_bytes()).hexdigest()


def test_brief_apply_writes_verified_obsidian_target(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    vault = tmp_path / "NBS_Analytics_Knowledge"
    target = vault / "70_Codex_Briefs/task.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Brief\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")
    after = before + "\n<!-- documentation-agent:implementation-evidence:start -->\nnew\n<!-- documentation-agent:implementation-evidence:end -->\n"
    preview = DocumentationPreview("preview_ready", (_preview_item(
        "brief_backfill", "docs/briefs/task.md", before, after,
        vault_relative_path="70_Codex_Briefs/task.md",
    ),))

    result = DocumentationController(tmp_path, obsidian_vault=vault).apply(
        preview, apply_brief=True, approved_targets=frozenset(),
    )

    assert result.status == "applied"
    assert target.read_text(encoding="utf-8") == after
    assert not (tmp_path / "docs/briefs/task.md").exists()


def test_high_risk_target_requires_explicit_approval(controller, system_map_preview, tmp_path):
    target = tmp_path / "NBS_ANALYTICS_SYSTEM_MAP.md"
    before = target.read_bytes() if target.exists() else b""

    result = controller.apply(system_map_preview, apply_brief=True, approved_targets=frozenset())

    assert result.status == "awaiting_target_approval"
    assert (target.read_bytes() if target.exists() else b"") == before


def test_adr_requires_approval_and_is_create_only(controller, tmp_path):
    preview = DocumentationPreview(
        "preview_ready",
        (_preview_item("adr", "Summay/ADR-new.md", None, "# New ADR\n", required_approval="adr"),),
    )

    waiting = controller.apply(preview, apply_brief=True, approved_targets=frozenset())
    assert waiting.status == "awaiting_target_approval"
    applied = controller.apply(preview, apply_brief=True, approved_targets=frozenset({"Summay/ADR-new.md"}))

    assert applied.status == "applied"
    created = list((tmp_path / "Summay").glob("ADR-*-new.md"))
    assert len(created) == 1
    assert created[0].read_text(encoding="utf-8") == "# New ADR\n"


def test_adr_existing_target_is_never_replaced(controller, tmp_path):
    target = tmp_path / "Summay/ADR-001-new.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Existing ADR\n", encoding="utf-8")
    preview = DocumentationPreview(
        "preview_ready",
        (_preview_item("adr", "Summay/ADR-new.md", None, "# New ADR\n", required_approval="adr"),),
    )

    result = controller.apply(preview, apply_brief=True, approved_targets=frozenset({"Summay/ADR-new.md"}))

    assert result.status == "applied"
    assert target.read_text(encoding="utf-8") == "# Existing ADR\n"
    created = sorted(tmp_path.joinpath("Summay").glob("ADR-*-new.md"))
    assert [path.name for path in created] == ["ADR-001-new.md", "ADR-002-new.md"]


def test_adr_exclusive_create_blocks_racing_existing_path(controller, tmp_path, monkeypatch):
    target = tmp_path / "Summay/ADR-001-new.md"
    target.parent.mkdir(parents=True)
    preview = DocumentationPreview(
        "preview_ready",
        (_preview_item("adr", "Summay/ADR-new.md", None, "# New ADR\n", required_approval="adr"),),
    )
    monkeypatch.setattr(controller, "_assign_adr_path", lambda item, path: target)

    def create_race(item, before):
        target.write_text("# Existing ADR\n", encoding="utf-8")
        return b"# New ADR\n"

    monkeypatch.setattr(controller, "_after_bytes", create_race)

    result = controller.apply(preview, apply_brief=True, approved_targets=frozenset({"Summay/ADR-new.md"}))

    assert result.status == "blocked"
    assert result.applications[0]["result"] == "write_failed: FileExistsError"
    assert target.read_text(encoding="utf-8") == "# Existing ADR\n"


def test_adr_reapply_is_idempotent(controller, tmp_path):
    preview = DocumentationPreview(
        "preview_ready",
        (_preview_item("adr", "Summay/ADR-new.md", None, "# New ADR\n", required_approval="adr"),),
    )
    approvals = frozenset({"Summay/ADR-new.md"})

    first = controller.apply(preview, apply_brief=True, approved_targets=approvals)
    second = controller.apply(preview, apply_brief=True, approved_targets=approvals)

    assert first.status == "applied"
    assert second.status == "applied"
    assert second.applications[0]["result"].startswith("already_applied")
    assert len(list((tmp_path / "Summay").glob("ADR-*-new.md"))) == 1


def test_stale_target_is_blocked_by_exact_hash(controller, brief_preview, tmp_path):
    target = tmp_path / "docs/briefs/task.md"
    target.parent.mkdir(parents=True)
    target.write_text("# changed\n", encoding="utf-8")

    result = controller.apply(brief_preview, apply_brief=True, approved_targets=frozenset())

    assert result.status == "blocked"
    assert "stale_target" in result.applications[0]["result"]
    assert target.read_text(encoding="utf-8") == "# changed\n"


def test_reapply_is_idempotent_without_second_backup(controller, brief_preview, tmp_path):
    target = tmp_path / "docs/briefs/task.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Brief\n", encoding="utf-8")

    first = controller.apply(brief_preview, apply_brief=True, approved_targets=frozenset())
    second = controller.apply(brief_preview, apply_brief=True, approved_targets=frozenset())

    assert first.status == "applied"
    assert second.status == "applied"
    assert second.applications[0]["result"].startswith("already_applied")
    assert len(list((tmp_path / ".nbs_agent_runtime/documentation-backups").rglob("*.md"))) == 1


def test_second_target_failure_is_reported_as_partial_and_first_stays_atomic(controller, tmp_path):
    first = tmp_path / "docs/briefs/one.md"
    second = tmp_path / "docs/briefs/two.md"
    first.parent.mkdir(parents=True)
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    preview = DocumentationPreview(
        "preview_ready",
        (
            _preview_item("brief_backfill", "docs/briefs/one.md", "one\n", "one updated\n"),
            _preview_item("brief_backfill", "docs/briefs/two.md", "stale\n", "two updated\n"),
        ),
    )

    result = controller.apply(preview, apply_brief=True, approved_targets=frozenset())

    assert result.status == "partially_applied"
    assert first.read_text(encoding="utf-8") == "one updated\n"
    assert second.read_text(encoding="utf-8") == "two\n"
    assert any(item["result"].startswith("stale_target") for item in result.applications)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _v2_preview(root: Path, *, target_id="handoff.current-conclusion"):
    from backend.agents.documentation_models import DocumentationProposalV2
    from backend.agents.documentation_policy import load_documentation_target
    from backend.agents.documentation_validator import DocumentationProposalValidator
    from backend.agents.verification_chain import git_source_probe
    from backend.agents.verification_session import VerificationSession
    from backend.agents.workflow_models import canonical_sha256

    target = load_documentation_target(target_id)
    path = root / target.repo_path
    path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "# Handoff\n\n"
        f"{target.section_heading}\nOld handoff text.\n\n"
        "### Nested detail\nPreserve this heading and surrounding bytes.\n\n"
        "## 2. Next section\nDo not modify.\n"
    )
    path.write_text(original, encoding="utf-8")
    brief = root / "docs/briefs/task-4.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("Task 4 review brief.\n", encoding="utf-8")
    review_contract = root / "docs/agents/REVIEW_AGENT_CONTRACT.md"
    review_contract.parent.mkdir(parents=True, exist_ok=True)
    review_contract.write_text("review contract\n", encoding="utf-8")
    policy = root / "agent_config/token_budgets.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").write_text(".nbs_agent_runtime/\nisolated-runtime/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "NBS test")
    _git(root, "config", "user.email", "nbs-test@example.invalid")
    _git(root, "add", target.repo_path, "docs/briefs/task-4.md", "docs/agents/REVIEW_AGENT_CONTRACT.md", "agent_config/token_budgets.json", ".gitignore")
    _git(root, "commit", "-qm", "accepted source")
    commit_sha = _git(root, "rev-parse", "HEAD")

    source = git_source_probe(
        root, brief_path="docs/briefs/task-4.md", base_sha=commit_sha,
        head_ref="WORKTREE", contract_path="docs/agents/REVIEW_AGENT_CONTRACT.md",
        policy_path="agent_config/token_budgets.json",
    )
    source_session = VerificationSession.create(
        project_id="nbs_analytics", base_sha=commit_sha, head_sha=source["head_sha"],
        brief_path="docs/briefs/task-4.md", brief_fingerprint=source["brief_fingerprint"],
        worktree_fingerprint=source["worktree_fingerprint"],
        diff_fingerprint=source["diff_fingerprint"],
        contract_fingerprint=source["contract_fingerprint"],
        policy_fingerprint=source["policy_fingerprint"],
        created_at="2026-09-29T09:00:00+08:00",
    )
    session_dir = root / ".nbs_agent_runtime/verification_sessions" / source_session.session_id
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(source_session.to_dict(), ensure_ascii=False), encoding="utf-8",
    )

    section = original[original.index(target.section_heading):original.index("\n## 2. Next section") + 1]
    content = f"{target.section_heading}\n\nUpdated handoff text.\n"
    source_fingerprint = source_session.source_fingerprint
    evidence_unsigned = {
        "schemaVersion": "documentation-evidence-v2",
        "taskId": "task-4",
        "generatedAt": "2026-09-29T09:00:00+08:00",
        "runId": "run-task-4",
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "selectedTargetId": target_id,
        "sources": [{"path": target.repo_path, "sha256": "d" * 64}],
        "gateResults": [
            {
                "gate": gate, "status": "pass", "sourceFingerprint": source_fingerprint,
                "evidenceFingerprint": "e" * 64,
            }
            for gate in ("review", "full-verification", "hermes")
        ],
        "guardrails": {
            "revenueScope": "不含掛賬核銷與TT退款轉團款",
            "mayBaseline": "HKD 12,057,968",
        },
        "expectedSectionSha256": sha256(section.encode("utf-8")).hexdigest(),
    }
    evidence = {
        **evidence_unsigned,
        "evidenceFingerprint": canonical_sha256(evidence_unsigned),
    }
    proposal_unsigned = {
        "schemaVersion": "documentation-proposal-v2",
        "taskId": "task-4",
        "generatedAt": "2026-09-29T09:01:00+08:00",
        "evidence": evidence,
        "evidenceFingerprint": evidence["evidenceFingerprint"],
        "status": "ready",
        "targetId": target.target_id,
        "targetKind": target.target_kind,
        "repoPath": target.repo_path,
        "sectionHeading": target.section_heading,
        "operation": target.operation,
        "expectedSectionSha256": evidence["expectedSectionSha256"],
        "content": content,
        "contentSha256": sha256(content.encode("utf-8")).hexdigest(),
    }
    proposal = DocumentationProposalV2.from_dict({
        **proposal_unsigned,
        "proposalFingerprint": canonical_sha256(proposal_unsigned),
    })
    return DocumentationProposalValidator(root).build_target_preview(proposal), path, original


def test_v2_apply_requires_exact_target_id_approval(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, path, original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)

    missing = controller.apply_target(preview, approved_target_ids=frozenset())
    wrong = controller.apply_target(preview, approved_target_ids=frozenset({"runbook.shard-boundary"}))

    assert missing.status == wrong.status == "awaiting_target_approval"
    assert missing.result == wrong.result == "preview"
    assert path.read_text(encoding="utf-8") == original


def test_v2_apply_replaces_only_section_atomically_and_verifies_hash(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, path, original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)

    result = controller.apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )

    section_start = original.index(preview.section_heading)
    section_end = original.index("\n## 2. Next section") + 1
    expected = original[:section_start] + preview.proposal.content + original[section_end:]
    assert result.status == "applied"
    assert result.result == "applied"
    assert path.read_text(encoding="utf-8") == expected
    assert result.after_section_sha256 == sha256(preview.proposal.content.encode("utf-8")).hexdigest()
    backups = list((tmp_path / ".nbs_agent_runtime/documentation-backups").rglob("*.md"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_v2_apply_blocks_source_or_target_drift(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    root = tmp_path / "source-drift"
    preview, path, original = _v2_preview(root)
    unrelated = root / "unrelated.md"
    unrelated.write_text("source changed\n", encoding="utf-8")
    _git(root, "add", "unrelated.md")
    _git(root, "commit", "-qm", "source drift")
    result = DocumentationController(root).apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )
    assert result.status == "blocked"
    assert path.read_text(encoding="utf-8") == original

    root2 = tmp_path / "target-drift"
    preview2, path2, original2 = _v2_preview(root2)
    changed = original2.replace("Old handoff text.", "Changed after preview.")
    path2.write_text(changed, encoding="utf-8")
    result2 = DocumentationController(root2).apply_target(
        preview2, approved_target_ids=frozenset({preview2.target_id}),
    )
    assert result2.status == "blocked"
    assert path2.read_text(encoding="utf-8") == changed


def test_v2_apply_blocks_uncommitted_source_drift(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, target_path, original = _v2_preview(tmp_path)
    unrelated = tmp_path / "backend/unrelated.py"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("# changed after source seal\n", encoding="utf-8")

    result = DocumentationController(tmp_path).apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )

    assert result.status == "blocked"
    assert target_path.read_text(encoding="utf-8") == original


def test_v2_apply_rejects_path_traversal_and_symlink(tmp_path):
    from dataclasses import replace

    from backend.agents.documentation_controller import DocumentationController

    root = tmp_path / "traversal"
    preview, path, original = _v2_preview(root)
    controller = DocumentationController(root)
    traversal = replace(preview, repo_path="../outside.md")
    blocked = controller.apply_target(
        traversal, approved_target_ids=frozenset({preview.target_id}),
    )
    assert blocked.status == "blocked"
    assert path.read_text(encoding="utf-8") == original

    external = root / "outside.md"
    external.write_text("must remain untouched\n", encoding="utf-8")
    path.unlink()
    path.symlink_to(external)
    symlink_result = controller.apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )
    assert symlink_result.status == "blocked"
    assert external.read_text(encoding="utf-8") == "must remain untouched\n"


def test_v2_apply_is_idempotent_after_success(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, path, _original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)
    approvals = frozenset({preview.target_id})

    first = controller.apply_target(preview, approved_target_ids=approvals)
    first_bytes = path.read_bytes()
    backups_before = list((tmp_path / ".nbs_agent_runtime/documentation-backups").rglob("*.md"))
    second = controller.apply_target(preview, approved_target_ids=approvals)

    assert first.status == second.status == "applied"
    assert path.read_bytes() == first_bytes
    assert second.result == "applied"
    assert len(list((tmp_path / ".nbs_agent_runtime/documentation-backups").rglob("*.md"))) == len(backups_before) == 1


def test_v2_reapply_blocks_uncommitted_source_drift_after_success(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, target_path, _original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)
    approvals = frozenset({preview.target_id})

    first = controller.apply_target(preview, approved_target_ids=approvals)
    applied_bytes = target_path.read_bytes()
    unrelated = tmp_path / "backend/unrelated.py"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("# changed after successful apply\n", encoding="utf-8")
    second = controller.apply_target(preview, approved_target_ids=approvals)

    assert first.status == "applied"
    assert second.status == "blocked"
    assert target_path.read_bytes() == applied_bytes


def test_v2_reapply_blocks_when_post_apply_receipt_is_missing(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, target_path, _original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)
    approvals = frozenset({preview.target_id})

    first = controller.apply_target(preview, approved_target_ids=approvals)
    applied_bytes = target_path.read_bytes()
    receipts = list((tmp_path / ".nbs_agent_runtime/verification_sessions").rglob(
        f"{preview.target_id}-{preview.preview_fingerprint}.json",
    ))
    assert first.status == "applied"
    assert len(receipts) == 1
    receipts[0].unlink()

    retry = controller.apply_target(preview, approved_target_ids=approvals)

    assert retry.status == "blocked"
    assert target_path.read_bytes() == applied_bytes


def test_v2_apply_reuses_identical_backup_after_interrupted_attempt(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, target_path, original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)
    backup = controller._target_backup_path(
        preview.run_id, preview.target_id, original.encode("utf-8"),
    )
    backup.parent.mkdir(parents=True)
    backup.write_bytes(original.encode("utf-8"))

    result = controller.apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )

    assert result.status == "applied"
    assert backup.read_bytes() == original.encode("utf-8")
    assert target_path.read_text(encoding="utf-8") != original


def test_v2_apply_blocks_when_existing_backup_differs(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, target_path, original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)
    backup = controller._target_backup_path(
        preview.run_id, preview.target_id, original.encode("utf-8"),
    )
    backup.parent.mkdir(parents=True)
    backup.write_text("corrupted backup\n", encoding="utf-8")

    result = controller.apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )

    assert result.status == "blocked"
    assert backup.read_text(encoding="utf-8") == "corrupted backup\n"
    assert target_path.read_text(encoding="utf-8") == original


def test_v2_apply_blocks_before_target_write_when_receipt_preflight_fails(tmp_path, monkeypatch):
    from backend.agents.documentation_controller import DocumentationController

    preview, target_path, original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)

    def fail_preflight(_preview, _session):
        raise OSError("receipt runtime is not writable")

    monkeypatch.setattr(
        controller, "_preflight_applied_source_receipt", fail_preflight, raising=False,
    )
    result = controller.apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )

    assert result.status == "blocked"
    assert target_path.read_text(encoding="utf-8") == original


def test_v2_artifacts_are_namespaced_per_target_id(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    controller = DocumentationController(tmp_path)
    first = controller.target_artifact_path(
        "run-1", "handoff.current-conclusion", "documentation-preview-v2.json",
    )
    second = controller.target_artifact_path(
        "run-1", "handoff.verification-snapshot", "documentation-preview-v2.json",
    )

    assert first == tmp_path / ".nbs_agent_runtime/runs/run-1/documentation/targets/handoff.current-conclusion/documentation-preview-v2.json"
    assert second.parent != first.parent
    with pytest.raises(ValueError):
        controller.target_artifact_path("../escape", "handoff.current-conclusion", "documentation-preview-v2.json")


def test_v2_artifacts_and_backups_respect_custom_runtime_root(tmp_path):
    from backend.agents.documentation_controller import DocumentationController

    preview, _path, _original = _v2_preview(tmp_path)
    runtime_root = tmp_path / "isolated-runtime"
    controller = DocumentationController(tmp_path, runtime_root=runtime_root)

    artifact = controller.write_target_artifact(
        preview.run_id, preview.target_id, "documentation-preview-v2.json", preview.to_dict(),
    )
    result = controller.apply_target(
        preview, approved_target_ids=frozenset({preview.target_id}),
    )

    assert result.status == "applied"
    assert artifact.is_relative_to(runtime_root)
    backups = list((runtime_root / "documentation-backups").rglob("*.md"))
    assert len(backups) == 1
    assert (tmp_path / ".nbs_agent_runtime/verification_sessions").is_dir()
    assert not (tmp_path / ".nbs_agent_runtime/runs").exists()
    assert not (tmp_path / ".nbs_agent_runtime/documentation-backups").exists()


def test_application_artifact_reconciles_blocked_to_applied_for_same_preview(tmp_path):
    from backend.agents.documentation_controller import DocumentationController
    from backend.agents.documentation_policy import load_documentation_target

    preview, _path, _original = _v2_preview(tmp_path)
    controller = DocumentationController(tmp_path)
    target = load_documentation_target(preview.target_id)
    blocked = controller._target_application(preview, target, "blocked").to_dict()
    applied = controller._target_application(preview, target, "applied").to_dict()

    controller.write_target_artifact(
        preview.run_id, preview.target_id, "documentation-application-v2.json", blocked,
    )
    controller.write_target_artifact(
        preview.run_id, preview.target_id, "documentation-application-v2.json", applied,
    )
    persisted = controller.read_target_artifact(
        preview.run_id, preview.target_id, "documentation-application-v2.json",
    )

    assert persisted["status"] == "applied"
    assert persisted["proposalFingerprint"] == preview.proposal_fingerprint


def test_application_record_never_contains_absolute_vault_path(controller, brief_preview, tmp_path):
    vault_root = tmp_path / "vault"
    preview = DocumentationPreview(
        "preview_ready",
        (_preview_item("brief_backfill", "docs/briefs/task.md", "# Brief\n", "# Brief\nnew\n", vault_relative_path="70_Codex_Briefs/task.md"),),
    )
    target = tmp_path / "docs/briefs/task.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Brief\n", encoding="utf-8")

    result = controller.apply(preview, apply_brief=True, approved_targets=frozenset())
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)

    assert str(vault_root) not in encoded
    assert "70_Codex_Briefs/task.md" in encoded
    manifest = list((tmp_path / ".nbs_agent_runtime/runs").rglob("documentation-application.json"))
    assert len(manifest) == 1
    assert str(vault_root) not in manifest[0].read_text(encoding="utf-8")


def test_blocked_application_never_serializes_absolute_identity(controller, tmp_path):
    absolute_vault_path = str(tmp_path / "vault/70_Codex_Briefs/task.md")
    preview = DocumentationPreview(
        "preview_ready",
        (_preview_item(
            "brief_backfill",
            "docs/briefs/task.md",
            "# Brief\n",
            "# Brief\nnew\n",
            vault_relative_path=absolute_vault_path,
        ),),
    )

    result = controller.apply(preview, apply_brief=False, approved_targets=frozenset())
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.status == "awaiting_target_approval"
    assert str(tmp_path) not in encoded
    assert "<absolute-target>" in encoded


@pytest.mark.parametrize(
    "absolute_vault_path",
    [
        r"C:\Users\analyst\vault\70_Codex_Briefs\task.md",
        r"\\server\shared\vault\70_Codex_Briefs\task.md",
    ],
)
def test_blocked_application_redacts_windows_absolute_identity_on_posix(controller, absolute_vault_path):
    preview = DocumentationPreview(
        "preview_ready",
        (_preview_item(
            "brief_backfill",
            "docs/briefs/task.md",
            "# Brief\n",
            "# Brief\nnew\n",
            vault_relative_path=absolute_vault_path,
        ),),
    )

    result = controller.apply(preview, apply_brief=False, approved_targets=frozenset())
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.status == "awaiting_target_approval"
    assert absolute_vault_path not in encoded
    assert "<absolute-target>" in encoded


def test_hash_is_rechecked_immediately_before_replace(controller, brief_preview, tmp_path, monkeypatch):
    target = tmp_path / "docs/briefs/task.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Brief\n", encoding="utf-8")

    def mutate_after_backup(path, identity, content):
        path.write_text("# changed during apply\n", encoding="utf-8")

    monkeypatch.setattr(controller, "_backup", mutate_after_backup)
    result = controller.apply(brief_preview, apply_brief=True, approved_targets=frozenset())

    assert result.status == "blocked"
    assert "stale_target" in result.applications[0]["result"]
    assert target.read_text(encoding="utf-8") == "# changed during apply\n"
