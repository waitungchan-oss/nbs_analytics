from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.agents.evidence_models import canonical_fingerprint


GATES = ("full_pytest", "hermes", "ui_acceptance")
STATUSES = ("PASS", "FAIL", "BLOCKED")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PAYLOAD_CHARS = 32_000
_MAX_AGE_SECONDS = 7_200
_SECRET = re.compile(r"(?i)(token|password|secret|authorization|api[_-]?key)\s*[:=]")
_SENSITIVE_FIELDS = {"token", "password", "secret", "authorization", "apikey", "api_key", "api-key"}


class ReleaseGateValidationError(ValueError):
    pass


def _schema_for(gate: str) -> str:
    return f"{gate.replace('_', '-')}-gate-v1"


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseGateValidationError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseGateValidationError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ReleaseGateValidationError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _scan(value: Any, field: str = "payload") -> None:
    if isinstance(value, str):
        if len(value) > _MAX_PAYLOAD_CHARS:
            raise ReleaseGateValidationError("payload exceeds size cap")
        if _SECRET.search(value) or value.startswith(("/", "file://")):
            raise ReleaseGateValidationError(f"{field} contains forbidden secret or path")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).replace("-", "_").lower() in _SENSITIVE_FIELDS:
                raise ReleaseGateValidationError(f"{field} contains forbidden secret field")
            _scan(str(key), field)
            _scan(item, f"{field}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan(item, f"{field}[{index}]")


def _unsigned(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in value if key != "evidenceFingerprint"}


@dataclass(frozen=True)
class ReleaseGateEvidence:
    schema_version: str
    gate: str
    status: str
    commit_sha: str
    source_fingerprint: str
    started_at: str
    finished_at: str
    result: dict[str, Any]
    metadata: dict[str, Any]
    evidence_fingerprint: str

    @property
    def fingerprint(self) -> str:
        return self.evidence_fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "gate": self.gate,
            "status": self.status,
            "commitSha": self.commit_sha,
            "sourceFingerprint": self.source_fingerprint,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "result": self.result,
            "metadata": self.metadata,
            "evidenceFingerprint": self.evidence_fingerprint,
        }


@dataclass(frozen=True)
class ReleaseGateAggregate:
    schema_version: str
    status: str
    commit_sha: str
    source_fingerprint: str
    gates: dict[str, dict[str, str]]
    freshness: dict[str, Any]
    evidence_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "commitSha": self.commit_sha,
            "sourceFingerprint": self.source_fingerprint,
            "gates": self.gates,
            "freshness": self.freshness,
            "evidenceFingerprint": self.evidence_fingerprint,
        }


def validate_release_gate_evidence(
    payload: Mapping[str, Any],
    expected_commit_sha: str,
    expected_source_fingerprint: str,
    now: datetime | None = None,
) -> ReleaseGateEvidence:
    if not isinstance(payload, Mapping):
        raise ReleaseGateValidationError("evidence must be an object")
    required = {"schemaVersion", "gate", "status", "commitSha", "sourceFingerprint", "startedAt", "finishedAt", "result", "metadata", "evidenceFingerprint"}
    if set(payload) != required:
        raise ReleaseGateValidationError("evidence schema is invalid")
    gate = payload["gate"]
    if gate not in GATES or payload["schemaVersion"] != _schema_for(gate):
        raise ReleaseGateValidationError("gate schema is invalid")
    if payload["status"] not in STATUSES:
        raise ReleaseGateValidationError("status is invalid")
    if not isinstance(payload["commitSha"], str) or not _SHA40.fullmatch(payload["commitSha"]):
        raise ReleaseGateValidationError("commit is invalid")
    if payload["commitSha"] != expected_commit_sha:
        raise ReleaseGateValidationError("commit mismatch")
    if not isinstance(payload["sourceFingerprint"], str) or not _SHA64.fullmatch(payload["sourceFingerprint"]):
        raise ReleaseGateValidationError("source fingerprint is invalid")
    if payload["sourceFingerprint"] != expected_source_fingerprint:
        raise ReleaseGateValidationError("source mismatch")
    if not isinstance(payload["result"], dict) or not isinstance(payload["metadata"], dict):
        raise ReleaseGateValidationError("result or metadata schema is invalid")
    started = _parse_time(payload["startedAt"], "startedAt")
    finished = _parse_time(payload["finishedAt"], "finishedAt")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if finished < started or (current - finished).total_seconds() > _MAX_AGE_SECONDS or finished > current:
        raise ReleaseGateValidationError("evidence is stale")
    _scan(payload)
    if len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) > _MAX_PAYLOAD_CHARS:
        raise ReleaseGateValidationError("payload exceeds total size cap")
    if not isinstance(payload["evidenceFingerprint"], str) or not _SHA64.fullmatch(payload["evidenceFingerprint"]):
        raise ReleaseGateValidationError("evidence fingerprint is invalid")
    if canonical_fingerprint(_unsigned(payload)) != payload["evidenceFingerprint"]:
        raise ReleaseGateValidationError("evidence fingerprint mismatch")
    return ReleaseGateEvidence(
        schema_version=payload["schemaVersion"], gate=gate, status=payload["status"],
        commit_sha=payload["commitSha"], source_fingerprint=payload["sourceFingerprint"],
        started_at=payload["startedAt"], finished_at=payload["finishedAt"], result=dict(payload["result"]),
        metadata=dict(payload["metadata"]), evidence_fingerprint=payload["evidenceFingerprint"],
    )


