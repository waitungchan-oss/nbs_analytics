from __future__ import annotations

import json
import difflib
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
