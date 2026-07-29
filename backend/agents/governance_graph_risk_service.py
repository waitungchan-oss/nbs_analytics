from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .governance_graph_comparison_models import GovernanceGraphComparisonResult
from .governance_graph_risk_models import (
    GovernanceGraphRiskFinding,
    GovernanceGraphRiskInput,
    GovernanceGraphRiskSummary,
)


D3_DOCUMENTATION_NODE_ALLOWLIST_V1 = frozenset({"documentation"})
_PROTECTED_SURFACES = frozenset({"baseline", "sqlite", "revenue", "rollback", "business_rules", "export_schema"})
_VERIFICATION_NODES = frozenset({"review", "targeted_verification", "full_verification", "hermes"})
_BEHAVIORAL_NODES = frozenset({"implementation", "api", "graph"})
_LEVEL_PRIORITY = {"R2": 2, "R1": 1, "R0": 0, "unknown": -1}


@dataclass(frozen=True)
class _Rule:
    rule_id: str
    priority: int
    level: str
    category: str


RISK_RULES_V1 = (
    _Rule("D3-PROTECTED-NODE", 90, "R2", "protected_surface"),
    _Rule("D3-PROTECTED-SURFACE", 80, "R2", "protected_surface"),
    _Rule("D3-VERIFICATION-REGRESSION", 70, "R1", "verification_integrity"),
    _Rule("D3-BEHAVIORAL-CHANGE", 60, "R1", "behavioral_change"),
    _Rule("D3-BLOCKED-COMPARISON", 50, "R1", "workflow_blocked"),
    _Rule("D3-DOCUMENTATION-ONLY", 20, "R0", "documentation_only"),
    _Rule("D3-UNKNOWN-COVERAGE", 10, "unknown", "coverage_gap"),
)


def _record_identity(kind: str, record: Mapping[str, Any]) -> str:
    if kind == "node":
        return str(record.get("nodeId", "unknown"))
    if kind == "edge":
        return f"{record.get('source', 'unknown')}->{record.get('target', 'unknown')}:{record.get('type', 'unknown')}"
    return str(record.get("path", "unknown"))


def _source(kind: str, record: Mapping[str, Any]) -> dict[str, str]:
    return {"kind": kind, "identity": _record_identity(kind, record), "changeType": str(record["changeType"])}


def _finding(rule: _Rule, kind: str, record: Mapping[str, Any], rationale: str, summary: str) -> GovernanceGraphRiskFinding:
    source = _source(kind, record)
    return GovernanceGraphRiskFinding.from_dict({
        "findingId": f"{rule.rule_id}:{kind}:{source['identity']}:{source['changeType']}",
        "ruleId": rule.rule_id, "level": rule.level, "category": rule.category, "confidence": "high",
        "sourceChange": source, "evidenceIdentities": [], "rationaleCode": rationale, "summary": summary,
    })


def _contains_protected(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, str) and item in _PROTECTED_SURFACES:
                return item
            result = _contains_protected(item)
            if result:
                return result
    elif isinstance(value, (list, tuple)):
        for item in value:
            result = _contains_protected(item)
            if result:
                return result
    return None


