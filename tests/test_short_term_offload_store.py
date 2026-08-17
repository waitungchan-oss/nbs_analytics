from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from backend.agents.short_term_offload_models import ShortTermOffloadArtifact
from backend.agents.short_term_offload_policy import ShortTermOffloadPolicy
from backend.agents.short_term_offload_store import ShortTermOffloadStore


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _artifact(ref: str, run: str = "run-1", content: str = "payload", *, created: datetime = NOW, expires: datetime | None = None) -> ShortTermOffloadArtifact:
    return ShortTermOffloadArtifact(
        "short-term-offload-v1", ref, run, "session-1", "tool_output", "summary", content,
        sha256(content.encode()).hexdigest(), created, expires or created + timedelta(minutes=30),
        sha256(content.encode()).hexdigest(), "clean", "ready",
    )


def test_store_round_trip_isolated_and_typed(tmp_path: Path) -> None:
    store = ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy())
    artifact = _artifact("ref-1")
    store.write(artifact, now=NOW)
    loaded = store.read("run-1", "session-1", "ref-1", now=NOW)
    assert loaded == artifact
    assert loaded is not None
    assert loaded.content == "payload"
    assert list((tmp_path / ".nbs_agent_runtime" / "short-term-offload").rglob("*.json"))


def test_store_rejects_path_escape_symlink_and_enforces_run_caps(tmp_path: Path) -> None:
    policy = ShortTermOffloadPolicy(max_artifacts_per_run=1, max_total_bytes_per_run=1000)
    store = ShortTermOffloadStore(tmp_path, policy=policy)
    store.write(_artifact("ref-1", content="1234567"), now=NOW)
    with pytest.raises(ValueError):
        store.write(_artifact("ref-2", content="x"))
    with pytest.raises(ValueError):
        store.read("../escape", "session-1", "ref-1")
    run_dir = tmp_path / ".nbs_agent_runtime" / "short-term-offload" / "run-2"
    run_dir.mkdir(parents=True)
    (run_dir / "session-1").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        store.write(_artifact("ref-3", run="run-2"))


def test_cleanup_expired_only_removes_expired_offload_files(tmp_path: Path) -> None:
    store = ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy())
    expired = _artifact("expired", created=NOW - timedelta(minutes=10), expires=NOW - timedelta(seconds=1))
    current = _artifact("current")
    store.write(expired, allow_expired=True, now=NOW)
    store.write(current, now=NOW)
    assert store.cleanup_expired(now=NOW) == ("expired",)
    assert store.read("run-1", "session-1", "current", now=NOW) == current


def test_read_expired_is_fail_closed_and_write_revalidates_envelope(tmp_path: Path) -> None:
    store = ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy())
    expired = _artifact("expired", created=NOW - timedelta(minutes=10), expires=NOW - timedelta(seconds=1))
    store.write(expired, allow_expired=True, now=NOW)
    assert store.read("run-1", "session-1", "expired", now=NOW) is None
    tampered = _artifact("tampered")
    object.__setattr__(tampered, "content_sha256", "0" * 64)
    with pytest.raises(ValueError):
        store.write(tampered)


def test_read_rejects_symlinked_isolated_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "run-1" / "session-1").mkdir(parents=True)
    artifact = _artifact("ref-1")
    (outside / "run-1" / "session-1" / "ref-1.json").write_text(
        __import__("json").dumps(artifact.to_dict()), encoding="utf-8"
    )
    root = tmp_path / ".nbs_agent_runtime" / "short-term-offload"
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy()).read("run-1", "session-1", "ref-1")


def test_write_rejects_symlinked_runtime_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.symlink_to(outside, target_is_directory=True)
    store = ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy())
    with pytest.raises(ValueError):
        store.write(_artifact("ref-1"))


def test_cleanup_rejects_symlinked_runtime_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime = tmp_path / ".nbs_agent_runtime"
    runtime.symlink_to(outside, target_is_directory=True)
    store = ShortTermOffloadStore(tmp_path, policy=ShortTermOffloadPolicy())
    with pytest.raises(ValueError):
        store.cleanup_expired(now=NOW)
