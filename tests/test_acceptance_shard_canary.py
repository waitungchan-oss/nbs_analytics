import socket
import subprocess

import pytest

from backend.agents.acceptance_rollout_models import RolloutConfig
from backend.agents.evidence_models import canonical_fingerprint
from scripts import acceptance_shard_canary
from scripts.acceptance_shard_canary import _port_readiness, summarize_canary_runs


def test_three_runs_require_no_failures_and_twenty_percent_speedup():
    result = summarize_canary_runs([
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 75, "parity": {"status": "PASS"}},
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 76, "parity": {"status": "PASS"}},
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 77, "parity": {"status": "PASS"}},
    ])

    assert result["rolloutCandidate"] == "eligible"
    assert result["status"] == "PASS"
    assert result["failureCode"] is None
    assert result["runCount"] == 3


@pytest.mark.parametrize(
    "runs, failure_code",
    [
        ([{"status": "FAIL", "serialSeconds": 100, "shardSeconds": 70}] * 3, "canary_run_failed"),
        ([{"status": "PASS", "serialSeconds": 100, "shardSeconds": 81, "parity": {"status": "PASS"}}] * 3, "speedup_threshold_not_met"),
    ],
)
def test_canary_is_ineligible_for_failures_or_insufficient_speedup(runs, failure_code):
    result = summarize_canary_runs(runs)

    assert result["status"] == "BLOCKED"
    assert result["rolloutCandidate"] == "ineligible"
    assert result["failureCode"] == failure_code


def test_canary_requires_explicit_serial_parity():
    result = summarize_canary_runs([
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 75},
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 76},
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 77},
    ])

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_parity_missing"


def test_canary_reports_parity_failure_code():
    result = summarize_canary_runs([
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 75, "parity": {"status": "BLOCKED", "failureCode": "serial_parity_mismatch"}},
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 76, "parity": {"status": "PASS"}},
        {"status": "PASS", "serialSeconds": 100, "shardSeconds": 77, "parity": {"status": "PASS"}},
    ])

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_parity_failed"


def test_port_readiness_uses_a_real_child_tcp_binder():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    assert _port_readiness({"shard": port}) is True


def test_run_canary_preserves_manifest_blocker(monkeypatch, tmp_path):
    monkeypatch.setenv("NBS_ACCEPTANCE_SOURCE_FINGERPRINT", "b" * 64)
    monkeypatch.setenv("NBS_ACCEPTANCE_COMMIT_SHA", "a" * 40)
    real_run = acceptance_shard_canary.subprocess.run

    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(acceptance_shard_canary.subprocess, "run", fake_run)
    monkeypatch.setattr(acceptance_shard_canary, "collect_pytest_manifest", lambda *args, **kwargs: {"status": "FAIL"})

    result = acceptance_shard_canary.run_canary(
        project_root=tmp_path, config=RolloutConfig(shards_enabled=True, shard_count=1)
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "manifest_invalid"
    assert result["runs"][0]["failureCode"] == "manifest_invalid"


def test_run_canary_converts_shard_runtime_error_to_bounded_blocker(monkeypatch, tmp_path):
    monkeypatch.setenv("NBS_ACCEPTANCE_SOURCE_FINGERPRINT", "b" * 64)
    monkeypatch.setenv("NBS_ACCEPTANCE_COMMIT_SHA", "a" * 40)
    real_run = acceptance_shard_canary.subprocess.run

    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, "a" * 40 + "\n", "")
        return real_run(argv, **kwargs)

    nodeids = ["tests/test_example.py::test_one"]
    manifest_unsigned = {
        "schemaVersion": "pytest-test-manifest-v1", "status": "PASS",
        "commitSha": "a" * 40, "sourceFingerprint": "b" * 64, "nodeids": nodeids,
    }
    manifest = {**manifest_unsigned, "manifestFingerprint": canonical_fingerprint(manifest_unsigned)}
    monkeypatch.setattr(acceptance_shard_canary.subprocess, "run", fake_run)
    monkeypatch.setattr(acceptance_shard_canary, "collect_pytest_manifest", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(acceptance_shard_canary, "_serial_control", lambda *args, **kwargs: {"status": "PASS", "result": {"passed": 1, "failed": 0, "skipped": 0}, "serialSeconds": 1.0})
    monkeypatch.setattr(acceptance_shard_canary, "run_pytest_shard", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("child failed")))

    result = acceptance_shard_canary.run_canary(
        project_root=tmp_path, config=RolloutConfig(shards_enabled=True, shard_count=1)
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "shard_runner_error"
    assert result["runs"][0]["failureCode"] == "shard_runner_error"


def test_serial_control_timeout_is_bounded(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(kwargs.get("args", args[0]), 1)

    monkeypatch.setattr(acceptance_shard_canary.subprocess, "run", timeout)

    result = acceptance_shard_canary._serial_control(tmp_path, timeout_seconds=1)

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_control_timeout"