class GovernanceGraphRiskService:
    """Pure, read-only risk projection over a bridge-complete D-2 result."""

    def evaluate(self, comparison: Mapping[str, Any] | GovernanceGraphComparisonResult) -> GovernanceGraphRiskSummary:
        if isinstance(comparison, GovernanceGraphComparisonResult):
            input_model = GovernanceGraphRiskInput.from_dict(comparison.to_dict())
        else:
            input_model = GovernanceGraphRiskInput.from_dict(comparison)
        result = input_model.comparison
        if result.status in {"invalid", "unavailable"}:
            return GovernanceGraphRiskSummary.from_parts(
                status=result.status, comparison_fingerprint=result.comparison_fingerprint,
                findings=(), coverage={"observedChanges": 0, "classifiedChanges": 0, "unknownChanges": 0, "invalidChanges": 0, "blockedChanges": 0},
                diagnostics=result.diagnostics,
            )

        findings: dict[tuple[str, str, str, str], GovernanceGraphRiskFinding] = {}
        records = [("node", item) for item in result.node_changes]
        records += [("edge", item) for item in result.edge_changes]
        records += [("evidence", item) for item in result.evidence_changes]
        for kind, record in records:
            node_id = record.get("nodeId")
            protected_rule = None
            if kind == "node" and node_id == "protected_incident":
                protected_rule = RISK_RULES_V1[0]
                finding = _finding(protected_rule, kind, record, "protected_node", "Protected incident node changed between snapshots.")
                findings[(protected_rule.rule_id, kind, _record_identity(kind, record), record["changeType"])] = finding
            elif _contains_protected(record) is not None:
                protected_rule = RISK_RULES_V1[1]
                finding = _finding(protected_rule, kind, record, "protected_surface_signal", "Protected governance surface appears in a bounded change record.")
                findings[(protected_rule.rule_id, kind, _record_identity(kind, record), record["changeType"])] = finding

            if kind == "node" and node_id in _VERIFICATION_NODES and record["changeType"] in {"removed", "changed"}:
                rule = RISK_RULES_V1[2]
                finding = _finding(rule, kind, record, "verification_node_changed", "Verification or Hermes bounded status changed between snapshots.")
                findings[(rule.rule_id, kind, _record_identity(kind, record), record["changeType"])] = finding
            if kind == "node" and node_id in _BEHAVIORAL_NODES and record["changeType"] in {"added", "removed", "changed"}:
                rule = RISK_RULES_V1[3]
                finding = _finding(rule, kind, record, "behavioral_node_changed", "Bounded implementation or API graph node changed between snapshots.")
                findings[(rule.rule_id, kind, _record_identity(kind, record), record["changeType"])] = finding

        if result.status == "blocked":
            rule = RISK_RULES_V1[4]
            record = {"changeType": "changed", "path": "comparison"}
            finding = GovernanceGraphRiskFinding.from_dict({
                "findingId": f"{rule.rule_id}:comparison:status:blocked", "ruleId": rule.rule_id,
                "level": rule.level, "category": rule.category, "confidence": "high",
                "sourceChange": {"kind": "comparison", "identity": "status", "changeType": "changed"},
                "evidenceIdentities": [], "rationaleCode": "comparison_blocked", "summary": "Comparison status is blocked by bounded upstream evidence.",
            })
            findings[(rule.rule_id, "comparison", "status", "changed")] = finding

        only_documentation = bool(result.node_changes) and all(
            item.get("nodeId") in D3_DOCUMENTATION_NODE_ALLOWLIST_V1 for item in result.node_changes
        ) and not result.edge_changes and not result.evidence_changes and result.status == "available"
        if only_documentation and not findings:
            rule = RISK_RULES_V1[5]
            for record in result.node_changes:
                finding = _finding(rule, "node", record, "documentation_only", "Only the explicitly allowlisted documentation node changed.")
                findings[(rule.rule_id, "node", _record_identity("node", record), record["changeType"])] = finding

        if result.status == "unknown" or (records and not findings):
            rule = RISK_RULES_V1[6]
            finding = GovernanceGraphRiskFinding.from_dict({
                "findingId": f"{rule.rule_id}:comparison:coverage:unknown", "ruleId": rule.rule_id,
                "level": rule.level, "category": rule.category, "confidence": "low",
                "sourceChange": {"kind": "comparison", "identity": "coverage", "changeType": "changed"},
                "evidenceIdentities": [], "rationaleCode": "coverage_unknown", "summary": "Bounded comparison coverage is insufficient for a safe risk classification.",
            })
            findings[(rule.rule_id, "comparison", "coverage", "changed")] = finding

        ordered = tuple(sorted(findings.values(), key=lambda item: (-_LEVEL_PRIORITY[item.level], item.finding_id)))
        observed = len(records)
        covered = len({(item.source_change["kind"], item.source_change["identity"], item.source_change["changeType"]) for item in ordered})
        unknown = sum(item.level == "unknown" for item in ordered)
        blocked = 1 if result.status == "blocked" else 0
        return GovernanceGraphRiskSummary.from_parts(
            status=result.status, comparison_fingerprint=result.comparison_fingerprint, findings=ordered,
            coverage={"observedChanges": observed, "classifiedChanges": covered, "unknownChanges": unknown, "invalidChanges": 0, "blockedChanges": blocked},
            diagnostics=result.diagnostics,
        )


__all__ = ["D3_DOCUMENTATION_NODE_ALLOWLIST_V1", "RISK_RULES_V1", "GovernanceGraphRiskService"]
