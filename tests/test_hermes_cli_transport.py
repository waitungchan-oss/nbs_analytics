from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from backend.agents.hermes_cli_transport import (
    CliInvokeRequest,
    CliProbeRequest,
    CliTransportError,
    HermesCliTransportAdapter,
)
from backend.agents.runner_identity import RunnerIdentity


def _identity(*, transport: str = "local_cli") -> RunnerIdentity:
    if transport == "local_cli":
        return RunnerIdentity.from_legacy_local_cli(
            runner_id="hermes-cli",
            provider="hermes",
            model="deepseek-v4-flash",
            profile="max",
            execution_environment="hermes-local",
        )
    return RunnerIdentity.from_legacy_hermes(
        runner_id="hermes-remote",
        provider="hermes",
        model="deepseek-v4-flash",
        profile="max",
        execution_environment="hermes-local",
    )


def _fake_cli(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "hermes-cli"
    path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _request(executable: Path, *, argv: tuple[str, ...] = ("run", "--json"), **kwargs: object) -> CliInvokeRequest:
    return CliInvokeRequest(
        identity=_identity(),
        executable=executable,
        argv=argv,
        cwd=executable.parent,
        source_fingerprint="a" * 64,
        turn_fingerprint="b" * 64,
        manifest_fingerprint="c" * 64,
        command_shape_fingerprint="d" * 64,
        **kwargs,
    )


def test_cli_request_requires_local_cli_identity(tmp_path: Path) -> None:
    executable = _fake_cli(tmp_path, "print('{}')")
    with pytest.raises(CliTransportError, match="local_cli"):
        CliProbeRequest(identity=_identity(transport="remote_api"), executable=executable, argv=("--version",), cwd=tmp_path)


def test_command_policy_rejects_shell_string_and_unapproved_flags(tmp_path: Path) -> None:
    executable = _fake_cli(tmp_path, "print('{}')")
    with pytest.raises(CliTransportError):
        _request(executable, argv=("sh -c 'echo unsafe'",))


def test_probe_and_invoke_accept_bounded_json(tmp_path: Path) -> None:
    executable = _fake_cli(
        tmp_path,
        "import json, sys; print(json.dumps({'version': '1.2.3', 'model': 'deepseek-v4-flash'} if sys.argv[1] == '--version' else {'response': 'ok', 'model': 'deepseek-v4-flash'}))",
    )
    adapter = HermesCliTransportAdapter()
    probe = adapter.probe(CliProbeRequest(identity=_identity(), executable=executable, argv=("--version",), cwd=tmp_path))
    assert probe.status == "ready"
    assert probe.cli_version == "1.2.3"
    result = adapter.invoke(_request(executable))
    assert result.status == "ready"
    assert result.response["response"] == "ok"
    assert result.response_fingerprint


def test_probe_model_mismatch_blocks_capability(tmp_path: Path) -> None:
    executable = _fake_cli(tmp_path, "import json; print(json.dumps({'version': '1.2.3', 'model': 'other-model'}))")
    result = HermesCliTransportAdapter().probe(CliProbeRequest(identity=_identity(), executable=executable, argv=("--version",), cwd=tmp_path))
    assert result.status == "blocked_runner_capability"
    assert result.reason == "observed_model_mismatch"


def test_nonzero_and_malformed_output_are_blocked(tmp_path: Path) -> None:
    nonzero = _fake_cli(tmp_path, "import sys; print('bad'); sys.exit(7)")
    result = HermesCliTransportAdapter().invoke(_request(nonzero))
    assert result.status == "blocked_runner_transport"
    assert result.reason == "non_zero_exit"

    malformed = _fake_cli(tmp_path, "print('not-json')")
    result = HermesCliTransportAdapter().invoke(_request(malformed))
    assert result.status == "blocked_runner_transport"
    assert result.reason == "invalid_response_json"


def test_timeout_and_output_limit_are_bounded(tmp_path: Path) -> None:
    slow = _fake_cli(tmp_path, "import time; time.sleep(2); print('{}')")
    result = HermesCliTransportAdapter().invoke(_request(slow, timeout_seconds=0.1))
    assert result.status == "blocked_runner_transport"
    assert result.reason == "timeout"
    assert result.timed_out is True

    large = _fake_cli(tmp_path, "print('x' * 10000)")
    result = HermesCliTransportAdapter().invoke(_request(large, stdout_limit_bytes=128))
    assert result.status == "blocked_runner_transport"
    assert result.reason == "stdout_limit_exceeded"


def test_environment_is_allowlisted_and_shell_is_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _fake_cli(tmp_path, "import json, os; print(json.dumps({'value': os.getenv('ALLOWED'), 'model': 'deepseek-v4-flash'}))")
    monkeypatch.setenv("SECRET_SHOULD_NOT_PASS", "secret")
    result = HermesCliTransportAdapter().invoke(_request(executable, environment={"ALLOWED": "yes"}))
    assert result.status == "ready"
    assert result.response == {"value": "yes", "model": "deepseek-v4-flash"}
    with pytest.raises(CliTransportError, match="secret-bearing"):
        _request(executable, environment={"ALLOWED": "yes", "SECRET_SHOULD_NOT_PASS": "secret"})


def test_symlink_executable_is_rejected(tmp_path: Path) -> None:
    target = _fake_cli(tmp_path, "print('{}')")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(CliTransportError, match="regular executable"):
        _request(link)
