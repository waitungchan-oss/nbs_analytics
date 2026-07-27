from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .governance_graph_models import (
    GRAPH_SCHEMA,
    GovernanceEvidenceRef,
    GovernanceGate,
    GovernanceGraphNode,
    GovernanceGraphSchemaError,
    GovernanceGraphSnapshot,
    GovernanceRisk,
)
from .workflow_store import ALLOWED_ARTIFACTS, WorkflowStore


CANONICAL_GRAPH_ARTIFACTS = {
    "risk": "risk-classification.json",
    "spec_gate": "design-spec-gate.json",
    "plan_gate": "plan-gate.json",
    "implementation": "implementation.json",
    "targeted_verification": "targeted-verification.json",
    "review": "review.json",
    "full_verification": "full-verification.json",
    "hermes": "hermes.json",
    "documentation": "documentation-application.json",
    "git_integration": "git-integration.json",
}
NODE_ORDER = tuple(CANONICAL_GRAPH_ARTIFACTS)
_PASS_STATUSES = frozenset({"pass", "passed", "completed", "applied", "committed", "merged", "kept_branch_by_user"})
_FAIL_STATUSES = frozenset({"failed", "changes_required", "fail", "rejected"})
_BLOCKED_STATUSES = frozenset({"blocked", "blocked_missing_runner", "awaiting_target_approval"})


