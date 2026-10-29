import json
import subprocess
from datetime import datetime, timezone

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import (
    ReleaseGateValidationError,
    validate_release_gate_evidence,
)
from scripts.hermes_gate import run_hermes_gate


COMMIT = "a" * 40
SOURCE = "b" * 64


def _report(status="pass"):
    return {
        "overallStatus": status,
        "results": [{"label": "read-only-policy", "exitCode": 0, "stdout": "writes=0 approval=0 dispatch=0", "stderr": ""}],
    }


def test_hermes_gate_requires_pass_and_binds_identity(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(_report()), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["schemaVersion"] == "hermes-gate-v1"
    assert evidence["status"] == "PASS"
    assert evidence["result"]["overallStatus"] == "pass"
    assert evidence["metadata"]["readOnly"] is True
    assert evidence["metadata"]["telemetry"]["attemptNumber"] == 1
    assert evidence["metadata"]["telemetry"]["durationSeconds"] >= 0
    assert "hermes_post_change_check.py" in " ".join(calls[0])


def test_hermes_gate_can_skip_external_service_acceptance(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(_report()), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE, skip_system_acceptance=True)

    assert evidence["status"] == "PASS"
    assert "--skip-system-acceptance" in calls[0]


def test_hermes_gate_nonpass_report_is_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, json.dumps(_report("fail")), "failure"),
    )
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["status"] == "FAIL"
    assert evidence["metadata"]["failureCode"] == "hermes_nonpass"


def test_hermes_gate_rejects_non_json_and_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "not-json", ""),
    )
    assert run_hermes_gate(tmp_path, COMMIT, SOURCE)["status"] == "FAIL"

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 2, output="partial")

    monkeypatch.setattr(subprocess, "run", timeout)
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE, timeout_seconds=2)
    assert evidence["status"] == "BLOCKED"
    assert evidence["metadata"]["failureCode"] == "timeout"


def test_hermes_gate_rejects_write_or_approval_claim(monkeypatch, tmp_path):
    report = _report()
    report["results"][0]["stdout"] = "writes=1 approval=1 dispatch=1 gateway=start"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(report), ""),
    )
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["status"] == "FAIL"
    assert evidence["metadata"]["failureCode"] == "read_only_boundary_violation"


def test_hermes_gate_rejects_structured_nonzero_read_only_indicators(monkeypatch, tmp_path):
    report = _report()
    report["readOnlyIndicators"] = {"writes": 1, "approvals": 0, "dispatches": 0}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(report), ""),
    )
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["status"] == "FAIL"
    assert evidence["metadata"]["failureCode"] == "read_only_boundary_violation"


def test_hermes_gate_converts_runner_os_error_to_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("hermes missing")))
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE)
    assert evidence["status"] == "BLOCKED"
    assert evidence["metadata"]["failureCode"] == "runner_os_error"


def test_hermes_gate_truncated_output_starts_at_complete_line(monkeypatch, tmp_path):
    report = _report()
    report["padding"] = "prefix/test_agent_runtime.py"
    rendered = json.dumps(report, indent=2)
    slash_index = rendered.rfind("/test_agent_runtime.py")
    padding = "x" * (4000 - (len(rendered) - slash_index))
    report["padding"] += padding
    rendered = json.dumps(report, indent=2)
    assert rendered[-4000] == "/"

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, rendered, ""),
    )
    evidence = run_hermes_gate(tmp_path, COMMIT, SOURCE)

    assert evidence["status"] == "PASS"
    assert len(evidence["metadata"]["stdoutTail"]) <= 4000
    assert not evidence["metadata"]["stdoutTail"].startswith("/")
    validate_release_gate_evidence(
        evidence,
        COMMIT,
        SOURCE,
        now=datetime.now(timezone.utc),
    )


def test_release_gate_still_rejects_complete_absolute_path_output():
    now = datetime.now(timezone.utc).isoformat()
    unsigned = {
        "schemaVersion": "hermes-gate-v1",
        "gate": "hermes",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "startedAt": now,
        "finishedAt": now,
        "result": {},
        "metadata": {"stdoutTail": "/Users/runner/work/nbs_analytics/output.log"},
    }
    evidence = {
        **unsigned,
        "evidenceFingerprint": canonical_fingerprint(unsigned),
    }

    with pytest.raises(ReleaseGateValidationError, match="forbidden secret or path"):
        validate_release_gate_evidence(evidence, COMMIT, SOURCE, now=datetime.now(timezone.utc))
