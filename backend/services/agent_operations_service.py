from __future__ import annotations

import json
import os
import re
import stat
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.agents.context_agent_service import context_bundle_from_payload
from backend.agents.canonical_evidence_reader import CanonicalEvidenceReader
from backend.agents.documentation_models import (
    DocumentationApplication,
    DocumentationEvidence,
    DocumentationProposal,
)
from backend.agents.governance_graph_models import (
    GovernanceGate,
    GovernanceGraphSchemaError,
    GovernanceGraphSnapshot,
)
from backend.agents.review_agent_service import validate_context_summary
from backend.agents.workflow_models import WorkflowEvent, WorkflowManifest, WorkflowStatus
from backend.agents.workflow_retention import RetentionPolicy


SNAPSHOT_SCHEMA = "agent-operations-snapshot-v1"
STAGE_FILES = {
    "context": "context.json",
    "implementation": "implementation.json",
    "targeted_verification": "targeted-verification.json",
    "review": "review.json",
    "full_verification": "full-verification.json",
    "hermes": "hermes.json",
}
GOVERNANCE_GRAPH_FILE = "governance-graph.json"
GOVERNANCE_GATE_FILES = {
    "specGate": ("design-spec-gate.json", "spec-gate"),
    "planGate": ("plan-gate.json", "plan-gate"),
}
DOCUMENTATION_FILES = {
    "evidence": "documentation-evidence.json",
    "proposal": "documentation-proposal.json",
    "preview": "documentation-preview.json",
    "application": "documentation-application.json",
    "telemetry": "documentation-telemetry.json",
}
SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
MAX_FINDINGS = 50
MAX_DIAGNOSTICS = 100
MAX_SAFE_MESSAGE_CHARS = 500
MAX_EVENT_LINES = 500
EVENT_READ_CHUNK_BYTES = 64 * 1024
MAX_EVENT_LINE_BYTES = 64 * 1024
DEFAULT_STAGE_ARTIFACT_MAX_BYTES = 5 * 1024 * 1024
MAX_EVENT_SCAN_BYTES = 1 * 1024 * 1024
MAX_EVENT_SCAN_LINES = 10_000
ARCHIVE_SUMMARY_SCHEMA = "agent-workflow-archive-summary-v1"
SAFE_VALUE_UNAVAILABLE = "value unavailable"
_SAFE_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_BRIEF_PATH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")
_SAFE_BRIEF_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_GIT_BRANCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
_URI_SCHEME_PATTERN = re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}:")
_ALLOWED_STATUS_STAGES = frozenset({
    "context",
    "authorization",
    "implementation",
    "targeted_verification",
    "review",
    "full_verification",
    "hermes",
})
_FULL_VERIFICATION_KEYS = frozenset({"fullPytest", "acceptance"})
_FULL_PYTEST_KEYS = frozenset({"exitCode", "stdoutTail", "stderrTail", "payload"})
_SAFE_DIAGNOSTIC_REASONS = frozenset({
    "archive summary is invalid",
    "runs root is not a regular directory",
    "run path is not a regular directory",
    "run artifact is invalid",
    "stage artifact is invalid",
    "stage artifact is unsafe",
    "retention policy is invalid",
})
_SENSITIVE_MESSAGE_PATTERN = re.compile(
    r"(?:stdout|stderr|prompt|argv|exception|traceback|runner|command|token|password|secret)",
    re.IGNORECASE,
)


class _ArtifactError(ValueError):
    pass


class _UnsafeArtifactError(_ArtifactError):
    pass


