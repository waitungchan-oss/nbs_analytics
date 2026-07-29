from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical_evidence_reader import CanonicalEvidenceReader
from .canonical_evidence_registry import CanonicalEvidenceRegistry
from .governance_graph_evidence_lineage_models import (
    EvidenceLineageDetail,
    EvidenceLineageInput,
    EvidenceLineageLink,
    EvidenceLineageResult,
    LINEAGE_POLICY_VERSION,
)
from .governance_graph_snapshot_reader import GovernanceGraphSnapshotReader


_PRECEDENCE = {"invalid": 6, "fingerprint_mismatch": 5, "blocked": 4, "stale": 3, "unknown": 2, "missing": 1, "available": 0}


class GovernanceGraphEvidenceLineageService:
    """Resolve one explicit evidence identity into a bounded read-only lineage result."""

    def __init__(self, project_root: Path | None = None, runtime_root: Path | None = None, *, snapshot_reader=None, canonical_reader=None, registry=None) -> None:
        if snapshot_reader is not None and canonical_reader is not None:
            self.snapshot_reader = snapshot_reader
            self.canonical_reader = canonical_reader
        else:
            if project_root is None:
                raise ValueError("project_root is required when readers are not supplied")
            self.snapshot_reader = GovernanceGraphSnapshotReader(project_root, runtime_root)
            self.canonical_reader = CanonicalEvidenceReader(project_root, runtime_root)
        self.registry = registry or CanonicalEvidenceRegistry()

    def resolve(self, request: EvidenceLineageInput) -> EvidenceLineageResult:
        # Read the immutable snapshot first; compare the caller's fingerprint here so
        # the service can preserve the E-1 `fingerprint_mismatch` precedence rather
        # than inheriting the reader's generic `invalid_snapshot` classification.
        read_result = self.snapshot_reader.read(request.run_id, expected_fingerprint=None)
        if read_result.status == "invalid":
            return self._result(request, "invalid", None, (), (), "invalid_snapshot")
        if read_result.status != "available" or read_result.snapshot is None or read_result.snapshot_identity is None:
            return self._result(request, "unknown", None, (), (), "missing_snapshot")

        identity = read_result.snapshot_identity
        snapshot = read_result.snapshot
        if request.snapshot_fingerprint is not None and identity.get("graphFingerprint") != request.snapshot_fingerprint:
            return self._result(request, "fingerprint_mismatch", identity.get("graphFingerprint"), (), (), "snapshot_fingerprint_mismatch")
        if request.evidence is None:
            return self._result(request, "missing", identity.get("graphFingerprint"), (), (), "missing_evidence")
        node_ref_sha = None
        if request.source_kind == "node":
            node_ref_sha = self._node_ref_sha(snapshot, request.source_identity, request.evidence.path)
            if node_ref_sha is None:
                return self._result(request, "unknown", identity.get("graphFingerprint"), (), (), "unknown_node_evidence_link")

        canonical = self._canonical_record(request.run_id, request.evidence.path)
        if canonical is None:
            return self._result(request, "invalid", identity.get("graphFingerprint"), (), (), "invalid_registry")
        actual_sha = canonical.get("sha256")
        fingerprint_matched = actual_sha == request.evidence.sha256 and actual_sha is not None and (node_ref_sha in {None, request.evidence.sha256})
        status = str(canonical.get("status", "invalid"))
        if not fingerprint_matched:
            overall = "fingerprint_mismatch"
        elif status == "invalid":
            overall = "invalid"
        elif status == "blocked":
            overall = "blocked"
        elif identity.get("freshness") in {"stale", "outdated"}:
            overall = "stale"
        elif status in {"unknown", "missing"}:
            overall = "unknown"
        else:
            overall = "available"
        detail_status = overall if overall in {"stale", "fingerprint_mismatch"} else status
        if overall in {"unknown", "invalid"}:
            return self._result(request, overall, identity.get("graphFingerprint"), (), (), str(canonical.get("reason") or "unknown_evidence"))
        entry = self.registry.for_kind(self._kind_for_path(request.evidence.path))
        detail = EvidenceLineageDetail(
            path=request.evidence.path,
            sha256=actual_sha or request.evidence.sha256,
            artifact_kind=entry.artifact_kind,
            schema_version=entry.schema_version,
            writer=entry.writer,
            status=detail_status,
            reason_code=canonical.get("reason"),
            finalized_at=canonical.get("finalizedAt"),
            fingerprint_matched=fingerprint_matched,
        )
        relation = {"node": "node_evidence", "finding": "finding_evidence", "impact": "impact_evidence"}[request.source_kind]
        link = EvidenceLineageLink(relation, request.source_identity, request.evidence.path, detail.sha256)
        return self._result(request, overall, identity.get("graphFingerprint"), (detail,), (link,), None)

    def _canonical_record(self, run_id: str, path: str) -> dict[str, Any] | None:
        kind = self._kind_for_path(path)
        run_root = getattr(self.canonical_reader, "runs_root", None)
        if run_root is None:
            return None
        run_dir = Path(run_root) / run_id
        records = self.canonical_reader.read(run_dir)
        return records.get(kind) if isinstance(records, dict) else None

    def _kind_for_path(self, path: str) -> str:
        for entry in self.registry.entries():
            if entry.filename == path:
                return entry.artifact_kind
        raise ValueError("evidence path is not registry-owned")

    @staticmethod
    def _node_ref_sha(snapshot: Any, identity: str, path: str) -> str | None:
        for node in getattr(snapshot, "nodes", ()):
            if getattr(node, "node_id", None) != identity:
                continue
            for ref in getattr(node, "evidence_refs", ()):
                if getattr(ref, "path", None) == path:
                    return getattr(ref, "sha256", None)
            return None
        return None

    @staticmethod
    def _result(request, status, snapshot_fingerprint, evidence, links, reason):
        diagnostics = () if reason is None else ({"code": reason, "summary": reason},)
        return EvidenceLineageResult(
            status=status,
            run_id=request.run_id,
            snapshot_fingerprint=snapshot_fingerprint if status not in {"invalid", "unknown", "missing"} else None,
            source_kind=request.source_kind,
            source_identity=request.source_identity,
            evidence=tuple(evidence),
            links=tuple(links),
            diagnostics=diagnostics,
            lineage_fingerprint=None,
        ).with_fingerprint()


__all__ = ["GovernanceGraphEvidenceLineageService"]
