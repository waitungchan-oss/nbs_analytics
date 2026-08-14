from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Mapping

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.runner_capability_evidence import RunnerCapabilityRun


SCHEMA_VERSION = "short-term-offload-ab-evidence-v1"
RESULTS = frozenset({"pass", "no_reduction", "blocked_runner_capability", "completion_missing"})
_MAX_REFS = 128
_MAX_LATENCY_MS = 3_600_000
_MIN_TOKEN_REDUCTION_RATIO = -20_000_000.0
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ShortTermOffloadABEvidenceError(ValueError):
    """Raised when offload A/B evidence is not immutable and bounded."""


def _require_finite_ratio(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ShortTermOffloadABEvidenceError("tokenReductionRatio must be finite")
    if value < _MIN_TOKEN_REDUCTION_RATIO or value > 1.0:
        raise ShortTermOffloadABEvidenceError("tokenReductionRatio is out of bounds")
    return float(value)


def _require_latency(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_LATENCY_MS:
        raise ShortTermOffloadABEvidenceError(f"{field} must be bounded")
    return value


def _require_refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or len(value) > _MAX_REFS:
        raise ShortTermOffloadABEvidenceError("provenanceRefs must be a non-empty bounded list")
    refs = tuple(value)
    if any(not isinstance(ref, str) or not ref or len(ref) > 512 or "\n" in ref or "\r" in ref for ref in refs):
        raise ShortTermOffloadABEvidenceError("provenanceRefs are invalid")
    if len(set(refs)) != len(refs):
        raise ShortTermOffloadABEvidenceError("provenanceRefs must be unique")
    return refs


@dataclass(frozen=True)
class ShortTermOffloadABEvidence:
    control: RunnerCapabilityRun
    treatment: RunnerCapabilityRun
    workload_fingerprint: str
    control_receipt_ref: str
    treatment_receipt_ref: str
    provenance_refs: tuple[str, ...]
    token_reduction_ratio: float
    latency_delta_ratio: float
    result: str
    reasons: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    evidence_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.result not in RESULTS:
            raise ShortTermOffloadABEvidenceError("schema or result is unsupported")
        if not isinstance(self.control, RunnerCapabilityRun) or not isinstance(self.treatment, RunnerCapabilityRun):
            raise ShortTermOffloadABEvidenceError("control and treatment must be typed runs")
        if not isinstance(self.workload_fingerprint, str) or not _SHA256.fullmatch(self.workload_fingerprint):
            raise ShortTermOffloadABEvidenceError("workloadFingerprint is invalid")
        for name in ("control_receipt_ref", "treatment_receipt_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 512 or "\n" in value or "\r" in value:
                raise ShortTermOffloadABEvidenceError(f"{name} is invalid")
        if self.control_receipt_ref == self.treatment_receipt_ref:
            raise ShortTermOffloadABEvidenceError("receipt references must differ")
        _require_refs(self.provenance_refs)
        _require_finite_ratio(self.token_reduction_ratio)
        if (isinstance(self.latency_delta_ratio, bool)
                or not isinstance(self.latency_delta_ratio, (int, float))
                or not math.isfinite(self.latency_delta_ratio)
                or self.latency_delta_ratio < -1.0 or self.latency_delta_ratio > 1.0):
            raise ShortTermOffloadABEvidenceError("latencyDeltaRatio must be finite")
        _require_latency(self.control.p95_ms, field="control.p95Ms")
        _require_latency(self.treatment.p95_ms, field="treatment.p95Ms")
        if any(not isinstance(reason, str) or not reason or len(reason) > 128 for reason in self.reasons):
            raise ShortTermOffloadABEvidenceError("reasons are invalid")
        object.__setattr__(self, "evidence_fingerprint", canonical_fingerprint(self.unsigned_dict()))

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "workloadFingerprint": self.workload_fingerprint,
            "control": self.control.to_dict(),
            "treatment": self.treatment.to_dict(),
            "controlReceiptRef": self.control_receipt_ref,
            "treatmentReceiptRef": self.treatment_receipt_ref,
            "provenanceRefs": list(self.provenance_refs),
            "tokenReductionRatio": self.token_reduction_ratio,
            "latencyDeltaRatio": self.latency_delta_ratio,
            "result": self.result,
            "reasons": list(self.reasons),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "evidenceFingerprint": self.evidence_fingerprint}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ShortTermOffloadABEvidence":
        expected = {
            "schemaVersion", "workloadFingerprint", "control", "treatment", "controlReceiptRef",
            "treatmentReceiptRef", "provenanceRefs", "tokenReductionRatio", "latencyDeltaRatio",
            "result", "reasons", "evidenceFingerprint",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ShortTermOffloadABEvidenceError("evidence keys mismatch")
        if not isinstance(payload["reasons"], (list, tuple)):
            raise ShortTermOffloadABEvidenceError("reasons must be a list")
        evidence = cls(
            control=RunnerCapabilityRun.from_dict(payload["control"]),
            treatment=RunnerCapabilityRun.from_dict(payload["treatment"]),
            workload_fingerprint=payload["workloadFingerprint"],
            control_receipt_ref=payload["controlReceiptRef"],
            treatment_receipt_ref=payload["treatmentReceiptRef"],
            provenance_refs=_require_refs(payload["provenanceRefs"]),
            token_reduction_ratio=_require_finite_ratio(payload["tokenReductionRatio"]),
            latency_delta_ratio=payload["latencyDeltaRatio"],
            result=payload["result"],
            reasons=tuple(payload["reasons"]),
            schema_version=payload["schemaVersion"],
        )
        if payload["evidenceFingerprint"] != evidence.evidence_fingerprint:
            raise ShortTermOffloadABEvidenceError("evidence fingerprint mismatch")
        return evidence
