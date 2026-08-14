from __future__ import annotations

from pathlib import Path

from scripts.hermes_post_change_check import short_term_offload_artifact_report


def test_short_term_offload_hermes_report_is_read_only_and_bounded(tmp_path: Path) -> None:
    root = tmp_path / ".nbs_agent_runtime" / "short-term-offload" / "run-1" / "session-1"
    root.mkdir(parents=True)
    (root / "ref-1.json").write_text("{}", encoding="utf-8")
    before = (tmp_path / ".nbs_agent_runtime" / "short-term-offload").stat().st_mtime_ns
    report = short_term_offload_artifact_report(tmp_path)
    after = (tmp_path / ".nbs_agent_runtime" / "short-term-offload").stat().st_mtime_ns
    assert report["schemaVersion"] == "short-term-offload-hermes-report-v1"
    assert report["policy"] == "read-only"
    assert report["artifactCount"] == 1
    assert before == after


def test_short_term_offload_hermes_report_blocks_root_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / ".nbs_agent_runtime"
    root.mkdir()
    (root / "short-term-offload").symlink_to(outside, target_is_directory=True)
    report = short_term_offload_artifact_report(tmp_path)
    assert report["status"] == "blocked"