class AgentOperationsService:
    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        candidate = Path(runtime_root) if runtime_root is not None else self.project_root / ".nbs_agent_runtime"
        self.runtime_root = self._safe_root(candidate)
        self.runs_root = self.runtime_root / "runs"
        self.retention_path = self.project_root / "agent_config" / "workflow_retention.json"
        self.canonical_evidence_reader = CanonicalEvidenceReader(self.project_root, self.runtime_root)

    def build_snapshot(self) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        diagnostics: list[dict[str, str]] = []
        retention, policy = self._retention(diagnostics)
        stage_artifact_max_bytes = (
            policy.stage_artifact_max_bytes if policy is not None else DEFAULT_STAGE_ARTIFACT_MAX_BYTES
        )
        runs = self._load_runs(diagnostics, stage_artifact_max_bytes)
        runs.sort(key=lambda item: (item["updatedAt"], item["runId"]), reverse=True)
        from backend.services.governance_telemetry_service import GovernanceTelemetryService

        return {
            "schemaVersion": SNAPSHOT_SCHEMA,
            "generatedAt": generated_at,
            "summary": self._summary(runs),
            "runs": runs,
            "governanceTelemetry": GovernanceTelemetryService(
                self.project_root, runtime_root=self.runtime_root
            ).build_snapshot(
                runs=runs,
                diagnostics=diagnostics,
                hard_cap=stage_artifact_max_bytes,
            ),
            "retention": retention,
            "diagnostics": diagnostics,
        }

    def _safe_root(self, candidate: Path) -> Path:
        candidate = candidate.expanduser()
        if candidate.is_symlink():
            raise ValueError("runtime root must not be a symlink")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("runtime root must be inside project root") from exc
        return resolved

    def _load_runs(self, diagnostics: list[dict[str, str]], stage_artifact_max_bytes: int) -> list[dict[str, Any]]:
        if not self.runs_root.exists():
            return []
        if self.runs_root.is_symlink() or not self.runs_root.is_dir():
            self._add_diagnostic(diagnostics, self.runs_root, "runs root is not a regular directory", "invalid_run")
            return []

        runs = []
        for run_path in sorted(self.runs_root.iterdir(), key=lambda item: item.name):
            safe_run_id = self._safe_run_id(run_path.name)
            if safe_run_id is None:
                self._add_diagnostic(diagnostics, self.runs_root, "run artifact is invalid", "invalid_run")
                continue
            if run_path.is_symlink() or not run_path.is_dir():
                self._add_diagnostic(diagnostics, run_path, "run path is not a regular directory", "invalid_run", safe_run_id)
                continue
            try:
                manifest = WorkflowManifest.from_dict(
                    self._read_json(run_path / "manifest.json", run_path, stage_artifact_max_bytes)
                )
                status = WorkflowStatus.from_dict(
                    self._read_json(run_path / "status.json", run_path, stage_artifact_max_bytes)
                )
                if manifest.run_id != status.run_id or manifest.run_id != safe_run_id:
                    raise ValueError("runId does not match manifest, status, and directory")
                runs.append(self._compact_run(run_path, manifest, status, stage_artifact_max_bytes, diagnostics))
            except _UnsafeArtifactError:
                self._add_diagnostic(diagnostics, run_path, "stage artifact is unsafe", "unsafe_artifact", safe_run_id)
            except (_ArtifactError, OSError, ValueError, json.JSONDecodeError):
                self._add_diagnostic(diagnostics, run_path, "run artifact is invalid", "invalid_run", safe_run_id)
        return runs

    @staticmethod
    def _read_json(
        path: Path,
        container: Path,
        hard_cap: int,
        *,
        optional: bool = False,
    ) -> dict[str, Any] | None:
        details = AgentOperationsService._inspect_regular_artifact(path, container, hard_cap, optional=optional)
        if details is None:
            return None
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise _ArtifactError("artifact JSON is invalid") from exc
        if not isinstance(payload, dict):
            raise _ArtifactError("artifact must contain an object")
        return payload

    @staticmethod
    def _inspect_regular_artifact(
        path: Path,
        container: Path,
        hard_cap: int,
        *,
        optional: bool = False,
    ) -> os.stat_result | None:
        try:
            path.resolve(strict=False).relative_to(container.resolve(strict=False))
        except (OSError, RuntimeError, ValueError) as exc:
            raise _UnsafeArtifactError("artifact escapes its container") from exc
        try:
            details = path.lstat()
        except FileNotFoundError:
            if optional:
                return None
            raise _ArtifactError("artifact is missing") from None
        except OSError as exc:
            raise _ArtifactError("artifact cannot be inspected") from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise _UnsafeArtifactError("artifact is not a regular file")
        if details.st_size > hard_cap:
            raise _UnsafeArtifactError("artifact exceeds configured cap")
        return details

    def _compact_run(
        self,
        run_dir: Path,
        manifest: WorkflowManifest,
        status: WorkflowStatus,
        stage_artifact_max_bytes: int,
        diagnostics: list[dict[str, str]],
    ) -> dict[str, Any]:
        started = datetime.fromisoformat(status.started_at)
        ended = datetime.fromisoformat(status.completed_at or status.updated_at)
        stage_payloads = self._read_stage_payloads(run_dir, stage_artifact_max_bytes)
        documentation = self._read_documentation(run_dir, stage_artifact_max_bytes)
        event_durations = self._event_durations(run_dir, stage_artifact_max_bytes, manifest.run_id)
        archive_summary = self._read_stage(run_dir, "archive-summary.json", stage_artifact_max_bytes)
        archived = self._is_valid_archive_summary(archive_summary, manifest.run_id)
        if archive_summary is not None and not archived:
            self._add_diagnostic(
                diagnostics,
                run_dir,
                "archive summary is invalid",
                "invalid_archive_summary",
                manifest.run_id,
            )
        item = {
            "runId": manifest.run_id,
            "briefName": self._safe_brief_name(manifest.brief_path),
            "gitBranch": self._safe_git_branch(manifest.git_branch),
            "gitHeadShort": manifest.git_head[:8],
            "createdAt": manifest.created_at,
            "updatedAt": status.updated_at,
            "completedAt": status.completed_at,
            "stage": self._safe_stage(status.stage),
            "status": status.status,
            "message": self._safe_message(status.message),
            "errorCode": self._safe_message(status.error_code) if status.error_code is not None else None,
            "artifactBytes": status.artifact_bytes,
            "durationMs": round((ended - started).total_seconds() * 1000),
            "stages": self._stages(stage_payloads, event_durations),
            "findings": self._findings(stage_payloads["review"]),
            "verification": self._verification(stage_payloads["full_verification"]),
            "hermes": self._hermes(stage_payloads["hermes"]),
            "tokenUsage": self._token_usage(stage_payloads),
            "lunaRepairLoops": self._luna_repair_loops(stage_payloads["implementation"]),
            "documentation": documentation,
            "governanceGraph": self._governance_graph(run_dir, stage_artifact_max_bytes),
            "governanceGates": self._governance_gates(run_dir, stage_artifact_max_bytes),
            "canonicalEvidence": self.canonical_evidence_reader.read(run_dir, stage_artifact_max_bytes),
            "retentionState": "archived_summary" if archived else "complete",
        }
        return item

    @staticmethod
    def _luna_repair_loops(payload: dict[str, Any] | None) -> int | None:
        value = payload.get("repairLoopsUsed") if isinstance(payload, dict) else None
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100 else None

    def _governance_graph(self, run_dir: Path, hard_cap: int) -> dict[str, Any]:
        try:
            payload = self._read_json(
                run_dir / GOVERNANCE_GRAPH_FILE,
                run_dir,
                hard_cap,
                optional=True,
            )
            if payload is None:
                return {"status": "unavailable"}
            snapshot = GovernanceGraphSnapshot.from_dict(payload)
        except _UnsafeArtifactError:
            return {"status": "invalid", "diagnostics": [{"code": "unsafe_projection"}]}
        except (GovernanceGraphSchemaError, _ArtifactError, OSError, ValueError, json.JSONDecodeError):
            return {"status": "invalid", "diagnostics": [{"code": "invalid_projection"}]}
        return self._compact_governance_graph(snapshot)

    def _governance_gates(self, run_dir: Path, hard_cap: int) -> dict[str, dict[str, str | None]]:
        return {
            name: self._governance_gate(run_dir, hard_cap, filename, gate_id)
            for name, (filename, gate_id) in GOVERNANCE_GATE_FILES.items()
        }

    def _governance_gate(
        self, run_dir: Path, hard_cap: int, filename: str, expected_gate_id: str,
    ) -> dict[str, str | None]:
        try:
            payload = self._read_json(run_dir / filename, run_dir, hard_cap, optional=True)
            if payload is None:
                return self._compact_gate("unknown", "unknown", "missing", filename, None)
            gate = GovernanceGate.from_dict(payload)
            if gate.gate_id != expected_gate_id:
                raise GovernanceGraphSchemaError("gate ID is invalid")
            if gate.status in {"passed", "failed"}:
                return self._compact_gate(gate.status, "available", gate.reason_code, filename, gate.fingerprint)
            if gate.status == "blocked":
                return self._compact_gate(gate.status, "blocked", gate.reason_code, filename, gate.fingerprint)
            return self._compact_gate("unknown", "unknown", "not_finalized", filename, None)
        except (GovernanceGraphSchemaError, _ArtifactError, OSError, ValueError, json.JSONDecodeError):
            return self._compact_gate("invalid", "invalid", "invalid_gate", filename, None)

    @staticmethod
    def _compact_gate(
        state: str, status: str, reason: str | None, artifact: str, sha256: str | None,
    ) -> dict[str, str | None]:
        return {
            "state": state,
            "status": status,
            "reason": reason,
            "artifact": artifact,
            "sha256": sha256,
        }

    @staticmethod
    def _compact_governance_graph(snapshot: GovernanceGraphSnapshot) -> dict[str, Any]:
        evidence = []
        for node in snapshot.nodes:
            for reference in node.evidence_refs[:1]:
                evidence.append({
                    "nodeId": node.node_id,
                    "artifact": Path(reference.path).name,
                    "sha256": reference.sha256,
                    "status": reference.status,
                })
        return {
            "status": "available",
            "overallStatus": snapshot.overall_status,
            "freshness": snapshot.freshness["status"],
            "nodes": [
                {
                    "nodeId": node.node_id,
                    "status": node.status,
                    "reasonCode": node.reason_code,
                }
                for node in snapshot.nodes
            ],
            "blockers": [dict(item) for item in snapshot.blockers],
            "diagnostics": [dict(item) for item in snapshot.diagnostics],
            "evidence": evidence,
        }

    def _read_documentation(self, run_dir: Path, hard_cap: int) -> dict[str, Any]:
        payloads = {
            name: self._read_documentation_stage(run_dir, filename, hard_cap)
            for name, filename in DOCUMENTATION_FILES.items()
        }
        if not any(payload is not None for payload in payloads.values()):
            return {"status": "not_requested"}

        evidence = payloads["evidence"]
        proposal = payloads["proposal"]
        application = payloads["application"]
        if evidence is not None:
            DocumentationEvidence.from_dict(evidence)
        proposal_model = DocumentationProposal.from_dict(proposal) if proposal is not None else None
        application_model = DocumentationApplication.from_dict(application) if application is not None else None
        self._validate_documentation_preview(payloads["preview"])
        telemetry = self._validate_documentation_telemetry(payloads["telemetry"])

        proposal_count = len(proposal_model.proposals) if proposal_model is not None else telemetry.get("proposalCount", 0)
        applications = application_model.applications if application_model is not None else ()
        applied_count = sum(item["result"] == "applied" for item in applications)
        status = application_model.status if application_model is not None else (
            proposal_model.status if proposal_model is not None else telemetry.get("result", "requested")
        )
        updated_at = (
            application.get("generatedAt") if application is not None else None
        ) or telemetry.get("updatedAt") or (proposal.get("generatedAt") if proposal is not None else None)
        compact = {"status": status, "proposalCount": proposal_count}
        if application_model is not None:
            compact.update({
                "appliedTargetCount": applied_count,
                "pendingApprovalCount": max(proposal_count - applied_count, 0),
            })
        if updated_at is not None:
            compact["updatedAt"] = self._safe_timestamp(updated_at)
        return compact

    @staticmethod
    def _read_documentation_stage(run_dir: Path, filename: str, hard_cap: int) -> dict[str, Any] | None:
        if filename not in DOCUMENTATION_FILES.values():
            raise _ArtifactError("documentation artifact name is not allowed")
        return AgentOperationsService._read_json(run_dir / filename, run_dir, hard_cap, optional=True)

    @staticmethod
    def _validate_documentation_preview(payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        if set(payload) != {"status", "items", "warnings"}:
            raise _ArtifactError("documentation preview schema is invalid")
        if payload["status"] not in {"preview_ready", "blocked"}:
            raise _ArtifactError("documentation preview schema is invalid")
        if not isinstance(payload["items"], list) or not isinstance(payload["warnings"], list):
            raise _ArtifactError("documentation preview schema is invalid")

    @staticmethod
    def _validate_documentation_telemetry(payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        allowed = {"schemaVersion", "runId", "documentationFingerprint", "proposalCount", "result", "updatedAt"}
        if payload.get("schemaVersion") != "documentation-telemetry-v1" or not set(payload) <= allowed:
            raise _ArtifactError("documentation telemetry schema is invalid")
        if not isinstance(payload.get("proposalCount"), int) or isinstance(payload["proposalCount"], bool):
            raise _ArtifactError("documentation telemetry schema is invalid")
        if not isinstance(payload.get("result"), str):
            raise _ArtifactError("documentation telemetry schema is invalid")
        return payload

    @staticmethod
    def _safe_timestamp(value: Any) -> str:
        if not isinstance(value, str):
            raise _ArtifactError("documentation timestamp is invalid")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise _ArtifactError("documentation timestamp is invalid") from exc
        if parsed.tzinfo is None:
            raise _ArtifactError("documentation timestamp is invalid")
        return value

    @staticmethod
    def _is_valid_archive_summary(payload: dict[str, Any] | None, run_id: str) -> bool:
        return (
            payload is not None
            and payload.get("schemaVersion") == ARCHIVE_SUMMARY_SCHEMA
            and payload.get("runId") == run_id
        )

    @staticmethod
    def _read_stage(run_dir: Path, filename: str, hard_cap: int) -> dict[str, Any] | None:
        if filename not in set(STAGE_FILES.values()) | {"archive-summary.json"}:
            raise _ArtifactError("artifact name is not allowed")
        path = run_dir / filename
        return AgentOperationsService._read_json(path, run_dir, hard_cap, optional=True)

    def _read_stage_payloads(self, run_dir: Path, hard_cap: int) -> dict[str, dict[str, Any] | None]:
        payloads = {
            name: self._read_stage(run_dir, filename, hard_cap)
            for name, filename in STAGE_FILES.items()
        }
        for stage, payload in payloads.items():
            self._validate_stage_payload(stage, payload)
        return payloads

    @staticmethod
    def _validate_stage_payload(stage: str, payload: dict[str, Any] | None) -> None:
        if payload is None:
            return
        schema = payload.get("schemaVersion")
        if stage == "context":
            try:
                if schema == "context-evidence-v1":
                    context_bundle_from_payload(payload)
                elif schema == "context-summary-v1":
                    validate_context_summary(payload)
                else:
                    raise ValueError("context schema is invalid")
            except (TypeError, ValueError) as exc:
                raise _ArtifactError("context artifact schema is invalid") from exc
            return
        if stage == "implementation":
            if not AgentOperationsService._valid_implementation_report(payload):
                raise _ArtifactError("implementation artifact schema is invalid")
            return
        if stage == "review":
            if not AgentOperationsService._valid_review_report(payload):
                raise _ArtifactError("review artifact schema is invalid")
            return
        if schema is not None:
            raise _ArtifactError(f"{stage} artifact schema is invalid")
        if stage == "targeted_verification":
            if not AgentOperationsService._valid_commands(payload.get("commands")):
                raise _ArtifactError("targeted verification artifact is invalid")
            return
        if stage == "full_verification" and not AgentOperationsService._valid_full_verification(payload):
            raise _ArtifactError("full verification artifact is invalid")
        if stage == "hermes" and not AgentOperationsService._valid_hermes(payload):
            raise _ArtifactError("Hermes artifact is invalid")

    @staticmethod
    def _valid_implementation_report(payload: dict[str, Any]) -> bool:
        required = {
            "schemaVersion", "status", "taskId", "contractFingerprint", "startHead", "endHead",
            "changedFiles", "diffStat", "redEvidence", "greenEvidence", "repairLoopsUsed",
            "testFilesChanged", "productionFilesChanged", "findings",
        }
        if not required.issubset(payload) or set(payload) - (required | {"durationMs", "usage"}):
            return False
        if payload.get("schemaVersion") != "implementation-run-report-v1" or payload.get("status") not in {
            "completed", "changes_required", "blocked_invalid_contract", "blocked_dirty_worktree",
            "blocked_wrong_branch", "blocked_head_mismatch", "blocked_scope", "blocked_high_risk",
            "blocked_diff_limit", "validation_failed", "context_overflow", "invalid_agent_output", "runtime_error",
        }:
            return False
        if not all(isinstance(payload.get(field), str) and payload[field] for field in (
            "taskId", "contractFingerprint", "startHead", "endHead",
        )):
            return False
        if not all(isinstance(payload.get(field), list) for field in (
            "changedFiles", "redEvidence", "greenEvidence", "testFilesChanged", "productionFilesChanged", "findings",
        )):
            return False
        if not all(isinstance(path, str) for field in ("changedFiles", "testFilesChanged", "productionFilesChanged") for path in payload[field]):
            return False
        diff_stat = payload.get("diffStat")
        return (
            isinstance(diff_stat, dict)
            and set(diff_stat) == {"files", "lines"}
            and all(AgentOperationsService._nonnegative_int(diff_stat.get(key)) for key in diff_stat)
            and AgentOperationsService._nonnegative_int(payload.get("repairLoopsUsed"))
        )

    @staticmethod
    def _valid_review_report(payload: dict[str, Any]) -> bool:
        required = {
            "schemaVersion", "verdict", "findings", "requirementCoverage", "testCoverage",
            "baselineRisk", "residualRisk", "hermesRequiredChecks", "reviewFingerprint",
        }
        if not required.issubset(payload) or set(payload) - (required | {"durationMs", "usage"}):
            return False
        if payload.get("schemaVersion") != "review-report-v1" or payload.get("verdict") not in {
            "pass", "changes_required", "blocked", "context_overflow", "invalid_bundle",
        }:
            return False
        if not isinstance(payload.get("baselineRisk"), str) or not isinstance(payload.get("reviewFingerprint"), str):
            return False
        for field in ("requirementCoverage", "testCoverage", "residualRisk", "hermesRequiredChecks"):
            if not isinstance(payload.get(field), list) or not all(isinstance(item, str) for item in payload[field]):
                return False
        findings = payload.get("findings")
        if not isinstance(findings, list):
            return False
        finding_keys = {"severity", "file", "line", "rule", "evidence", "impact", "recommendedAction"}
        for finding in findings:
            if not isinstance(finding, dict) or set(finding) != finding_keys:
                return False
            if finding.get("severity") not in {"critical", "high", "medium", "low"}:
                return False
            if not isinstance(finding.get("line"), int) or isinstance(finding["line"], bool) or finding["line"] < 1:
                return False
            if not all(isinstance(finding.get(field), str) and finding[field] for field in (
                "file", "rule", "evidence", "impact", "recommendedAction",
            )):
                return False
        if payload["verdict"] != "pass":
            return True
        return not findings and all(payload[field] for field in (
            "requirementCoverage", "testCoverage", "residualRisk", "hermesRequiredChecks",
        ))

    @staticmethod
    def _valid_commands(commands: Any) -> bool:
        if not isinstance(commands, list) or not commands:
            return False
        for command in commands:
            if not isinstance(command, dict):
                return False
            if not isinstance(command.get("label"), str) or not command["label"]:
                return False
            argv = command.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
                return False
            if not AgentOperationsService._nonnegative_int(command.get("exitCode")):
                return False
            if not isinstance(command.get("stdoutTail"), str) or not isinstance(command.get("stderrTail"), str):
                return False
        return True

    @staticmethod
    def _valid_full_verification(payload: dict[str, Any]) -> bool:
        if not payload or not set(payload).issubset(_FULL_VERIFICATION_KEYS):
            return False
        if "fullPytest" in payload:
            full_pytest = payload["fullPytest"]
            if not isinstance(full_pytest, dict) or set(full_pytest) != _FULL_PYTEST_KEYS:
                return False
            if not (
                isinstance(full_pytest["exitCode"], int)
                and not isinstance(full_pytest["exitCode"], bool)
                and isinstance(full_pytest["stdoutTail"], str)
                and isinstance(full_pytest["stderrTail"], str)
                and isinstance(full_pytest["payload"], dict)
            ):
                return False
        if "acceptance" not in payload:
            return True
        acceptance = payload["acceptance"]
        return isinstance(acceptance, dict) and isinstance(acceptance.get("status"), str)

    @staticmethod
    def _valid_hermes(payload: dict[str, Any]) -> bool:
        return payload.get("overallStatus") in {"pass", "fail", "warning", "blocked"}

    def _event_durations(self, run_dir: Path, hard_cap: int, run_id: str) -> dict[str, int]:
        path = run_dir / "events.jsonl"
        details = self._inspect_regular_artifact(path, run_dir, hard_cap, optional=True)
        if details is None:
            return {}
        valid_events = self._read_events(path, details.st_size, run_id, min(MAX_EVENT_SCAN_BYTES, hard_cap))
        stage_points: dict[str, list[datetime]] = {}
        for event in valid_events:
            stage = event.metadata.get("stage")
            if not isinstance(stage, str) or stage not in STAGE_FILES:
                continue
            stage_points.setdefault(stage, []).append(datetime.fromisoformat(event.occurred_at))
        durations = {}
        for stage, points in stage_points.items():
            if len(points) < 2:
                continue
            duration = round((points[-1] - points[0]).total_seconds() * 1000)
            if duration >= 0:
                durations[stage] = duration
        return durations

    @staticmethod
    def _read_events(path: Path, file_size: int, run_id: str, scan_byte_budget: int) -> deque[WorkflowEvent]:
        valid_events: deque[WorkflowEvent] = deque()
        pending = b""
        skipping_oversize_line = False
        scanned_bytes = 0
        scanned_lines = 0

        def collect(line: bytes) -> None:
            nonlocal scanned_lines
            scanned_lines += 1
            if not line or len(line) > MAX_EVENT_LINE_BYTES:
                return
            try:
                payload = json.loads(line.rstrip(b"\r"))
                event = WorkflowEvent.from_dict(payload)
            except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
                return
            if event.run_id == run_id:
                valid_events.appendleft(event)

        try:
            with path.open("rb") as handle:
                position = file_size
                while (
                    position > 0
                    and len(valid_events) < MAX_EVENT_LINES
                    and scanned_bytes < scan_byte_budget
                    and scanned_lines < MAX_EVENT_SCAN_LINES
                ):
                    read_size = min(EVENT_READ_CHUNK_BYTES, position, scan_byte_budget - scanned_bytes)
                    position -= read_size
                    handle.seek(position)
                    chunk = handle.read(read_size)
                    if len(chunk) != read_size:
                        raise _ArtifactError("events cannot be read")
                    scanned_bytes += len(chunk)

                    if skipping_oversize_line:
                        parts = chunk.split(b"\n")
                        if len(parts) == 1:
                            continue
                        pending = parts[0]
                        for line in reversed(parts[1:-1]):
                            collect(line)
                            if len(valid_events) == MAX_EVENT_LINES or scanned_lines == MAX_EVENT_SCAN_LINES:
                                break
                        skipping_oversize_line = False
                    else:
                        parts = (chunk + pending).split(b"\n")
                        pending = parts[0]
                        if len(pending) > MAX_EVENT_LINE_BYTES:
                            pending = b""
                            skipping_oversize_line = True
                        for line in reversed(parts[1:]):
                            collect(line)
                            if len(valid_events) == MAX_EVENT_LINES or scanned_lines == MAX_EVENT_SCAN_LINES:
                                break

                if (
                    position == 0
                    and not skipping_oversize_line
                    and len(valid_events) < MAX_EVENT_LINES
                    and scanned_lines < MAX_EVENT_SCAN_LINES
                ):
                    collect(pending)
        except (OSError, UnicodeError) as exc:
            raise _ArtifactError("events cannot be read") from exc
        return valid_events

    @staticmethod
    def _stages(stage_payloads: dict[str, dict[str, Any] | None], event_durations: dict[str, int]) -> dict[str, dict[str, Any]]:
        stages = {}
        for name, payload in stage_payloads.items():
            duration = payload.get("durationMs") if payload is not None else None
            if not AgentOperationsService._nonnegative_int(duration):
                duration = event_durations.get(name)
            stages[name] = {"available": payload is not None, "durationMs": duration}
        return stages

    @staticmethod
    def _findings(review: dict[str, Any] | None) -> dict[str, Any]:
        findings = review.get("findings") if review is not None else None
        items = []
        if isinstance(findings, list):
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                severity = finding.get("severity")
                if severity not in SEVERITY_ORDER:
                    continue
                code = finding.get("code", finding.get("rule"))
                if not isinstance(code, str) or not code.strip():
                    code = "review_finding"
                message = finding.get("message", finding.get("evidence"))
                items.append({
                    "severity": severity,
                    "code": AgentOperationsService._safe_message(code),
                    "message": AgentOperationsService._safe_message(message),
                })
                if len(items) == MAX_FINDINGS:
                    break
        items.sort(key=lambda item: SEVERITY_ORDER[item["severity"]], reverse=True)
        highest = items[0]["severity"] if items else None
        return {"count": len(items), "highestSeverity": highest, "items": items}

    @staticmethod
    def _verification(payload: dict[str, Any] | None) -> dict[str, str]:
        if payload is None:
            return {"status": "unavailable"}
        full_pytest = payload.get("fullPytest")
        acceptance = payload.get("acceptance")
        if isinstance(full_pytest, dict) and full_pytest.get("exitCode") != 0:
            return {"status": "fail"}
        if isinstance(acceptance, dict) and acceptance.get("status") in {"failed", "critical", "fail"}:
            return {"status": "fail"}
        if (
            isinstance(full_pytest, dict)
            and full_pytest.get("exitCode") == 0
            and isinstance(acceptance, dict)
            and acceptance.get("status") == "passed"
        ):
            return {"status": "pass"}
        return {"status": "blocked"}

    @staticmethod
    def _hermes(payload: dict[str, Any] | None) -> dict[str, str]:
        status = payload.get("overallStatus") if payload is not None else None
        return {
            "status": status
            if isinstance(status, str) and status in {"pass", "fail", "warning", "blocked"}
            else "unavailable"
        }

    @staticmethod
    def _token_usage(stage_payloads: dict[str, dict[str, Any] | None]) -> dict[str, int] | None:
        input_tokens = 0
        output_tokens = 0
        valid_usage = False
        for payload in stage_payloads.values():
            usage = payload.get("usage") if payload is not None else None
            if not isinstance(usage, dict):
                continue
            input_value = usage.get("inputTokens")
            output_value = usage.get("outputTokens")
            total_value = usage.get("totalTokens")
            if not (
                AgentOperationsService._nonnegative_int(input_value)
                and AgentOperationsService._nonnegative_int(output_value)
            ):
                continue
            if total_value is not None and (
                not AgentOperationsService._nonnegative_int(total_value)
                or total_value != input_value + output_value
            ):
                continue
            input_tokens += input_value
            output_tokens += output_value
            valid_usage = True
        if not valid_usage:
            return None
        return {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
        }

    @staticmethod
    def _nonnegative_int(value: Any) -> bool:
        return not isinstance(value, bool) and isinstance(value, int) and value >= 0

    @staticmethod
    def _summary(runs: list[dict[str, Any]]) -> dict[str, int]:
        statuses = [run["status"] for run in runs]
        return {
            "runCount": len(runs),
            "activeCount": sum(status not in {"completed", "changes_required", "blocked", "failed"} for status in statuses),
            "awaitingAuthorizationCount": statuses.count("awaiting_authorization"),
            "completedCount": statuses.count("completed"),
            "changesRequiredCount": statuses.count("changes_required"),
            "blockedCount": statuses.count("blocked"),
            "failedCount": statuses.count("failed"),
        }

    def _retention(self, diagnostics: list[dict[str, str]]) -> tuple[dict[str, int] | dict[str, str], RetentionPolicy | None]:
        try:
            payload = self._read_json(
                self.retention_path,
                self.project_root,
                DEFAULT_STAGE_ARTIFACT_MAX_BYTES,
            )
            policy = RetentionPolicy.from_dict(payload)
        except (_ArtifactError, OSError, ValueError, json.JSONDecodeError):
            self._add_diagnostic(diagnostics, self.retention_path, "retention policy is invalid", "retention_config_invalid")
            return {"status": "unavailable"}, None
        return {
            "retainDays": policy.retain_days,
            "retainLatestTerminalRuns": policy.retain_latest_terminal_runs,
            "stageArtifactMaxBytes": policy.stage_artifact_max_bytes,
            "runArtifactSoftCapBytes": policy.run_artifact_soft_cap_bytes,
            "commandOutputTailCharacters": policy.command_output_tail_characters,
        }, policy

    def _add_diagnostic(
        self,
        diagnostics: list[dict[str, str]],
        path: Path,
        reason: str,
        code: str,
        run_id: str | None = None,
    ) -> None:
        if len(diagnostics) >= MAX_DIAGNOSTICS:
            return
        diagnostic = self._diagnostic(path, reason)
        diagnostic["code"] = code
        safe_run_id = self._safe_run_id(run_id)
        if safe_run_id is not None:
            diagnostic["runId"] = safe_run_id
        diagnostics.append(diagnostic)

    def _diagnostic(self, path: Path, reason: str) -> dict[str, str]:
        try:
            display_path = str(path.resolve(strict=False).relative_to(self.project_root))
        except ValueError:
            display_path = path.name
        return {"path": display_path, "reason": self._safe_reason(reason)}

    @staticmethod
    def _safe_reason(reason: str) -> str:
        if reason in _SAFE_DIAGNOSTIC_REASONS:
            return reason
        return "operation artifact is invalid"

    @staticmethod
    def _safe_message(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return "finding detail unavailable"
        message = " ".join(value.split())[:MAX_SAFE_MESSAGE_CHARS]
        if "/" in message or "\\" in message or _URI_SCHEME_PATTERN.search(message) or _SENSITIVE_MESSAGE_PATTERN.search(message):
            return "finding detail unavailable"
        return message

    @staticmethod
    def _safe_run_id(value: Any) -> str | None:
        if isinstance(value, str) and _SAFE_RUN_ID_PATTERN.fullmatch(value):
            return value
        return None

    @staticmethod
    def _safe_brief_name(value: Any) -> str:
        if not isinstance(value, str) or not _SAFE_BRIEF_PATH_PATTERN.fullmatch(value):
            return SAFE_VALUE_UNAVAILABLE
        name = Path(value).name
        return name if _SAFE_BRIEF_NAME_PATTERN.fullmatch(name) else SAFE_VALUE_UNAVAILABLE

    @staticmethod
    def _safe_git_branch(value: Any) -> str:
        if not isinstance(value, str) or not _SAFE_GIT_BRANCH_PATTERN.fullmatch(value):
            return SAFE_VALUE_UNAVAILABLE
        if value.startswith("/") or value.endswith("/") or "@{" in value or ".." in value:
            return SAFE_VALUE_UNAVAILABLE
        forbidden = set("\\ :?*[")
        if any(character in forbidden or ord(character) < 32 or ord(character) == 127 for character in value):
            return SAFE_VALUE_UNAVAILABLE
        segments = value.split("/")
        if any(
            not segment
            or segment in {".", ".."}
            or segment.startswith(".")
            or segment.endswith(".")
            or segment.endswith(".lock")
            for segment in segments
        ):
            return SAFE_VALUE_UNAVAILABLE
        if all(segments):
            return value
        return SAFE_VALUE_UNAVAILABLE

    @staticmethod
    def _safe_stage(value: Any) -> str:
        if isinstance(value, str) and value in _ALLOWED_STATUS_STAGES:
            return value
        return SAFE_VALUE_UNAVAILABLE
