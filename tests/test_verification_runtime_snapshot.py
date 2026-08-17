from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.services.verification_runtime_snapshot import (
    VerificationRuntimeSnapshotError,
    build_read_only_snapshot,
    load_snapshot_read_only,
)


def _db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("create table sample (value text)")
        conn.execute("insert into sample values ('source')")


def test_snapshot_matches_source_and_connection_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "verification" / "snapshot.sqlite"
    _db(source)
    source_before = source.read_bytes()

    evidence = build_read_only_snapshot(source, destination)

    assert evidence.integrity == "ok"
    assert len(evidence.source_fingerprint) == 64
    assert len(evidence.snapshot_fingerprint) == 64
    assert source.read_bytes() == source_before
    with load_snapshot_read_only(destination) as conn:
        assert conn.execute("select value from sample").fetchone() == ("source",)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("insert into sample values ('blocked')")
    with pytest.raises(sqlite3.OperationalError):
        with sqlite3.connect(destination) as conn:
            conn.execute("insert into sample values ('blocked')")


def test_snapshot_rejects_missing_source_and_does_not_create_destination(tmp_path: Path) -> None:
    destination = tmp_path / "verification" / "snapshot.sqlite"
    with pytest.raises(VerificationRuntimeSnapshotError):
        build_read_only_snapshot(tmp_path / "missing.sqlite", destination)
    assert not destination.exists()


def test_snapshot_rejects_symlink_and_traversal_destinations(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite"
    _db(source)
    target = tmp_path / "real.sqlite"
    target.write_bytes(b"sentinel")
    link = tmp_path / "link.sqlite"
    link.symlink_to(target)
    with pytest.raises(VerificationRuntimeSnapshotError):
        build_read_only_snapshot(source, link)
    with pytest.raises(VerificationRuntimeSnapshotError):
        build_read_only_snapshot(source, tmp_path / "verification" / ".." / "escape.sqlite")
