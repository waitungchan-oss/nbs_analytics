from pathlib import Path
import json

from backend.agents.governance_graph_preflight_adapter import read_governance_observation


def test_graph_observation_is_bounded_source_bound_and_read_only(tmp_path: Path):
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.mkdir()
    payload = {"schemaVersion": "governance-graph-preflight-observation-v1", "sourceFingerprint": "a" * 64, "status": "available", "graphFingerprint": "b" * 64, "blockers": [], "diagnostics": []}
    (runtime / "governance-graph-observation.json").write_text(json.dumps(payload), encoding="utf-8")
    result = read_governance_observation(tmp_path, session_source="a" * 64)
    assert result["status"] == "available"
    assert result["authority"] == "read_only_governance_graph"
    assert result["sourceFingerprint"] == "a" * 64
    assert (runtime / "governance-graph-observation.json").read_text(encoding="utf-8") == json.dumps(payload)


def test_graph_missing_or_mismatched_source_never_becomes_ready(tmp_path: Path):
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.mkdir()
    (runtime / "governance-graph-observation.json").write_text(json.dumps({"sourceFingerprint": "b" * 64}), encoding="utf-8")
    assert read_governance_observation(tmp_path, session_source="a" * 64)["status"] == "invalid_evidence"
    assert read_governance_observation(tmp_path / "missing", session_source="a" * 64)["status"] == "unavailable"
