from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class VerificationRuntimeSnapshotError(ValueError):
    """Raised when a verification snapshot cannot be safely built or opened."""


@dataclass(frozen=True)
class SnapshotEvidence:
    source_fingerprint: str
    snapshot_fingerprint: str
    integrity: str
    snapshot_ref: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_source(path: Path) -> Path:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise VerificationRuntimeSnapshotError("source database must be a regular non-symlink file")
    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as conn:
            if conn.execute("pragma integrity_check").fetchone() != ("ok",):
                raise VerificationRuntimeSnapshotError("source database integrity check failed")
    except sqlite3.Error as exc:
        raise VerificationRuntimeSnapshotError("source database is unreadable") from exc
    return target


def _validate_destination(path: Path) -> Path:
    target = Path(path)
    if ".." in target.parts:
        raise VerificationRuntimeSnapshotError("snapshot destination cannot contain parent traversal")
    current = target.parent
    while current != current.parent:
        if current.is_symlink():
            raise VerificationRuntimeSnapshotError("snapshot destination parent cannot be symlinked")
        current = current.parent
    if target.is_symlink():
        raise VerificationRuntimeSnapshotError("snapshot destination cannot be symlinked")
    if target.exists():
        raise VerificationRuntimeSnapshotError("snapshot destination already exists")
    return target


def build_read_only_snapshot(source_db: Path, destination: Path) -> SnapshotEvidence:
    source = _validate_source(source_db)
    target = _validate_destination(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_conn, sqlite3.connect(target) as target_conn:
            source_conn.backup(target_conn)
            if target_conn.execute("pragma integrity_check").fetchone() != ("ok",):
                raise VerificationRuntimeSnapshotError("snapshot integrity check failed")
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        raise VerificationRuntimeSnapshotError("snapshot creation failed") from exc
    try:
        target.chmod(0o444)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise VerificationRuntimeSnapshotError("snapshot could not be made read-only") from exc
    source_fingerprint = _sha256(source)
    snapshot_fingerprint = _sha256(target)
    return SnapshotEvidence(source_fingerprint, snapshot_fingerprint, "ok", target.name)


def load_snapshot_read_only(snapshot: Path) -> sqlite3.Connection:
    target = Path(snapshot)
    if target.is_symlink() or not target.is_file():
        raise VerificationRuntimeSnapshotError("snapshot must be a regular non-symlink file")
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        if conn.execute("pragma integrity_check").fetchone() != ("ok",):
            conn.close()
            raise VerificationRuntimeSnapshotError("snapshot integrity check failed")
        conn.execute("pragma query_only=1")
        return conn
    except sqlite3.Error as exc:
        raise VerificationRuntimeSnapshotError("snapshot is unreadable") from exc
