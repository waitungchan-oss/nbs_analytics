from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .governance_graph_comparison_models import (
    COMPARISON_STATUSES,
    GovernanceGraphComparisonResult,
    GovernanceGraphComparisonSchemaError,
)
from .workflow_models import canonical_sha256


RISK_SUMMARY_SCHEMA = "governance-graph-risk-summary-v1"
RISK_RULE_REGISTRY_VERSION = "d3-risk-rules-v1"
RISK_LEVELS = frozenset({"R0", "R1", "R2", "unknown"})
RISK_STATUSES = COMPARISON_STATUSES
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9_./:-]{1,256}$")
_FINDING_KEYS = frozenset({"findingId", "ruleId", "level", "category", "confidence", "sourceChange", "evidenceIdentities", "rationaleCode", "summary"})
_SOURCE_KEYS = frozenset({"kind", "identity", "changeType"})
_COVERAGE_KEYS = frozenset({"observedChanges", "classifiedChanges", "unknownChanges", "invalidChanges", "blockedChanges"})
_SUMMARY_KEYS = frozenset({
    "schemaVersion", "status", "riskRuleRegistryVersion", "comparisonFingerprint",
    "riskSummaryFingerprint", "overallRiskLevel", "findings", "coverage", "diagnostics",
})


class GovernanceGraphRiskSchemaError(ValueError):
    """Raised when the bounded D-3 risk contract is invalid."""


def _text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or not _SAFE_RE.fullmatch(value):
        raise GovernanceGraphRiskSchemaError(f"{key} is invalid or unsafe")
    return value


def _summary_text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise GovernanceGraphRiskSchemaError(f"{key} is invalid or unsafe")
    if value.startswith("/") or "\\" in value:
        raise GovernanceGraphRiskSchemaError(f"{key} must not expose a path")
    return value


def _identity_text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value) or value.startswith("/") or ".." in value:
        raise GovernanceGraphRiskSchemaError(f"{key} is invalid or unsafe")
    return value


