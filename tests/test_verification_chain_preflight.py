from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verification_chain import validate_preflight_for_session


def _preflight(source: str, status: str = "ready") -> dict:
    return {
        "schemaVersion": "strict-review-preflight-v1", "status": status,
        "sessionId": "s1", "sourceFingerprint": source, "bundleFingerprint": "b" * 64,
        "changedFiles": ["backend/agents/x.py"],
        "coverage": {"targetedTests": "pass", "compileStatic": "pass", "diffCheck": "pass", "runnerCapability": "available", "contextCompatibility": "ready", "governanceLineage": "unavailable", "memoryReadiness": "unavailable"},
        "generatedEvidence": ["verification-v1.json"], "verificationPath": ".nbs_agent_runtime/verification_sessions/s1/verification-v1.json", "diagnostics": [], "createdAt": "now",
    }


def test_preflight_contract_rejects_non_ready_before_review(tmp_path: Path):
    payload = _preflight("a" * 64, "verification_failed")
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not ready"):
        validate_preflight_for_session(str(path), source_fingerprint="a" * 64)


def test_preflight_artifact_is_source_bound_and_exact(tmp_path: Path):
    payload = _preflight("a" * 64)
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_preflight_for_session(str(path), source_fingerprint="a" * 64)["sourceFingerprint"] == "a" * 64
    payload["sourceFingerprint"] = "d" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        validate_preflight_for_session(str(path), source_fingerprint="a" * 64)
