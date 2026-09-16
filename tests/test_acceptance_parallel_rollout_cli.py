from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.agents.evidence_models import canonical_fingerprint
from scripts.pytest_manifest import _manifest_fingerprint


COMMIT = "a" * 40
SOURCE = "b" * 64
CONTRACT = "c" * 64
MANIFEST = "d" * 64
POPULATION = "e" * 64
RUNNER = "f" * 64
ENVIRONMENT = "0" * 64
DATASET = "1" * 64


def _contract_payload():
    from backend.agents.acceptance_contract import build_acceptance_contract

    return build_acceptance_contract(
        contract_id="parallel-rollout",
        contract_version="v1",
        scope_fingerprint="1" * 64,
        semantic_rules_fingerprint="2" * 64,
        dataset_snapshot_fingerprint=DATASET,
        supersedes_contract_fingerprint=None,
        status="active",
    )


def _manifest_payload():
    nodeids = sorted(f"tests/test_parallel.py::test_{index}" for index in range(16))
    return {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
        "manifestFingerprint": _manifest_fingerprint(COMMIT, SOURCE, nodeids),
    }


def _argv(tmp_path, shard_count: int, repeats: int) -> list[str]:
    manifest = tmp_path / "manifest.json"
    contract = tmp_path / "contract.json"
    manifest.write_text(json.dumps(_manifest_payload()), encoding="utf-8")
    contract.write_text(json.dumps(_contract_payload()), encoding="utf-8")
    return [
        "--project-root", str(tmp_path),
        "--manifest", str(manifest),
        "--contract", str(contract),
        "--commit-sha", COMMIT,
        "--source-fingerprint", SOURCE,
        "--runner-fingerprint", RUNNER,
        "--environment-fingerprint", ENVIRONMENT,
        "--baseline-family-id", "acceptance-full-serial-ci-v1",
        "--shard-count", str(shard_count),
        "--repeats", str(repeats),
        "--timeout", "1800",
        "--output", str(tmp_path.parent / f"{tmp_path.name}-rollout.json"),
    ]


def _passing_serial_control(*, run_index: int, **kwargs):
    return {
        "status": "PASS",
        "failureCode": None,
        "result": {"passed": 16, "failed": 0, "skipped": 0},
        "serialWallSeconds": 100.0,
        "artifactFingerprint": ("1" if run_index == 0 else "2") * 64,
    }


def _passing_parallel_shards(*, shard_count: int, output_root, **kwargs):
    shards = []
    nodeids = sorted(_manifest_payload()["nodeids"])
    manifest_fingerprint = _manifest_payload()["manifestFingerprint"]
    for index in range(shard_count):
        assigned = [nodeid for position, nodeid in enumerate(nodeids) if position % shard_count == index]
        unsigned = {
            "schemaVersion": "full-pytest-shard-v1",
            "status": "PASS",
            "authority": "prototype",
            "formalReleaseEnabled": False,
            "commitSha": COMMIT,
            "sourceFingerprint": SOURCE,
            "manifestFingerprint": manifest_fingerprint,
            "shardIndex": index,
            "shardCount": shard_count,
            "assignedNodeids": assigned,
            "executedNodeids": assigned,
            "result": {"passed": len(assigned), "failed": 0, "skipped": 0},
        }
        shards.append({**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)})
    aggregate_unsigned = {
        "schemaVersion": "full-pytest-shard-aggregate-v1",
        "status": "PASS",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "manifestFingerprint": manifest_fingerprint,
        "shardCount": shard_count,
        "coveredNodeids": len(nodeids),
        "shards": {str(item["shardIndex"]): item["evidenceFingerprint"] for item in shards},
        "result": {"passed": 16, "failed": 0, "skipped": 0},
    }
    aggregate = {**aggregate_unsigned, "evidenceFingerprint": canonical_fingerprint(aggregate_unsigned)}
    return {
        "status": "PASS",
        "failureCode": None,
        "parallelWallSeconds": 75.0,
        "aggregate": aggregate,
        "shards": shards,
        "shardArtifactPaths": [str(output_root / f"shard-{index}.json") for index in range(shard_count)],
        "cleanup": {"allProcessGroupsTerminated": True},
    }


