from __future__ import annotations

import json

import pytest

from backend.agents.agent_eval_store import publish_bundle, read_json


def test_traversal_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsafe_path"):
        read_json(tmp_path, "../secret.json", max_bytes=64)


def test_over_quota_never_publishes(tmp_path):
    with pytest.raises(ValueError, match="quota_exceeded"):
        publish_bundle(tmp_path, "exp-1", {"report.json": b"x" * 65}, quota_bytes=64)
    assert not (tmp_path / "exp-1").exists()


def test_json_is_strict_and_bundle_is_atomic_idempotent(tmp_path):
    (tmp_path / "input.json").write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_json_key"):
        read_json(tmp_path, "input.json", max_bytes=64)
    result = publish_bundle(tmp_path, "exp-1", {"report.json": b"{}"})
    assert result["status"] == "published"
    assert publish_bundle(tmp_path, "exp-1", {"report.json": b"{}"})["status"] == "existing"
    with pytest.raises(ValueError, match="experiment_collision"):
        publish_bundle(tmp_path, "exp-1", {"report.json": b"different"})


def test_symlink_is_rejected(tmp_path):
    (tmp_path / "secret").write_text("{}", encoding="utf-8")
    (tmp_path / "link.json").symlink_to(tmp_path / "secret")
    with pytest.raises(ValueError, match="invalid_artifact"):
        read_json(tmp_path, "link.json", max_bytes=64)


def test_quota_includes_bundle_metadata(tmp_path):
    with pytest.raises(ValueError, match="quota_exceeded"):
        publish_bundle(tmp_path, "exp-1", {"report.json": b"{}"}, quota_bytes=2)


def test_existing_bundle_detects_tampered_file(tmp_path):
    publish_bundle(tmp_path, "exp-1", {"report.json": b"{}"})
    (tmp_path / "exp-1" / "report.json").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="experiment_collision"):
        publish_bundle(tmp_path, "exp-1", {"report.json": b"{}"})


def test_root_and_nested_symlinks_are_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "input.json").write_text("{}", encoding="utf-8")
    (tmp_path / "root-link").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe_path"):
        read_json(tmp_path / "root-link", "input.json", max_bytes=64)
    (tmp_path / "nested-link").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="invalid_artifact"):
        read_json(tmp_path, "nested-link/input.json", max_bytes=64)
