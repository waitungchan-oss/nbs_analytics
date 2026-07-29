from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .governance_graph_risk_models import GovernanceGraphRiskSchemaError, GovernanceGraphRiskSummary
from .workflow_models import canonical_sha256


IMPACT_INPUT_SCHEMA = "governance-graph-impact-input-v1"
IMPACT_SUMMARY_SCHEMA = "governance-graph-change-impact-v1"
D4_IMPACT_POLICY_VERSION = "d4-impact-policy-v1"
_INPUT_KEYS = frozenset({"schemaVersion", "riskSummary"})
_COVERAGE_KEYS = frozenset({"coverageStatus", "changedSeeds", "mappedImpacts", "protectedSignals", "unknownImpacts", "blockedImpacts"})
_SHA = set("0123456789abcdef")


class GovernanceGraphImpactModelError(ValueError):
    pass


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _SHA:
        raise GovernanceGraphImpactModelError(f"{name} must be a lowercase SHA-256")
    return value


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100000:
        raise GovernanceGraphImpactModelError(f"{name} is invalid")
    return value


@dataclass(frozen=True)
class GovernanceGraphImpactInput:
    risk_summary: GovernanceGraphRiskSummary

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphImpactInput":
        if not isinstance(payload, Mapping) or set(payload) != _INPUT_KEYS or payload.get("schemaVersion") != IMPACT_INPUT_SCHEMA:
            raise GovernanceGraphImpactModelError("impact input envelope is invalid")
        try:
            return cls(GovernanceGraphRiskSummary.from_dict(payload["riskSummary"]))
        except (GovernanceGraphRiskSchemaError, KeyError, TypeError) as exc:
            raise GovernanceGraphImpactModelError(str(exc)) from exc


@dataclass(frozen=True)
class GovernanceGraphImpactSummary:
    status: str
    comparison_fingerprint: str | None
    risk_summary_fingerprint: str | None
    impacts: tuple[Mapping[str, Any], ...]
    coverage: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, str], ...]

    @classmethod
    def from_parts(cls, *, status: str, comparison_fingerprint: str | None, risk_summary_fingerprint: str | None, impacts: Any, coverage: Mapping[str, Any], diagnostics: Any) -> "GovernanceGraphImpactSummary":
        if status not in {"available", "unavailable", "unknown", "invalid", "blocked"}:
            raise GovernanceGraphImpactModelError("status is invalid")
        if not isinstance(coverage, Mapping) or set(coverage) != _COVERAGE_KEYS or coverage.get("coverageStatus") not in {"available", "blocked", "unknown"}:
            raise GovernanceGraphImpactModelError("coverage is invalid")
        normalized_coverage = MappingProxyType({key: coverage[key] if key == "coverageStatus" else _count(coverage[key], key) for key in sorted(_COVERAGE_KEYS)})
        if status in {"invalid", "unavailable"}:
            if comparison_fingerprint is not None or risk_summary_fingerprint is not None or impacts:
                raise GovernanceGraphImpactModelError("invalid provenance must be empty")
        else:
            comparison_fingerprint = _sha(comparison_fingerprint, "comparisonFingerprint")
            risk_summary_fingerprint = _sha(risk_summary_fingerprint, "riskSummaryFingerprint")
        normalized_impacts = tuple(sorted((MappingProxyType(dict(item)) for item in impacts), key=lambda item: str(item.get("sourceFindingId", ""))))
        normalized_diagnostics = tuple(MappingProxyType({"code": str(item["code"]), "summary": str(item["summary"])}) for item in diagnostics)
        return cls(status, comparison_fingerprint, risk_summary_fingerprint, normalized_impacts, normalized_coverage, normalized_diagnostics)

    @classmethod
    def invalid(cls, code: str) -> "GovernanceGraphImpactSummary":
        return cls.from_parts(status="invalid", comparison_fingerprint=None, risk_summary_fingerprint=None, impacts=(), coverage={"coverageStatus": "unknown", "changedSeeds": 0, "mappedImpacts": 0, "protectedSignals": 0, "unknownImpacts": 0, "blockedImpacts": 0}, diagnostics=({"code": code, "summary": "Change impact input is invalid."},))

    @property
    def impact_summary_fingerprint(self) -> str | None:
        if self.status in {"invalid", "unavailable"}:
            return None
        return canonical_sha256({"schemaVersion": IMPACT_SUMMARY_SCHEMA, "status": self.status, "impactPolicyVersion": D4_IMPACT_POLICY_VERSION, "comparisonFingerprint": self.comparison_fingerprint, "riskSummaryFingerprint": self.risk_summary_fingerprint, "coverage": dict(self.coverage), "impacts": [dict(item) for item in self.impacts], "diagnostics": [dict(item) for item in self.diagnostics]})

    def to_dict(self) -> dict[str, Any]:
        return {"schemaVersion": IMPACT_SUMMARY_SCHEMA, "status": self.status, "impactPolicyVersion": D4_IMPACT_POLICY_VERSION, "riskSummaryFingerprint": self.risk_summary_fingerprint, "comparisonFingerprint": self.comparison_fingerprint, "impactSummaryFingerprint": self.impact_summary_fingerprint, "coverage": dict(self.coverage), "impacts": [dict(item) for item in self.impacts], "diagnostics": [dict(item) for item in self.diagnostics]}