def _missing_shard_result(*, shard_count: int, **kwargs):
    return {"status": "BLOCKED", "failureCode": "shard_set_incomplete", "parallelWallSeconds": 75.0}


def _qualified_v2(*, role: str, total: float):
    from backend.agents.acceptance_performance_v2 import build_performance_baseline_v2

    return build_performance_baseline_v2(
        baseline_family_id="acceptance-full-serial-ci-v1",
        baseline_role=role,
        lifecycle="qualified",
        contract_fingerprint=CONTRACT,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest_fingerprint=MANIFEST,
        test_population_fingerprint=POPULATION,
        runner_fingerprint=RUNNER,
        environment_fingerprint=ENVIRONMENT,
        dataset_snapshot_fingerprint=DATASET,
        selection_mode="full",
        stages={
            "collectionSeconds": 1.0,
            "fixturePreparationSeconds": 2.0,
            "pytestExecutionSeconds": total - 4.0,
            "aggregateSeconds": 1.0,
            "totalWallSeconds": total,
        },
        test_count={"collected": 16, "passed": 16, "failed": 0, "skipped": 0},
    )


def test_cli_runs_three_fresh_measurements_and_writes_rollout_artifact(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "run_serial_control", _passing_serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", _passing_parallel_shards)
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))
    assert code == 0
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert rollout["status"] == "PASS"
    assert rollout["rolloutCandidate"] == "eligible"
    assert len(rollout["measuredRuns"]) == 3
    assert all(run["parity"]["status"] == "PASS" for run in rollout["measuredRuns"])


def test_cli_refuses_to_overwrite_existing_output(tmp_path):
    import pytest
    from scripts import acceptance_parallel_rollout as subject

    output = tmp_path / "rollout.json"
    output.write_text("keep this evidence", encoding="utf-8")
    with pytest.raises(ValueError, match="new regular file"):
        subject._write_output(output, {"status": "PASS"})
    assert output.read_text(encoding="utf-8") == "keep this evidence"


def test_cli_blocks_and_suppresses_speedup_when_one_shard_is_missing(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "run_serial_control", _passing_serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", _missing_shard_result)
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))
    assert code == 2
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert rollout["status"] == "BLOCKED"
    assert rollout["speedup"] is None
    assert "shard_set_incomplete" in rollout["blockers"]


def test_cli_skips_parallel_when_serial_control_is_blocked(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    def blocked_serial_control(*, run_index: int, **kwargs):
        return {
            "status": "BLOCKED",
            "failureCode": "serial_source_dirty",
            "result": {"passed": 0, "failed": 0, "skipped": 0},
            "serialWallSeconds": 0.001,
            "artifactFingerprint": ("1" if run_index == 0 else "2") * 64,
        }

    def unexpected_parallel(**kwargs):
        raise AssertionError("parallel execution must be skipped")

    monkeypatch.setattr(subject, "run_serial_control", blocked_serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", unexpected_parallel)
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))

    assert code == 2
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert rollout["status"] == "BLOCKED"
    assert all(run["failureCode"] == "serial_source_dirty" for run in rollout["measuredRuns"])


def test_cli_records_blocked_rollout_when_parallel_runner_raises(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "run_serial_control", _passing_serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("runner crashed")))
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))

    assert code == 2
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert rollout["status"] == "BLOCKED"
    assert len(rollout["measuredRuns"]) == 3
    assert all(run["failureCode"] == "parallel_runner_error" for run in rollout["measuredRuns"])
    assert rollout["speedup"] is None


def test_serial_control_blocks_when_source_identity_is_not_current(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(
        subject,
        "_current_source_identity",
        lambda project_root: ("9" * 40, "8" * 64, ""),
    )

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_source_identity_mismatch"


def test_serial_control_accepts_only_an_exact_source_seal_for_dirty_worktree(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            return {"status": "PASS", "allProcessGroupsTerminated": True}

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, " M approved.py\n"))
    monkeypatch.setattr(subject, "_current_worktree_fingerprint", lambda project_root: "d" * 64)
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *args, **kwargs: subject.subprocess.CompletedProcess(args[0], 0, "1 passed in 0.01s\n", ""),
    )

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal={
            "schemaVersion": "verification-session-v1",
            "headSha": COMMIT,
            "sourceFingerprint": SOURCE,
            "worktreeFingerprint": "d" * 64,
        },
    )

    assert result["status"] == "PASS"


