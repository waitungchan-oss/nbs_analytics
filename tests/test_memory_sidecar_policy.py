from __future__ import annotations

import json

import pytest

from backend.agents.memory_sidecar_policy import MemorySidecarPolicy, MemorySidecarPolicyError


def _write_policy(tmp_path):
    path = tmp_path / "memory-sidecar-policy.json"
    path.write_text(json.dumps({
        "schemaVersion": "memory-sidecar-policy-v1",
        "maxItems": 3,
        "maxBytes": 6000,
        "timeoutMs": 800,
        "summaryMaxBytes": 2048,
        "ttlDays": 90,
        "allowedKinds": ["decision", "sop", "failure_pattern", "verification_pattern", "preference"],
        "deniedPatterns": [".env", "**/.env", "*.db", "*.sqlite", "*.csv", "*.xlsx", "*.log", "credentials/**", "**/credentials/**", "Secrets/**", "**/Secrets/**"],
    }), encoding="utf-8")
    return path


def test_policy_loads_exact_limits_and_allowed_kinds(tmp_path):
    policy = MemorySidecarPolicy.from_file(_write_policy(tmp_path))
    assert policy.schema_version == "memory-sidecar-policy-v1"
    assert policy.max_items == 3
    assert policy.max_bytes == 6000
    assert policy.timeout_ms == 800
    assert policy.is_allowed_kind("sop")
    assert not policy.is_allowed_kind("risk_decision")


def test_policy_rejects_limits_outside_pilot_caps(tmp_path):
    policy = MemorySidecarPolicy.from_file(_write_policy(tmp_path))
    with pytest.raises(MemorySidecarPolicyError):
        policy.validate_limits(max_items=4, max_bytes=6000, timeout_ms=800)
    with pytest.raises(MemorySidecarPolicyError):
        policy.validate_limits(max_items=3, max_bytes=6001, timeout_ms=800)
    with pytest.raises(MemorySidecarPolicyError):
        policy.validate_limits(max_items=3, max_bytes=6000, timeout_ms=801)


def test_policy_rejects_unknown_schema_and_denied_paths(tmp_path):
    path = _write_policy(tmp_path)
    policy = MemorySidecarPolicy.from_file(path)
    for denied in (".env", "nested/.env", "exports/data.sqlite", "run.log", "credentials/token.json", "artifacts/credentials/api_key", "runs/x/Secrets/token"):
        assert policy.is_denied_path(denied)
    assert not policy.is_denied_path("docs/verification.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schemaVersion"] = "wrong"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)


def test_policy_rejects_unknown_or_duplicate_allowed_kinds(tmp_path):
    path = _write_policy(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schemaVersion"] = "memory-sidecar-policy-v1"
    payload["allowedKinds"] = ["decision", "unsupported"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)


def test_policy_rejects_missing_mandatory_denied_patterns(tmp_path):
    path = _write_policy(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["deniedPatterns"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)
    payload["deniedPatterns"] = [".env", "**/.env", "*.db", "*.sqlite", "*.csv", "*.xlsx", "*.log", "credentials/**", "**/credentials/**", "Secrets/**", "**/Secrets/**", "*.secret"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)


def test_policy_rejects_zero_summary_cap_and_ttl(tmp_path):
    path = _write_policy(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["summaryMaxBytes"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)
    payload["summaryMaxBytes"] = 2048
    payload["ttlDays"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)
    payload["ttlDays"] = 90
    payload["allowedKinds"] = ["decision", "decision"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)
    payload["summaryMaxBytes"] = 2048
    payload["ttlDays"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)
    payload["ttlDays"] = 90
    payload["allowedKinds"] = ["decision", "decision"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MemorySidecarPolicyError):
        MemorySidecarPolicy.from_file(path)
