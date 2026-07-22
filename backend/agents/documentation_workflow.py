from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .documentation_agent_service import DocumentationAgentService
from .documentation_controller import DocumentationController
from .documentation_evidence import DocumentationEvidenceCollector, DocumentationEvidenceError
from .documentation_targets import ObsidianTargetResolver
from .documentation_validator import DocumentationProposalValidator, DocumentationValidationError
from .workflow_store import WorkflowStore


class DocumentationWorkflow:
    """Run documentation as a sidecar after a completed governed workflow."""

    def __init__(self, project_root: Path, *, runner=None, store: WorkflowStore | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = store or WorkflowStore(self.project_root)
        self.collector = DocumentationEvidenceCollector(self.project_root, store=self.store)
        self.service = DocumentationAgentService(self.project_root, runner=runner)
        self.validator = DocumentationProposalValidator(self.project_root)

    def run(
        self,
        run_id: str,
        *,
        agent_command: str | None,
        obsidian_vault: Path | None = None,
        apply_brief: bool = False,
        approved_targets: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        try:
            status = self.store.load_status(run_id)
            if status.status != "completed":
                return self._blocked(run_id, "run must be completed")
            evidence = self.collector.collect(run_id)
        except (DocumentationEvidenceError, FileNotFoundError, PermissionError, ValueError) as exc:
            return self._blocked(run_id, str(exc))

        self.store.write_artifact(run_id, "documentation-evidence.json", evidence.to_dict())
        proposal = self.service.draft(evidence, agent_command=agent_command)
        proposal_payload = proposal.to_dict()
        self.store.write_artifact(run_id, "documentation-proposal.json", proposal_payload)
        if proposal.status != "ready":
            result = dict(proposal_payload)
            result["status"] = proposal.status
            self._write_telemetry(run_id, evidence.documentation_fingerprint, proposal.status, 0)
            return result

        obsidian = ObsidianTargetResolver.from_sources(
            self.project_root, cli_root=obsidian_vault, environ=os.environ,
        )
        controller = DocumentationController(
            self.project_root,
            obsidian_vault=obsidian.vault_root if obsidian else None,
        )
        try:
            preview = self.validator.build_preview(proposal, obsidian=obsidian)
        except (DocumentationValidationError, FileNotFoundError, PermissionError, ValueError) as exc:
            result = {"status": "blocked", "message": str(exc), "runId": run_id}
            self._write_telemetry(run_id, evidence.documentation_fingerprint, "blocked", 0)
            return result

        preview_payload = preview.to_dict()
        self.store.write_artifact(run_id, "documentation-preview.json", preview_payload)
        if not apply_brief and not approved_targets:
            self._write_telemetry(run_id, evidence.documentation_fingerprint, "preview_ready", len(preview.items))
            return {"status": "preview_ready", "runId": run_id, **preview_payload}

        approvals = set(approved_targets)
        for item in preview.items:
            if item.target_kind in approved_targets:
                approvals.add(item.path_identity)
                if item.vault_relative_path:
                    approvals.add(item.vault_relative_path)
        application = controller.apply(
            preview, apply_brief=apply_brief, approved_targets=frozenset(approvals),
        )
        application_payload = application.to_dict()
        self.store.write_artifact(run_id, "documentation-application.json", application_payload)
        self._write_telemetry(
            run_id, evidence.documentation_fingerprint, application.status, len(preview.items),
        )
        return application_payload

    def _blocked(self, run_id: str, message: str) -> dict[str, Any]:
        return {"status": "blocked", "runId": run_id, "message": message}

    def _write_telemetry(self, run_id: str, fingerprint: str, result: str, proposal_count: int) -> None:
        payload = {
            "schemaVersion": "documentation-telemetry-v1",
            "runId": run_id,
            "documentationFingerprint": fingerprint,
            "proposalCount": proposal_count,
            "result": result,
        }
        self.store.write_artifact(run_id, "documentation-telemetry.json", payload)


__all__ = ["DocumentationWorkflow"]
