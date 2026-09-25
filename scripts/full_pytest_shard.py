"""Run one opt-in, diagnostic-only pytest shard against an isolated fixture."""

from __future__ import annotations

import argparse
import json
import math
import os
import select
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_paths import is_temporary_path
from backend.agents.acceptance_telemetry import build_gate_telemetry
from backend.agents.acceptance_shard_runtime import ShardRuntime, allocate_shard_runtime
from backend.agents.evidence_models import canonical_fingerprint
from scripts.full_pytest_gate import _parse_summary
from scripts.pytest_manifest import _identity, _manifest_fingerprint, _parse_nodeids


_TAIL = 4000
_EXECUTION_EVIDENCE_ENV = "NBS_ACCEPTANCE_EXECUTION_EVIDENCE"
_EXECUTION_EVIDENCE_LIMIT = 4 * 1024 * 1024
_CHILD_START_TIMEOUT_ENV = "NBS_ACCEPTANCE_CHILD_START_TIMEOUT_SECONDS"
SOCKET_ACTIVATION_PROTOCOL = "reserved-fd-v1"
_ADOPTED_RESERVED_PORTS: dict[str, socket.socket] = {}
_ACTIVATED_PORTS: dict[str, int] = {}


class _PytestExecutionRecorder:
    """Capture actual collection and test-start events in the child process."""

    def __init__(self) -> None:
        self.collected_nodeids: list[str] = []
        self.started_nodeids: list[str] = []

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected_nodeids = [item.nodeid for item in session.items]

    def pytest_runtest_logstart(self, nodeid: str, location: tuple[Any, ...]) -> None:
        self.started_nodeids.append(nodeid)


