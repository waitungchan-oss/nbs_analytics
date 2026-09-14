"""Kernel-backed port lock allocation for acceptance shard runtimes."""

from __future__ import annotations

import os
import socket
import tempfile
import time
from pathlib import Path


PORT_LOCK_ROOT = Path(tempfile.gettempdir()) / "nbs-acceptance-port-locks-v2"
PORT_NAMESPACE_LOCK = PORT_LOCK_ROOT / "namespace.lock"


def _lock_file(descriptor: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.ftruncate(descriptor, 1)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise RuntimeError("shard runtime port reservation failed") from exc


def open_namespace_lock(timeout: float = 2.0) -> int:
    PORT_LOCK_ROOT.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(PORT_NAMESPACE_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    while True:
        try:
            _lock_file(descriptor)
            return descriptor
        except RuntimeError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise
            time.sleep(0.05)


def _open_port_lock(lock_path: Path, allocation_id: str) -> int:
    descriptor = None
    lock_acquired = False
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        _lock_file(descriptor)
        lock_acquired = True
    except (OSError, RuntimeError) as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise exc
    try:
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        payload = f"{os.getpid()}:{allocation_id}".encode("ascii")
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError("port lock owner write was incomplete")
        return descriptor
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if lock_acquired:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
        raise RuntimeError("shard runtime port reservation failed") from exc


def reserve_ports(
    ports: dict[str, int], allocation_id: str
) -> tuple[dict[str, socket.socket], dict[str, int], dict[str, Path]]:
    reservations: dict[str, socket.socket] = {}
    lock_fds: dict[str, int] = {}
    lock_paths: dict[str, Path] = {}
    namespace_descriptor = open_namespace_lock()
    try:
        for name, port in ports.items():
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                reservation.bind(("127.0.0.1", port))
                reservation.listen(1)
            except OSError as exc:
                reservation.close()
                raise RuntimeError("shard runtime port reservation failed") from exc
            reservations[name] = reservation
            lock_path = PORT_LOCK_ROOT / f"port-{port}.lock"
            try:
                descriptor = _open_port_lock(lock_path, allocation_id)
            except RuntimeError:
                reservation.close()
                raise
            lock_fds[name] = descriptor
            lock_paths[name] = lock_path
    except Exception:
        for reservation in reservations.values():
            reservation.close()
        for descriptor in lock_fds.values():
            os.close(descriptor)
        for lock_path in lock_paths.values():
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(namespace_descriptor)
    return reservations, lock_fds, lock_paths
