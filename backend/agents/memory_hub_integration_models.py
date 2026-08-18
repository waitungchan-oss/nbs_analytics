from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .evidence_models import canonical_fingerprint


INTEGRATION_SCHEMA = "memory-hub-agent-integration-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MODES = frozenset({
    "direct_query", "bounded_consumer", "evidence_comparator", "observation_only",
    "derived_lineage", "gated_context", "approved_evidence_only",
})
_STATUSES = frozenset({"ready", "empty", "blocked", "degraded", "ignored"})
_KEYS = {
    "schemaVersion", "projectId", "consumerId", "integrationMode", "status", "reason",
    "authority", "queryFingerprint", "hintsFingerprint", "policyDecisionFingerprints",
    "sourceRefs", "hintCount", "generatedAt", "evidenceFingerprint",
}


def _id(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{key} is invalid")
    return value


def _sha(value: Any, key: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"{key} must be a lowercase SHA-256")
    return value


def _refs(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > 16 or any(not isinstance(value, str) for value in values):
        raise ValueError("sourceRefs are invalid")
    normalized = tuple(values)
    if normalized != tuple(sorted(set(normalized))):
        raise ValueError("sourceRefs must be unique and sorted")
    for value in normalized:
        if not value or value.startswith(("/", "\\")) or "\\" in value or ".." in value.split("/"):
            raise ValueError("sourceRefs must be relative and bounded")
    return normalized


@dataclass(frozen=True)
class MemoryHubIntegrationEvidence:
    project_id: str
    consumer_id: str
    integration_mode: str
    status: str
    reason: str
    query_fingerprint: str | None
    hints_fingerprint: str | None
    policy_decision_fingerprints: tuple[str, ...]
    source_refs: tuple[str, ...]
    hint_count: int
    generated_at: str
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _id(self.project_id, "projectId")
        _id(self.consumer_id, "consumerId")
        if self.integration_mode not in _MODES:
            raise ValueError("integrationMode is invalid")
        if self.status not in _STATUSES:
            raise ValueError("status is invalid")
        if not isinstance(self.reason, str) or not self.reason or len(self.reason) > 120:
            raise ValueError("reason is invalid")
        _sha(self.query_fingerprint, "queryFingerprint", nullable=True)
        _sha(self.hints_fingerprint, "hintsFingerprint", nullable=True)
        if tuple(sorted(set(self.policy_decision_fingerprints))) != self.policy_decision_fingerprints:
            raise ValueError("policyDecisionFingerprints must be unique and sorted")
        for value in self.policy_decision_fingerprints:
            _sha(value, "policyDecisionFingerprint")
        _refs(list(self.source_refs))
        if not isinstance(self.hint_count, int) or isinstance(self.hint_count, bool) or not 0 <= self.hint_count <= 3:
            raise ValueError("hintCount is out of bounds")
        try:
            datetime.fromisoformat(self.generated_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("generatedAt is invalid") from exc
        _sha(self.evidence_fingerprint, "evidenceFingerprint")
        if self.evidence_fingerprint != canonical_fingerprint(self._unsigned()):
            raise ValueError("evidenceFingerprint mismatch")

    def _unsigned(self) -> dict[str, Any]:
        return {
            "schemaVersion": INTEGRATION_SCHEMA,
            "projectId": self.project_id,
            "consumerId": self.consumer_id,
            "integrationMode": self.integration_mode,
            "status": self.status,
            "reason": self.reason,
            "authority": "non_authoritative_memory",
            "queryFingerprint": self.query_fingerprint,
            "hintsFingerprint": self.hints_fingerprint,
            "policyDecisionFingerprints": list(self.policy_decision_fingerprints),
            "sourceRefs": list(self.source_refs),
            "hintCount": self.hint_count,
            "generatedAt": self.generated_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned(), "evidenceFingerprint": self.evidence_fingerprint}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MemoryHubIntegrationEvidence":
        if not isinstance(payload, Mapping) or set(payload) != _KEYS:
            raise ValueError("memory hub integration evidence keys are invalid")
        if payload["schemaVersion"] != INTEGRATION_SCHEMA or payload["authority"] != "non_authoritative_memory":
            raise ValueError("memory hub integration evidence schema is invalid")
        return cls(
            _id(payload["projectId"], "projectId"),
            _id(payload["consumerId"], "consumerId"),
            payload["integrationMode"],
            payload["status"],
            payload["reason"],
            _sha(payload["queryFingerprint"], "queryFingerprint", nullable=True),
            _sha(payload["hintsFingerprint"], "hintsFingerprint", nullable=True),
            tuple(_sha(value, "policyDecisionFingerprint") for value in payload["policyDecisionFingerprints"]),
            _refs(payload["sourceRefs"]),
            payload["hintCount"],
            payload["generatedAt"],
            _sha(payload["evidenceFingerprint"], "evidenceFingerprint"),
        )


def build_memory_hub_integration_evidence(**kwargs: Any) -> MemoryHubIntegrationEvidence:
    unsigned = {
        "schemaVersion": INTEGRATION_SCHEMA,
        "projectId": kwargs["project_id"],
        "consumerId": kwargs["consumer_id"],
        "integrationMode": kwargs["integration_mode"],
        "status": kwargs["status"],
        "reason": kwargs["reason"],
        "authority": "non_authoritative_memory",
        "queryFingerprint": kwargs["query_fingerprint"],
        "hintsFingerprint": kwargs["hints_fingerprint"],
        "policyDecisionFingerprints": list(kwargs["policy_decision_fingerprints"]),
        "sourceRefs": list(kwargs["source_refs"]),
        "hintCount": kwargs["hint_count"],
        "generatedAt": kwargs["generated_at"],
    }
    return MemoryHubIntegrationEvidence(
        kwargs["project_id"], kwargs["consumer_id"], kwargs["integration_mode"], kwargs["status"],
        kwargs["reason"], kwargs["query_fingerprint"], kwargs["hints_fingerprint"],
        tuple(kwargs["policy_decision_fingerprints"]), tuple(kwargs["source_refs"]), kwargs["hint_count"],
        kwargs["generated_at"], canonical_fingerprint(unsigned),
    )
