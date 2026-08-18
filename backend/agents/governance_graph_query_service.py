from __future__ import annotations

from pathlib import Path
from typing import Any

from .governance_graph_query_models import (
    GovernanceGraphQuery,
    GovernanceGraphQueryResult,
    GovernanceGraphQuerySchemaError,
)
from .governance_graph_memory_integration_service import GovernanceGraphMemoryIntegrationService
from .governance_graph_snapshot_reader import GovernanceGraphSnapshotReader


_STATUS_PRECEDENCE = ("invalid", "blocked", "unknown", "available")
_ARTIFACT_KIND_BY_PATH = {
    "context.json": "context",
    "implementation.json": "implementation",
    "targeted-verification.json": "targeted_verification",
    "review.json": "review",
    "full-verification.json": "full_verification",
    "hermes.json": "hermes",
    "documentation-evidence.json": "documentation",
    "documentation-proposal.json": "documentation",
    "documentation-preview.json": "documentation",
    "documentation-application.json": "documentation",
    "documentation-telemetry.json": "documentation",
    "risk-classification.json": "risk",
    "design-spec-gate.json": "spec_gate",
    "plan-gate.json": "plan_gate",
    "git-integration.json": "git_integration",
    "task-gate.json": "task_gate",
    "terra-diagnosis.json": "terra_diagnosis",
    "protected-incident.json": "protected_incident",
}


class GovernanceGraphQueryService:
    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.runtime_root = Path(runtime_root) if runtime_root is not None else self.project_root / ".nbs_agent_runtime"
        self.reader = GovernanceGraphSnapshotReader(self.project_root, self.runtime_root)

    def query(
        self,
        *,
        run_id: str | None = None,
        node_type: str | None = None,
        node_status: str | None = None,
        node_id: str | None = None,
        edge_type: str | None = None,
        artifact_kind: str | None = None,
        evidence_status: str | None = None,
        snapshot_fingerprint: str | None = None,
    ) -> GovernanceGraphQueryResult:
        try:
            query = GovernanceGraphQuery.from_dict({
                "runId": run_id,
                "nodeType": node_type,
                "nodeStatus": node_status,
                "nodeId": node_id,
                "edgeType": edge_type,
                "artifactKind": artifact_kind,
                "evidenceStatus": evidence_status,
                "snapshotFingerprint": snapshot_fingerprint,
            })
        except GovernanceGraphQuerySchemaError:
            return self._invalid(GovernanceGraphQuery.from_dict({}), "invalid_query")
        if run_id is None:
            raise ValueError("runId is required for deterministic query")
        read_result = self.reader.read(run_id, expected_fingerprint=snapshot_fingerprint)
        if read_result.status == "unavailable":
            return GovernanceGraphQueryResult.from_parts(
                status="unavailable", snapshot_identity=None, filters=query,
                matched_nodes=(), matched_edges=(), evidence_refs=(),
                unknown_count=0, invalid_count=0, blocked_count=0, diagnostics=(),
            )
        if read_result.status == "invalid":
            code = read_result.diagnostics[0].get("code", "invalid_snapshot") if read_result.diagnostics else "invalid_snapshot"
            return self._invalid(query, code)
        snapshot = read_result.snapshot
        identity = read_result.snapshot_identity
        if snapshot is None or identity is None:
            return self._invalid(query, "invalid_snapshot")

        nodes = [self._node_record(node) for node in snapshot.nodes]
        nodes = [node for node in nodes if self._node_matches(node, query.filters)]
        refs = [ref for node in nodes for ref in node.get("evidenceRefs", []) if self._ref_matches(ref, query.filters)]
        edges: list[dict[str, Any]] = []
        if query.filters.get("edgeType"):
            nodes = []
            refs = []
        nodes.sort(key=lambda item: (item["nodeId"], item["nodeType"]))
        refs.sort(key=lambda item: (item.get("path", ""), item.get("sha256", "")))
        counts = self._counts(nodes, refs)
        status = next((candidate for candidate in _STATUS_PRECEDENCE if counts[candidate] > 0), "available")
        return GovernanceGraphQueryResult.from_parts(
            status=status, snapshot_identity=identity, filters=query,
            matched_nodes=tuple(nodes), matched_edges=tuple(edges), evidence_refs=tuple(refs),
            unknown_count=counts["unknown"], invalid_count=counts["invalid"],
            blocked_count=counts["blocked"], diagnostics=(),
        )

    def memory_lineage(self, run_id: str) -> dict[str, Any]:
        """Return the separate bounded Memory Hub lineage projection."""
        return GovernanceGraphMemoryIntegrationService(self.project_root).project(run_id)

    @staticmethod
    def _node_record(node) -> dict[str, Any]:
        return {
            "nodeId": node.node_id,
            "nodeType": node.node_type,
            "status": node.status,
            "reasonCode": node.reason_code,
            "attempt": node.attempt,
            "maxAttempts": node.max_attempts,
            "fingerprint": node.fingerprint,
            "evidenceRefs": [ref.to_dict() for ref in node.evidence_refs],
        }

    @staticmethod
    def _ref_record(ref) -> dict[str, Any]:
        payload = ref.to_dict()
        if "finalizedAt" not in payload:
            payload["finalizedAt"] = payload.pop("generatedAt")
        return payload

    @staticmethod
    def _node_matches(node: dict[str, Any], filters: dict[str, str]) -> bool:
        direct_match = all(
            node.get(key) == value
            for key, value in {
                "nodeType": filters.get("nodeType"),
                "status": filters.get("nodeStatus"),
                "nodeId": filters.get("nodeId"),
            }.items()
            if value is not None
        )
        if not direct_match:
            return False
        ref_filters = {
            "artifactKind": filters.get("artifactKind"),
            "evidenceStatus": filters.get("evidenceStatus"),
        }
        if not any(value is not None for value in ref_filters.values()):
            return True
        refs = node.get("evidenceRefs")
        if not isinstance(refs, list):
            refs = []
        if not refs:
            return False
        return any(
            GovernanceGraphQueryService._ref_matches(ref, ref_filters)
            for ref in refs if isinstance(ref, dict)
        )

    @staticmethod
    def _ref_matches(ref: dict[str, Any], filters: dict[str, str]) -> bool:
        path = str(ref.get("path", ""))
        artifact_kind = _ARTIFACT_KIND_BY_PATH.get(path)
        return all(
            actual == expected
            for actual, expected in (
                (artifact_kind, filters.get("artifactKind")),
                (ref.get("status"), filters.get("evidenceStatus")),
            )
            if expected is not None
        )

    @staticmethod
    def _counts(nodes: list[dict[str, Any]], refs: list[dict[str, Any]]) -> dict[str, int]:
        counts = {
            "invalid": sum(node.get("status") == "invalid" for node in nodes),
            "blocked": sum(node.get("status") == "blocked" for node in nodes),
            "unknown": sum(node.get("status") == "unknown" for node in nodes),
            "available": 0,
        }
        for ref in refs:
            status = ref.get("status")
            if status in {"invalid", "blocked", "unknown"}:
                counts[status] += 1
        return counts

    @staticmethod
    def _invalid(query: GovernanceGraphQuery, code: str) -> GovernanceGraphQueryResult:
        return GovernanceGraphQueryResult.from_parts(
            status="invalid", snapshot_identity=None, filters=query,
            matched_nodes=(), matched_edges=(), evidence_refs=(),
            unknown_count=0, invalid_count=1, blocked_count=0,
            diagnostics=({"code": code},),
        )
