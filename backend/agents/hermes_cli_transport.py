"""Fail-closed, bounded transport adapter for a local Hermes CLI."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Literal, Mapping

from backend.agents.runner_identity import RunnerIdentity


CliTransportStatus = Literal["ready", "blocked_runner_capability", "blocked_runner_transport", "invalid_evidence"]
_SHA256_LENGTH = 64
_MAX_TIMEOUT_SECONDS = 600.0
_MAX_OUTPUT_BYTES = 10_000_000
_MAX_ARG_BYTES = 4096
_DEFAULT_OUTPUT_BYTES = 1_000_000
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SENSITIVE_ENV_RE = re.compile(r"(?:SECRET|TOKEN|PASSWORD|API[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL)", re.IGNORECASE)


class CliTransportError(ValueError):
    """Raised when a CLI request is unsafe or malformed."""


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise CliTransportError(f"{label} must be lowercase sha256")


def _validate_executable(executable: Path, cwd: Path) -> None:
    if not isinstance(executable, Path) or not executable.is_absolute():
        raise CliTransportError("executable must be an absolute path")
    if executable.is_symlink() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise CliTransportError("executable must be a regular executable")
    if not isinstance(cwd, Path) or not cwd.is_absolute() or cwd.is_symlink() or not cwd.is_dir():
        raise CliTransportError("cwd must be a regular directory")


def _validate_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(argv, tuple) or not argv:
        raise CliTransportError("argv must be a non-empty tuple")
    for item in argv:
        if not isinstance(item, str) or not item or len(item.encode("utf-8")) > _MAX_ARG_BYTES:
            raise CliTransportError("argv items must be bounded strings")
        if "\x00" in item or item in {"-c", "--command"} or item.startswith("sh -c") or item.startswith("bash -c"):
            raise CliTransportError("shell command argv is not allowed")
    return argv


def _validate_limits(timeout_seconds: float, *limits: int) -> None:
    if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS:
        raise CliTransportError("timeout must be bounded")
    if any(isinstance(limit, bool) or not 0 < limit <= _MAX_OUTPUT_BYTES for limit in limits):
        raise CliTransportError("output limits must be bounded")


def _validate_environment(environment: Mapping[str, str]) -> None:
    if not isinstance(environment, Mapping):
        raise CliTransportError("environment must be a mapping")
    for key, value in environment.items():
        if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key) or not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_ARG_BYTES:
            raise CliTransportError("environment must contain bounded uppercase keys and values")
        if _SENSITIVE_ENV_RE.search(key):
            raise CliTransportError("secret-bearing environment keys are not allowed")


@dataclass(frozen=True)
class CliProbeRequest:
    identity: RunnerIdentity
    executable: Path
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: float = 60.0
    stdout_limit_bytes: int = _DEFAULT_OUTPUT_BYTES
    stderr_limit_bytes: int = _DEFAULT_OUTPUT_BYTES
    response_limit_bytes: int = _DEFAULT_OUTPUT_BYTES
    environment: Mapping[str, str] = field(default_factory=dict)
    command_shape_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.identity.transport != "local_cli":
            raise CliTransportError("CLI request requires local_cli identity transport")
        _validate_executable(self.executable, self.cwd)
        _validate_argv(self.argv)
        _validate_limits(self.timeout_seconds, self.stdout_limit_bytes, self.stderr_limit_bytes, self.response_limit_bytes)
        _validate_environment(self.environment)
        if self.command_shape_fingerprint is not None:
            _sha256(self.command_shape_fingerprint, "command shape fingerprint")


@dataclass(frozen=True)
class CliInvokeRequest(CliProbeRequest):
    source_fingerprint: str = ""
    turn_fingerprint: str = ""
    manifest_fingerprint: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        _sha256(self.source_fingerprint, "source fingerprint")
        _sha256(self.turn_fingerprint, "turn fingerprint")
        _sha256(self.manifest_fingerprint, "manifest fingerprint")


@dataclass(frozen=True)
class CliProbeResult:
    status: CliTransportStatus
    cli_version: str | None = None
    observed_model: str | None = None
    reason: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class CliInvokeResult:
    status: CliTransportStatus
    identity: RunnerIdentity
    response: Mapping[str, Any] = field(default_factory=dict)
    response_fingerprint: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_digest: str = ""
    stderr_digest: str = ""
    cli_version: str | None = None
    observed_model: str | None = None
    reason: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Execution:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limit_exceeded: bool = False


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _execute(request: CliProbeRequest) -> _Execution:
    env = dict(request.environment)
    kwargs: dict[str, Any] = {
        "cwd": request.cwd,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        process: subprocess.Popen[bytes] = subprocess.Popen([str(request.executable), *request.argv], **kwargs)
    except OSError as exc:
        return _Execution(None, b"", str(exc).encode("utf-8", errors="replace"), output_limit_exceeded=False)

    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": request.stdout_limit_bytes, "stderr": request.stderr_limit_bytes}
    deadline = time.monotonic() + request.timeout_seconds
    timed_out = False
    output_limit_exceeded = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate(process)
                break
            for key, _ in selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = buffers[key.data]
                buffer.extend(chunk)
                if len(buffer) > limits[key.data]:
                    output_limit_exceeded = True
                    _terminate(process)
                    break
            if output_limit_exceeded:
                break
        if timed_out or output_limit_exceeded:
            while selector.get_map():
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        buffers[key.data].extend(chunk[: limits[key.data] + 1 - len(buffers[key.data])])
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        _terminate(process)
        process.wait(timeout=1)
    finally:
        selector.close()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    return _Execution(process.returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"]), timed_out, output_limit_exceeded)


def _decode_json_or_event_stream(raw: bytes, limit: int) -> Mapping[str, Any]:
    if len(raw) > limit:
        raise CliTransportError("response_limit_exceeded")
    text = raw.decode("utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        values = []
        for line in text.splitlines():
            if line.strip():
                values.append(json.loads(line))
        value = values[-1] if values else None
    if not isinstance(value, Mapping):
        raise CliTransportError("invalid_response_schema")
    return dict(value)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class HermesCliTransportAdapter:
    """Execute one allowlisted local CLI request with bounded evidence."""

    def probe(self, request: CliProbeRequest) -> CliProbeResult:
        execution = _execute(request)
        if execution.timed_out:
            return CliProbeResult("blocked_runner_capability", reason="probe_timeout")
        if execution.output_limit_exceeded:
            return CliProbeResult("blocked_runner_capability", reason="probe_output_limit_exceeded")
        if execution.exit_code is None:
            return CliProbeResult("blocked_runner_capability", reason="probe_launch_failed")
        if execution.exit_code != 0:
            return CliProbeResult("blocked_runner_capability", reason="probe_non_zero_exit")
        try:
            value = _decode_json_or_event_stream(execution.stdout, request.response_limit_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, CliTransportError) as exc:
            return CliProbeResult("blocked_runner_capability", reason="probe_invalid_response", diagnostics=(str(exc),))
        version = value.get("version")
        model = value.get("model")
        if not isinstance(version, str) or not version or not isinstance(model, str) or not model:
            return CliProbeResult("blocked_runner_capability", reason="probe_schema_mismatch")
        if model != request.identity.model:
            return CliProbeResult("blocked_runner_capability", cli_version=version, observed_model=model, reason="observed_model_mismatch")
        return CliProbeResult("ready", cli_version=version, observed_model=model)

    def invoke(self, request: CliInvokeRequest) -> CliInvokeResult:
        execution = _execute(request)
        common = {
            "identity": request.identity,
            "exit_code": execution.exit_code,
            "timed_out": execution.timed_out,
            "stdout_bytes": len(execution.stdout),
            "stderr_bytes": len(execution.stderr),
            "stdout_digest": _digest(execution.stdout),
            "stderr_digest": _digest(execution.stderr),
        }
        if execution.timed_out:
            return CliInvokeResult("blocked_runner_transport", reason="timeout", **common)
        if execution.output_limit_exceeded:
            reason = "stdout_limit_exceeded" if len(execution.stdout) > request.stdout_limit_bytes else "stderr_limit_exceeded"
            return CliInvokeResult("blocked_runner_transport", reason=reason, **common)
        if execution.exit_code is None:
            return CliInvokeResult("blocked_runner_transport", reason="launch_failed", **common)
        if execution.exit_code != 0:
            return CliInvokeResult("blocked_runner_transport", reason="non_zero_exit", **common)
        try:
            response = _decode_json_or_event_stream(execution.stdout, request.response_limit_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, CliTransportError) as exc:
            reason = "invalid_response_json" if isinstance(exc, (UnicodeDecodeError, json.JSONDecodeError)) else str(exc)
            return CliInvokeResult("blocked_runner_transport", reason=reason, diagnostics=(str(exc),), **common)
        observed_model = response.get("model")
        if observed_model != request.identity.model:
            return CliInvokeResult("blocked_runner_capability", observed_model=str(observed_model) if observed_model is not None else None, reason="observed_model_mismatch", **common)
        response_fingerprint = _fingerprint(response)
        return CliInvokeResult("ready", response=response, response_fingerprint=response_fingerprint, observed_model=observed_model, **common)
