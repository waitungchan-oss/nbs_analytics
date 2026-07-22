from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.agents.workflow_retention import (
    RetentionCandidate,
    RetentionPolicy,
    RetentionReport,
    WorkflowRetention,
)


NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
TERMINAL = {"completed", "blocked", "failed", "changes_required"}


def _run(root: Path, run_id: str, created: datetime, status: str = "completed", *, schema="agent-workflow") -> Path:
    path = root / run_id
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({"schemaVersion": f"{schema}-manifest-v1", "runId": run_id, "createdAt": created.isoformat()}))
    (path / "status.json").write_text(json.dumps({"schemaVersion": f"{schema}-status-v1", "runId": run_id, "status": status, "updatedAt": created.isoformat()}))
    (path / "approval.json").write_text("approval")
    (path / "events.jsonl").write_text('{"eventType":"error","metadata":{"risk":"baseline"}}\n')
    return path


def _policy() -> RetentionPolicy:
    return RetentionPolicy(
        retain_days=90,
        retain_latest_terminal_runs=30,
        stage_artifact_max_bytes=5 * 1024 * 1024,
        run_artifact_soft_cap_bytes=25 * 1024 * 1024,
        command_output_tail_characters=12000,
    )


def test_missing_module_is_replaced_by_policy_and_config():
    assert _policy().retain_days == 90


def test_recent_and_latest_terminal_runs_remain_complete(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    for index in range(35):
        age = 100 + index
        path = _run(runs, f"run-{index:02d}", NOW - timedelta(days=age))
        (path / "review.json").write_text("review")
    report = WorkflowRetention(tmp_path, policy=_policy()).plan(NOW)
    assert {c.run_id for c in report.candidates if c.action == "compact"} == {"run-30", "run-31", "run-32", "run-33", "run-34"}
    assert all(c.run_id != "run-00" for c in report.candidates)


def test_nonterminal_and_risk_terminal_runs_are_never_pruned(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    for status in ("implementation_running", "blocked", "failed", "changes_required"):
        path = _run(runs, status, NOW - timedelta(days=200), status)
        (path / "review.json").write_text("keep risk metadata")
    report = WorkflowRetention(tmp_path, policy=_policy()).plan(NOW)
    assert report.candidates == ()


def test_old_completed_stage_reports_are_compacted_and_summary_precedes_delete(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "old", NOW - timedelta(days=200))
    (path / "context.json").write_text("context")
    (path / "implementation.json").write_text("implementation")
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    retention = WorkflowRetention(tmp_path, policy=_policy())
    report = retention.plan(NOW)
    assert report.candidates[0].delete_paths == ("context.json", "implementation.json", "events.jsonl")
    dry_before = {p.name: p.read_bytes() for p in path.iterdir()}
    retention.apply(report, dry_run=True)
    assert {p.name: p.read_bytes() for p in path.iterdir()} == dry_before == before
    retention.apply(report)
    assert not (path / "context.json").exists()
    assert (path / "manifest.json").exists()
    summary = json.loads((path / "archive-summary.json").read_text())
    assert summary["deletedFiles"] == ["context.json", "implementation.json", "events.jsonl"]


def test_old_completed_governance_projection_is_compacted(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "old", NOW - timedelta(days=200))
    (path / "governance-graph.json").write_text("projection")
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))

    report = WorkflowRetention(tmp_path, policy=_policy()).plan(NOW)
    candidate = next(candidate for candidate in report.candidates if candidate.run_id == "old")

    assert "governance-graph.json" in candidate.delete_paths
    WorkflowRetention(tmp_path, policy=_policy()).apply(report)
    assert not (path / "governance-graph.json").exists()


@pytest.mark.parametrize("status", ["blocked", "failed", "changes_required"])
def test_old_noncompleted_governance_projection_is_preserved(tmp_path, status):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, f"old-{status}", NOW - timedelta(days=200), status)
    (path / "governance-graph.json").write_text("projection")
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))

    report = WorkflowRetention(tmp_path, policy=_policy()).plan(NOW)

    assert all(candidate.run_id != f"old-{status}" for candidate in report.candidates)
    assert (path / "governance-graph.json").exists()


