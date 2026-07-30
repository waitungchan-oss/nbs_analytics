from __future__ import annotations

from types import SimpleNamespace

from backend.agents.governance_graph_evidence_lineage_models import EvidenceLineageInput
from backend.agents.governance_graph_evidence_lineage_service import GovernanceGraphEvidenceLineageService
from backend.agents.governance_graph_snapshot_reader import SnapshotReadResult


SHA = "a" * 64


def _request(kind="node", identity="protected_incident", evidence=True, sha=SHA):
    return EvidenceLineageInput.from_dict({
        "schemaVersion": "governance-graph-evidence-lineage-input-v1",
        "runId": "run-123",
        "snapshotFingerprint": SHA,
        "source": {"kind": kind, "identity": identity},
        "evidence": {"path": "protected-incident.json", "sha256": sha} if evidence else None,
    })


def _reader(*, snapshot_status="available", freshness="fresh", canonical_status="available", canonical_sha=SHA):
    ref = SimpleNamespace(path="protected-incident.json", sha256=SHA)
    node = SimpleNamespace(node_id="protected_incident", node_type="protected_incident", evidence_refs=(ref,))
    snapshot = SimpleNamespace(
        run_id="run-123", graph_fingerprint=SHA, freshness={"status": freshness},
        nodes=(node,),
    )
    snapshot_result = SnapshotReadResult(
        snapshot_status, snapshot if snapshot_status == "available" else None,
        {"runId": "run-123", "graphFingerprint": SHA, "generatedAt": "2026-07-29T08:00:00+00:00", "freshness": freshness} if snapshot_status == "available" else None,
        (),
    )
    class Snapshot:
        def read(self, run_id, expected_fingerprint=None):
            return snapshot_result
    class Canonical:
        runs_root = "/safe/runs"

        def read(self, run_dir):
            return {
                "protected_incident": {
                    "state": "detected", "status": canonical_status, "reason": None,
                    "finalizedAt": "2026-07-29T08:00:00+00:00",
                    "artifact": "protected-incident.json", "sha256": canonical_sha,
                }
            }
    return Snapshot(), Canonical()


def test_available_node_evidence_returns_registry_bounded_lineage():
    snapshot, canonical = _reader()
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request())

    payload = result.to_dict()
    assert payload["status"] == "available"
    assert payload["evidence"][0]["artifactKind"] == "protected_incident"
    assert payload["evidence"][0]["writer"] == "protected_incident_recorder"
    assert payload["links"][0]["relation"] == "node_evidence"
    assert payload["lineageFingerprint"]


def test_missing_explicit_evidence_does_not_infer_from_source_identity():
    snapshot, canonical = _reader()
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request(evidence=False))

    assert result.status in {"missing", "unknown"}
    assert result.to_dict()["evidence"] == []
    assert result.to_dict()["links"] == []
    assert result.to_dict()["lineageFingerprint"] is None


def test_finding_and_impact_use_explicit_identity_without_reverse_lookup():
    snapshot, canonical = _reader()
    for kind in ("finding", "impact"):
        result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request(kind=kind, identity="risk-1", evidence=False))
        assert result.status == "missing"
        assert result.to_dict()["links"] == []


def test_explicit_sha_mismatch_has_no_false_available_result():
    snapshot, canonical = _reader()
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request(sha="b" * 64))

    assert result.status == "fingerprint_mismatch"
    assert result.to_dict()["evidence"][0]["fingerprintMatched"] is False


def test_blocked_and_stale_are_preserved_from_upstream_state():
    for kwargs, expected in [({"canonical_status": "blocked"}, "blocked"), ({"freshness": "stale"}, "stale")]:
        snapshot, canonical = _reader(**kwargs)
        result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request())
        assert result.status == expected


def test_invalid_snapshot_is_fail_closed_and_read_only():
    snapshot, canonical = _reader(snapshot_status="invalid")
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request())

    assert result.status == "invalid"
    assert result.to_dict()["snapshotFingerprint"] is None
    assert result.to_dict()["lineageFingerprint"] is None


def test_node_graph_sha_mismatch_is_fingerprint_mismatch():
    snapshot, canonical = _reader()
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request(sha="b" * 64))
    assert result.status == "fingerprint_mismatch"


def test_snapshot_expected_fingerprint_mismatch_is_invalid_and_deterministic():
    snapshot, canonical = _reader()
    request = _request()
    first = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(request).to_dict()
    second = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(request).to_dict()
    assert first == second
    assert first["lineageFingerprint"]


def test_custom_canonical_reader_without_safe_runs_root_fails_closed():
    snapshot, canonical = _reader()
    class UnsafeCanonical:
        def read(self, run_dir):
            return canonical.read(run_dir)
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=UnsafeCanonical()).resolve(_request())
    assert result.status == "invalid"


def test_graph_ref_sha_mismatch_is_detected_when_request_and_canonical_match():
    snapshot, canonical = _reader(canonical_sha="b" * 64)
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request(sha="b" * 64))
    assert result.status == "fingerprint_mismatch"


def test_unknown_canonical_state_is_not_promoted_to_available():
    snapshot, canonical = _reader(canonical_status="unknown")
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request())
    assert result.status == "unknown"


def test_fingerprint_mismatch_precedes_blocked_and_stale():
    for kwargs in ({"canonical_status": "blocked"}, {"freshness": "stale"}):
        snapshot, canonical = _reader(**kwargs)
        result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request(sha="b" * 64))
        assert result.status == "fingerprint_mismatch"


def test_projection_has_no_raw_or_absolute_path_fields_and_readers_are_not_writers():
    snapshot, canonical = _reader()
    calls = []
    original = canonical.read
    canonical.read = lambda run_dir: (calls.append(str(run_dir)) or original(run_dir))
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(_request())
    output = result.to_dict()
    serialized = str(output)
    assert calls and calls[0].endswith("/run-123")
    assert "rawPayload" not in output and "prompt" not in output and "secret" not in serialized
    assert "/tmp" not in serialized and "sqlite" not in serialized.lower()


def test_explicit_snapshot_fingerprint_mismatch_keeps_distinct_status():
    snapshot, canonical = _reader()
    request = EvidenceLineageInput.from_dict({
        "schemaVersion": "governance-graph-evidence-lineage-input-v1",
        "runId": "run-123",
        "snapshotFingerprint": "b" * 64,
        "source": {"kind": "node", "identity": "protected_incident"},
        "evidence": {"path": "protected-incident.json", "sha256": SHA},
    })
    result = GovernanceGraphEvidenceLineageService(snapshot_reader=snapshot, canonical_reader=canonical).resolve(request)
    assert result.status == "fingerprint_mismatch"
