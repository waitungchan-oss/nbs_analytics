import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agents.acceptance_rollout_models import RolloutConfig
from backend.agents.evidence_models import canonical_fingerprint
from scripts import acceptance_shard_canary
from scripts.acceptance_shard_canary import _port_readiness, summarize_canary_runs


def test_canary_script_entrypoint_bootstraps_project_imports():
    script = Path(__file__).parents[1] / "scripts" / "acceptance_shard_canary.py"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=script.parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


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


def test_canary_speed_threshold_is_diagnostic_only():
    result = summarize_canary_runs([
        {
            "status": "PASS",
            "serialSeconds": 100.0,
            "shardSeconds": 70.0,
            "parity": {"status": "PASS"},
        }
        for _ in range(3)
    ])

    assert result["status"] == "PASS"
    assert result["authority"] == "prototype"
    assert result["formalReleaseEnabled"] is False
    assert result["rolloutCandidate"] == "eligible"


def test_canary_never_marks_missing_or_mismatched_run_as_eligible():
    result = summarize_canary_runs([
        {"status": "PASS", "serialSeconds": 100.0, "shardSeconds": 70.0, "parity": {"status": "PASS"}},
        {"status": "BLOCKED", "serialSeconds": 100.0, "shardSeconds": 0.0, "parity": {"status": "BLOCKED"}},
        {"status": "PASS", "serialSeconds": 100.0, "shardSeconds": 70.0, "parity": {"status": "PASS"}},
    ])

    assert result["rolloutCandidate"] == "ineligible"


def test_canary_source_binding_keeps_prototype_authority():
    result = acceptance_shard_canary._bind_source_identity(
        acceptance_shard_canary.summarize_canary_runs([
            {"status": "PASS", "serialSeconds": 100.0, "shardSeconds": 70.0, "parity": {"status": "PASS"}}
            for _ in range(3)
        ]),
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        manifest_fingerprint="c" * 64,
    )

    assert result["authority"] == "prototype"
    assert result["formalReleaseEnabled"] is False
    assert result["commitSha"] == "a" * 40
    assert result["sourceFingerprint"] == "b" * 64
    assert result["manifestFingerprint"] == "c" * 64


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


def test_source_identity_binding_recomputes_evidence_fingerprint():
    result = acceptance_shard_canary._bind_source_identity(
        {"schemaVersion": "acceptance-shard-canary-v1", "status": "BLOCKED"},
        commit_sha="a" * 40, source_fingerprint="b" * 64,
        manifest_fingerprint="c" * 64,
    )

    assert result["commitSha"] == "a" * 40
    assert result["sourceFingerprint"] == "b" * 64
    assert result["manifestFingerprint"] == "c" * 64
    assert result["evidenceFingerprint"] == canonical_fingerprint({key: value for key, value in result.items() if key != "evidenceFingerprint"})


def test_source_identity_binding_rejects_malformed_manifest_fingerprint():
    with pytest.raises(ValueError, match="manifestFingerprint is invalid"):
        acceptance_shard_canary._bind_source_identity(
            {"schemaVersion": "acceptance-shard-canary-v1", "status": "BLOCKED"},
            commit_sha="a" * 40,
            source_fingerprint="b" * 64,
            manifest_fingerprint="not-a-sha256",
        )


def test_v2_shard_binding_keeps_prototype_authority_and_adds_lineage():
    result = acceptance_shard_canary._bind_source_identity(
        acceptance_shard_canary.summarize_canary_runs([
            {"status": "PASS", "serialSeconds": 100.0, "shardSeconds": 70.0, "parity": {"status": "PASS"}}
            for _ in range(3)
        ]),
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        manifest_fingerprint="c" * 64,
        contract_fingerprint="d" * 64,
        test_population_fingerprint="e" * 64,
    )

    assert result["authority"] == "prototype"
    assert result["formalReleaseEnabled"] is False
    assert result["contractFingerprint"] == "d" * 64
    assert result["testPopulationFingerprint"] == "e" * 64


def test_canary_summary_retains_lineage_for_each_repeat_and_rejects_population_drift():
    runs = [
        {
            "status": "PASS",
            "serialSeconds": 100.0,
            "shardSeconds": 70.0,
            "parity": {"status": "PASS"},
            "manifestFingerprint": "c" * 64,
            "contractFingerprint": "d" * 64,
            "testPopulationFingerprint": "e" * 64,
        }
        for _ in range(3)
    ]

    result = summarize_canary_runs(runs)

    assert result["status"] == "PASS"
    assert all(run["contractFingerprint"] == "d" * 64 for run in result["runs"])
    assert all(run["testPopulationFingerprint"] == "e" * 64 for run in result["runs"])

    runs[2]["testPopulationFingerprint"] = "f" * 64
    drifted = summarize_canary_runs(runs)
    assert drifted["status"] == "BLOCKED"
    assert drifted["failureCode"] == "canary_population_mismatch"

    runs[2]["testPopulationFingerprint"] = "e" * 64
    runs[2].pop("contractFingerprint")
    incomplete = summarize_canary_runs(runs)
    assert incomplete["status"] == "BLOCKED"
    assert incomplete["failureCode"] in {"canary_lineage_invalid", "canary_lineage_mismatch"}


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
