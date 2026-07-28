from __future__ import annotations

import json

from backend.services.governance_telemetry_service import GovernanceTelemetryService
from test_governance_telemetry_service import _valid_run, _write_graph, _write_implementation, _write_json


def test_canonical_gate_artifacts_are_counted_without_graph_projection(tmp_path):
    run = _valid_run(tmp_path)
    _write_implementation(run)
    _write_json(run / "design-spec-gate.json", {"schemaVersion": "nbs-governance-gate-v1", "gateId": "spec-gate", "status": "passed", "fingerprint": "a" * 64, "evidenceRefs": [], "reasonCode": None})
    _write_json(run / "plan-gate.json", {"schemaVersion": "nbs-governance-gate-v1", "gateId": "plan-gate", "status": "failed", "fingerprint": "b" * 64, "evidenceRefs": [], "reasonCode": "gate_failed"})
    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()
    assert telemetry["gateFailures"]["specGate"]["status"] == "available"
    assert telemetry["gateFailures"]["specGate"]["unknownCount"] == 0
    assert telemetry["gateFailures"]["planGate"]["failed"] == 1


def test_malformed_only_runtime_is_invalid(tmp_path):
    run = _valid_run(tmp_path)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest["unexpected"] = True
    _write_json(run / "manifest.json", manifest)
    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()
    assert telemetry["status"] == "invalid"
    assert telemetry["coverage"]["eligibleRunCount"] == 0


def test_stage_duration_uses_verified_artifact_duration_without_file_mtime(tmp_path):
    run = _valid_run(tmp_path)
    _write_implementation(run, duration_ms=1234)
    (run / "implementation.json").touch()
    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()
    assert telemetry["cycleTimes"]["implementation"]["totalMs"] == 1234
    assert telemetry["cycleTimes"]["implementation"]["observedCount"] == 1


def test_stale_count_only_uses_validated_graph_and_isolates_malformed_run(tmp_path):
    good = _valid_run(tmp_path, "good")
    bad = _valid_run(tmp_path, "bad")
    _write_implementation(good)
    _write_implementation(bad)
    _write_graph(tmp_path, good.name, freshness="stale", overall_status="blocked")
    (bad / "governance-graph.json").write_text("{bad", encoding="utf-8")
    telemetry = GovernanceTelemetryService(tmp_path).build_snapshot()
    assert telemetry["evidenceHealth"]["stale"]["observedCount"] == 1
    assert telemetry["coverage"]["unknownRunCount"] >= 1
    assert str(tmp_path) not in json.dumps(telemetry)


def test_agent_operations_snapshot_exposes_telemetry_without_writes(tmp_path):
    run = _valid_run(tmp_path)
    _write_implementation(run, usage={"inputTokens": 1, "outputTokens": 2, "totalTokens": 3})
    before = (run / "implementation.json").read_bytes()
    from backend.services.agent_operations_service import AgentOperationsService
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    assert snapshot["governanceTelemetry"]["schemaVersion"] == "governance-telemetry-snapshot-v1"
    assert (run / "implementation.json").read_bytes() == before
    assert str(tmp_path) not in json.dumps(snapshot)


def test_agent_operations_passes_captured_runs_to_telemetry(monkeypatch, tmp_path):
    _valid_run(tmp_path)
    captured = []
    def build_from_captured(self, **kwargs):
        captured.append(kwargs["runs"])
        return {"schemaVersion": "governance-telemetry-snapshot-v1", "status": "unavailable"}
    monkeypatch.setattr("backend.services.governance_telemetry_service.GovernanceTelemetryService.build_snapshot", build_from_captured)
    from backend.services.agent_operations_service import AgentOperationsService
    snapshot = AgentOperationsService(tmp_path).build_snapshot()
    assert captured and captured[0] is snapshot["runs"]
