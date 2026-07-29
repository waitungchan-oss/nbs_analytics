from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .governance_graph_comparison_models import (
    GovernanceGraphComparisonResult,
    GovernanceGraphComparisonSchemaError,
    GovernanceGraphSnapshotReference,
)
from .governance_graph_snapshot_reader import GovernanceGraphSnapshotReader, SnapshotReadResult


_ZERO_SUMMARY = {
    "addedNodes": 0,
    "removedNodes": 0,
    "changedNodes": 0,
    "unchangedNodes": 0,
    "addedEdges": 0,
    "removedEdges": 0,
    "changedEdges": 0,
    "addedEvidenceRefs": 0,
    "removedEvidenceRefs": 0,
    "changedEvidenceRefs": 0,
}
_STATUS_ORDER = {"available": 0, "unknown": 1, "blocked": 2, "unavailable": 3, "invalid": 4}


class GovernanceGraphComparisonService:
    """Compare two explicit, already-validated Governance Graph snapshots."""

    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.reader = GovernanceGraphSnapshotReader(project_root, runtime_root)

    def compare(
        self,
        *,
        left_run_id: str,
        right_run_id: str,
        left_snapshot_fingerprint: str | None = None,
        right_snapshot_fingerprint: str | None = None,
    ) -> GovernanceGraphComparisonResult:
        left_reference = self._reference(left_run_id, left_snapshot_fingerprint, "left")
        right_reference = self._reference(right_run_id, right_snapshot_fingerprint, "right")
        if left_reference is None or right_reference is None:
            return self._invalid_result(left_reference, right_reference)

        left = self.reader.read(left_reference.run_id, expected_fingerprint=left_reference.snapshot_fingerprint)
        right = self.reader.read(right_reference.run_id, expected_fingerprint=right_reference.snapshot_fingerprint)
        status = self._read_status(left, right)
        if status != "available":
            diagnostics = self._read_diagnostics(left, right)
            return GovernanceGraphComparisonResult.from_parts(
                status=status,
                left_reference=left_reference,
                right_reference=right_reference,
                left_snapshot=left.snapshot_identity,
                right_snapshot=right.snapshot_identity,
                summary=_ZERO_SUMMARY,
                node_changes=(), edge_changes=(), evidence_changes=(), diagnostics=diagnostics,
            )
        assert left.snapshot is not None and right.snapshot is not None
        node_changes, node_summary = self._compare_records(
            self._nodes(left.snapshot), self._nodes(right.snapshot), "nodeId", "Nodes",
        )
        evidence_changes, evidence_summary = self._compare_records(
            self._evidence(left.snapshot), self._evidence(right.snapshot), ("path", "sha256"), "EvidenceRefs",
        )
        edge_changes, edge_summary = self._compare_records(
            self._edges(left.snapshot), self._edges(right.snapshot), ("source", "target", "type"), "Edges",
        )
        summary = {
            **node_summary,
            **edge_summary,
            **evidence_summary,
        }
        return GovernanceGraphComparisonResult.from_parts(
            status=self._snapshot_status(left.snapshot, right.snapshot),
            left_reference=left_reference,
            right_reference=right_reference,
            left_snapshot=left.snapshot_identity,
            right_snapshot=right.snapshot_identity,
            summary=summary,
            node_changes=node_changes,
            edge_changes=edge_changes,
            evidence_changes=evidence_changes,
            diagnostics=(),
        )

    @staticmethod
    def _reference(run_id: str, fingerprint: str | None, side: str) -> GovernanceGraphSnapshotReference | None:
        try:
            return GovernanceGraphSnapshotReference.from_dict({"runId": run_id, "snapshotFingerprint": fingerprint})
        except GovernanceGraphComparisonSchemaError:
            return None

    @staticmethod
    def _invalid_result(left: GovernanceGraphSnapshotReference | None, right: GovernanceGraphSnapshotReference | None) -> GovernanceGraphComparisonResult:
        left = left or GovernanceGraphSnapshotReference.from_dict({"runId": "invalid-left"})
        right = right or GovernanceGraphSnapshotReference.from_dict({"runId": "invalid-right"})
        return GovernanceGraphComparisonResult.from_parts(
            status="invalid", left_reference=left, right_reference=right,
            left_snapshot=None, right_snapshot=None, summary=_ZERO_SUMMARY,
            node_changes=(), edge_changes=(), evidence_changes=(),
            diagnostics=({"code": "invalid_reference", "summary": "snapshot reference is invalid"},),
        )

    @staticmethod
    def _read_status(left: SnapshotReadResult, right: SnapshotReadResult) -> str:
        statuses = (left.status, right.status)
        return max(statuses, key=lambda value: _STATUS_ORDER[value])

    @staticmethod
    def _read_diagnostics(left: SnapshotReadResult, right: SnapshotReadResult) -> tuple[dict[str, str], ...]:
        diagnostics = []
        for side, result in (("left", left), ("right", right)):
            if result.status != "available":
                code = result.diagnostics[0].get("code", result.status) if result.diagnostics else result.status
                diagnostics.append({"code": f"{side}_{code}", "summary": f"{side} snapshot is {result.status}"})
        return tuple(diagnostics)

    @staticmethod
    def _snapshot_status(left: Any, right: Any) -> str:
        statuses = [left.overall_status, right.overall_status]
        node_statuses = [node.status for snapshot in (left, right) for node in snapshot.nodes]
        evidence_statuses = [ref.status for snapshot in (left, right) for node in snapshot.nodes for ref in node.evidence_refs]
        values = statuses + node_statuses + evidence_statuses
        if any(value == "blocked" or value.startswith("blocked_") for value in values if isinstance(value, str)):
            return "blocked"
        if any(value == "unknown" for value in values):
            return "unknown"
        return "available"

    @staticmethod
    def _nodes(snapshot: Any) -> dict[str, dict[str, Any]]:
        return {node.node_id: node.to_dict() for node in snapshot.nodes}

    @staticmethod
    def _evidence(snapshot: Any) -> dict[tuple[str, str], dict[str, Any]]:
        records = {}
        for node in snapshot.nodes:
            for ref in node.evidence_refs:
                record = ref.to_dict()
                records[(ref.path, ref.sha256)] = record
        return records

    @staticmethod
    def _edges(snapshot: Any) -> dict[tuple[str, str, str], dict[str, Any]]:
        raw_edges = getattr(snapshot, "edges", ())
        records = {}
        for edge in raw_edges:
            record = edge.to_dict() if hasattr(edge, "to_dict") else dict(edge)
            key = (record["source"], record["target"], record["type"])
            records[key] = record
        return records

    @staticmethod
    def _compare_records(left: dict, right: dict, identity: str | tuple[str, ...], label: str) -> tuple[tuple[dict, ...], dict[str, int]]:
        keys = sorted(set(left) | set(right))
        changes = []
        for key in keys:
            before = left.get(key)
            after = right.get(key)
            if before is None:
                changes.append({**GovernanceGraphComparisonService._identity_fields(key, identity), "changeType": "added", "before": None, "after": after})
            elif after is None:
                changes.append({**GovernanceGraphComparisonService._identity_fields(key, identity), "changeType": "removed", "before": before, "after": None})
            elif before != after:
                changes.append({**GovernanceGraphComparisonService._identity_fields(key, identity), "changeType": "changed", "before": before, "after": after})
        counts = {
            f"added{label}": sum(item["changeType"] == "added" for item in changes),
            f"removed{label}": sum(item["changeType"] == "removed" for item in changes),
            f"changed{label}": sum(item["changeType"] == "changed" for item in changes),
        }
        if label == "Nodes":
            counts["unchangedNodes"] = sum(key in left and key in right and left[key] == right[key] for key in set(left) & set(right))
        return tuple(changes), counts

    @staticmethod
    def _identity_fields(key: Any, identity: str | tuple[str, ...]) -> dict[str, str]:
        names = (identity,) if isinstance(identity, str) else identity
        values = (key,) if isinstance(identity, str) else key
        return dict(zip(names, values))


__all__ = ["GovernanceGraphComparisonService"]
