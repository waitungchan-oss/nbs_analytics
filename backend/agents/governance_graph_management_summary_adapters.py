from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .governance_graph_comparison_models import GovernanceGraphComparisonResult, GovernanceGraphComparisonSchemaError
from .governance_graph_risk_models import GovernanceGraphRiskSummary, GovernanceGraphRiskSchemaError
from .governance_graph_impact_models import GovernanceGraphImpactSummary, GovernanceGraphImpactModelError
from .governance_graph_evidence_lineage_models import EvidenceLineageResult, EvidenceLineageSchemaError
from .governance_graph_catalog_models import GovernanceGraphOwnerDependencyReadModel, GovernanceGraphCatalogSchemaError


_SHA = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = {"available", "partial", "unknown", "missing", "unavailable", "stale", "blocked", "invalid"}


@dataclass(frozen=True)
class SourceCoverage:
    status: str
    required: bool = True
    diagnostics: tuple[Mapping[str, str], ...] = ()
    fingerprint: str | None = None


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _status(source: Mapping[str, Any] | None, required: bool = True) -> str:
    if source is None:
        return "missing" if required else "unavailable"
    value = source.get("status")
    return value if isinstance(value, str) and value in _STATUSES else "invalid"


def adapt_d1_coverage(source: Mapping[str, Any] | None, selected_snapshot: str) -> SourceCoverage:
    if source is None:
        return SourceCoverage("unavailable", required=False)
    status = _status(source, required=False)
    if source.get("schemaVersion") != "governance-graph-query-v1":
        return SourceCoverage("invalid", required=False)
    query_fp = source.get("queryFingerprint")
    query_identity = source.get("queryIdentity")
    valid_identity = isinstance(query_identity, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+@#%=-]{0,127}", query_identity))
    if status == "available" and (not isinstance(source.get("runId"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+@#%=-]{0,127}", source.get("runId", ""))):
        return SourceCoverage("invalid", required=False)
    if status == "available" and query_fp is not None and not _sha(query_fp):
        return SourceCoverage("invalid", required=False)
    if status == "available" and query_fp is None and query_identity is not None and not valid_identity:
        return SourceCoverage("invalid", required=False)
    if status == "available" and query_fp is None and query_identity is None:
        return SourceCoverage("unknown", required=False)
    fingerprint = source.get("snapshotFingerprint")
    if status == "available" and fingerprint is None:
        return SourceCoverage("unknown", required=False)
    if status == "available" and fingerprint is not None and not _sha(fingerprint):
        return SourceCoverage("invalid", required=False)
    if status == "available" and fingerprint != selected_snapshot:
        return SourceCoverage("stale", required=False)
    return SourceCoverage(status, required=False, fingerprint=fingerprint if _sha(fingerprint) else None)


def adapt_d2_coverage(source: Mapping[str, Any] | None, selected_snapshot: str) -> SourceCoverage:
    if source is None:
        return SourceCoverage("missing")
    status = _status(source)
    if source.get("schemaVersion") != "governance-graph-comparison-v1":
        return SourceCoverage("invalid")
    if "leftReference" in source:
        try:
            parsed = GovernanceGraphComparisonResult.from_dict(source)
        except (GovernanceGraphComparisonSchemaError, KeyError, TypeError, ValueError):
            return SourceCoverage("invalid")
        if parsed.right_reference.snapshot_fingerprint != selected_snapshot:
            return SourceCoverage("stale")
        return SourceCoverage("available" if status == "available" else status, fingerprint=parsed.comparison_fingerprint)
    left = source.get("leftSnapshot")
    right = source.get("rightSnapshot")
    if status == "available":
        if not isinstance(left, Mapping) or not isinstance(right, Mapping) or not _sha(left.get("graphFingerprint")) or not _sha(right.get("graphFingerprint")):
            return SourceCoverage("invalid")
        if right.get("graphFingerprint") != selected_snapshot:
            return SourceCoverage("stale")
        if not _sha(source.get("comparisonFingerprint")):
            return SourceCoverage("invalid")
        if left.get("freshness") != "fresh" or right.get("freshness") != "fresh":
            return SourceCoverage("partial", fingerprint=source["comparisonFingerprint"])
        return SourceCoverage("available", fingerprint=source["comparisonFingerprint"])
    return SourceCoverage(status)


def adapt_d3_coverage(source: Mapping[str, Any] | None) -> SourceCoverage:
    if source is None:
        return SourceCoverage("missing")
    status = _status(source)
    if status != "available":
        if "findings" in source:
            try:
                parsed = GovernanceGraphRiskSummary.from_dict(source)
                cov = parsed.coverage
                mapped = "available" if cov["observedChanges"] == cov["classifiedChanges"] and all(cov[key] == 0 for key in ("unknownChanges", "invalidChanges", "blockedChanges")) else "partial"
                return SourceCoverage(status if status != "available" else mapped, fingerprint=parsed.risk_summary_fingerprint)
            except (GovernanceGraphRiskSchemaError, KeyError, TypeError, ValueError):
                return SourceCoverage("invalid")
        return SourceCoverage(status)
    if source.get("schemaVersion") != "governance-graph-risk-summary-v1" or source.get("riskRuleRegistryVersion") not in {None, "d3-risk-rules-v1"} or not _sha(source.get("comparisonFingerprint")) or not _sha(source.get("riskSummaryFingerprint")):
        return SourceCoverage("invalid")
    if "findings" in source:
        try:
            parsed = GovernanceGraphRiskSummary.from_dict(source)
            cov = parsed.coverage
            mapped = "available" if cov["observedChanges"] == cov["classifiedChanges"] and all(cov[key] == 0 for key in ("unknownChanges", "invalidChanges", "blockedChanges")) else "partial"
            return SourceCoverage(mapped, fingerprint=parsed.risk_summary_fingerprint)
        except (GovernanceGraphRiskSchemaError, KeyError, TypeError, ValueError):
            return SourceCoverage("invalid")
        return SourceCoverage("available", fingerprint=parsed.risk_summary_fingerprint)
    coverage = source.get("coverage")
    if not isinstance(coverage, Mapping):
        return SourceCoverage("invalid")
    keys = ("observedChanges", "classifiedChanges", "unknownChanges", "invalidChanges", "blockedChanges")
    if any(not isinstance(coverage.get(key), int) or isinstance(coverage.get(key), bool) or coverage.get(key) < 0 for key in keys):
        return SourceCoverage("invalid")
    complete = coverage["observedChanges"] == coverage["classifiedChanges"] and all(coverage[key] == 0 for key in keys[2:])
    return SourceCoverage("available" if complete else "partial", fingerprint=source.get("riskSummaryFingerprint"))


def adapt_d4_coverage(source: Mapping[str, Any] | None) -> SourceCoverage:
    if source is None:
        return SourceCoverage("missing")
    status = _status(source)
    coverage = source.get("coverage")
    if status in {"available", "blocked", "unknown"} and source.get("schemaVersion") == "governance-graph-change-impact-v1" and source.get("impactPolicyVersion") in {None, "d4-impact-policy-v1"} and _sha(source.get("comparisonFingerprint")) and _sha(source.get("riskSummaryFingerprint")) and _sha(source.get("impactSummaryFingerprint")) and isinstance(coverage, Mapping):
        if "impacts" in source:
            try:
                GovernanceGraphImpactSummary.from_dict(source)
            except (GovernanceGraphImpactModelError, KeyError, TypeError, ValueError):
                return SourceCoverage("invalid")
        value = coverage.get("coverageStatus")
        if status != "available":
            return SourceCoverage(status, fingerprint=source.get("impactSummaryFingerprint"))
        return {"available": SourceCoverage("available"), "blocked": SourceCoverage("blocked"), "unknown": SourceCoverage("unknown")}.get(value, SourceCoverage("invalid"))
    return SourceCoverage(status)


def adapt_e1_coverage(source: Mapping[str, Any] | None, selected_snapshot: str) -> SourceCoverage:
    if source is None:
        return SourceCoverage("missing")
    status = _status(source)
    if status in {"invalid", "unavailable", "unknown", "blocked", "stale"}:
        return SourceCoverage(status)
    if source.get("snapshotFingerprint") != selected_snapshot or not _sha(source.get("snapshotFingerprint")) or not _sha(source.get("lineageFingerprint")):
        return SourceCoverage("stale" if source.get("snapshotFingerprint") != selected_snapshot else "invalid")
    if source.get("schemaVersion") != "governance-graph-evidence-lineage-v1" or source.get("lineagePolicyVersion") not in {None, "e1-canonical-evidence-lineage-v1"}:
        return SourceCoverage("invalid")
    if "links" in source:
        try:
            EvidenceLineageResult.from_dict(source)
        except (EvidenceLineageSchemaError, KeyError, TypeError, ValueError):
            return SourceCoverage("invalid")
    evidence = source.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(item, Mapping) or not isinstance(item.get("status"), str) for item in evidence):
        return SourceCoverage("invalid")
    return SourceCoverage("available" if isinstance(evidence, list) and evidence else "partial", fingerprint=source["lineageFingerprint"])


def adapt_e3_coverage(source: Mapping[str, Any] | None, selected_snapshot: str) -> SourceCoverage:
    if source is None:
        return SourceCoverage("missing")
    status = _status(source)
    if status != "available":
        return SourceCoverage(status)
    if source.get("schemaVersion") != "governance-graph-owner-dependency-read-v1":
        return SourceCoverage("invalid")
    if source.get("ownerPolicyVersion") not in {None, "e3-owner-policy-v1"} or source.get("dependencyPolicyVersion") not in {None, "e3-dependency-policy-v1"}:
        return SourceCoverage("invalid")
    if "owners" in source or "dependencies" in source:
        try:
            parsed = GovernanceGraphOwnerDependencyReadModel.from_parts(status=status, snapshot_fingerprint=source["snapshotFingerprint"], owner_catalog_fingerprint=source.get("ownerCatalogFingerprint"), dependency_catalog_fingerprint=source.get("dependencyCatalogFingerprint"), owner_policy_version=source.get("ownerPolicyVersion", "e3-owner-policy-v1"), dependency_policy_version=source.get("dependencyPolicyVersion", "e3-dependency-policy-v1"), owners=source.get("owners", []), dependencies=source.get("dependencies", []), coverage=source["coverage"], diagnostics=source.get("diagnostics", []))
        except (GovernanceGraphCatalogSchemaError, KeyError, TypeError, ValueError):
            return SourceCoverage("invalid")
        return SourceCoverage("available", fingerprint=parsed.read_model_fingerprint)
    if source.get("snapshotFingerprint") != selected_snapshot or not _sha(source.get("snapshotFingerprint")) or not _sha(source.get("readModelFingerprint")):
        return SourceCoverage("stale" if source.get("snapshotFingerprint") != selected_snapshot else "invalid")
    coverage = source.get("coverage")
    if not isinstance(coverage, Mapping):
        return SourceCoverage("invalid")
    owner = coverage.get("ownerStatus")
    dependency = coverage.get("dependencyStatus")
    if owner == "available" and dependency == "available":
        return SourceCoverage("available", fingerprint=source["readModelFingerprint"])
    if owner in _STATUSES and dependency in _STATUSES:
        return SourceCoverage("partial", fingerprint=source["readModelFingerprint"])
    return SourceCoverage("invalid")
