from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.strict_review_evidence_cache import (
    CommandEvidenceCache,
    cache_identity,
    write_preflight_artifacts,
)


def _identity(**overrides):
    value = {
        "sourceFingerprint": "a" * 64,
        "command": ["python", "-m", "compileall"],
        "policyFingerprint": "b" * 64,
        "runnerFingerprint": "c" * 64,
    }
    value.update(overrides)
    return value


def test_cache_hit_requires_exact_identity_and_success(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache = CommandEvidenceCache()
    command = {"label": "python-compile", "exitCode": 0, "stdoutTail": "", "stderrTail": ""}
    cache.store(cache_path, identity=_identity(), command=command)

    assert cache.load(cache_path, identity=_identity()) == command
    assert cache.load(cache_path, identity=_identity(sourceFingerprint="d" * 64)) is None
    assert cache.load(cache_path, identity=_identity(policyFingerprint="d" * 64)) is None


def test_failed_command_is_never_reused_and_malformed_is_miss(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache = CommandEvidenceCache()
    failed = {"label": "targeted-tests", "exitCode": 1, "stdoutTail": "x", "stderrTail": "y"}
    cache.store(cache_path, identity=_identity(), command=failed)
    assert cache.load(cache_path, identity=_identity()) is None
    cache_path.write_text("not json", encoding="utf-8")
    assert cache.load(cache_path, identity=_identity()) is None


def test_cache_identity_is_stable_and_artifacts_are_bounded_and_atomic(tmp_path: Path):
    assert cache_identity("a" * 64, ["python", "x"], "b" * 64, "c" * 64) == cache_identity("a" * 64, ["python", "x"], "b" * 64, "c" * 64)
    cache = CommandEvidenceCache(max_output_chars=10)
    cache_path = tmp_path / "cache.json"
    cache.store(cache_path, identity=_identity(), command={"label": "x", "exitCode": 0, "stdoutTail": "1234567890123", "stderrTail": "abcdefghijk"})
    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    assert len(stored["command"]["stdoutTail"]) <= 10
    assert len(stored["command"]["stderrTail"]) <= 10
    assert not list(tmp_path.glob("*.tmp"))


def test_artifact_writer_rejects_symlink_session_and_writes_two_bounded_files(tmp_path: Path):
    session_dir = tmp_path / "session"
    preflight = {"schemaVersion": "strict-review-preflight-v1", "diagnostics": ["x" * 1000]}
    verification = {"commands": [{"label": "x", "argv": ["x"], "exitCode": 0, "stdoutTail": "", "stderrTail": ""}]}
    paths = write_preflight_artifacts(session_dir, preflight, verification)
    assert all(path.exists() for path in paths)
    assert json.loads(paths[0].read_text(encoding="utf-8")) == preflight
    assert json.loads(paths[1].read_text(encoding="utf-8")) == verification

    linked = tmp_path / "linked"
    linked.symlink_to(session_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_preflight_artifacts(linked, preflight, verification)