def test_unknown_schema_symlink_and_external_path_are_skipped(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    _run(runs, "unknown", NOW - timedelta(days=200), schema="unknown")
    external = tmp_path / "external"
    external.mkdir()
    (external / "manifest.json").write_text("outside")
    os.symlink(external, runs / "external-link", target_is_directory=True)
    report = WorkflowRetention(tmp_path, policy=_policy()).plan(NOW)
    assert report.candidates == ()
    assert (external / "manifest.json").exists()


def test_held_lock_is_skipped_and_plan_does_not_change_bytes(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "locked", NOW - timedelta(days=200))
    lock = path / ".lock"
    lock.write_text("")
    retention = WorkflowRetention(tmp_path, policy=_policy())
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with retention.store.run_lock("locked"):
        report = retention.plan(NOW)
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert report.candidates == ()
    assert after == before


def test_apply_rejects_report_paths_outside_runs(tmp_path):
    retention = WorkflowRetention(tmp_path, policy=_policy())
    outside = tmp_path / "outside.json"
    outside.write_text("do not delete")
    report = retention.plan(NOW)
    report = report.__class__(generated_at=report.generated_at, candidates=(
        RetentionCandidate(run_id="../outside", action="compact", reasons=("test",), delete_paths=(str(outside),), estimated_bytes=outside.stat().st_size),
    ))
    with pytest.raises(PermissionError):
        retention.apply(report)
    assert outside.exists()


def test_apply_rechecks_status_and_skips_stale_nonterminal_report(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "stale", NOW - timedelta(days=200))
    (path / "context.json").write_text("context")
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))
    retention = WorkflowRetention(tmp_path, policy=_policy())
    report = retention.plan(NOW)
    status = json.loads((path / "status.json").read_text())
    status["status"] = "implementation_running"
    (path / "status.json").write_text(json.dumps(status))
    retention.apply(report)
    assert (path / "context.json").exists()
    assert not (path / "archive-summary.json").exists()


def test_apply_rechecks_latest_terminal_set_and_skips_newly_protected_run(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "old", NOW - timedelta(days=200))
    (path / "context.json").write_text("context")
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))
    retention = WorkflowRetention(tmp_path, policy=_policy())
    report = retention.plan(NOW)
    changed = runs / "new-00" / "status.json"
    status = json.loads(changed.read_text())
    status["status"] = "implementation_running"
    changed.write_text(json.dumps(status))
    retention.apply(report)
    assert (path / "context.json").exists()


@pytest.mark.parametrize("run_id", ["", ".", "..", "a/b", "a/../b", "/absolute"])
def test_apply_rejects_non_single_run_directory_ids(tmp_path, run_id):
    retention = WorkflowRetention(tmp_path, policy=_policy())
    report = RetentionReport(
        generated_at=NOW.isoformat(),
        candidates=(RetentionCandidate(run_id, "compact", ("test",), ("context.json",), 1),),
    )
    with pytest.raises(PermissionError):
        retention.apply(report)


def test_stage_cap_rejects_oversized_stage_and_run_soft_cap_compacts_all_stages(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "oversized", NOW - timedelta(days=200))
    (path / "context.json").write_bytes(b"x" * 11)
    (path / "implementation.json").write_bytes(b"y" * 11)
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))
    policy = RetentionPolicy(90, 30, 10, 15, 12_000)
    report = WorkflowRetention(tmp_path, policy=policy).plan(NOW)
    assert report.candidates == ()
    policy = RetentionPolicy(90, 30, 20, 15, 12_000)
    report = WorkflowRetention(tmp_path, policy=policy).plan(NOW)
    assert report.candidates[0].delete_paths == ("context.json", "implementation.json", "events.jsonl")


def test_old_completed_events_are_compacted_to_bounded_archive_summary(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "old", NOW - timedelta(days=200))
    event = {
        "eventType": "failed",
        "message": "failure",
        "metadata": {"errorCode": "E_TEST", "riskSurface": "baseline"},
        "commandOutput": "z" * 100,
    }
    (path / "events.jsonl").write_text(json.dumps(event) + "\n")
    (path / "context.json").write_text("context")
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))
    retention = WorkflowRetention(tmp_path, policy=RetentionPolicy(90, 30, 100, 1000, 12))
    report = retention.plan(NOW)
    retention.apply(report)
    summary = json.loads((path / "archive-summary.json").read_text())
    assert not (path / "events.jsonl").exists()
    assert summary["eventSummary"]["errorCodes"] == ["E_TEST"]
    assert len(json.dumps(summary)) <= 1000
    assert len(summary["eventSummary"]["commandOutputTails"][0]) <= 12


def test_apply_is_idempotent_for_the_same_report(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    runs.mkdir(parents=True)
    path = _run(runs, "old", NOW - timedelta(days=200))
    (path / "context.json").write_text("context")
    for index in range(30):
        _run(runs, f"new-{index:02d}", NOW - timedelta(days=199 - index))
    retention = WorkflowRetention(tmp_path, policy=_policy())
    report = retention.plan(NOW)
    retention.apply(report)
    first_summary = (path / "archive-summary.json").read_bytes()
    retention.apply(report)
    assert (path / "archive-summary.json").read_bytes() == first_summary


def test_policy_requires_object_and_positive_caps():
    with pytest.raises(ValueError):
        RetentionPolicy.from_dict([])
    payload = {
        "schemaVersion": "agent-workflow-retention-v1",
        "retainDays": 90,
        "retainLatestTerminalRuns": 30,
        "stageArtifactMaxBytes": 0,
        "runArtifactSoftCapBytes": 1,
        "commandOutputTailCharacters": 1,
    }
    with pytest.raises(ValueError):
        RetentionPolicy.from_dict(payload)