def _write_child_execution_evidence(
    path: Path, recorder: _PytestExecutionRecorder
) -> None:
    unsigned = {
        "schemaVersion": "pytest-shard-execution-v1",
        "collectedNodeids": recorder.collected_nodeids,
        "startedNodeids": recorder.started_nodeids,
    }
    payload = {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > _EXECUTION_EVIDENCE_LIMIT:
        raise ValueError("child execution evidence exceeds the size limit")
    if path.is_symlink() or path.exists() or not is_temporary_path(path):
        raise ValueError("child execution evidence path is not a fresh temporary file")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _read_child_execution_evidence(path: Path) -> dict[str, list[str]]:
    if path.is_symlink() or not path.is_file() or not is_temporary_path(path):
        raise ValueError("child execution evidence is missing or unsafe")
    if path.stat().st_size > _EXECUTION_EVIDENCE_LIMIT:
        raise ValueError("child execution evidence exceeds the size limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"schemaVersion", "collectedNodeids", "startedNodeids", "evidenceFingerprint"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("child execution evidence schema is invalid")
    unsigned = {key: payload[key] for key in required if key != "evidenceFingerprint"}
    if (
        payload["schemaVersion"] != "pytest-shard-execution-v1"
        or not _valid_fingerprint(payload["evidenceFingerprint"])
        or canonical_fingerprint(unsigned) != payload["evidenceFingerprint"]
    ):
        raise ValueError("child execution evidence identity is invalid")
    result = {}
    for field in ("collectedNodeids", "startedNodeids"):
        values = payload[field]
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            raise ValueError("child execution nodeids are invalid")
        result[field] = values
    return result


def _valid_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _validate_shard_output_path(path: Path, project_root: Path) -> Path:
    target = Path(os.path.abspath(Path(path).expanduser()))
    if target.name in {"", ".", ".."} or target.is_symlink() or target.exists():
        raise ValueError("shard output must be a new regular file")
    if not target.parent.is_dir():
        raise ValueError("shard output parent must already exist")

    resolved_target = target.resolve(strict=False)
    if is_temporary_path(resolved_target):
        return resolved_target

    project = Path(project_root).expanduser().resolve()
    runtime_root = project / ".nbs_agent_runtime" / "acceptance-parallel-rollout"
    try:
        relative = target.relative_to(runtime_root)
    except ValueError:
        relative = None
    if relative is not None and relative.parts:
        current = project
        runtime_parts = (".nbs_agent_runtime", "acceptance-parallel-rollout", *relative.parts[:-1])
        for part in runtime_parts:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise ValueError("shard output parent must be a real directory")
        return target

    raise ValueError("shard output must be inside a temporary root or acceptance-parallel-rollout runtime")


def _write_shard_output(path: Path, result: Mapping[str, Any], project_root: Path) -> None:
    target = _validate_shard_output_path(path, project_root)
    encoded = (json.dumps(dict(result), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _register_activated_socket(name: str, sock: socket.socket, port: int) -> None:
    """Register an inherited listening socket for a child-side service."""
    if not isinstance(name, str) or not name or name in _ADOPTED_RESERVED_PORTS or name in _ACTIVATED_PORTS:
        raise ValueError("activation socket name is invalid or already registered")
    if sock.fileno() < 0 or isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port < 65536:
        raise ValueError("activation socket is invalid")
    if sock.getsockname()[:2] != ("127.0.0.1", port):
        raise ValueError("activation socket endpoint identity mismatch")
    # A socket-aware child service can consume this descriptor directly or
    # pass it to its own bootstrap; the pytest wrapper owns final cleanup.
    sock.set_inheritable(True)
    _ADOPTED_RESERVED_PORTS[name] = sock
    _ACTIVATED_PORTS[name] = port


def activated_socket(name: str) -> socket.socket:
    """Return the listening socket a child-side service should consume."""
    try:
        return _ADOPTED_RESERVED_PORTS[name]
    except KeyError as exc:
        raise RuntimeError(f"activation socket is not available: {name}") from exc


def activated_ports() -> Mapping[str, int]:
    """Return endpoint identities advertised to child-side services."""
    return dict(_ACTIVATED_PORTS)


def close_activated_sockets() -> None:
    """Close all inherited service sockets at child wrapper shutdown."""
    for sock in tuple(_ADOPTED_RESERVED_PORTS.values()):
        try:
            sock.close()
        except OSError:
            pass
    _ADOPTED_RESERVED_PORTS.clear()
    _ACTIVATED_PORTS.clear()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _adopt_reserved_port_fds() -> None:
    """Adopt the socket-activation descriptors passed by ShardRuntime."""
    if os.environ.get("NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL") != "reserved-fd-v1":
        raise RuntimeError("reserved port handoff protocol is missing")
    raw = os.environ.get("NBS_ACCEPTANCE_RESERVED_PORT_FDS", "")
    entries = [item for item in raw.split(",") if item]
    if not entries:
        raise RuntimeError("reserved port handoff descriptors are missing")
    adopted: dict[str, socket.socket] = {}
    raw_fds: set[int] = set()
    seen_fds: set[int] = set()
    try:
        for entry in entries:
            name, descriptor_spec = entry.split("=", 1)
            descriptor, expected_port = descriptor_spec.split(":", 1)
            fd = int(descriptor)
            port = int(expected_port)
            if name in adopted or fd < 0 or fd in seen_fds or not 1024 <= port < 65536:
                raise ValueError
            seen_fds.add(fd)
            raw_fds.add(fd)
            sock = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
            adopted[name] = sock
            if sock.getsockname()[:2] != ("127.0.0.1", port):
                raise RuntimeError("reserved port endpoint identity mismatch")
            os.close(fd)
            raw_fds.remove(fd)
            _register_activated_socket(name, sock, port)
    except (OSError, RuntimeError, ValueError) as exc:
        for fd in tuple(raw_fds):
            try:
                os.close(fd)
            except OSError:
                pass
        close_activated_sockets()
        for sock in adopted.values():
            try:
                sock.close()
            except OSError:
                pass
        raise RuntimeError("reserved port descriptor adoption failed") from exc


def _signal_child_ready_and_wait() -> None:
    """Prove socket adoption, then wait for the controller's start release."""
    if os.environ.get("NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL") != SOCKET_ACTIVATION_PROTOCOL:
        raise RuntimeError("reserved port handoff protocol is missing")
    try:
        ready_fd = int(os.environ["NBS_ACCEPTANCE_CHILD_READY_FD"])
        start_fd = int(os.environ["NBS_ACCEPTANCE_CHILD_START_FD"])
        start_timeout = float(os.environ[_CHILD_START_TIMEOUT_ENV])
    except (KeyError, ValueError) as exc:
        raise RuntimeError("child readiness descriptors are missing") from exc
    if not math.isfinite(start_timeout) or start_timeout <= 0:
        raise RuntimeError("child start timeout is invalid")
    try:
        identity = ",".join(f"{name}={port}" for name, port in sorted(_ACTIVATED_PORTS.items()))
        os.write(ready_fd, f"READY {identity}\n".encode("ascii"))
    finally:
        os.close(ready_fd)
    try:
        readable, _, _ = select.select([start_fd], [], [], start_timeout)
        if not readable:
            close_activated_sockets()
            raise RuntimeError("child start handshake timed out")
        release = os.read(start_fd, 16)
    finally:
        os.close(start_fd)
    if release != b"START\n":
        close_activated_sockets()
        raise RuntimeError("child start release is invalid")


def select_shard_nodeids(nodeids: Sequence[str], shard_index: int, shard_count: int) -> list[str]:
    if isinstance(shard_index, bool) or isinstance(shard_count, bool) or shard_count <= 0:
        raise ValueError("shard count must be positive")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard index is out of range")
    ordered = sorted(nodeids)
    if len(ordered) != len(set(ordered)) or any(not isinstance(item, str) or not item for item in ordered):
        raise ValueError("manifest nodeids must be unique non-empty strings")
    return [nodeid for position, nodeid in enumerate(ordered) if position % shard_count == shard_index]


def _validate_manifest(manifest: Mapping[str, Any]) -> tuple[str, str, list[str], str]:
    if manifest.get("schemaVersion") != "pytest-test-manifest-v1" or manifest.get("status") != "PASS":
        raise ValueError("manifest is not a passing pytest manifest")
    commit_sha = manifest.get("commitSha")
    source_fingerprint = manifest.get("sourceFingerprint")
    nodeids = manifest.get("nodeids")
    fingerprint = manifest.get("manifestFingerprint")
    if not isinstance(commit_sha, str) or not isinstance(source_fingerprint, str) or not isinstance(nodeids, list) or not isinstance(fingerprint, str):
        raise ValueError("manifest identity is invalid")
    _identity(commit_sha, source_fingerprint)
    expected = _manifest_fingerprint(commit_sha, source_fingerprint, sorted(nodeids))
    if fingerprint != expected:
        raise ValueError("manifest fingerprint mismatch")
    if sorted(nodeids) != nodeids or len(nodeids) != len(set(nodeids)):
        raise ValueError("manifest nodeids are not stable and unique")
    return commit_sha, source_fingerprint, nodeids, fingerprint


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _run_pytest_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    runtime: ShardRuntime,
    port_handoff: bool = True,
    readiness_callback: Callable[[], None] | None = None,
    start_callback: Callable[[], None] | None = None,
    readiness_probe: Callable[[dict[str, int]], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        # A post-Popen Job assignment has an unbounded race before children
        # are contained. Without an approved suspended-process launcher, fail
        # closed instead of running an unqualified Windows shard.
        raise RuntimeError("windows shard launcher is not qualified")
    launcher = getattr(runtime, "launch_process_with_port_handoff", None) if port_handoff else None
    launch_argv = list(argv)
    if port_handoff and len(launch_argv) >= 3 and launch_argv[1:3] == ["-m", "pytest"]:
        launch_argv = [
            launch_argv[0], "-m", "scripts.full_pytest_shard",
            "--child-adopt-reserved-fds", *launch_argv[3:],
        ]
    if port_handoff and not callable(launcher):
        raise RuntimeError("qualified port-handoff launcher is required")
    child_env = dict(env)
    if port_handoff:
        child_env[_CHILD_START_TIMEOUT_ENV] = str(timeout)
    if callable(launcher):
        process = launcher(launch_argv, cwd=cwd, env=child_env)
    else:
        process = subprocess.Popen(
            launch_argv,
            cwd=cwd,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name != "nt",
        )
        try:
            runtime.register_process_group(process.pid)
        except Exception:
            if os.name == "nt":
                try:
                    process.kill()
                except OSError:
                    pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    try:
                        process.kill()
                    except OSError:
                        pass
            try:
                process.communicate(timeout=1)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.communicate(timeout=1)
                except (KeyboardInterrupt, subprocess.TimeoutExpired):
                    pass
            raise RuntimeError("pytest process registration failed")
    deadline = time.monotonic() + float(timeout) if port_handoff else None

    def remaining_timeout() -> float:
        if deadline is None:
            return float(timeout)
        return max(deadline - time.monotonic(), 0.0)

    readiness_reader = getattr(process, "_nbs_readiness_reader", None)
    start_writer = getattr(process, "_nbs_start_writer", None)
    try:
        if port_handoff:
            complete_handoff = getattr(runtime, "complete_port_handoff", None)
            if readiness_reader is None or start_writer is None or not callable(complete_handoff):
                raise RuntimeError("child readiness handshake contract is missing")
            handoff_kwargs = {"timeout": remaining_timeout(), "readiness_callback": readiness_callback}
            if start_callback is not None:
                handoff_kwargs["start_callback"] = start_callback
            if readiness_probe is not None:
                handoff_kwargs["readiness_probe"] = readiness_probe
            complete_handoff(process, **handoff_kwargs)
    except (OSError, RuntimeError, ValueError):
        runtime.terminate_process_groups(force=True)
        try:
            process.communicate(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    finally:
        for descriptor_name in ("_nbs_readiness_reader", "_nbs_start_writer"):
            descriptor = getattr(process, descriptor_name, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                try:
                    setattr(process, descriptor_name, None)
                except Exception:
                    pass
    try:
        stdout, stderr = process.communicate(timeout=remaining_timeout())
    except KeyboardInterrupt:
        runtime.terminate_process_groups(force=True)
        try:
            process.communicate(timeout=1)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.communicate(timeout=1)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                pass
        raise
    except subprocess.TimeoutExpired as exc:
        runtime.terminate_process_groups()
        partial_stdout = _as_text(exc.output)
        partial_stderr = _as_text(exc.stderr)
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired as grace_exc:
            runtime.terminate_process_groups(force=True)
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired as force_exc:
                process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=1)
                except subprocess.TimeoutExpired as final_exc:
                    stdout = _as_text(final_exc.output)
                    stderr = _as_text(final_exc.stderr)
            partial_stdout = partial_stdout or _as_text(grace_exc.output)
            partial_stderr = partial_stderr or _as_text(grace_exc.stderr)
        raise subprocess.TimeoutExpired(
            launch_argv, timeout,
            output=_as_text(stdout) or partial_stdout,
            stderr=_as_text(stderr) or partial_stderr,
        ) from exc
    return subprocess.CompletedProcess(list(argv), process.returncode, stdout, stderr)


def _artifact(
    *,
    status: str,
    failure_code: str | None,
    commit_sha: str,
    source_fingerprint: str,
    manifest_fingerprint: str,
    shard_index: int,
    shard_count: int,
    assigned: list[str],
    executed: list[str],
    result: dict[str, Any],
    started_at: str,
    finished_at: str | None,
    monotonic_started: float,
    stdout: str,
    stderr: str,
    cleanup_report: Mapping[str, Any] | None = None,
    lineage: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    unsigned = {
        "schemaVersion": "full-pytest-shard-v1",
        "status": status,
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "shardIndex": shard_index,
        "shardCount": shard_count,
        "assignedNodeids": assigned,
        "executedNodeids": executed,
        "result": result,
        "lineage": dict(lineage or {}),
        "startedAt": started_at,
        "finishedAt": finished_at or _timestamp(),
        "metadata": {
            "commandId": "full-pytest-shard",
            "failureCode": failure_code,
            "stdoutTail": stdout[-_TAIL:],
            "stderrTail": stderr[-_TAIL:],
            "cleanup": dict(cleanup_report or {
                "status": "UNKNOWN",
                "failureCode": "cleanup_not_recorded",
                "leakedFiles": [],
                "leakedLocks": [],
                "leakedProcesses": [],
            }),
            "telemetry": build_gate_telemetry(
                duration_seconds=time.perf_counter() - monotonic_started,
                failure_code=failure_code,
                blocked_reason=failure_code if status == "BLOCKED" else None,
            ),
        },
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def run_pytest_shard(
    project_root: Path,
    manifest: Mapping[str, Any],
    *,
    shard_index: int,
    shard_count: int,
    fixture_root: Path | None = None,
    run_id: str | None = None,
    timeout_seconds: int = 1800,
    port_readiness_probe: Callable[[dict[str, int]], bool] | None = None,
    lineage: Mapping[str, str] | None = None,
    runtime_observer: Callable[[str, ShardRuntime], None] | None = None,
    readiness_callback: Callable[[], None] | None = None,
    start_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    commit_sha, source_fingerprint, nodeids, manifest_fingerprint = _validate_manifest(manifest)
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    if fixture_root is not None:
        legacy_fixture = Path(fixture_root).expanduser()
        if legacy_fixture.is_symlink() or legacy_fixture.exists():
            raise ValueError("shard fixture root must be unique and not already exist")
        if not is_temporary_path(legacy_fixture):
            raise ValueError("shard fixture root must be inside a temporary root")

    assigned = select_shard_nodeids(nodeids, shard_index, shard_count)
    started_at = _timestamp()
    monotonic_started = time.perf_counter()

    runtime: ShardRuntime | None = None
    try:
        runtime = allocate_shard_runtime(
            project_root=Path(project_root),
            run_id=run_id or f"shard-{uuid.uuid4().hex}",
            shard_index=shard_index,
            fixture_root=fixture_root,
        )
        runtime.validate_isolation()
        if runtime_observer is not None:
            runtime_observer("registered", runtime)
    except RuntimeError:
        cleanup = runtime.cleanup() if runtime is not None else {
            "status": "PASS", "failureCode": None, "leakedFiles": [],
            "leakedLocks": [], "leakedProcesses": [],
        }
        return _artifact(
            status="BLOCKED", failure_code="runtime_allocation_failed", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="runtime allocation failed",
            cleanup_report=cleanup,
            lineage=lineage,
        )
    except ValueError:
        cleanup = runtime.cleanup() if runtime is not None else {
            "status": "PASS", "failureCode": None, "leakedFiles": [],
            "leakedLocks": [], "leakedProcesses": [],
        }
        return _artifact(
            status="BLOCKED", failure_code="isolation_violation", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="runtime isolation validation failed",
            cleanup_report=cleanup,
            lineage=lineage,
        )

    cleanup_finished = False

    def finish(**kwargs: Any) -> dict[str, Any]:
        nonlocal cleanup_finished
        cleanup_finished = True
        cleanup = runtime.cleanup()
        if cleanup["status"] != "PASS":
            kwargs["status"] = "BLOCKED"
            kwargs["failure_code"] = "isolation_violation"
        kwargs.setdefault("lineage", lineage)
        return _artifact(cleanup_report=cleanup, **kwargs)

    if not assigned:
        if readiness_callback is not None:
            readiness_callback()
        if start_callback is not None:
            start_callback()
        return finish(
            status="PASS", failure_code=None, commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=[], executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout="", stderr="",
        )

    if port_readiness_probe is None:
        return finish(
            status="BLOCKED", failure_code="port_handoff_requires_readiness", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="explicit child bind/readiness probe is required",
        )
    env = os.environ.copy()
    env.update(runtime.environment())
    execution_evidence_path = Path(runtime.root) / "pytest-shard-execution-evidence.json"
    if execution_evidence_path.exists() or execution_evidence_path.is_symlink():
        return finish(
            status="BLOCKED", failure_code="runner_unexpected_error", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="child execution evidence path is not fresh",
        )
    env[_EXECUTION_EVIDENCE_ENV] = str(execution_evidence_path)
    run_argv = [sys.executable, "-m", "pytest", "-q", "--sandbox-preflight", "required", "--", *assigned]
    stdout = stderr = ""
    try:
        completed = _run_pytest_command(
            run_argv,
            cwd=Path(project_root).resolve(),
            env=env,
            timeout=timeout_seconds,
            runtime=runtime,
            readiness_callback=readiness_callback,
            start_callback=start_callback,
            readiness_probe=port_readiness_probe,
        )
        stdout, stderr = _as_text(completed.stdout), _as_text(completed.stderr)
        combined_output = f"{stdout}\n{stderr}"
        execution_evidence = _read_child_execution_evidence(execution_evidence_path)
        execution_evidence_path.unlink()
        collected_nodeids = execution_evidence["collectedNodeids"]
        started_nodeids = execution_evidence["startedNodeids"]
        if sorted(collected_nodeids) != assigned or sorted(started_nodeids) != assigned:
            return finish(
                status="FAIL", failure_code="collection_mismatch", commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                shard_index=shard_index, shard_count=shard_count, assigned=assigned,
                executed=sorted(started_nodeids),
                result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
                started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
                stdout=stdout, stderr=stderr,
            )
        try:
            result = _parse_summary(combined_output)
        except ValueError:
            if completed.returncode != 0 and "ERROR: not found:" in combined_output:
                return finish(
                    status="FAIL", failure_code="collection_mismatch", commit_sha=commit_sha,
                    source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                    shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
                    result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
                    started_at=started_at, monotonic_started=monotonic_started,
                    finished_at=None, stdout=stdout, stderr=stderr,
                )
            raise
        status = "PASS" if completed.returncode == 0 and result["failed"] == 0 else "FAIL"
        failure_code = None if status == "PASS" else "pytest_failed"
        return finish(
            status=status, failure_code=failure_code, commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned,
            executed=sorted(started_nodeids),
            result=result, started_at=started_at, monotonic_started=monotonic_started,
            finished_at=None,
            stdout=stdout, stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _as_text(exc.output), _as_text(exc.stderr)
        return finish(
            status="BLOCKED", failure_code="timeout", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout=stdout, stderr=stderr,
        )
    except OSError as exc:
        return finish(
            status="BLOCKED", failure_code="runner_os_error", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout="", stderr=str(exc),
        )
    except RuntimeError:
        return finish(
            status="BLOCKED", failure_code="runtime_process_launch_failed", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="runtime process launch failed",
        )
    except Exception as exc:
        return finish(
            status="BLOCKED", failure_code="runner_unexpected_error", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout=stdout, stderr=str(exc),
        )
    except KeyboardInterrupt:
        runtime.terminate_process_groups(force=True)
        raise
    finally:
        if not cleanup_finished:
            try:
                runtime.cleanup()
            finally:
                if runtime_observer is not None:
                    runtime_observer("unregistered", runtime)
        elif runtime_observer is not None:
            runtime_observer("unregistered", runtime)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--child-adopt-reserved-fds":
        try:
            _adopt_reserved_port_fds()
            _signal_child_ready_and_wait()
        except RuntimeError as exc:
            close_activated_sockets()
            print(f"reserved port handoff blocked: {exc}", file=sys.stderr)
            return 2
        import pytest

        try:
            raw_evidence_path = os.environ.get(_EXECUTION_EVIDENCE_ENV)
            if not raw_evidence_path:
                raise RuntimeError("child execution evidence path is missing")
            recorder = _PytestExecutionRecorder()
            exit_code = int(pytest.main(arguments[1:], plugins=[recorder]))
            _write_child_execution_evidence(Path(raw_evidence_path), recorder)
            return exit_code
        except Exception as exc:
            print(f"child execution evidence failed: {type(exc).__name__}", file=sys.stderr)
            return 2
        finally:
            close_activated_sockets()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--port-readiness-file",
        type=Path,
        help="Temporary marker written by the external service bootstrap after all profile ports accept TCP.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        output_path = _validate_shard_output_path(args.output, args.project_root)
    except (OSError, ValueError) as exc:
        print(f"shard output path rejected: {exc}", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        assigned = select_shard_nodeids(
            manifest["nodeids"], args.shard_index, args.shard_count
        )
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    readiness_probe = None
    if assigned:
        if args.port_readiness_file is None:
            parser.error(
                "--port-readiness-file is required for non-empty shards; "
                "the external bootstrap must prove child bind/readiness before pytest starts"
            )
        readiness_file = args.port_readiness_file.expanduser()
        if readiness_file.is_symlink() or not is_temporary_path(readiness_file):
            parser.error("--port-readiness-file must be a non-symlink path inside a temporary root")

        def readiness_probe(ports: dict[str, int]) -> bool:
            if readiness_file.is_symlink() or not readiness_file.is_file():
                return False
            for port in ports.values():
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        pass
                except OSError:
                    return False
            return True

    result = run_pytest_shard(
        args.project_root, manifest, shard_index=args.shard_index, shard_count=args.shard_count,
        fixture_root=args.fixture_root, run_id=args.run_id, timeout_seconds=args.timeout,
        port_readiness_probe=readiness_probe,
    )
    try:
        _write_shard_output(output_path, result, args.project_root)
    except (OSError, ValueError) as exc:
        print(f"shard output write rejected: {exc}", file=sys.stderr)
        return 2
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    import scripts as scripts_package

    wrapper_module = sys.modules[__name__]
    imported_module = sys.modules.get("scripts.full_pytest_shard")
    package_module = getattr(scripts_package, "full_pytest_shard", None)
    if any(module is not None and module is not wrapper_module for module in (imported_module, package_module)):
        raise RuntimeError("shard wrapper module identity is already occupied")
    sys.modules["scripts.full_pytest_shard"] = wrapper_module
    scripts_package.full_pytest_shard = wrapper_module
    raise SystemExit(main())