class GovernanceGraphBuilder:
    def __init__(self, project_root: Path, *, store: WorkflowStore | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = store or WorkflowStore(self.project_root)

    def build(self, run_id: str) -> GovernanceGraphSnapshot:
        manifest = self.store.load_manifest(run_id)
        workflow_status = self.store.load_status(run_id)
        artifacts: dict[str, tuple[dict[str, Any], bytes, str]] = {}
        nodes: list[GovernanceGraphNode] = []
        blockers: list[dict[str, str]] = []
        diagnostics: list[dict[str, str]] = []
        risk: GovernanceRisk | None = None

        for node_id in NODE_ORDER:
            artifact_name = CANONICAL_GRAPH_ARTIFACTS[node_id]
            try:
                loaded = self._load_optional_artifact(run_id, artifact_name)
            except (OSError, ValueError, json.JSONDecodeError, PermissionError):
                node = self._blocked_node(node_id, "malformed_artifact")
                blockers.append({"code": "malformed_artifact", "nodeId": node_id})
                diagnostics.append({"code": "malformed_artifact", "nodeId": node_id})
                nodes.append(node)
                continue
            if loaded is None:
                nodes.append(self._missing_node(node_id))
                continue

            payload, raw, generated_at = loaded
            artifacts[node_id] = loaded
            try:
                if node_id == "risk":
                    risk = GovernanceRisk.from_dict(payload)
                status, reason = self._status_for(node_id, payload)
                if self._is_stale(payload, manifest.git_head) and node_id in {
                    "review", "full_verification", "hermes", "documentation", "git_integration",
                }:
                    status, reason = "blocked", "stale_artifact"
                node = self._node(node_id, status, raw, generated_at, reason)
            except (GovernanceGraphSchemaError, KeyError, TypeError, ValueError):
                node = self._blocked_node(node_id, "malformed_artifact")
                blockers.append({"code": "malformed_artifact", "nodeId": node_id})
                diagnostics.append({"code": "malformed_artifact", "nodeId": node_id})
            if node.status == "blocked" and node.reason_code == "stale_artifact":
                blockers.append({"code": "stale_artifact", "nodeId": node_id})
                diagnostics.append({"code": "stale_artifact", "nodeId": node_id})
            nodes.append(node)

        nodes = self._invalidate_descendants(nodes, blockers, diagnostics)
        overall_status = self._overall_status(nodes)
        if overall_status == "blocked_missing_runner":
            blockers.append({"code": "blocked_missing_runner", "nodeId": "documentation"})
        allowed = self._allowed_next(nodes, overall_status)
        generated_at = workflow_status.updated_at
        freshness = {
            "status": "stale" if any(item["code"] == "stale_artifact" for item in blockers) else "fresh",
            "workflowUpdatedAt": generated_at,
            "graphGeneratedAt": generated_at,
        }
        payload = {
            "schemaVersion": GRAPH_SCHEMA,
            "runId": run_id,
            "generatedAt": generated_at,
            "graphFingerprint": "0" * 64,
            "risk": risk.to_dict() if risk is not None else None,
            "authorizationMode": "per_task",
            "overallStatus": overall_status,
            "nodes": [node.to_dict() for node in nodes],
            "allowedNextNodes": list(allowed),
            "blockers": blockers,
            "freshness": freshness,
            "diagnostics": diagnostics,
        }
        payload["graphFingerprint"] = self._sha256(payload)
        return GovernanceGraphSnapshot.from_dict(payload)

    def persist(self, run_id: str) -> GovernanceGraphSnapshot:
        snapshot = self.build(run_id)
        self.store.write_projection(run_id, "governance-graph.json", snapshot.to_dict())
        return snapshot

    def validate(self, run_id: str) -> GovernanceGraphSnapshot:
        return GovernanceGraphSnapshot.from_dict(self.store.read_projection(run_id, "governance-graph.json"))

    def status(self, run_id: str) -> dict[str, Any]:
        snapshot = self.validate(run_id)
        return {
            "schemaVersion": GRAPH_SCHEMA,
            "runId": run_id,
            "graphFingerprint": snapshot.graph_fingerprint,
            "overallStatus": snapshot.overall_status,
            "nodeStatuses": {node.node_id: node.status for node in snapshot.nodes},
            "blockers": [dict(item) for item in snapshot.blockers],
        }

    def _load_optional_artifact(self, run_id: str, name: str) -> tuple[dict[str, Any], bytes, str] | None:
        if name not in ALLOWED_ARTIFACTS:
            raise ValueError("artifact name is not allowed")
        path = self.store._run_file(run_id, name)
        if path.is_symlink():
            self.store._assert_regular_file(path, "workflow artifact")
        if not path.exists():
            return None
        self.store._assert_regular_file(path, "workflow artifact")
        if path.stat().st_size > self.store.stage_artifact_max_bytes:
            raise ValueError("stage artifact exceeds hard cap")
        raw = path.read_bytes()
        payload = self.store.read_artifact(run_id, name)
        generated_at = str(payload.get("generatedAt") or self.store.load_status(run_id).updated_at)
        return payload, raw, generated_at

    @staticmethod
    def _sha256(payload: dict[str, Any]) -> str:
        unsigned = dict(payload)
        unsigned.pop("graphFingerprint", None)
        encoded = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _artifact_sha(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def _node(self, node_id: str, status: str, raw: bytes, generated_at: str, reason: str | None) -> GovernanceGraphNode:
        return GovernanceGraphNode(
            node_id=node_id, node_type=node_id, status=status, attempt=1 if status != "not_started" else 0,
            max_attempts=1, evidence_refs=(GovernanceEvidenceRef(
                "nbs-governance-evidence-ref-v1", CANONICAL_GRAPH_ARTIFACTS[node_id],
                self._artifact_sha(raw), status, generated_at,
            ),), fingerprint=self._artifact_sha(raw), reason_code=reason,
        )

    @staticmethod
    def _missing_node(node_id: str) -> GovernanceGraphNode:
        seed = hashlib.sha256(f"{node_id}:not_started".encode()).hexdigest()
        return GovernanceGraphNode(node_id, node_id, "not_started", 0, 1, (), seed, None)

    @staticmethod
    def _blocked_node(node_id: str, reason: str) -> GovernanceGraphNode:
        seed = hashlib.sha256(f"{node_id}:{reason}".encode()).hexdigest()
        return GovernanceGraphNode(node_id, node_id, "blocked", 1, 1, (), seed, reason)

    @staticmethod
    def _status_for(node_id: str, payload: dict[str, Any]) -> tuple[str, str | None]:
        if node_id == "risk":
            return "passed", None
        if node_id in {"spec_gate", "plan_gate"}:
            GovernanceGate.from_dict(payload)
            return payload["status"], None if payload["status"] == "passed" else "gate_failed"
        if node_id == "implementation":
            value = payload.get("status")
        elif node_id == "targeted_verification":
            commands = payload.get("commands")
            if not isinstance(commands, list) or not commands or any(not isinstance(item, dict) or not isinstance(item.get("exitCode"), int) for item in commands):
                raise ValueError("targeted verification schema is invalid")
            return ("passed", None) if all(item["exitCode"] == 0 for item in commands) else ("failed", "verification_failed")
        elif node_id == "review":
            value = payload.get("verdict")
        elif node_id == "full_verification":
            full = payload.get("fullPytest")
            acceptance = payload.get("acceptance")
            if not isinstance(full, dict) or not isinstance(full.get("exitCode"), int) or not isinstance(acceptance, dict):
                raise ValueError("full verification schema is invalid")
            return ("passed", None) if full["exitCode"] == 0 and acceptance.get("status") == "passed" else ("failed", "verification_failed")
        elif node_id == "hermes":
            value = payload.get("overallStatus")
        elif node_id == "documentation":
            value = payload.get("status")
            if value == "no_documentation_needed":
                return "passed", "deterministic_no_doc"
            if value == "skipped":
                reason_code = payload.get("reasonCode")
                if not isinstance(reason_code, str) or not reason_code:
                    raise ValueError("documentation skip requires reasonCode")
                return "passed", "deterministic_no_doc"
        elif node_id == "git_integration":
            value = payload.get("status") or payload.get("outcome")
            if value in {"committed", "merged", "kept_branch_by_user"}:
                return "passed", None
            if value in _FAIL_STATUSES:
                return "failed", "integration_failed"
            if value in _BLOCKED_STATUSES:
                return "blocked", str(value)
            if value is None:
                return "not_started", None
            raise ValueError("git integration outcome is invalid")
        else:
            value = payload.get("status") or payload.get("outcome")
        if value in _PASS_STATUSES:
            return "passed", None
        if value in _FAIL_STATUSES:
            return "failed", "gate_failed"
        if value in _BLOCKED_STATUSES:
            return "blocked", str(value)
        raise ValueError("canonical artifact status is invalid")

    @staticmethod
    def _is_stale(payload: dict[str, Any], git_head: str) -> bool:
        candidates = [payload.get("gitHead"), payload.get("headSha"), payload.get("sourceGitHead")]
        nested = payload.get("gitDiff")
        if isinstance(nested, dict):
            candidates.extend([nested.get("head"), nested.get("headSha")])
        return any(value is not None and value != git_head for value in candidates)

    @staticmethod
    def _invalidate_descendants(nodes: list[GovernanceGraphNode], blockers: list[dict[str, str]], diagnostics: list[dict[str, str]]) -> list[GovernanceGraphNode]:
        blocked = False
        result = []
        for node in nodes:
            if blocked and node.status == "passed":
                node = GovernanceGraphBuilder._blocked_node(node.node_id, "stale_descendant")
                blockers.append({"code": "stale_descendant", "nodeId": node.node_id})
                diagnostics.append({"code": "stale_descendant", "nodeId": node.node_id})
            result.append(node)
            if node.status in {"blocked", "failed"}:
                blocked = True
        return result

    @staticmethod
    def _overall_status(nodes: list[GovernanceGraphNode]) -> str:
        if any(node.reason_code == "blocked_missing_runner" for node in nodes):
            return "blocked_missing_runner"
        if any(node.status == "blocked" for node in nodes):
            return "blocked"
        if any(node.status == "failed" for node in nodes):
            return "blocked"
        statuses = {node.node_id: node.status for node in nodes}
        if all(statuses[node_id] == "passed" for node_id in NODE_ORDER):
            return "completed"
        if statuses["hermes"] == "passed" and statuses["documentation"] == "not_started":
            return "awaiting_documentation"
        if statuses["documentation"] == "passed" and statuses["git_integration"] == "not_started":
            return "ready_for_integration"
        if statuses["risk"] == "not_started":
            return "not_started"
        return "awaiting_authorization"

    @staticmethod
    def _allowed_next(nodes: list[GovernanceGraphNode], overall_status: str) -> tuple[str, ...]:
        if overall_status in {"blocked", "blocked_missing_runner", "completed"}:
            return ()
        for index, node in enumerate(nodes):
            if node.status != "passed":
                return (node.node_id,)
        return ()


__all__ = ["CANONICAL_GRAPH_ARTIFACTS", "GovernanceGraphBuilder"]
