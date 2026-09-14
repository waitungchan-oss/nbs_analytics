"""Isolated, diagnostic-only runtime allocation for acceptance shards."""

from __future__ import annotations

import hashlib
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.agents.acceptance_port_lock import (
    PORT_LOCK_ROOT as _PORT_LOCK_ROOT_PATH,
    open_namespace_lock as _open_namespace_lock,
    reserve_ports as _reserve_ports_impl,
)
from backend.agents import acceptance_windows_job
from backend.agents.acceptance_paths import canonical_path, is_temporary_path


_PORT_BASE = 45_000
_PORT_SPAN = 10_000
_PORT_NAMES = ("streamlit", "mcp", "health")
_PORT_LOCK_ROOT = _PORT_LOCK_ROOT_PATH
_ALLOWED_ROOT_ENTRIES = {"shard.db", "coordination.db", "cache", "runtime-profile.json"}
_PRODUCTION_PATH_NAMES = {
    "nbs_marketing_data.db",
    "nbs_marketing_data.sqlite",
    "production.db",
    "baseline.db",
}


def _safe_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in run_id)
    return safe.strip("-")[:48] or "run"


def _check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise ValueError(f"acceptance shard port {port} is unavailable") from exc


def _windows_process_alive(process_id: int) -> bool:
    return acceptance_windows_job.process_alive(process_id)


def _create_windows_job() -> int:
    return acceptance_windows_job.create_job()


def _assign_windows_process_to_job(job_handle: int, process_id: int) -> None:
    acceptance_windows_job.assign_process(job_handle, process_id)


def _windows_job_active_processes(job_handle: int) -> int:
    return acceptance_windows_job.active_processes(job_handle)


def _terminate_windows_job(job_handle: int) -> None:
    acceptance_windows_job.terminate_job(job_handle)


