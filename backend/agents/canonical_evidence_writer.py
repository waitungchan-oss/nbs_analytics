from __future__ import annotations

import errno
import os
from pathlib import Path

from .canonical_evidence_models import CanonicalEvidenceEnvelope, CanonicalEvidenceSchemaError
from .canonical_evidence_registry import CanonicalEvidenceRegistry
from .workflow_models import WorkflowApproval
from .workflow_store import WorkflowStore


class CanonicalEvidenceWriteError(RuntimeError):
    """Raised when final canonical evidence cannot be safely created."""


class CanonicalEvidenceWriter:
    """Create one immutable, registry-owned canonical evidence artifact per run."""

    def __init__(self, project_root: Path) -> None:
        self._store = WorkflowStore(Path(project_root))
        self._registry = CanonicalEvidenceRegistry()

    def write_final(self, run_id: str, envelope: CanonicalEvidenceEnvelope) -> Path:
        try:
            entry = self._registry.for_kind(envelope.artifact_kind)
            canonical = CanonicalEvidenceEnvelope.from_dict(
                envelope.to_dict(), expected_kind=entry.artifact_kind
            )
            if canonical.run_id != run_id:
                raise CanonicalEvidenceWriteError("envelope run ID does not match target run")
            if not canonical.is_finalized:
                raise CanonicalEvidenceWriteError("canonical evidence must be finalized")

            with self._store.run_lock(run_id):
                manifest = self._store.load_manifest(run_id)
                if manifest.run_id != run_id:
                    raise CanonicalEvidenceWriteError("run manifest does not match target run")
                approval = self._load_approval(run_id)
                if approval.run_id != run_id:
                    raise CanonicalEvidenceWriteError("approval run ID does not match target run")
                if approval.authorization_status != "approved":
                    raise CanonicalEvidenceWriteError("approval is not approved")
                if approval.contract_fingerprint != canonical.contract_fingerprint:
                    raise CanonicalEvidenceWriteError("approval contract does not match envelope")
                target = self._store._run_file(run_id, entry.filename)
                self._create_only_json(target, canonical.to_dict())
            return target
        except CanonicalEvidenceWriteError:
            raise
        except (CanonicalEvidenceSchemaError, OSError, PermissionError, ValueError, FileNotFoundError) as exc:
            raise CanonicalEvidenceWriteError("canonical evidence write rejected") from exc

    def _load_approval(self, run_id: str) -> WorkflowApproval:
        approval_path = self._store._run_file(run_id, "approval.json")
        return WorkflowApproval.from_dict(self._store._read_json(approval_path))

    @staticmethod
    def _create_only_json(target: Path, payload: dict) -> None:
        if target.is_symlink():
            raise CanonicalEvidenceWriteError("canonical target must not be a symlink")
        if target.exists():
            raise CanonicalEvidenceWriteError("canonical evidence already exists")
        encoded = WorkflowStore._json_bytes(payload)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            fd = os.open(target, flags, 0o600)
        except OSError as exc:
            if exc.errno in {errno.EEXIST, errno.ELOOP}:
                raise CanonicalEvidenceWriteError("canonical evidence already exists or is unsafe") from exc
            raise
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _write_expected_kind(project_root: Path, run_id: str, envelope: CanonicalEvidenceEnvelope, expected_kind: str) -> Path:
    if envelope.artifact_kind != expected_kind:
        raise CanonicalEvidenceWriteError("writer entrypoint does not own envelope kind")
    return CanonicalEvidenceWriter(project_root).write_final(run_id, envelope)


def write_task_gate(project_root: Path, run_id: str, envelope: CanonicalEvidenceEnvelope) -> Path:
    return _write_expected_kind(project_root, run_id, envelope, "task_gate")


def write_terra_diagnosis(project_root: Path, run_id: str, envelope: CanonicalEvidenceEnvelope) -> Path:
    return _write_expected_kind(project_root, run_id, envelope, "terra_diagnosis")


def write_protected_incident(project_root: Path, run_id: str, envelope: CanonicalEvidenceEnvelope) -> Path:
    return _write_expected_kind(project_root, run_id, envelope, "protected_incident")
