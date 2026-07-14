from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.agents.evidence_models import canonical_fingerprint, load_json_config


_CONTRACT_KEYS = {
    "schemaVersion", "taskId", "planPath", "planFingerprint", "objective",
    "approvedBaseSha", "approvedWorktree", "allowedWritePaths", "validationCommands",
    "riskSurfaces", "maxChangedFiles", "maxDiffLines", "maxRepairLoops",
}
_CONTRACT_SCHEMA = "implementation-task-v1"
_REPORT_SCHEMA = "implementation-run-report-v1"


def _require_string(payload: dict[str, Any], key: str, *, non_empty: bool = True) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (non_empty and not value.strip()):
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _require_string_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return tuple(value)


def _require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


@dataclass(frozen=True)
class ImplementationTaskContract:
    schema_version: str
    task_id: str
    plan_path: str
    plan_fingerprint: str
    objective: str
    approved_base_sha: str
    approved_worktree: str
    allowed_write_paths: tuple[str, ...]
    validation_commands: tuple[str, ...]
    risk_surfaces: tuple[str, ...] = ()
    max_changed_files: int = 8
    max_diff_lines: int = 800
    max_repair_loops: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != _CONTRACT_SCHEMA:
            raise ValueError("schemaVersion must be implementation-task-v1")
        for key, value in (
            ("taskId", self.task_id), ("planPath", self.plan_path),
            ("planFingerprint", self.plan_fingerprint), ("objective", self.objective),
            ("approvedBaseSha", self.approved_base_sha), ("approvedWorktree", self.approved_worktree),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty string")
        if any(separator in self.task_id for separator in (",", " ")):
            raise ValueError("taskId must identify exactly one task")
        if not self.allowed_write_paths or not all(self.allowed_write_paths):
            raise ValueError("allowedWritePaths must contain at least one path")
        if not self.validation_commands:
            raise ValueError("validationCommands must contain at least one command ID")
        for key, value in (
            ("maxChangedFiles", self.max_changed_files),
            ("maxDiffLines", self.max_diff_lines),
            ("maxRepairLoops", self.max_repair_loops),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{key} must be a positive integer")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ImplementationTaskContract":
        if not isinstance(payload, dict):
            raise ValueError("Implementation task contract must be an object")
        if set(payload) != _CONTRACT_KEYS:
            raise ValueError("Implementation task contract schema keys are invalid")
        schema_version = _require_string(payload, "schemaVersion")
        if schema_version != _CONTRACT_SCHEMA:
            raise ValueError("schemaVersion must be implementation-task-v1")
        return cls(
            schema_version=schema_version,
            task_id=_require_string(payload, "taskId"),
            plan_path=_require_string(payload, "planPath"),
            plan_fingerprint=_require_string(payload, "planFingerprint"),
            objective=_require_string(payload, "objective"),
            approved_base_sha=_require_string(payload, "approvedBaseSha"),
            approved_worktree=_require_string(payload, "approvedWorktree"),
            allowed_write_paths=_require_string_list(payload, "allowedWritePaths"),
            validation_commands=_require_string_list(payload, "validationCommands"),
            risk_surfaces=_require_string_list(payload, "riskSurfaces"),
            max_changed_files=_require_positive_int(payload, "maxChangedFiles"),
            max_diff_lines=_require_positive_int(payload, "maxDiffLines"),
            max_repair_loops=_require_positive_int(payload, "maxRepairLoops"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "taskId": self.task_id,
            "planPath": self.plan_path,
            "planFingerprint": self.plan_fingerprint,
            "objective": self.objective,
            "approvedBaseSha": self.approved_base_sha,
            "approvedWorktree": self.approved_worktree,
            "allowedWritePaths": list(self.allowed_write_paths),
            "validationCommands": list(self.validation_commands),
            "riskSurfaces": list(self.risk_surfaces),
            "maxChangedFiles": self.max_changed_files,
            "maxDiffLines": self.max_diff_lines,
            "maxRepairLoops": self.max_repair_loops,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.to_dict())


@dataclass(frozen=True)
class ValidationResult:
    command_id: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "commandId": self.command_id,
            "argv": list(self.argv),
            "exitCode": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "durationMs": self.duration_ms,
            "timedOut": self.timed_out,
        }


@dataclass(frozen=True)
class ImplementationRunReport:
    schema_version: str
    status: str
    task_id: str
    contract_fingerprint: str
    start_head: str
    end_head: str
    changed_files: tuple[str, ...]
    diff_stat: dict[str, int]
    red_evidence: tuple[ValidationResult, ...]
    green_evidence: tuple[ValidationResult, ...]
    findings: tuple[dict, ...]

    def __post_init__(self) -> None:
        if self.schema_version != _REPORT_SCHEMA:
            raise ValueError("schemaVersion must be implementation-run-report-v1")
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("status must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "taskId": self.task_id,
            "contractFingerprint": self.contract_fingerprint,
            "startHead": self.start_head,
            "endHead": self.end_head,
            "changedFiles": list(self.changed_files),
            "diffStat": self.diff_stat,
            "redEvidence": [item.to_dict() for item in self.red_evidence],
            "greenEvidence": [item.to_dict() for item in self.green_evidence],
            "findings": list(self.findings),
        }


def load_implementation_policy(project_root: Path) -> dict[str, Any]:
    return load_json_config(project_root, "agent_config/implementation_policies.json")
