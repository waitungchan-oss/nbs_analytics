from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .governance_graph_models import GovernanceGraphSchemaError, GovernanceGraphSnapshot
from .governance_graph_query_models import GovernanceGraphQuery, GovernanceGraphQueryResult


GRAPH_FILE = "governance-graph.json"
MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024
_STATUS_PRECEDENCE = ("invalid", "blocked", "unknown", "available")


class GovernanceGraphQueryService:
    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve(strict=True)
        self.runtime_root = Path(runtime_root) if runtime_root is not None else self.project_root / ".nbs_agent_runtime"
        self._assert_directory(self.runtime_root, "runtime root")
        self.runs_root = self.runtime_root / "runs"
        self._assert_directory(self.runs_root, "runs root")

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
        if run_id is None:
            raise ValueError("runId is required for deterministic query")
        run_dir = self._run_dir(run_id)
        snapshot_path = run_dir / GRAPH_FILE
        if snapshot_path.is_symlink():
            return self._invalid(query, "unsafe_snapshot")
        if not snapshot_path.exists():
            return GovernanceGraphQueryResult.from_parts(
                status="unavailable", snapshot_identity=None, filters=query,
                matched_nodes=(), matched_edges=(), evidence_refs=(),
                unknown_count=0, invalid_count=0, blocked_count=0, diagnostics=(),
            )
        try:
            self._assert_regular_file(snapshot_path, "graph snapshot")
            if snapshot_path.stat().st_size > MAX_SNAPSHOT_BYTES:
                raise ValueError("graph snapshot exceeds hard cap")
            payload = self._read_json(snapshot_path)
            snapshot = GovernanceGraphSnapshot.from_dict(payload)
            if snapshot.run_id != run_id:
                raise ValueError("graph snapshot run ID does not match query run ID")
            identity = {
                "runId": snapshot.run_id,
                "graphFingerprint": snapshot.graph_fingerprint,
                "generatedAt": snapshot.generated_at,
                "freshness": snapshot.freshness["status"],
            }
            if snapshot_fingerprint is not None and snapshot.graph_fingerprint != snapshot_fingerprint:
                raise ValueError("snapshot fingerprint does not match query")
        except (OSError, ValueError, GovernanceGraphSchemaError, json.JSONDecodeError):
            return self._invalid(query, "invalid_snapshot")

        nodes = [self._node_record(node) for node in snapshot.nodes]
        refs = [self._ref_record(ref) for node in snapshot.nodes for ref in node.evidence_refs]
        nodes = [node for node in nodes if self._node_matches(node, query.filters)]
        refs = [ref for ref in refs if self._ref_matches(ref, query.filters)]
        edges: list[dict[str, Any]] = []
        if query.filters.get("edgeType"):
            edges = []
        counts = self._counts(nodes)
        status = next((candidate for candidate in _STATUS_PRECEDENCE if counts[candidate] > 0), "available")
        return GovernanceGraphQueryResult.from_parts(
            status=status, snapshot_identity=identity, filters=query,
            matched_nodes=tuple(nodes), matched_edges=tuple(edges), evidence_refs=tuple(refs),
            unknown_count=counts["unknown"], invalid_count=counts["invalid"],
            blocked_count=counts["blocked"], diagnostics=(),
        )

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
            raise ValueError("runId must be a safe single path component")
        path = self.runs_root / run_id
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self.runs_root.resolve())
        except ValueError as exc:
            raise ValueError("runId escapes runs root") from exc
        self._assert_directory(path, "run directory")
        return path

    @staticmethod
    def _assert_directory(path: Path, label: str) -> None:
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"{label} must be a regular directory")

    @staticmethod
    def _assert_regular_file(path: Path, label: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} must be a regular file")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle, object_pairs_hook=reject_duplicates)
        if not isinstance(payload, dict):
            raise ValueError("graph snapshot must be an object")
        return payload

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
            artifact_kind = ref_filters["artifactKind"]
            evidence_status = ref_filters["evidenceStatus"]
            return (
                artifact_kind == node.get("nodeType")
                and (evidence_status is None or evidence_status == node.get("status"))
            )
        return any(
            GovernanceGraphQueryService._ref_matches(ref, ref_filters)
            for ref in refs if isinstance(ref, dict)
        )

    @staticmethod
    def _ref_matches(ref: dict[str, Any], filters: dict[str, str]) -> bool:
        path = str(ref.get("path", ""))
        artifact_kind = Path(path).stem.replace("-", "_")
        return all(
            actual == expected
            for actual, expected in (
                (artifact_kind, filters.get("artifactKind")),
                (ref.get("status"), filters.get("evidenceStatus")),
            )
            if expected is not None
        )

    @staticmethod
    def _counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "invalid": sum(node.get("status") == "invalid" for node in nodes),
            "blocked": sum(node.get("status") == "blocked" for node in nodes),
            "unknown": sum(node.get("status") == "unknown" for node in nodes),
            "available": 0,
        }

    @staticmethod
    def _invalid(query: GovernanceGraphQuery, code: str) -> GovernanceGraphQueryResult:
        return GovernanceGraphQueryResult.from_parts(
            status="invalid", snapshot_identity=None, filters=query,
            matched_nodes=(), matched_edges=(), evidence_refs=(),
            unknown_count=0, invalid_count=1, blocked_count=0,
            diagnostics=({"code": code},),
        )