@dataclass
class ShardRuntime:
    """The paths and process identity belonging to one shard invocation."""

    project_root: Path
    root: Path
    run_id: str
    shard_index: int
    platform_name: str
    _ports: dict[str, int]
    allocation_id: str
    _process_groups: set[int] = field(default_factory=set, repr=False)
    _port_reservations: dict[str, socket.socket] = field(default_factory=dict, repr=False)
    _port_lock_fds: dict[str, int] = field(default_factory=dict, repr=False)
    _port_lock_paths: dict[str, Path] = field(default_factory=dict, repr=False)
    _windows_job_handle: int | None = field(default=None, repr=False)
    _cleanup: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @property
    def db_path(self) -> Path:
        return self.root / "shard.db"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def coordination_db_path(self) -> Path:
        return self.root / "coordination.db"

    @property
    def process_profile(self) -> str:
        return f"acceptance-shard-{self.allocation_id}"

    @property
    def _is_windows(self) -> bool:
        return self.platform_name.startswith("win")

    def environment(self) -> dict[str, str]:
        """Return only bounded child-process identity and fixture overrides."""
        return {
            "NBS_ACCEPTANCE_RUNTIME_ROOT": str(self.root),
            "NBS_ANALYTICS_DB_FILE": str(self.db_path),
            "NBS_ANALYTICS_CACHE_DIR": str(self.cache_dir),
            "NBS_ANALYTICS_COORDINATION_DB": str(self.coordination_db_path),
            "NBS_ANALYTICS_PROCESS_PROFILE": self.process_profile,
            "NBS_ACCEPTANCE_SHARD_RUN_ID": self.run_id,
            "NBS_ACCEPTANCE_SHARD_INDEX": str(self.shard_index),
            "NBS_ACCEPTANCE_PROFILE_PORTS": ",".join(
                f"{name}={port}" for name, port in sorted(self._ports.items())
            ),
        }

    def profile_ports(self) -> dict[str, int]:
        return dict(self._ports)

    def handoff_ports(self) -> None:
        """Release probes only through an explicit child bind/readiness protocol."""
        raise RuntimeError("port handoff requires an explicit bind/readiness protocol")

    def handoff_ports_with_readiness(
        self, bind_and_probe: Callable[[dict[str, int]], bool]
    ) -> None:
        """Run a lock-coordinated bind/readiness handoff for an external child."""
        if not callable(bind_and_probe):
            raise ValueError("bind_and_probe must be callable")
        namespace_descriptor = _open_namespace_lock()
        for reservation in self._port_reservations.values():
            try:
                reservation.close()
            except OSError:
                continue
        self._port_reservations.clear()
        try:
            if not bind_and_probe(self.profile_ports()):
                raise RuntimeError("child port readiness probe failed")
        finally:
            os.close(namespace_descriptor)

    def register_process_group(self, process_id: int) -> None:
        if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
            raise ValueError("process_id must be a positive integer")
        if self._is_windows:
            if self._windows_job_handle is None:
                self._windows_job_handle = _create_windows_job()
            try:
                _assign_windows_process_to_job(self._windows_job_handle, process_id)
            except RuntimeError:
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                raise
        self._process_groups.add(process_id)

    def _process_group_alive(self, process_id: int) -> bool:
        try:
            if self._is_windows:
                return _windows_process_alive(process_id)
            else:
                os.killpg(process_id, 0)
        except ProcessLookupError:
            return False
        except (OSError, PermissionError):
            return True
        return True

    def terminate_process_groups(self, *, force: bool = False) -> None:
        if self._is_windows and self._windows_job_handle is not None:
            try:
                _terminate_windows_job(self._windows_job_handle)
            except (OSError, RuntimeError):
                pass
            return
        for process_id in tuple(self._process_groups):
            if not self._process_group_alive(process_id):
                continue
            try:
                if self._is_windows:
                    subprocess.run(
                        ["taskkill", "/PID", str(process_id), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                else:
                    os.killpg(process_id, signal.SIGKILL if force else signal.SIGTERM)
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                continue

    def validate_isolation(self) -> None:
        project_root = canonical_path(self.project_root)
        root_as_given = Path(self.root).expanduser()
        if root_as_given.is_symlink():
            raise ValueError("shard runtime root must not be a symlink")
        root = canonical_path(root_as_given)
        if not is_temporary_path(root):
            raise ValueError("shard runtime root must be inside a temporary root")
        if project_root == root or project_root in root.parents:
            raise ValueError("shard runtime root must not be inside the project root")
        if not root.exists() or not root.is_dir():
            raise ValueError("shard runtime root must be an existing directory")

        for path in (self.db_path, self.cache_dir, self.coordination_db_path):
            if path.name in _PRODUCTION_PATH_NAMES:
                raise ValueError("shard runtime cannot use a production path")
            resolved = canonical_path(path)
            if project_root == resolved or project_root in resolved.parents:
                raise ValueError("shard runtime path must not be inside the project root")
            if root != resolved and root not in resolved.parents:
                raise ValueError("shard runtime paths must be contained by the runtime root")

        ports = tuple(self._ports.values())
        if set(self._ports) != set(_PORT_NAMES) or len(ports) != len(set(ports)):
            raise ValueError("shard profile ports must be unique and complete")
        if any(not isinstance(port, int) or port < 1024 or port >= 65536 for port in ports):
            raise ValueError("shard profile ports are outside the permitted range")

    def _leak_report(self) -> dict[str, Any]:
        leaked_files: list[str] = []
        leaked_locks: list[str] = []
        if self._is_windows and self._windows_job_handle is not None:
            try:
                leaked_processes = ["process_tree"] if _windows_job_active_processes(self._windows_job_handle) else []
            except RuntimeError:
                leaked_processes = ["process_tree_unknown"]
        else:
            leaked_processes = [
                "process_group"
                for process_id in sorted(self._process_groups)
                if self._process_group_alive(process_id)
            ]
            detached_process_ids = self._detached_process_ids()
            if detached_process_ids is None:
                leaked_processes.append("process_tree_unknown")
            elif detached_process_ids:
                leaked_processes.append("detached_process")
        if self.root.exists() and not self.root.is_symlink():
            for entry in sorted(self.root.iterdir(), key=lambda item: item.name):
                if entry.name in _ALLOWED_ROOT_ENTRIES:
                    if entry.name == "cache" and entry.is_symlink():
                        leaked_files.append("cache")
                        leaked_locks.append("cache")
                    elif entry.name == "cache" and entry.is_dir():
                        for nested in sorted(entry.rglob("*"), key=lambda item: str(item)):
                            if nested.is_symlink() or nested.name.endswith((".lock", ".pid")) or "lock" in nested.name.lower():
                                relative = nested.relative_to(self.root).as_posix()
                                leaked_files.append(relative)
                                if nested.is_symlink() or nested.name.endswith((".lock", ".pid")) or "lock" in nested.name.lower():
                                    leaked_locks.append(relative)
                    continue
                relative = entry.relative_to(self.root).as_posix()
                leaked_files.append(relative)
                if entry.name.endswith((".lock", ".pid")) or "lock" in entry.name.lower():
                    leaked_locks.append(relative)
        return {
            "status": "BLOCKED" if leaked_files or leaked_processes else "PASS",
            "failureCode": "isolation_violation" if leaked_files or leaked_processes else None,
            "leakedFiles": leaked_files,
            "leakedLocks": leaked_locks,
            "leakedProcesses": leaked_processes,
        }

    def _detached_process_ids(self) -> list[int] | None:
        if not self._process_groups:
            return []
        try:
            observed = subprocess.run(
                ["ps", "eww", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if observed.returncode != 0:
            return None
        token = re.compile(
            rf"(?:^|\s)NBS_ANALYTICS_PROCESS_PROFILE={re.escape(self.process_profile)}(?:\s|$)"
        )
        process_ids: list[int] = []
        for line in (observed.stdout or "").splitlines():
            if not token.search(line):
                continue
            try:
                process_id = int(line.strip().split(None, 1)[0])
            except (ValueError, IndexError):
                continue
            if process_id != os.getpid() and process_id not in process_ids:
                process_ids.append(process_id)
        return process_ids

    def _terminate_detached_processes(self) -> None:
        for _ in range(3):
            process_ids = self._detached_process_ids()
            if not process_ids:
                return
            for process_id in process_ids:
                try:
                    os.kill(process_id, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    continue

    def _reap_process_groups(self) -> None:
        if self._is_windows:
            return
        for process_id in self._process_groups:
            try:
                os.waitpid(process_id, os.WNOHANG)
            except (ChildProcessError, OSError):
                continue

    def _wait_for_process_cleanup(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            self._reap_process_groups()
            process_groups_alive = any(
                self._process_group_alive(process_id)
                for process_id in self._process_groups
            )
            detached_processes = self._detached_process_ids()
            if not process_groups_alive and detached_processes == []:
                self._process_groups.clear()
                return True
            if detached_processes is None or time.monotonic() >= deadline:
                return False
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def cleanup_report(self) -> dict[str, Any]:
        self._cleanup = self._leak_report()
        return {
            **self._cleanup,
            "leakedFiles": list(self._cleanup["leakedFiles"]),
            "leakedLocks": list(self._cleanup["leakedLocks"]),
            "leakedProcesses": list(self._cleanup["leakedProcesses"]),
        }

    def cleanup(self) -> dict[str, Any]:
        """Remove the shard runtime and return a bounded cleanup receipt."""
        initial = self._leak_report()
        cleanup_failed = False
        report = {
            **initial,
            "leakedFiles": list(initial["leakedFiles"]),
            "leakedLocks": list(initial["leakedLocks"]),
            "leakedProcesses": list(initial["leakedProcesses"]),
        }
        try:
            if report["leakedProcesses"]:
                self.terminate_process_groups(force=True)
                if "detached_process" in report["leakedProcesses"]:
                    self._terminate_detached_processes()
                if not self._wait_for_process_cleanup():
                    cleanup_failed = True
                    report["leakedProcesses"].append("process_cleanup_indeterminate")
            if self.root.is_symlink():
                report["status"] = "BLOCKED"
                report["failureCode"] = "isolation_violation"
                report["leakedFiles"] = ["symlinked_runtime_root"]
            elif self.root.exists():
                shutil.rmtree(self.root)
        except OSError:
            cleanup_failed = True
        finally:
            for reservation in self._port_reservations.values():
                try:
                    reservation.close()
                except OSError:
                    cleanup_failed = True
            self._port_reservations.clear()
            namespace_descriptor = None
            try:
                namespace_descriptor = _open_namespace_lock()
            except (OSError, RuntimeError):
                cleanup_failed = True
            lock_descriptors = tuple(self._port_lock_fds.values())
            lock_paths = tuple(self._port_lock_paths.values())
            self._port_lock_fds.clear()
            for descriptor in lock_descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
            residual_lock_paths: list[Path] = []
            for lock_path in lock_paths:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    cleanup_failed = True
                if lock_path.exists():
                    residual_lock_paths.append(lock_path)
            if namespace_descriptor is not None:
                try:
                    os.close(namespace_descriptor)
                except OSError:
                    cleanup_failed = True
            self._port_lock_paths.clear()
            if self._windows_job_handle is not None:
                try:
                    acceptance_windows_job.close_job(self._windows_job_handle)
                except (OSError, RuntimeError):
                    cleanup_failed = True
                self._windows_job_handle = None
        final = self._leak_report()
        if residual_lock_paths:
            residual = [f"port-lock/{path.name}" for path in residual_lock_paths]
            final["leakedLocks"] = sorted(set(final["leakedLocks"]) | set(residual))
            final["leakedFiles"] = sorted(set(final["leakedFiles"]) | set(residual))
            cleanup_failed = True
        if initial["leakedFiles"]:
            final["leakedFiles"] = sorted(
                set(initial["leakedFiles"]) | set(final["leakedFiles"])
            )
            final["leakedLocks"] = sorted(
                set(initial["leakedLocks"]) | set(final["leakedLocks"])
            )
            final["status"] = "BLOCKED"
            final["failureCode"] = "isolation_violation"
        if self.root.is_symlink():
            final["status"] = "BLOCKED"
            final["failureCode"] = "isolation_violation"
            final["leakedFiles"] = ["symlinked_runtime_root"]
        if cleanup_failed:
            final["status"] = "BLOCKED"
            final["failureCode"] = "isolation_violation"
            if "cleanup_failed" not in final["leakedFiles"]:
                final["leakedFiles"].append("cleanup_failed")
        self._cleanup = final
        return {
            **final,
            "leakedFiles": list(final["leakedFiles"]),
            "leakedLocks": list(final["leakedLocks"]),
            "leakedProcesses": list(final["leakedProcesses"]),
        }


def _ports_for(run_id: str, shard_index: int) -> dict[str, int]:
    digest = int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8], 16)
    offset = (digest + shard_index * len(_PORT_NAMES)) % _PORT_SPAN
    ports = {name: _PORT_BASE + offset + position for position, name in enumerate(_PORT_NAMES)}
    if max(ports.values()) >= 65536:
        offset = offset % (_PORT_SPAN - len(_PORT_NAMES))
        ports = {name: _PORT_BASE + offset + position for position, name in enumerate(_PORT_NAMES)}
    try:
        for port in ports.values():
            _check_port(port)
    except ValueError as exc:
        raise RuntimeError("shard runtime port allocation failed") from exc
    return ports


def _reserve_ports(
    ports: dict[str, int], allocation_id: str
) -> tuple[dict[str, socket.socket], dict[str, int], dict[str, Path]]:
    return _reserve_ports_impl(ports, allocation_id)


def allocate_shard_runtime(
    *,
    project_root: Path,
    run_id: str,
    shard_index: int,
    platform_name: str | None = None,
    fixture_root: Path | None = None,
) -> ShardRuntime:
    """Allocate a unique temporary runtime without starting any service."""
    if isinstance(shard_index, bool) or not isinstance(shard_index, int) or shard_index < 0:
        raise ValueError("shard_index must be a non-negative integer")
    safe_id = _safe_run_id(run_id)
    project = canonical_path(project_root)
    if not project.exists() or not project.is_dir():
        raise ValueError("project_root must be an existing directory")

    if fixture_root is None:
        root = Path(
            tempfile.mkdtemp(
                prefix=f"nbs-acceptance-{safe_id}-{shard_index}-",
                dir=tempfile.gettempdir(),
            )
        ).resolve()
    else:
        candidate = Path(fixture_root).expanduser()
        if candidate.is_symlink():
            raise ValueError("shard fixture root must not be a symlink")
        if candidate.exists():
            raise ValueError("shard fixture root must be unique and not already exist")
        if not is_temporary_path(candidate):
            raise ValueError("shard fixture root must be inside a temporary root")
        if (
            project == canonical_path(candidate).parent or project in canonical_path(candidate).parents
        ) and not is_temporary_path(project):
            raise ValueError("shard fixture root must not be inside the project root")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.mkdir()
        root = candidate.resolve()

    try:
        ports = _ports_for(run_id, shard_index)
        allocation_id = uuid.uuid4().hex
        port_reservations, port_lock_fds, port_lock_paths = _reserve_ports(ports, allocation_id)
        runtime = ShardRuntime(
            project_root=project,
            root=root,
            run_id=safe_id,
            shard_index=shard_index,
            platform_name=platform_name or sys.platform,
            _ports=ports,
            allocation_id=allocation_id,
            _port_reservations=port_reservations,
            _port_lock_fds=port_lock_fds,
            _port_lock_paths=port_lock_paths,
        )
        runtime.validate_isolation()
    except Exception:
        for reservation in locals().get("port_reservations", {}).values():
            reservation.close()
        for descriptor in locals().get("port_lock_fds", {}).values():
            os.close(descriptor)
        for lock_path in locals().get("port_lock_paths", {}).values():
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
        if root.exists() and not root.is_symlink():
            shutil.rmtree(root)
        raise
    return runtime
