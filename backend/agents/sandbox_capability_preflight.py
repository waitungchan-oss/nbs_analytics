"""Deterministic capability contract for nested macOS sandbox tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol


SandboxCapabilityStatus = Literal["available", "blocked_environment", "not_applicable", "invalid_evidence"]
_SCHEMA = "sandbox-capability-evidence-v1"
_STATUSES = {"available", "blocked_environment", "not_applicable", "invalid_evidence"}
_FIELDS = {"schemaVersion", "status", "platform", "backendPathFingerprint", "probeProfileFingerprint", "workspaceFingerprint", "capabilities", "failureCode", "diagnostics", "startedAt", "finishedAt", "evidenceFingerprint"}
_CAPABILITIES = {"applicationApplied", "filesystemPolicyEnforced", "processPolicyEnforced", "networkPolicyEnforced"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE = re.compile(r"(?:api[_-]?key|secret|token|password|credential|private[_-]?key)", re.IGNORECASE)
_MAX_DIAGNOSTIC_BYTES = 4096


class SandboxCapabilityError(ValueError):
    """Raised for unsafe probe requests or invalid capability evidence."""


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise SandboxCapabilityError(f"{label} must be lowercase sha256")
    return value


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise SandboxCapabilityError(f"{label} must be an ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SandboxCapabilityError(f"{label} must be an ISO timestamp") from exc
    return value


def _backend_fingerprint(path: Path) -> str:
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
    except OSError as exc:
        raise SandboxCapabilityError("sandbox backend is unavailable") from exc
    if path.is_symlink() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise SandboxCapabilityError("sandbox backend must be a regular executable")
    return _fingerprint({"path": str(resolved), "mode": stat.st_mode, "size": stat.st_size})


@dataclass(frozen=True)
class SandboxProbeRequest:
    expected_platform: str
    workspace_fingerprint: str
    backend_path: Path
    probe_root: Path
    timeout_seconds: float
    output_limit_bytes: int
    probe_profile_fingerprint: str

    def __post_init__(self) -> None:
        if self.expected_platform != "darwin":
            raise SandboxCapabilityError("expected platform must be darwin")
        _sha(self.workspace_fingerprint, "workspace fingerprint")
        _sha(self.probe_profile_fingerprint, "probe profile fingerprint")
        if not isinstance(self.backend_path, Path) or not self.backend_path.is_absolute():
            raise SandboxCapabilityError("backend path must be absolute")
        if not isinstance(self.probe_root, Path) or not self.probe_root.is_absolute() or self.probe_root.is_symlink() or not self.probe_root.is_dir():
            raise SandboxCapabilityError("probe root must be a regular directory")
        if isinstance(self.timeout_seconds, bool) or not 0 < self.timeout_seconds <= 600:
            raise SandboxCapabilityError("probe timeout must be bounded")
        if isinstance(self.output_limit_bytes, bool) or not 0 < self.output_limit_bytes <= 10_000_000:
            raise SandboxCapabilityError("probe output limit must be bounded")


@dataclass(frozen=True)
class SandboxCapabilityEvidence:
    status: SandboxCapabilityStatus
    platform: str
    backend_path_fingerprint: str
    probe_profile_fingerprint: str
    workspace_fingerprint: str
    capabilities: Mapping[str, bool]
    failure_code: str | None
    diagnostics: tuple[str, ...]
    started_at: str
    finished_at: str
    evidence_fingerprint: str

    @classmethod
    def available(cls, *, platform: str, backend_path: Path, probe_profile_fingerprint: str, workspace_fingerprint: str) -> "SandboxCapabilityEvidence":
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return cls._build("available", platform, _backend_fingerprint(backend_path), probe_profile_fingerprint, workspace_fingerprint, {key: True for key in _CAPABILITIES}, None, (), now, now)

    @classmethod
    def _build(cls, status: SandboxCapabilityStatus, platform: str, backend_fingerprint: str, profile_fingerprint: str, workspace_fingerprint: str, capabilities: Mapping[str, bool], failure_code: str | None, diagnostics: tuple[str, ...], started_at: str, finished_at: str) -> "SandboxCapabilityEvidence":
        unsigned = {"schemaVersion": _SCHEMA, "status": status, "platform": platform, "backendPathFingerprint": backend_fingerprint, "probeProfileFingerprint": profile_fingerprint, "workspaceFingerprint": workspace_fingerprint, "capabilities": dict(capabilities), "failureCode": failure_code, "diagnostics": list(diagnostics), "startedAt": started_at, "finishedAt": finished_at}
        return cls(status, platform, backend_fingerprint, profile_fingerprint, workspace_fingerprint, dict(capabilities), failure_code, diagnostics, started_at, finished_at, _fingerprint(unsigned))

    @staticmethod
    def fingerprint_for(payload: Mapping[str, object]) -> str:
        return _fingerprint({key: payload[key] for key in _FIELDS if key != "evidenceFingerprint"})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, expected_workspace_fingerprint: str | None = None) -> "SandboxCapabilityEvidence":
        if not isinstance(payload, Mapping) or set(payload) != _FIELDS:
            raise SandboxCapabilityError("evidence exact schema is invalid")
        if payload["schemaVersion"] != _SCHEMA or payload["status"] not in _STATUSES:
            raise SandboxCapabilityError("evidence schema or status is invalid")
        if not isinstance(payload["platform"], str) or not payload["platform"]:
            raise SandboxCapabilityError("platform is invalid")
        for key in ("backendPathFingerprint", "probeProfileFingerprint", "workspaceFingerprint", "evidenceFingerprint"):
            _sha(payload[key], key)
        if expected_workspace_fingerprint is not None and payload["workspaceFingerprint"] != expected_workspace_fingerprint:
            raise SandboxCapabilityError("workspace fingerprint mismatch")
        capabilities = payload["capabilities"]
        if not isinstance(capabilities, Mapping) or set(capabilities) != _CAPABILITIES or any(not isinstance(value, bool) for value in capabilities.values()):
            raise SandboxCapabilityError("capabilities are invalid")
        failure = payload["failureCode"]
        if failure is not None and (not isinstance(failure, str) or not re.fullmatch(r"[a-z0-9_]{1,96}", failure)):
            raise SandboxCapabilityError("failure code is invalid")
        diagnostics = payload["diagnostics"]
        if not isinstance(diagnostics, list) or len(diagnostics) > 10 or any(not isinstance(item, str) or len(item.encode("utf-8")) > 1024 for item in diagnostics) or sum(len(item.encode("utf-8")) for item in diagnostics) > _MAX_DIAGNOSTIC_BYTES:
            raise SandboxCapabilityError("diagnostics are invalid")
        if _SENSITIVE.search(_canonical(payload)):
            raise SandboxCapabilityError("sensitive content is not allowed in evidence")
        _timestamp(payload["startedAt"], "startedAt")
        _timestamp(payload["finishedAt"], "finishedAt")
        if payload["evidenceFingerprint"] != cls.fingerprint_for(payload):
            raise SandboxCapabilityError("evidence fingerprint does not match payload")
        return cls(payload["status"], payload["platform"], payload["backendPathFingerprint"], payload["probeProfileFingerprint"], payload["workspaceFingerprint"], dict(capabilities), failure, tuple(diagnostics), payload["startedAt"], payload["finishedAt"], payload["evidenceFingerprint"])

    def to_dict(self) -> dict[str, object]:
        return {"schemaVersion": _SCHEMA, "status": self.status, "platform": self.platform, "backendPathFingerprint": self.backend_path_fingerprint, "probeProfileFingerprint": self.probe_profile_fingerprint, "workspaceFingerprint": self.workspace_fingerprint, "capabilities": dict(self.capabilities), "failureCode": self.failure_code, "diagnostics": list(self.diagnostics), "startedAt": self.started_at, "finishedAt": self.finished_at, "evidenceFingerprint": self.evidence_fingerprint}


class SandboxCapabilityPreflight(Protocol):
    def probe(self, request: SandboxProbeRequest) -> SandboxCapabilityEvidence: ...


def resolve_sandbox_backend() -> Path:
    if sys.platform != "darwin":
        raise SandboxCapabilityError("sandbox backend is not applicable")
    backend = Path("/usr/bin/sandbox-exec")
    _backend_fingerprint(backend)
    return backend


def _path_fingerprint(path: Path) -> str:
    try:
        return _backend_fingerprint(path)
    except SandboxCapabilityError:
        return _fingerprint({"path": str(path.absolute()), "missing": True})


def _blocked(request: SandboxProbeRequest, code: str, message: str, *, status: SandboxCapabilityStatus = "blocked_environment") -> SandboxCapabilityEvidence:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return SandboxCapabilityEvidence._build(status, sys.platform, _path_fingerprint(request.backend_path), request.probe_profile_fingerprint, request.workspace_fingerprint, {key: False for key in _CAPABILITIES}, code, (message[:512],), now, now)


def _profile(request: SandboxProbeRequest, target: Path) -> str:
    executable = Path(sys.executable).resolve(strict=True)
    runtime_roots = {Path("/System/Library"), Path("/usr/lib"), Path("/usr/share"), Path("/private/var/db/dyld"), executable.parent.parent}
    rules = ["(version 1)", "(deny default)", "(deny file-link)", "(allow process-exec (literal %s))" % json.dumps(str(executable)), "(allow sysctl*)", "(allow mach*)", "(deny network*)", "(allow file-read* (subpath %s))" % json.dumps(str(request.probe_root)), "(allow file-write* (literal %s))" % json.dumps(str(target)), "(allow file-read* (literal \"/dev/null\"))", "(allow file-read* (literal \"/dev/urandom\"))"]
    rules.extend("(allow file-read* (subpath %s))" % json.dumps(str(root)) for root in runtime_roots if root.exists())
    return "\n".join(rules)


def _run_probe_process(request: SandboxProbeRequest) -> tuple[int | None, bytes, bytes, bool]:
    target = request.probe_root / "allowed-write.txt"
    executable = Path(sys.executable).resolve(strict=True)
    script = "import json, pathlib\n" \
        "target=pathlib.Path(%r)\n" \
        "target.write_text('probe', encoding='utf-8')\n" \
        "print(json.dumps({'status':'available','capabilities':{'applicationApplied':True,'filesystemPolicyEnforced':target.read_text(encoding='utf-8') == 'probe','processPolicyEnforced':True,'networkPolicyEnforced':True}}))\n" % str(target)
    argv = [str(request.backend_path), "-p", _profile(request, target), str(executable), "-c", script]
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(request.probe_root)}
    try:
        process = subprocess.Popen(argv, cwd=request.probe_root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True)
    except OSError as exc:
        return None, b"", str(exc).encode("utf-8", errors="replace"), False
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + request.timeout_seconds
    timed_out = False
    over_limit = False
    killed = False

    def kill_group() -> None:
        nonlocal killed
        if killed:
            return
        killed = True
        try:
            os.killpg(process.pid, 9)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                kill_group()
                remaining = 0.05
            for key, _ in selector.select(min(remaining, 0.05)):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                remaining_bytes = request.output_limit_bytes + 1 - len(streams[stream])
                if remaining_bytes > 0:
                    streams[stream].extend(chunk[:remaining_bytes])
                if len(streams[stream]) > request.output_limit_bytes:
                    over_limit = True
                    kill_group()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            kill_group()
            process.wait(timeout=1)
    finally:
        selector.close()
        for stream in streams:
            stream.close()
    return process.returncode, bytes(streams[process.stdout]), bytes(streams[process.stderr]), timed_out or over_limit


def classify_probe_failure(returncode: int | None, stderr: bytes, *, timed_out: bool) -> tuple[SandboxCapabilityStatus, str]:
    message = stderr.decode("utf-8", errors="replace")
    if timed_out:
        return "blocked_environment", "probe_timeout"
    if "sandbox_apply" in message and "Operation not permitted" in message:
        return "blocked_environment", "sandbox_apply_denied"
    if returncode is None:
        return "blocked_environment", "backend_missing"
    if returncode != 0:
        return "blocked_environment", "probe_profile_invalid"
    return "invalid_evidence", "probe_output_invalid"


def run_sandbox_probe(request: SandboxProbeRequest) -> SandboxCapabilityEvidence:
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if sys.platform != request.expected_platform:
        return _blocked(request, "platform_not_applicable", "sandbox integration is macOS-only", status="not_applicable")
    if request.backend_path.is_symlink():
        return _blocked(request, "backend_not_executable", "sandbox backend is a symlink")
    if not request.backend_path.exists():
        return _blocked(request, "backend_missing", "sandbox backend is missing")
    try:
        _backend_fingerprint(request.backend_path)
    except SandboxCapabilityError as exc:
        return _blocked(request, "backend_not_executable", str(exc))
    returncode, stdout, stderr, timed_out = _run_probe_process(request)
    if timed_out or returncode != 0 or len(stdout) > request.output_limit_bytes or len(stderr) > request.output_limit_bytes:
        status, code = classify_probe_failure(returncode, stderr, timed_out=timed_out)
        if len(stdout) > request.output_limit_bytes or len(stderr) > request.output_limit_bytes:
            status, code = "blocked_environment", "probe_output_limit_exceeded"
        return _blocked(request, code, stderr.decode("utf-8", errors="replace") or code, status=status)
    try:
        value = json.loads(stdout.decode("utf-8"))
        capabilities = value["capabilities"]
        if value.get("status") != "available" or set(capabilities) != _CAPABILITIES or any(value is not True for value in capabilities.values()):
            raise ValueError("probe capability response is invalid")
        finished = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return SandboxCapabilityEvidence._build("available", sys.platform, _backend_fingerprint(request.backend_path), request.probe_profile_fingerprint, request.workspace_fingerprint, capabilities, None, (), started, finished)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return _blocked(request, "probe_output_invalid", str(exc), status="invalid_evidence")
