from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from backend.agents.evidence_models import canonical_fingerprint, load_json_config
from backend.agents.memory_hub_integration_models import MemoryHubIntegrationEvidence
from backend.agents.safe_repo_file import open_repo_file_nofollow


_CONTRACT_KEYS = {
    "schemaVersion", "taskId", "planPath", "planFingerprint", "objective",
    "approvedBaseSha", "approvedWorktree", "allowedWritePaths", "validationCommands",
    "riskSurfaces", "maxChangedFiles", "maxDiffLines", "maxRepairLoops",
}
_OPTIONAL_CONTRACT_KEYS = {
    "taskType", "redCommands", "greenCommands", "approvedTestBehaviorChanges",
    "memoryContextAllowed", "expectedMemoryEvidenceFingerprint", "carryForwardFiles",
}
_TASK_TYPES = {"behavior", "refactor", "test", "documentation", "configuration"}
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
class CarryForwardFile:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        parsed = PurePosixPath(self.path) if isinstance(self.path, str) else None
        if (
            parsed is None or not self.path or "\\" in self.path or "\x00" in self.path
            or parsed.is_absolute() or str(parsed) != self.path
            or any(part in {"", ".", ".."} for part in parsed.parts)
        ):
            raise ValueError("carryForwardFiles path is invalid")
        if (
            not isinstance(self.sha256, str) or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ValueError("carryForwardFiles sha256 is invalid")

    @classmethod
    def from_dict(cls, payload: object) -> "CarryForwardFile":
        if not isinstance(payload, dict) or set(payload) != {"path", "sha256"}:
            raise ValueError("carryForwardFiles entry schema is invalid")
        try:
            return cls(path=payload["path"], sha256=payload["sha256"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"carryForwardFiles entry is invalid: {exc}") from exc

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


def _require_carry_forward_files(payload: dict[str, Any]) -> tuple[CarryForwardFile, ...]:
    raw = payload.get("carryForwardFiles", [])
    if not isinstance(raw, list):
        raise ValueError("carryForwardFiles must be a list")
    entries = tuple(CarryForwardFile.from_dict(item) for item in raw)
    if len(entries) > 50:
        raise ValueError("carryForwardFiles exceeds the bounded entry limit")
    paths = [item.path for item in entries]
    if len(paths) != len(set(paths)):
        raise ValueError("carryForwardFiles contains duplicate paths")
    write_paths = payload.get("allowedWritePaths", [])
    for entry in entries:
        entry_parts = PurePosixPath(entry.path).parts
        for write_path in write_paths:
            write_parts = PurePosixPath(write_path).parts
            if entry_parts[:len(write_parts)] == write_parts or write_parts[:len(entry_parts)] == entry_parts:
                raise ValueError("carryForwardFiles must not overlap allowedWritePaths")
    return entries


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
    task_type: str = "refactor"
    red_commands: tuple[str, ...] = ()
    green_commands: tuple[str, ...] = ()
    approved_test_behavior_changes: tuple[str, ...] = ()
    memory_context_allowed: bool = False
    expected_memory_evidence_fingerprint: str | None = None
    carry_forward_files: tuple[CarryForwardFile, ...] = ()

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
        if self.task_type not in _TASK_TYPES:
            raise ValueError("taskType is invalid")
        for key, value in (
            ("redCommands", self.red_commands),
            ("greenCommands", self.green_commands),
            ("approvedTestBehaviorChanges", self.approved_test_behavior_changes),
        ):
            if not all(isinstance(item, str) and item for item in value):
                raise ValueError(f"{key} must be a list of strings")
        if not isinstance(self.memory_context_allowed, bool):
            raise ValueError("memoryContextAllowed must be boolean")
        if self.expected_memory_evidence_fingerprint is not None and (
            not isinstance(self.expected_memory_evidence_fingerprint, str)
            or len(self.expected_memory_evidence_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.expected_memory_evidence_fingerprint)
        ):
            raise ValueError("expectedMemoryEvidenceFingerprint is invalid")
        if not self.memory_context_allowed and self.expected_memory_evidence_fingerprint is not None:
            raise ValueError("expectedMemoryEvidenceFingerprint requires memoryContextAllowed")
        paths = [item.path for item in self.carry_forward_files]
        if len(paths) != len(set(paths)):
            raise ValueError("carryForwardFiles contains duplicate paths")
        for entry in self.carry_forward_files:
            entry_parts = PurePosixPath(entry.path).parts
            for write_path in self.allowed_write_paths:
                write_parts = PurePosixPath(write_path).parts
                if entry_parts[:len(write_parts)] == write_parts or write_parts[:len(entry_parts)] == entry_parts:
                    raise ValueError("carryForwardFiles must not overlap allowedWritePaths")
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
        if not _CONTRACT_KEYS <= set(payload) <= (_CONTRACT_KEYS | _OPTIONAL_CONTRACT_KEYS):
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
            task_type=payload.get("taskType", "refactor"),
            red_commands=_require_string_list(payload, "redCommands") if "redCommands" in payload else (),
            green_commands=_require_string_list(payload, "greenCommands") if "greenCommands" in payload else (),
            approved_test_behavior_changes=(
                _require_string_list(payload, "approvedTestBehaviorChanges")
                if "approvedTestBehaviorChanges" in payload else ()
            ),
            memory_context_allowed=payload.get("memoryContextAllowed", False),
            expected_memory_evidence_fingerprint=payload.get("expectedMemoryEvidenceFingerprint"),
            carry_forward_files=_require_carry_forward_files(payload),
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
            "taskType": self.task_type,
            "redCommands": list(self.red_commands),
            "greenCommands": list(self.green_commands),
            "approvedTestBehaviorChanges": list(self.approved_test_behavior_changes),
            **({
                "memoryContextAllowed": True,
                "expectedMemoryEvidenceFingerprint": self.expected_memory_evidence_fingerprint,
            } if self.memory_context_allowed else {}),
            **({
                "carryForwardFiles": [item.to_dict() for item in self.carry_forward_files],
            } if self.carry_forward_files else {}),
        }

    @property
    def effective_green_commands(self) -> tuple[str, ...]:
        return self.green_commands or self.validation_commands

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.to_dict())