def aggregate_release_gates(
    evidence: Mapping[str, Mapping[str, Any]],
    expected_commit_sha: str,
    expected_source_fingerprint: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if set(evidence) != set(GATES):
        raise ReleaseGateValidationError("gate set is invalid")
    children = {
        gate: validate_release_gate_evidence(evidence[gate], expected_commit_sha, expected_source_fingerprint, now)
        for gate in GATES
    }
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ages = {gate: max(0, int((current - _parse_time(item.finished_at, "finishedAt")).total_seconds())) for gate, item in children.items()}
    unsigned = {
        "schemaVersion": "release-gate-result-v1",
        "status": "PASS" if all(item.status == "PASS" for item in children.values()) else "BLOCKED",
        "commitSha": expected_commit_sha,
        "sourceFingerprint": expected_source_fingerprint,
        "gates": {gate: {"status": item.status, "evidenceFingerprint": item.fingerprint} for gate, item in children.items()},
        "freshness": {"status": "fresh", "maxAgeSeconds": _MAX_AGE_SECONDS, "agesSeconds": ages},
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def validate_release_gate_aggregate(payload: Mapping[str, Any], expected_commit_sha: str, now: datetime | None = None) -> ReleaseGateAggregate:
    required = {"schemaVersion", "status", "commitSha", "sourceFingerprint", "gates", "freshness", "evidenceFingerprint"}
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise ReleaseGateValidationError("aggregate schema is invalid")
    if payload["schemaVersion"] != "release-gate-result-v1" or payload["status"] not in STATUSES or payload["commitSha"] != expected_commit_sha:
        raise ReleaseGateValidationError("aggregate identity or status is invalid")
    if not isinstance(payload["sourceFingerprint"], str) or not _SHA64.fullmatch(payload["sourceFingerprint"]):
        raise ReleaseGateValidationError("aggregate source fingerprint is invalid")
    if not isinstance(payload["gates"], dict) or set(payload["gates"]) != set(GATES):
        raise ReleaseGateValidationError("aggregate gate set is invalid")
    for gate, child in payload["gates"].items():
        if not isinstance(child, dict) or set(child) != {"status", "evidenceFingerprint"} or child["status"] not in STATUSES or not _SHA64.fullmatch(child["evidenceFingerprint"]):
            raise ReleaseGateValidationError(f"aggregate gate {gate} is invalid")
    if not isinstance(payload["freshness"], dict) or payload["freshness"].get("status") != "fresh":
        raise ReleaseGateValidationError("aggregate freshness is invalid")
    _scan(payload)
    if canonical_fingerprint(_unsigned(payload)) != payload["evidenceFingerprint"]:
        raise ReleaseGateValidationError("aggregate fingerprint mismatch")
    expected_status = "PASS" if all(payload["gates"][gate]["status"] == "PASS" for gate in GATES) else "BLOCKED"
    if payload["status"] != expected_status:
        raise ReleaseGateValidationError("aggregate status is invalid")
    return ReleaseGateAggregate(
        schema_version=payload["schemaVersion"], status=payload["status"], commit_sha=payload["commitSha"],
        source_fingerprint=payload["sourceFingerprint"], gates=dict(payload["gates"]),
        freshness=dict(payload["freshness"]), evidence_fingerprint=payload["evidenceFingerprint"],
    )