def _sha(value: Any, key: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise GovernanceGraphRiskSchemaError(f"{key} must be a lowercase SHA-256")
    return value


def _count(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100000:
        raise GovernanceGraphRiskSchemaError(f"{key} must be a bounded non-negative integer")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(dict(sorted((str(k), _freeze(v)) for k, v in value.items())))
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class GovernanceGraphRiskInput:
    comparison: GovernanceGraphComparisonResult

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphRiskInput":
        try:
            return cls(GovernanceGraphComparisonResult.from_dict(payload))
        except GovernanceGraphComparisonSchemaError as exc:
            raise GovernanceGraphRiskSchemaError(str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return self.comparison.to_dict()


@dataclass(frozen=True)
class GovernanceGraphRiskFinding:
    finding_id: str
    rule_id: str
    level: str
    category: str
    confidence: str
    source_change: Mapping[str, str]
    evidence_identities: tuple[str, ...]
    rationale_code: str
    summary: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphRiskFinding":
        if not isinstance(payload, Mapping) or set(payload) != _FINDING_KEYS:
            raise GovernanceGraphRiskSchemaError("finding keys are invalid")
        source = payload["sourceChange"]
        if not isinstance(source, Mapping) or set(source) != _SOURCE_KEYS:
            raise GovernanceGraphRiskSchemaError("sourceChange keys are invalid")
        if source["kind"] not in {"node", "edge", "evidence", "comparison"} or source["changeType"] not in {"added", "removed", "changed"}:
            raise GovernanceGraphRiskSchemaError("sourceChange values are invalid")
        evidence = payload["evidenceIdentities"]
        if not isinstance(evidence, (list, tuple)) or len(evidence) > 32:
            raise GovernanceGraphRiskSchemaError("evidenceIdentities is invalid")
        evidence_values = tuple(sorted({_identity_text(item, "evidenceIdentities") for item in evidence}))
        level = payload["level"]
        if level not in RISK_LEVELS:
            raise GovernanceGraphRiskSchemaError("finding level is invalid")
        return cls(
            _text(payload["findingId"], "findingId"), _text(payload["ruleId"], "ruleId"), level,
            _text(payload["category"], "category"), _text(payload["confidence"], "confidence"),
            MappingProxyType({key: (_identity_text(source[key], f"sourceChange.{key}") if key == "identity" else _text(source[key], f"sourceChange.{key}")) for key in sorted(_SOURCE_KEYS)}),
            evidence_values, _text(payload["rationaleCode"], "rationaleCode"), _summary_text(payload["summary"], "summary"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "findingId": self.finding_id, "ruleId": self.rule_id, "level": self.level,
            "category": self.category, "confidence": self.confidence,
            "sourceChange": _thaw(self.source_change), "evidenceIdentities": list(self.evidence_identities),
            "rationaleCode": self.rationale_code, "summary": self.summary,
        }


@dataclass(frozen=True)
class GovernanceGraphRiskSummary:
    status: str
    comparison_fingerprint: str
    overall_risk_level: str
    findings: tuple[GovernanceGraphRiskFinding, ...]
    coverage: Mapping[str, int]
    diagnostics: tuple[Mapping[str, str], ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceGraphRiskSummary":
        if not isinstance(payload, Mapping) or set(payload) != _SUMMARY_KEYS:
            raise GovernanceGraphRiskSchemaError("risk summary keys are invalid")
        if payload.get("schemaVersion") != RISK_SUMMARY_SCHEMA:
            raise GovernanceGraphRiskSchemaError("schemaVersion is invalid")
        if payload.get("riskRuleRegistryVersion") != RISK_RULE_REGISTRY_VERSION:
            raise GovernanceGraphRiskSchemaError("riskRuleRegistryVersion is invalid")
        findings_payload = payload.get("findings")
        if not isinstance(findings_payload, (list, tuple)) or len(findings_payload) > 100000:
            raise GovernanceGraphRiskSchemaError("findings is invalid")
        try:
            findings = tuple(GovernanceGraphRiskFinding.from_dict(item) for item in findings_payload)
            if len({item.finding_id for item in findings}) != len(findings):
                raise GovernanceGraphRiskSchemaError("findingId values must be unique")
            result = cls.from_parts(
                status=payload["status"],
                comparison_fingerprint=payload["comparisonFingerprint"],
                findings=findings,
                coverage=payload["coverage"],
                diagnostics=payload["diagnostics"],
            )
        except GovernanceGraphRiskSchemaError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise GovernanceGraphRiskSchemaError("risk summary payload is malformed") from exc
        if payload.get("overallRiskLevel") != result.overall_risk_level:
            raise GovernanceGraphRiskSchemaError("overallRiskLevel does not match findings")
        if payload.get("riskSummaryFingerprint") != result.risk_summary_fingerprint:
            raise GovernanceGraphRiskSchemaError("riskSummaryFingerprint does not match payload")
        return result

    @classmethod
    def from_parts(cls, *, status: str, comparison_fingerprint: str, findings: Any, coverage: Mapping[str, Any], diagnostics: Any) -> "GovernanceGraphRiskSummary":
        if status not in RISK_STATUSES:
            raise GovernanceGraphRiskSchemaError("status is invalid")
        fingerprint = _sha(comparison_fingerprint, "comparisonFingerprint")
        if not isinstance(coverage, Mapping) or set(coverage) != _COVERAGE_KEYS:
            raise GovernanceGraphRiskSchemaError("coverage keys are invalid")
        normalized_coverage = MappingProxyType({key: _count(coverage[key], key) for key in sorted(_COVERAGE_KEYS)})
        normalized_findings = tuple(sorted((item if isinstance(item, GovernanceGraphRiskFinding) else GovernanceGraphRiskFinding.from_dict(item) for item in findings), key=lambda item: item.finding_id))
        if status in {"invalid", "unavailable"} and normalized_findings:
            raise GovernanceGraphRiskSchemaError("invalid or unavailable summaries cannot contain findings")
        normalized_diagnostics = tuple(MappingProxyType({"code": _text(item["code"], "diagnostics.code"), "summary": _summary_text(item["summary"], "diagnostics.summary")}) for item in diagnostics)
        level = "unknown" if not normalized_findings else max((item.level for item in normalized_findings), key={"R0": 0, "R1": 1, "R2": 2, "unknown": -1}.__getitem__)
        return cls(status, fingerprint, level, normalized_findings, normalized_coverage, normalized_diagnostics)

    @property
    def risk_summary_fingerprint(self) -> str:
        return canonical_sha256({
            "schemaVersion": RISK_SUMMARY_SCHEMA, "status": self.status,
            "riskRuleRegistryVersion": RISK_RULE_REGISTRY_VERSION,
            "comparisonFingerprint": self.comparison_fingerprint, "overallRiskLevel": self.overall_risk_level,
            "findings": [item.to_dict() for item in self.findings], "coverage": _thaw(self.coverage),
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": RISK_SUMMARY_SCHEMA, "status": self.status,
            "riskRuleRegistryVersion": RISK_RULE_REGISTRY_VERSION,
            "comparisonFingerprint": self.comparison_fingerprint,
            "riskSummaryFingerprint": self.risk_summary_fingerprint,
            "overallRiskLevel": self.overall_risk_level,
            "findings": [item.to_dict() for item in self.findings], "coverage": _thaw(self.coverage),
            "diagnostics": [_thaw(item) for item in self.diagnostics],
        }
