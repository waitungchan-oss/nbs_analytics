import subprocess

from scripts.full_pytest_gate import run_full_pytest_gate


COMMIT = "a" * 40
SOURCE = "b" * 64


def test_full_pytest_gate_records_bounded_pass(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "120 passed, 3 skipped in 2.50s (0:00:02)\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evidence = run_full_pytest_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["schemaVersion"] == "full-pytest-gate-v1"
    assert evidence["status"] == "PASS"
    assert evidence["result"] == {"passed": 120, "failed": 0, "skipped": 3, "durationSeconds": 2.5}
    assert evidence["metadata"]["exitCode"] == 0
    assert "--sandbox-preflight" in calls[0][0]
    assert calls[0][0][calls[0][0].index("--sandbox-preflight") + 1] == "required"


def test_full_pytest_gate_nonzero_is_fail_and_output_is_bounded(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "2 failed, 8 passed in 1.00s\n" + "x" * 10000, "error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evidence = run_full_pytest_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["status"] == "FAIL"
    assert evidence["result"]["failed"] == 2
    assert len(evidence["metadata"]["stdoutTail"]) <= 4000


def test_full_pytest_gate_sandbox_block_is_blocked(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "sandbox capability status: blocked_environment\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evidence = run_full_pytest_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["status"] == "BLOCKED"
    assert evidence["metadata"]["failureCode"] == "sandbox_capability_blocked"


def test_full_pytest_gate_timeout_is_fail_closed(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 3, output="partial")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evidence = run_full_pytest_gate(tmp_path, COMMIT, SOURCE, timeout_seconds=3)
    assert evidence["status"] == "BLOCKED"
    assert evidence["metadata"]["failureCode"] == "timeout"


def test_full_pytest_gate_rejects_invalid_identity(tmp_path):
    try:
        run_full_pytest_gate(tmp_path, "bad", SOURCE)
    except ValueError as exc:
        assert "commit" in str(exc)
    else:
        raise AssertionError("invalid commit must be rejected")
