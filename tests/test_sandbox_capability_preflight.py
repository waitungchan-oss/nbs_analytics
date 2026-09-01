from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from backend.agents.sandbox_capability_preflight import (
    SandboxCapabilityError,
    SandboxCapabilityEvidence,
    SandboxCapabilityPreflight,
    SandboxProbeRequest,
    run_sandbox_probe,
)
import backend.agents.sandbox_capability_preflight as preflight
from backend.agents.sandbox_capability_receipt import read_capability_evidence, write_capability_evidence


EXPECTED_FIELDS = {
    "schemaVersion", "status", "platform", "backendPathFingerprint", "probeProfileFingerprint",
    "workspaceFingerprint", "capabilities", "failureCode", "diagnostics", "startedAt", "finishedAt",
    "evidenceFingerprint",
}


def _request(tmp_path: Path, backend: Path | None = None) -> SandboxProbeRequest:
    backend = backend or Path("/usr/bin/sandbox-exec")
    return SandboxProbeRequest(
        expected_platform="darwin",
        workspace_fingerprint="a" * 64,
        backend_path=backend,
        probe_root=tmp_path,
        timeout_seconds=2.0,
        output_limit_bytes=4096,
        probe_profile_fingerprint="b" * 64,
    )


def _available() -> SandboxCapabilityEvidence:
    return SandboxCapabilityEvidence.available(
        platform="darwin",
        backend_path=Path("/usr/bin/sandbox-exec"),
        probe_profile_fingerprint="b" * 64,
        workspace_fingerprint="a" * 64,
    )


def test_evidence_fingerprint_and_exact_fields() -> None:
    evidence = _available()
    payload = evidence.to_dict()
    assert set(payload) == EXPECTED_FIELDS
    assert SandboxCapabilityEvidence.from_dict(payload).to_dict() == payload


def test_tampered_or_stale_evidence_is_invalid() -> None:
    payload = _available().to_dict()
    payload["workspaceFingerprint"] = "f" * 64
    with pytest.raises(SandboxCapabilityError, match="fingerprint"):
        SandboxCapabilityEvidence.from_dict(payload, expected_workspace_fingerprint="a" * 64)

    payload = _available().to_dict()
    payload["evidenceFingerprint"] = "0" * 64
    with pytest.raises(SandboxCapabilityError, match="fingerprint"):
        SandboxCapabilityEvidence.from_dict(payload)


def test_evidence_rejects_unbounded_or_secret_diagnostics() -> None:
    payload = _available().to_dict()
    payload["diagnostics"] = ["API_KEY=secret"]
    payload["evidenceFingerprint"] = _available().fingerprint_for(payload)
    with pytest.raises(SandboxCapabilityError, match="sensitive"):
        SandboxCapabilityEvidence.from_dict(payload)

    payload = _available().to_dict()
    payload["diagnostics"] = ["x" * 4097]
    with pytest.raises(SandboxCapabilityError, match="diagnostics"):
        SandboxCapabilityEvidence.from_dict(payload)


def test_atomic_receipt_round_trip_and_symlink_rejection(tmp_path: Path) -> None:
    path = tmp_path / "sandbox-capability.json"
    evidence = _available()
    assert write_capability_evidence(path, evidence) == path
    assert read_capability_evidence(path, expected_workspace_fingerprint="a" * 64).to_dict() == evidence.to_dict()

    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(SandboxCapabilityError, match="regular file"):
        read_capability_evidence(link)


def test_probe_request_rejects_unsafe_paths_and_limits(tmp_path: Path) -> None:
    with pytest.raises(SandboxCapabilityError):
        _request(tmp_path, Path("relative/sandbox-exec"))
    with pytest.raises(SandboxCapabilityError):
        SandboxProbeRequest("darwin", "a" * 64, Path("/usr/bin/sandbox-exec"), tmp_path, 601, 4096, "b" * 64)


def test_probe_profile_uses_resolved_interpreter_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "python3"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(preflight.sys, "executable", str(executable))
    profile = preflight._profile(_request(tmp_path), tmp_path / "allowed-write.txt")
    assert f'(allow process-exec (literal {json.dumps(str(executable))}))' in profile


def test_preflight_interface_is_available() -> None:
    assert hasattr(SandboxCapabilityPreflight, "probe")


def _fake_backend(tmp_path: Path, output: str, *, exit_code: int = 0, sleep_seconds: float = 0, stderr: bool = False) -> Path:
    path = tmp_path / "sandbox-exec"
    stream = "sys.stderr" if stderr else "sys.stdout"
    path.write_text(
        f"#!{sys.executable}\nimport sys, time\ntime.sleep({sleep_seconds})\nprint({output!r}, file={stream})\nraise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_outer_sandbox_denial_is_blocked_environment(tmp_path: Path) -> None:
    backend = _fake_backend(tmp_path, "sandbox-exec: sandbox_apply: Operation not permitted", exit_code=71, stderr=True)
    evidence = run_sandbox_probe(_request(tmp_path, backend))
    assert evidence.status == "blocked_environment"
    assert evidence.failure_code == "sandbox_apply_denied"


def test_probe_success_returns_available_evidence(tmp_path: Path) -> None:
    output = json.dumps({"status": "available", "capabilities": {"applicationApplied": True, "filesystemPolicyEnforced": True, "processPolicyEnforced": True, "networkPolicyEnforced": True}})
    backend = _fake_backend(tmp_path, output)
    evidence = run_sandbox_probe(_request(tmp_path, backend))
    assert evidence.status == "available"
    assert all(evidence.capabilities.values())


def test_probe_timeout_is_blocked_and_bounded(tmp_path: Path) -> None:
    backend = _fake_backend(tmp_path, "{}", sleep_seconds=0.5)
    request = SandboxProbeRequest("darwin", "a" * 64, backend, tmp_path, 0.05, 4096, "b" * 64)
    evidence = run_sandbox_probe(request)
    assert evidence.status == "blocked_environment"
    assert evidence.failure_code == "probe_timeout"


def test_probe_malformed_output_is_invalid_evidence(tmp_path: Path) -> None:
    backend = _fake_backend(tmp_path, "not-json")
    evidence = run_sandbox_probe(_request(tmp_path, backend))
    assert evidence.status == "invalid_evidence"
    assert evidence.failure_code == "probe_output_invalid"


def test_probe_missing_backend_is_blocked(tmp_path: Path) -> None:
    evidence = run_sandbox_probe(_request(tmp_path, tmp_path / "missing-sandbox-exec"))
    assert evidence.status == "blocked_environment"
    assert evidence.failure_code == "backend_missing"


def test_non_darwin_is_not_applicable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.agents.sandbox_capability_preflight.sys.platform", "linux")
    evidence = run_sandbox_probe(_request(tmp_path, tmp_path / "missing"))
    assert evidence.status == "not_applicable"
    assert evidence.failure_code == "platform_not_applicable"