def build_implementation_memory_context(
    contract: ImplementationTaskContract,
    payload: object,
) -> dict[str, Any] | None:
    """Return only authorized, precomputed memory metadata; never query a provider."""
    if not contract.memory_context_allowed:
        return None
    try:
        evidence = MemoryHubIntegrationEvidence.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise PermissionError("memory evidence is invalid") from exc
    if evidence.status != "ready" or evidence.consumer_id != "context-agent":
        raise PermissionError("memory evidence is not ready for implementation")
    if evidence.integration_mode != "direct_query":
        raise PermissionError("memory evidence integration mode is not authorized")
    if evidence.evidence_fingerprint != contract.expected_memory_evidence_fingerprint:
        raise PermissionError("memory evidence fingerprint does not match approved contract")
    return {
        "schemaVersion": "memory-hub-agent-implementation-context-v1",
        "status": "ready",
        "authority": "non_authoritative_memory",
        "evidenceFingerprint": evidence.evidence_fingerprint,
        "hintCount": evidence.hint_count,
        "sourceRefs": list(evidence.source_refs),
    }


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
    repair_loops_used: int
    test_files_changed: tuple[str, ...]
    production_files_changed: tuple[str, ...]
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
            "repairLoopsUsed": self.repair_loops_used,
            "testFilesChanged": list(self.test_files_changed),
            "productionFilesChanged": list(self.production_files_changed),
            "findings": list(self.findings),
        }


def load_implementation_policy(project_root: Path) -> dict[str, Any]:
    return load_json_config(project_root, "agent_config/implementation_policies.json")


def validate_carry_forward_approval(
    contract: ImplementationTaskContract,
    *,
    project_root: Path,
    brief_path: str,
    expected_brief_fingerprint: str,
) -> None:
    """Bind hash-pinned Review omissions to the exact source-sealed brief manifest."""
    if not contract.carry_forward_files:
        return
    if contract.plan_path != brief_path:
        raise ValueError("carry-forward approval is not bound to the sealed brief path")
    root = Path(project_root).resolve()
    relative = PurePosixPath(contract.plan_path)
    if (
        relative.is_absolute()
        or "\\" in contract.plan_path
        or "\x00" in contract.plan_path
        or str(relative) != contract.plan_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("carry-forward approval brief path is unsafe")
    try:
        with open_repo_file_nofollow(root, contract.plan_path) as descriptor:
            before = os.fstat(descriptor)
            chunks = []
            size = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            after = os.fstat(descriptor)
            identity_before = (
                before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                before.st_size, before.st_mtime_ns, before.st_ctime_ns,
            )
            identity_after = (
                after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            )
            if identity_before != identity_after or size != before.st_size:
                raise ValueError("carry-forward approval brief changed while being read")
            raw = b"".join(chunks)
    except (OSError, ValueError) as exc:
        raise ValueError("carry-forward approval brief is unavailable or unsafe") from exc
    actual_fingerprint = hashlib.sha256(raw).hexdigest()
    if (
        actual_fingerprint != contract.plan_fingerprint
        or actual_fingerprint != expected_brief_fingerprint
    ):
        raise ValueError("carry-forward approval brief fingerprint does not match the source seal")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("carry-forward approval brief is not UTF-8") from exc
    start_marker = "<!-- review-carry-forward-v1:start -->"
    end_marker = "<!-- review-carry-forward-v1:end -->"
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise ValueError("carry-forward approval manifest markers are missing or duplicated")
    manifest_text = text.split(start_marker, 1)[1].split(end_marker, 1)[0].strip()
    if manifest_text.startswith("```json") and manifest_text.endswith("```"):
        manifest_text = manifest_text[len("```json"): -len("```")].strip()
    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        raise ValueError("carry-forward approval manifest is malformed") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schemaVersion", "files"}:
        raise ValueError("carry-forward approval manifest schema is invalid")
    if manifest["schemaVersion"] != "review-carry-forward-v1" or not isinstance(manifest["files"], list):
        raise ValueError("carry-forward approval manifest schema is invalid")
    approved = tuple(CarryForwardFile.from_dict(item) for item in manifest["files"])
    if tuple(item.to_dict() for item in approved) != tuple(
        item.to_dict() for item in contract.carry_forward_files
    ):
        raise ValueError("carry-forward approval list does not match the sealed brief")
