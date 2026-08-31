from pathlib import Path
import json

from backend.agents.memory_preflight_adapter import read_memory_observation, merge_non_authoritative_observations


def test_memory_observation_is_non_authoritative_and_source_bound(tmp_path: Path):
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.mkdir()
    payload = {"schemaVersion": "strict-review-memory-observation-v1", "sourceFingerprint": "a" * 64, "status": "degraded", "authority": "non_authoritative_memory", "diagnostics": ["provider unavailable"], "hints": []}
    (runtime / "memory-preflight-observation.json").write_text(json.dumps(payload), encoding="utf-8")
    result = read_memory_observation(tmp_path, session_source="a" * 64)
    assert result["status"] == "degraded"
    assert result["authority"] == "non_authoritative_memory"


def test_merge_cannot_change_canonical_status(tmp_path: Path):
    preflight = {"status": "ready", "sourceFingerprint": "a" * 64}
    merged = merge_non_authoritative_observations(preflight, governance={"status": "blocked"}, memory={"status": "ready"})
    assert merged["status"] == "ready"
    assert merged["observations"]["governance"]["status"] == "blocked"
    assert merged["observations"]["memory"]["status"] == "ready"
