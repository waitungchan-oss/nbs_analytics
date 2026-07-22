from __future__ import annotations

import json
from pathlib import Path

from backend.agents.documentation_agent_service import DocumentationRunnerResult
from backend.agents.documentation_models import DOCUMENTATION_DRAFT_SCHEMA
from backend.agents.documentation_workflow import DocumentationWorkflow
from backend.agents.workflow_models import (
    APPROVAL_SCHEMA,
    MANIFEST_SCHEMA,
    STATUS_SCHEMA,
    WorkflowApproval,
    WorkflowManifest,
    WorkflowStatus,
)
from backend.agents.workflow_store import WorkflowStore


class BackfillRunner:
    command = "codex"

    def run(self, argv, *, input_text, timeout_seconds, max_output_bytes):
        evidence = json.loads(input_text)
        payload = {
            "schemaVersion": DOCUMENTATION_DRAFT_SCHEMA,
            "evidenceFingerprint": evidence["evidenceFingerprint"],
            "status": "ready",
            "proposals": [
                {"targetKind": "brief_backfill", "content": "verified evidence\n"},
                {"targetKind": "system_map", "content": "verified documentation\n"},
            ],
        }
        return DocumentationRunnerResult(0, json.dumps(payload), "", 1)


def create_verified_run(tmp_path: Path) -> dict[str, object]:
    store = WorkflowStore(tmp_path)
    run_id = "verified-backfill"
    manifest = WorkflowManifest(
        MANIFEST_SCHEMA, run_id, "docs/briefs/verified.md", "a" * 64,
        "main", "b" * 40, (), "2026-07-19T10:00:00+00:00", "c" * 64,
    )
    status = WorkflowStatus(
        STATUS_SCHEMA, run_id, "hermes", "completed",
        "2026-07-19T10:00:00+00:00", "2026-07-19T10:01:00+00:00",
        "2026-07-19T10:01:00+00:00", "done", None, 0,
    )
    store.create_run(manifest, status)
    store.write_approval(run_id, WorkflowApproval(
        APPROVAL_SCHEMA, run_id, "contract.json", "d" * 64, "e" * 40,
        "2026-07-19T10:00:30+00:00", "approved",
    ))
    for name in ("implementation.json", "targeted-verification.json", "review.json", "full-verification.json"):
        store.write_artifact(run_id, name, {"status": "pass", "changedPaths": ["backend/agents/documentation_workflow.py"]})
    store.write_artifact(run_id, "hermes.json", {"overallStatus": "pass"})

    brief = tmp_path / "docs/briefs/verified.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Verified\n", encoding="utf-8")
    system_map = tmp_path / "NBS_ANALYTICS_SYSTEM_MAP.md"
    system_map.write_text(
        "# Root\n\n## 2A. Agent Evidence Pipeline\noriginal\n\n## Other\nkeep\n",
        encoding="utf-8",
    )
    vault = tmp_path / "temporary-vault"
    (vault / "70_Codex_Briefs").mkdir(parents=True)
    (vault / "10_System").mkdir(parents=True)
    return {
        "run_id": run_id,
        "store": store,
        "workflow": DocumentationWorkflow(tmp_path, runner=BackfillRunner()),
        "vault": vault,
        "brief": brief,
        "system_map": system_map,
    }


def apply_with_system_map_approval(run: dict[str, object]) -> dict[str, object]:
    return run["workflow"].run(
        run["run_id"], agent_command=BackfillRunner.command,
        obsidian_vault=run["vault"], apply_brief=True,
        approved_targets=frozenset({"system_map"}),
    )


def test_verified_backfill_can_preview_then_apply_only_approved_targets(tmp_path):
    run = create_verified_run(tmp_path)
    preview = run["workflow"].run(
        run["run_id"], agent_command=BackfillRunner.command,
        obsidian_vault=run["vault"],
    )
    assert preview["status"] == "preview_ready"
    brief_before = run["brief"].read_bytes()
    system_map_before = run["system_map"].read_bytes()

    waiting = run["workflow"].run(
        run["run_id"], agent_command=BackfillRunner.command,
        obsidian_vault=run["vault"], apply_brief=True,
    )
    assert waiting["status"] == "awaiting_target_approval"
    assert run["brief"].read_bytes() == brief_before
    assert run["system_map"].read_bytes() == system_map_before

    applied = apply_with_system_map_approval(run)
    assert applied["status"] == "applied"
    assert run["brief"].read_bytes() != brief_before
    assert run["system_map"].read_bytes() != system_map_before

    serialized = json.dumps(applied, ensure_ascii=False)
    assert str(run["vault"]) not in serialized
    application = run["store"]._run_file(run["run_id"], "documentation-application.json")
    assert str(run["vault"]) not in application.read_text(encoding="utf-8")
