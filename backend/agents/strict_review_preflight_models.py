from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from backend.agents.evidence_models import canonical_fingerprint


PreflightStatus = Literal[
    "ready", "blocked", "invalid_evidence", "verification_failed", "degraded"
]

_SCHEMA = "strict-review-preflight-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = {"ready", "blocked", "invalid_evidence", "verification_failed", "degraded"}
_COVERAGE_KEYS = {
    "targetedTests", "compileStatic", "diffCheck", "runnerCapability",
    "contextCompatibility", "governanceLineage", "memoryReadiness",
}
_RESULT_KEYS = {
    "schemaVersion", "status", "sessionId", "sourceFingerprint", "bundleFingerprint",
    "changedFiles", "coverage", "generatedEvidence", "verificationPath",
    "diagnostics", "createdAt",
}
_MAX_DIAGNOSTIC_CHARS = 512


def build_preflight_fingerprint(payload: dict) -> str:
    """Build a stable fingerprint from a public preflight payload."""
    return canonical_fingerprint({key: value for key, value in payload.items() if key != "preflightFingerprint"})


def _validate_sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} fingerprint is invalid")
    return value


def _validate_string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return list(value)


@dataclass(frozen=True)
class CoverageResult:
    targeted_tests: str
    compile_static: str
    diff_check: str
    runner_capability: str
    context_compatibility: str
    governance_lineage: str
    memory_readiness: str

    def to_dict(self) -> dict[str, str]:
        return {
            "targetedTests": self.targeted_tests,
            "compileStatic": self.compile_static,
            "diffCheck": self.diff_check,
            "runnerCapability": self.runner_capability,
            "contextCompatibility": self.context_compatibility,
            "governanceLineage": self.governance_lineage,
            "memoryReadiness": self.memory_readiness,
        }

    @classmethod
    def from_dict(cls, value: object) -> "CoverageResult":
        if not isinstance(value, dict) or set(value) != _COVERAGE_KEYS:
            raise ValueError("coverage schema is invalid")
        if not all(isinstance(item, str) and item for item in value.values()):
            raise ValueError("coverage values must be non-empty strings")
        return cls(
            targeted_tests=value["targetedTests"], compile_static=value["compileStatic"],
            diff_check=value["diffCheck"], runner_capability=value["runnerCapability"],
            context_compatibility=value["contextCompatibility"],
            governance_lineage=value["governanceLineage"], memory_readiness=value["memoryReadiness"],
        )


@dataclass(frozen=True)
class PreflightResult:
    status: PreflightStatus
    session_id: str
    source_fingerprint: str
    bundle_fingerprint: str
    changed_files: tuple[str, ...]
    coverage: CoverageResult
    generated_evidence: tuple[str, ...]
    verification_path: str
    diagnostics: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict:
        return {
            "schemaVersion": _SCHEMA,
            "status": self.status,
            "sessionId": self.session_id,
            "sourceFingerprint": self.source_fingerprint,
            "bundleFingerprint": self.bundle_fingerprint,
            "changedFiles": list(self.changed_files),
            "coverage": self.coverage.to_dict(),
            "generatedEvidence": list(self.generated_evidence),
            "verificationPath": self.verification_path,
            "diagnostics": list(self.diagnostics),
            "createdAt": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "PreflightResult":
        validate_preflight_result(value)
        return cls(
            status=value["status"], session_id=value["sessionId"],
            source_fingerprint=value["sourceFingerprint"],
            bundle_fingerprint=value["bundleFingerprint"],
            changed_files=tuple(value["changedFiles"]),
            coverage=CoverageResult.from_dict(value["coverage"]),
            generated_evidence=tuple(value["generatedEvidence"]),
            verification_path=value["verificationPath"],
            diagnostics=tuple(value["diagnostics"]), created_at=value["createdAt"],
        )


def validate_preflight_result(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != _RESULT_KEYS:
        raise ValueError("preflight schema is invalid")
    if payload["schemaVersion"] != _SCHEMA:
        raise ValueError("preflight schema is invalid")
    if payload["status"] not in _STATUSES:
        raise ValueError("preflight status is invalid")
    for field in ("sourceFingerprint", "bundleFingerprint"):
        _validate_sha(payload[field], field)
    for field in ("sessionId", "verificationPath", "createdAt"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"{field} must be a non-empty string")
    if not payload["verificationPath"].startswith(".nbs_agent_runtime/"):
        raise ValueError("verificationPath must stay under .nbs_agent_runtime")
    changed_files = _validate_string_list(payload["changedFiles"], "changedFiles")
    if any(path.startswith("/") or ".." in path.split("/") for path in changed_files):
        raise ValueError("changedFiles contains an unsafe path")
    _validate_string_list(payload["generatedEvidence"], "generatedEvidence")
    diagnostics = _validate_string_list(payload["diagnostics"], "diagnostics") if payload["diagnostics"] else []
    if any(len(item) > _MAX_DIAGNOSTIC_CHARS for item in diagnostics):
        raise ValueError("diagnostic exceeds bounded limit")
    CoverageResult.from_dict(payload["coverage"])
    return payload