def test_verification_session_source_fingerprint_is_not_archive_hash(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            return {"status": "PASS", "allProcessGroupsTerminated": True}

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, "a" * 64, " M implementation.py\n"))
    monkeypatch.setattr(subject, "_sealed_worktree_fingerprint", lambda project_root, source_seal: "d" * 64)
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())
    monkeypatch.setattr(
        subject.subprocess,
        "run",
        lambda *args, **kwargs: subject.subprocess.CompletedProcess(args[0], 0, "1 passed in 0.01s\n", ""),
    )

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal={
            "schemaVersion": "verification-session-v1",
            "headSha": COMMIT,
            "sourceFingerprint": SOURCE,
            "worktreeFingerprint": "d" * 64,
        },
    )

    assert result["status"] == "PASS"


def test_real_source_seal_uses_canonical_worktree_fingerprint_for_serial_control(monkeypatch):
    from backend.agents.verification_chain import git_source_probe
    from scripts import acceptance_parallel_rollout as subject

    project_root = Path(__file__).resolve().parents[1]
    commit_sha, source_fingerprint, _ = subject._current_source_identity(project_root)
    probe = git_source_probe(
        project_root,
        brief_path="docs/superpowers/specs/2026-09-15-acceptance-parallel-rollout-and-speedup-design.md",
        base_sha=commit_sha,
        head_ref="WORKTREE",
    )
    assert subject._current_worktree_fingerprint(project_root) == probe["worktree_fingerprint"]

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            return {"status": "PASS", "allProcessGroupsTerminated": True}

    real_run = subject.subprocess.run

    def fake_run(argv, *args, **kwargs):
        if argv and argv[0] == "git":
            return real_run(argv, *args, **kwargs)
        return subject.subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())
    monkeypatch.setattr(subject.subprocess, "run", fake_run)

    result = subject.run_serial_control(
        project_root=project_root,
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal={
            "schemaVersion": "verification-session-v1",
            "baseSha": commit_sha,
            "briefPath": "docs/superpowers/specs/2026-09-15-acceptance-parallel-rollout-and-speedup-design.md",
            "headSha": commit_sha,
            "sourceFingerprint": source_fingerprint,
            "worktreeFingerprint": probe["worktree_fingerprint"],
        },
    )

    assert result["status"] == "PASS"


def test_serial_control_converts_cleanup_exception_to_blocked_evidence(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            raise RuntimeError("cleanup unavailable")

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, ""))
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "isolation_violation"
    assert result["cleanup"]["status"] == "BLOCKED"


def test_v2_comparison_reports_speedup_multiple_without_cross_lineage_ratio():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    baseline = _qualified_v2(role="serial_control", total=100.0)
    candidate = _qualified_v2(role="parallel_candidate", total=75.0)
    result = compare_performance_baselines_v2(baseline, candidate)
    assert result["status"] == "compared"
    assert result["totalSpeedupMultiple"] >= 1.0
    candidate["manifestFingerprint"] = "9" * 64
    candidate["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in candidate.items() if key != "evidenceFingerprint"}
    )
    result = compare_performance_baselines_v2(baseline, candidate)
    assert result["status"] == "not_compared"
    assert "totalSpeedupMultiple" not in result


def test_v2_artifact_rejects_non_finite_duration():
    import pytest
    from scripts import acceptance_parallel_rollout as subject

    with pytest.raises(ValueError, match="performance duration"):
        subject._build_v2_artifact(
            role="serial_control",
            total_seconds=float("nan"),
            result={"passed": 16, "failed": 0, "skipped": 0},
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            baseline_family_id="acceptance-full-serial-ci-v1",
            lineage={
                "contractFingerprint": CONTRACT,
                "manifestFingerprint": MANIFEST,
                "testPopulationFingerprint": POPULATION,
                "runnerFingerprint": RUNNER,
                "environmentFingerprint": ENVIRONMENT,
                "datasetSnapshotFingerprint": DATASET,
            },
        )
