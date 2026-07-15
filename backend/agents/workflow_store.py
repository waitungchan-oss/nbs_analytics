from __future__ import annotations

import errno
import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .workflow_models import (
    WorkflowApproval,
    WorkflowEvent,
    WorkflowManifest,
    WorkflowStatus,
    EVENT_SCHEMA,
    legal_transition,
)


ALLOWED_ARTIFACTS = frozenset(
    {
        "approval.json",
        "context.json",
        "implementation.json",
        "targeted-verification.json",
        "review.json",
        "full-verification.json",
        "hermes.json",
        "archive-summary.json",
    }
)
DEFAULT_STAGE_ARTIFACT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_RUN_ARTIFACT_SOFT_CAP_BYTES = 25 * 1024 * 1024


class WorkflowLockedError(RuntimeError):
    """Raised when another process already owns a run lock."""


class WorkflowStore:
    def __init__(
        self,
        project_root: Path,
        *,
        stage_artifact_max_bytes: int = DEFAULT_STAGE_ARTIFACT_MAX_BYTES,
        run_artifact_soft_cap_bytes: int = DEFAULT_RUN_ARTIFACT_SOFT_CAP_BYTES,
    ) -> None:
        if isinstance(stage_artifact_max_bytes, bool) or stage_artifact_max_bytes <= 0:
            raise ValueError("stage artifact cap must be a positive integer")
        if isinstance(run_artifact_soft_cap_bytes, bool) or run_artifact_soft_cap_bytes <= 0:
            raise ValueError("run artifact soft cap must be a positive integer")
        self.project_root = self._existing_directory(Path(project_root), "project root")
        self.runtime_root = self._prepare_directory(self.project_root / ".nbs_agent_runtime", "runtime root")
        self.runs_root = self._prepare_directory(self.runtime_root / "runs", "runs root")
        self.stage_artifact_max_bytes = stage_artifact_max_bytes
        self.run_artifact_soft_cap_bytes = run_artifact_soft_cap_bytes

    def create_run(self, manifest: WorkflowManifest, status: WorkflowStatus) -> Path:
        if manifest.run_id != status.run_id:
            raise ValueError("manifest and status run IDs must match")
        run_dir = self._run_dir(manifest.run_id)
        if run_dir.is_symlink():
            raise PermissionError("run target must not be a symlink")
        if run_dir.exists():
            raise FileExistsError(run_dir)
        staging = Path(tempfile.mkdtemp(prefix=f".{manifest.run_id}.", dir=self.runs_root))
        try:
            self._assert_regular_directory(staging, "staging run directory")
            self._atomic_json(staging / "manifest.json", manifest.to_dict())
            self._atomic_json(staging / "status.json", status.to_dict())
            if run_dir.is_symlink():
                raise PermissionError("run target must not be a symlink")
            if run_dir.exists():
                raise FileExistsError(run_dir)
            os.rename(staging, run_dir)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return run_dir

    def load_manifest(self, run_id: str) -> WorkflowManifest:
        return WorkflowManifest.from_dict(self._read_json(self._run_file(run_id, "manifest.json")))

    def load_status(self, run_id: str) -> WorkflowStatus:
        return WorkflowStatus.from_dict(self._read_json(self._run_file(run_id, "status.json")))

    def write_approval(self, run_id: str, approval: WorkflowApproval) -> None:
        if approval.run_id != run_id:
            raise ValueError("approval run ID does not match run ID")
        with self.run_lock(run_id):
            approval_path = self._run_file(run_id, "approval.json")
            if approval_path.is_symlink():
                raise PermissionError("approval target must not be a symlink")
            if approval_path.exists():
                raise FileExistsError("approval already exists")
            self._atomic_json(approval_path, approval.to_dict())

    def transition(self, run_id: str, status: WorkflowStatus, event: WorkflowEvent) -> None:
        if status.run_id != run_id or event.run_id != run_id:
            raise ValueError("workflow artifact run IDs must match run ID")
        current = self.load_status(run_id)
        if not legal_transition(current.status, status.status):
            raise ValueError("illegal workflow transition")
        if event.from_status != current.status or event.to_status != status.status:
            raise ValueError("transition event does not match status transition")
        with self.run_lock(run_id):
            current = self.load_status(run_id)
            if not legal_transition(current.status, status.status):
                raise ValueError("illegal workflow transition")
            if event.from_status != current.status:
                raise ValueError("transition event does not match current status")
            self._append_event(run_id, event)
            self._atomic_json(self._run_file(run_id, "status.json"), status.to_dict())

    def write_artifact(self, run_id: str, name: str, payload: dict) -> Path:
        if Path(name).name != name:
            raise PermissionError("artifact path must stay inside the run directory")
        if name == "approval.json":
            raise ValueError("approval.json can only be written by write_approval")
        if name not in ALLOWED_ARTIFACTS:
            raise ValueError("artifact name is not allowed")
        target = self._run_file(run_id, name)
        artifact_bytes = self._json_bytes(payload)
        if len(artifact_bytes) > self.stage_artifact_max_bytes:
            raise ValueError("stage artifact exceeds hard cap")
        with self.run_lock(run_id):
            previous_bytes = self.artifact_bytes(run_id)
            self._atomic_json(target, payload)
            status = self.load_status(run_id)
            total_bytes = self.artifact_bytes(run_id)
            updated = WorkflowStatus.from_dict(
                {**status.to_dict(), "artifactBytes": total_bytes}
            )
            self._atomic_json(self._run_file(run_id, "status.json"), updated.to_dict())
            if previous_bytes <= self.run_artifact_soft_cap_bytes < total_bytes:
                now = datetime.now(timezone.utc).isoformat()
                self._append_event(
                    run_id,
                    WorkflowEvent(
                        EVENT_SCHEMA,
                        run_id,
                        f"event-{uuid4().hex}",
                        "artifact_size_warning",
                        None,
                        None,
                        now,
                        "Run artifact soft cap exceeded",
                        {
                            "artifactBytes": total_bytes,
                            "runArtifactSoftCapBytes": self.run_artifact_soft_cap_bytes,
                        },
                    ),
                )
        return target

    def append_event(self, run_id: str, event: WorkflowEvent) -> None:
        if event.run_id != run_id:
            raise ValueError("event run ID does not match run ID")
        with self.run_lock(run_id):
            self._append_event(run_id, event)

    @contextmanager
    def run_lock(self, run_id: str, *, blocking: bool = False) -> Iterator[None]:
        lock_path = self._run_file(run_id, ".lock")
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PermissionError("lock target must not be a symlink") from exc
            raise
        try:
            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError as exc:
                raise WorkflowLockedError(f"run {run_id} is already locked") from exc
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def artifact_bytes(self, run_id: str) -> int:
        run_dir = self._run_dir(run_id)
        total = 0
        for name in ALLOWED_ARTIFACTS - {"approval.json"}:
            path = run_dir / name
            if path.is_symlink():
                raise PermissionError("artifact must be a regular file")
            if not path.exists():
                continue
            self._assert_regular_file(path, "artifact")
            total += path.stat().st_size
        return total

    def _append_event(self, run_id: str, event: WorkflowEvent) -> None:
        path = self._run_file(run_id, "events.jsonl")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise PermissionError("events target must not be a symlink") from exc
            raise
        line = (json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _run_file(self, run_id: str, name: str) -> Path:
        run_dir = self._run_dir(run_id)
        if Path(name).name != name:
            raise PermissionError("path must stay inside the run directory")
        return self._contained(run_dir / name, run_dir)

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise PermissionError("run path must stay inside the runs directory")
        path = self._contained(self.runs_root / run_id, self.runs_root)
        if path.exists() or path.is_symlink():
            self._assert_regular_directory(path, "run directory")
        return path

    @staticmethod
    def _contained(path: Path, parent: Path) -> Path:
        try:
            path.relative_to(parent)
            path.resolve(strict=False).relative_to(parent.resolve())
        except ValueError as exc:
            raise PermissionError("path escapes workflow runtime") from exc
        return path

    @staticmethod
    def _existing_directory(path: Path, label: str) -> Path:
        if path.is_symlink():
            raise PermissionError(f"{label} must not be a symlink")
        if not path.exists() or not path.is_dir():
            raise NotADirectoryError(label)
        return path

    def _prepare_directory(self, path: Path, label: str) -> Path:
        if path.is_symlink():
            raise PermissionError(f"{label} must not be a symlink")
        path.mkdir(exist_ok=True)
        return self._existing_directory(path, label)

    @staticmethod
    def _assert_regular_directory(path: Path, label: str) -> None:
        if path.is_symlink() or not path.is_dir():
            raise PermissionError(f"{label} must be a regular directory")

    @staticmethod
    def _assert_regular_file(path: Path, label: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise PermissionError(f"{label} must be a regular file")

    @staticmethod
    def _read_json(path: Path) -> dict:
        WorkflowStore._assert_regular_file(path, "workflow artifact")
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _json_bytes(payload: dict) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        parent = path.parent
        WorkflowStore._assert_regular_directory(parent, "artifact parent")
        if path.exists() or path.is_symlink():
            WorkflowStore._assert_regular_file(path, "artifact target")
        encoded = WorkflowStore._json_bytes(payload)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp_path.unlink(missing_ok=True)
