from __future__ import annotations

import json
import errno
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the SQLite fallback.
    fcntl = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COORDINATION_DB = Path(
    os.environ.get(
        "NBS_ANALYTICS_COORDINATION_DB",
        str(PROJECT_ROOT / ".nbs_runtime" / "upload_coordination.db"),
    )
)


@dataclass(frozen=True)
class UploadOperation:
    operation_id: str
    entry_point: str
    pid: int
    started_at: str
    source_files: tuple[str, ...]


class UploadBusyError(RuntimeError):
    def __init__(self, owner: dict | None = None):
        super().__init__("another upload operation is already running")
        self.owner = owner or {}


def _owner_path(coordination_db_path: Path) -> Path:
    return coordination_db_path.with_name(f"{coordination_db_path.stem}_owner.json")


def _read_owner(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _public_owner(value: dict) -> dict:
    return {
        key: value.get(key)
        for key in ("operation_id", "entry_point", "pid", "started_at")
        if value.get(key) is not None
    } | {"source_count": len(value.get("source_files") or [])}


def _write_owner(path: Path, operation: UploadOperation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(asdict(operation), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _process_lock_path(coordination_db_path: Path) -> Path:
    return coordination_db_path.with_name(f"{coordination_db_path.name}.lock")


def _acquire_process_lock(coordination_db_path: Path, owner_path: Path):
    if fcntl is None:
        return None
    lock_path = _process_lock_path(coordination_db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise UploadBusyError(_public_owner(_read_owner(owner_path))) from exc
        raise
    return handle


def _release_process_lock(handle) -> None:
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


class UploadLease:
    def __init__(
        self,
        connection: sqlite3.Connection,
        operation: UploadOperation,
        owner_path: Path,
        process_lock,
    ):
        self._connection = connection
        self.operation = operation
        self._owner_path = owner_path
        self._process_lock = process_lock
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def release(self) -> None:
        if not self._active:
            return
        try:
            try:
                owner = _read_owner(self._owner_path)
                if owner.get("operation_id") == self.operation.operation_id:
                    self._owner_path.unlink(missing_ok=True)
            except OSError:
                pass
        finally:
            try:
                self._connection.rollback()
            finally:
                try:
                    self._connection.close()
                finally:
                    try:
                        _release_process_lock(self._process_lock)
                    finally:
                        self._active = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def acquire_upload_lease(
    *,
    entry_point: str,
    source_files: list[str],
    coordination_db_path: str | Path = DEFAULT_COORDINATION_DB,
    timeout_seconds: float = 0.1,
    operation_id: str | None = None,
) -> UploadLease:
    path = Path(coordination_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    owner_path = _owner_path(path)
    process_lock = _acquire_process_lock(path, owner_path)
    try:
        connection = sqlite3.connect(path, timeout=max(0.0, timeout_seconds), isolation_level=None)
    except Exception:
        _release_process_lock(process_lock)
        raise
    try:
        connection.execute(f"PRAGMA busy_timeout = {max(0, int(timeout_seconds * 1000))}")
        connection.execute("BEGIN EXCLUSIVE")
    except sqlite3.OperationalError as exc:
        connection.close()
        _release_process_lock(process_lock)
        if "locked" in str(exc).lower():
            raise UploadBusyError(_public_owner(_read_owner(owner_path))) from exc
        raise
    operation = UploadOperation(
        operation_id=operation_id or uuid.uuid4().hex,
        entry_point=str(entry_point),
        pid=os.getpid(),
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        source_files=tuple(str(item) for item in source_files),
    )
    try:
        _write_owner(owner_path, operation)
    except Exception:
        connection.rollback()
        connection.close()
        _release_process_lock(process_lock)
        raise
    return UploadLease(connection, operation, owner_path, process_lock)


def probe_upload_lease(
    coordination_db_path: str | Path = DEFAULT_COORDINATION_DB,
) -> dict:
    path = Path(coordination_db_path)
    owner_path = _owner_path(path)
    try:
        with acquire_upload_lease(
            entry_point="health_probe",
            source_files=[],
            coordination_db_path=path,
            timeout_seconds=0.0,
        ):
            return {"locked": False, "owner": {}}
    except UploadBusyError as exc:
        return {"locked": True, "owner": exc.owner or _public_owner(_read_owner(owner_path))}
