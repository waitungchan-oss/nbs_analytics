from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .documentation_agent_service import DocumentationAgentService
from .documentation_controller import DocumentationController
from .documentation_evidence import DocumentationEvidenceCollector, DocumentationEvidenceError
from .documentation_models import DocumentationSchemaError
from .documentation_policy import load_documentation_target
from .documentation_targets import ObsidianTargetResolver
from .documentation_validator import (
    DocumentationPreviewV2,
    DocumentationProposalValidator,
    DocumentationValidationError,
)
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
        target_id: str | None = None,
        approve_target_id: str | None = None,
    ) -> dict[str, Any]:
        if approve_target_id is not None and target_id is None:
            return self._blocked(run_id, "--approve-target-id requires --target-id")
        if target_id is not None:
            return self._run_target(
                run_id,
                target_id=target_id,
                approve_target_id=approve_target_id,
                agent_command=agent_command,
                obsidian_vault=obsidian_vault,
                apply_brief=apply_brief,
                approved_targets=approved_targets,
            )

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

    def _run_target(
        self,
        run_id: str,
        *,
        target_id: str,
        approve_target_id: str | None,
        agent_command: str | None,
        obsidian_vault: Path | None,
        apply_brief: bool,
        approved_targets: frozenset[str],
    ) -> dict[str, Any]:
        if apply_brief or approved_targets or obsidian_vault is not None:
            return self._blocked(run_id, "v1 apply/vault flags cannot be combined with a v2 target")
        try:
            target = load_documentation_target(target_id)
        except (DocumentationSchemaError, TypeError, ValueError) as exc:
            return self._blocked(run_id, str(exc))
        if approve_target_id is not None and approve_target_id != target.required_approval_id:
            return self._blocked(run_id, "approval ID must exactly match the selected target ID")

        try:
            status = self.store.load_status(run_id)
            if status.status != "completed":
                return self._blocked(run_id, "run must be completed")
            evidence = self.collector.collect_target(run_id, target.target_id)
        except (DocumentationEvidenceError, FileNotFoundError, PermissionError, ValueError) as exc:
            return self._blocked(run_id, str(exc))

        controller = DocumentationController(self.project_root)
        if approve_target_id is not None:
            try:
                preview_payload = controller.read_target_artifact(
                    run_id, target.target_id, "documentation-preview-v2.json",
                )
                preview = DocumentationPreviewV2.from_dict(preview_payload)
                accepted_evidence = preview.proposal.evidence
                if (
                    preview.run_id != run_id
                    or preview.target_id != target.target_id
                    or accepted_evidence.run_id != evidence.run_id
                    or accepted_evidence.selected_target_id != evidence.selected_target_id
                    or accepted_evidence.commit_sha != evidence.commit_sha
                    or accepted_evidence.source_fingerprint != evidence.source_fingerprint
                    or accepted_evidence.expected_section_sha256 != evidence.expected_section_sha256
                    or accepted_evidence.evidence_fingerprint != evidence.evidence_fingerprint
                    or accepted_evidence.to_dict() != evidence.to_dict()
                ):
                    return self._blocked(run_id, "saved preview no longer matches fresh source and target evidence")
                application = controller.apply_target(
                    preview, approved_target_ids=frozenset({approve_target_id}),
                )
                application_payload = application.to_dict()
                controller.write_target_artifact(
                    run_id, target.target_id, "documentation-application-v2.json", application_payload,
                )
                return controller.read_target_artifact(
                    run_id, target.target_id, "documentation-application-v2.json",
                )
            except (
                DocumentationSchemaError, DocumentationValidationError,
                FileNotFoundError, OSError, PermissionError, ValueError,
            ) as exc:
                return self._blocked(run_id, str(exc))

        try:
            controller.write_target_artifact(
                run_id, target.target_id, "documentation-evidence-v2.json", evidence.to_dict(),
            )
            proposal = self.service.draft_target(evidence, agent_command=agent_command)
            proposal_payload = proposal.to_dict()
            controller.write_target_artifact(
                run_id, target.target_id, "documentation-proposal-v2.json", proposal_payload,
            )
            if proposal.status != "ready":
                return {**proposal_payload, "status": proposal.status, "runId": run_id}
            preview = self.validator.build_target_preview(proposal)
            preview_payload = preview.to_dict()
            controller.write_target_artifact(
                run_id, target.target_id, "documentation-preview-v2.json", preview_payload,
            )
            return {"status": "preview_ready", "runId": run_id, **preview_payload}
        except (
            DocumentationSchemaError, DocumentationValidationError,
            FileNotFoundError, OSError, PermissionError, ValueError,
        ) as exc:
            return self._blocked(run_id, str(exc))

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
