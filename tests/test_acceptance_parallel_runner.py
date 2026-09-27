from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from scripts.pytest_manifest import _manifest_fingerprint


COMMIT = "a" * 40
SOURCE = "b" * 64
_EXECUTION_LINEAGE = {
    "runnerFingerprint": "c" * 64,
    "environmentFingerprint": "d" * 64,
    "datasetSnapshotFingerprint": "e" * 64,
}
_RUNNER_CAPABILITY_UNSIGNED = {"schemaVersion": "acceptance-runner-capability-v1", "runnerFingerprint": "c" * 64, "maxWorkers": 4}
_RUNNER_CAPABILITY = {
    **_RUNNER_CAPABILITY_UNSIGNED,
    "capabilityFingerprint": canonical_fingerprint(_RUNNER_CAPABILITY_UNSIGNED),
}


@pytest.fixture(autouse=True)
def _runtime_identity(monkeypatch):
    from backend.agents import acceptance_parallel_runner as subject

    monkeypatch.setattr(
        subject,
        "observe_runtime_fingerprints",
        lambda project_root: {
            "runnerFingerprint": _EXECUTION_LINEAGE["runnerFingerprint"],
            "environmentFingerprint": _EXECUTION_LINEAGE["environmentFingerprint"],
        },
    )


def _manifest(node_count: int) -> dict[str, object]:
    nodeids = [f"tests/test_parallel.py::test_case_{index:02d}" for index in range(node_count)]
    unsigned = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
        "manifestFingerprint": _manifest_fingerprint(COMMIT, SOURCE, nodeids),
    }
    return unsigned


def _passing_shard(index: int, fixture_root) -> dict[str, object]:
    nodeids = sorted(_manifest(16)["nodeids"])
    assigned = [nodeid for position, nodeid in enumerate(nodeids) if position % 4 == index]
    unsigned = {
        "schemaVersion": "full-pytest-shard-v1",
        "status": "PASS",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "manifestFingerprint": _manifest(16)["manifestFingerprint"],
        "shardIndex": index,
        "shardCount": 4,
        "assignedNodeids": assigned,
        "executedNodeids": assigned,
        "result": {"passed": len(assigned), "failed": 0, "skipped": 0},
        "startedAt": "2026-09-15T00:00:00Z",
        "finishedAt": "2026-09-15T00:00:01Z",
        "metadata": {
            "cleanup": {
                "status": "PASS",
                "allProcessGroupsTerminated": True,
            }
        },
        "fixtureRoot": str(fixture_root),
        "lineage": dict(_EXECUTION_LINEAGE),
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _output_root(tmp_path):
    return tmp_path.parent / f"{tmp_path.name}-runtime"


def _one_failing_shard(index: int, fixture_root, **kwargs) -> dict[str, object]:
    kwargs["readiness_callback"]()
    result = _passing_shard(index, fixture_root)
    result["status"] = "BLOCKED"
    result["metadata"] = {
        "cleanup": {"status": "PASS", "allProcessGroupsTerminated": True},
        "failureCode": "pytest_failed",
    }
    unsigned = {key: value for key, value in result.items() if key != "evidenceFingerprint"}
    result["evidenceFingerprint"] = canonical_fingerprint(unsigned)
    return result


def _ready_and_start(kwargs):
    kwargs["readiness_callback"]()
    kwargs["start_callback"]()


def _run_parallel_shards(subject, **kwargs):
    kwargs.setdefault("execution_lineage", dict(_EXECUTION_LINEAGE))
    kwargs.setdefault("runner_capability", dict(_RUNNER_CAPABILITY))
    return subject.run_parallel_shards(**kwargs)


def test_shard_artifact_writer_rejects_dangling_symlink(tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    output_root = tmp_path / "runtime"
    output_root.mkdir()
    target = output_root / "shard-0.json"
    target.symlink_to(tmp_path / "missing.json")

    with pytest.raises(ValueError, match="new and non-symlink"):
        subject._write_artifact(output_root, 0, {"status": "PASS"})

    assert target.is_symlink()


def test_runner_requires_fresh_output_root(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    output_root = _output_root(tmp_path)
    output_root.mkdir()
    marker = output_root / "keep.txt"
    marker.write_text("pre-existing evidence", encoding="utf-8")

    def passing_run(*, shard_index, fixture_root, **kwargs):
        _mark_runtime_cleanup(kwargs)
        _ready_and_start(kwargs)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", passing_run)
    with pytest.raises(ValueError, match="fresh|new"):
        _run_parallel_shards(subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=output_root,
        )

    assert marker.read_text(encoding="utf-8") == "pre-existing evidence"


def test_artifact_write_failure_returns_structured_blocked_result(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    def passing_run(*, shard_index, fixture_root, **kwargs):
        _mark_runtime_cleanup(kwargs)
        _ready_and_start(kwargs)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", passing_run)
    original_write = subject._write_artifact
    writes = 0

    def fail_second_write(output_root, index, artifact):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated disk full")
        return original_write(output_root, index, artifact)

    monkeypatch.setattr(subject, "_write_artifact", fail_second_write)
    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "artifact_write_failed"
    assert result["artifactWrite"]["status"] == "BLOCKED"
    assert result["artifactWrite"]["expectedShardArtifacts"] == 4
    assert result["artifactWrite"]["writtenShardArtifacts"] == 1
    assert len(result["shardArtifactPaths"]) == 1
    for field in (
        "aggregate", "coverage", "parity", "speedupMultiple", "speedRatio",
        "comparison", "performance",
    ):
        assert field not in result


def test_timeout_does_not_cleanup_runtime_while_worker_is_active(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    release_worker = threading.Event()
    worker_active = threading.Event()
    cleanup_calls = []
    owner_thread = []

    class OwnedRuntime:
        def __init__(self):
            self._cleanup = None

        def terminate_process_groups(self, *, force=False):
            return None

        def cleanup(self):
            cleanup_calls.append({
                "thread": threading.get_ident(),
                "workerActive": worker_active.is_set(),
            })
            self._cleanup = {
                "status": "PASS",
                "allProcessGroupsTerminated": True,
                "leakedProcesses": [],
            }
            return dict(self._cleanup)

    def blocked_run(*, shard_index, fixture_root, runtime_observer, **kwargs):
        runtime = OwnedRuntime()
        owner_thread.append(threading.get_ident())
        worker_active.set()
        runtime_observer("registered", runtime)
        _ready_and_start(kwargs)
        try:
            release_worker.wait(timeout=10)
            return _passing_shard(shard_index, fixture_root)
        finally:
            worker_active.clear()
            runtime.cleanup()
            runtime_observer("unregistered", runtime)

    monkeypatch.setattr(subject, "run_pytest_shard", blocked_run)
    try:
        result = _run_parallel_shards(subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
            timeout_seconds=1,
        )
        assert result["status"] == "BLOCKED"
        assert cleanup_calls == []
        assert result["cleanup"]["runtimeCleanupConfirmed"] is False
    finally:
        release_worker.set()


class _TestRuntime:
    def __init__(self):
        self._cleanup = {
            "status": "PASS",
            "allProcessGroupsTerminated": True,
            "leakedProcesses": [],
        }


def _mark_runtime_cleanup(kwargs):
    observer = kwargs.get("runtime_observer")
    if observer is None:
        return
    runtime = _TestRuntime()
    observer("registered", runtime)
    observer("unregistered", runtime)


def test_run_parallel_shards_starts_all_child_jobs_before_completion(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    started = []
    completed = []
    release = threading.Event()

    def fake_run_shard(*, shard_index, fixture_root, **kwargs):
        started.append((shard_index, fixture_root))
        _mark_runtime_cleanup(kwargs)
        _ready_and_start(kwargs)
        release.wait(timeout=1)
        assert len(started) == 4
        completed.append(shard_index)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", fake_run_shard)
    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )
    assert result["status"] == "PASS"
    assert len(started) == 4
    assert len(completed) == 4
    assert len({str(item[1]) for item in started}) == 4
    assert result["parallelWallSeconds"] >= 0


def test_execution_clock_excludes_worker_preparation(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    clock = [100.0]
    lock = threading.Lock()
    started = 0
    all_started = threading.Event()

    def fake_run_shard(*, shard_index, fixture_root, readiness_callback, start_callback, **kwargs):
        nonlocal started
        _mark_runtime_cleanup(kwargs)
        with lock:
            clock[0] += 10.0
        readiness_callback()
        with lock:
            clock[0] += 7.0
        start_callback()
        with lock:
            started += 1
            if started == 4:
                all_started.set()
        all_started.wait(timeout=1)
        with lock:
            clock[0] += 1.0
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(subject, "run_pytest_shard", fake_run_shard)
    result = _run_parallel_shards(subject,
        project_root=tmp_path, manifest=_manifest(16), commit_sha=COMMIT,
        source_fingerprint=SOURCE, shard_count=4, output_root=_output_root(tmp_path),
    )
    assert result["status"] == "PASS"
    assert result["parallelWallSeconds"] == 4.0
    assert result["startedAt"] == result["readiness"]["releasedAt"]


def test_runner_blocks_child_without_readiness_callback(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    def collection_timeout(*, shard_index, fixture_root, **kwargs):
        artifact = _passing_shard(shard_index, fixture_root)
        artifact["status"] = "BLOCKED"
        artifact["metadata"] = {
            "cleanup": {"status": "PASS", "allProcessGroupsTerminated": True},
            "failureCode": "pytest_collection_timeout",
        }
        unsigned = {key: value for key, value in artifact.items() if key != "evidenceFingerprint"}
        return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}

    monkeypatch.setattr(subject, "run_pytest_shard", collection_timeout)
    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "shard_failed"
    assert any(item["metadata"]["failureCode"] == "pytest_collection_timeout" for item in result["shards"])


def test_runner_blocks_invalid_shard_count_and_manifest_identity(tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    with pytest.raises(ValueError, match="inside the project root"):
        _run_parallel_shards(subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=tmp_path / "runtime",
        )

    with pytest.raises(ValueError, match="shard count"):
        _run_parallel_shards(subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=3,
            output_root=_output_root(tmp_path),
        )

    with pytest.raises(ValueError, match="manifest identity"):
        _run_parallel_shards(subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha="c" * 40,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
        )


def test_runner_requires_lineage_and_respects_capability_bound(tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    with pytest.raises(ValueError, match="execution lineage is required"):
        subject.run_parallel_shards(
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
            runner_capability=_RUNNER_CAPABILITY,
        )

    with pytest.raises(ValueError, match="runner capability is required"):
        subject.run_parallel_shards(
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
            execution_lineage=_EXECUTION_LINEAGE,
        )

    with pytest.raises(ValueError, match="exceeds runner capability"):
        restricted = {**_RUNNER_CAPABILITY_UNSIGNED, "maxWorkers": 2}
        subject.run_parallel_shards(
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
            execution_lineage=_EXECUTION_LINEAGE,
            runner_capability={
                **restricted,
                "capabilityFingerprint": canonical_fingerprint(restricted),
            },
        )


def test_runner_rejects_forged_live_runtime_lineage(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    monkeypatch.setattr(
        subject,
        "observe_runtime_fingerprints",
        lambda project_root: {
            "runnerFingerprint": "9" * 64,
            "environmentFingerprint": "8" * 64,
        },
    )

    with pytest.raises(ValueError, match="does not match live runtime identity"):
        _run_parallel_shards(
            subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
        )


def test_runner_rejects_timeout_above_bounded_cap(tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    with pytest.raises(ValueError, match="timeout exceeds bounded maximum"):
        _run_parallel_shards(
            subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
            timeout_seconds=subject.MAX_TIMEOUT_SECONDS + 1,
        )


@pytest.mark.parametrize("dangling", [False, True])
def test_output_root_rejects_symlinked_parent_inside_temporary_root(tmp_path, dangling):
    from backend.agents import acceptance_parallel_runner as subject

    actual_parent = tmp_path.parent / f"actual-parent-{tmp_path.name}"
    if not dangling:
        actual_parent.mkdir()
    link_parent = tmp_path.parent / f"linked-parent-{tmp_path.name}"
    link_parent.symlink_to(actual_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="parent.*symlink"):
        subject._validate_output_root(
            tmp_path,
            link_parent / "runtime",
        )

    assert not actual_parent.joinpath("runtime").exists()


def test_runner_capability_receipt_is_bound_to_live_runner_and_capacity(monkeypatch):
    from backend.agents import acceptance_parallel_runner as subject

    monkeypatch.setattr(subject, "_live_worker_capacity", lambda: 4)
    receipt = subject.build_runner_capability_receipt("c" * 64)
    assert receipt["maxWorkers"] == 4
    assert subject._validate_runner_capability(receipt, "c" * 64) == 4
    with pytest.raises(ValueError, match="binding mismatch"):
        subject._validate_runner_capability(receipt, "d" * 64)
    unsigned = {key: receipt[key] for key in ("schemaVersion", "runnerFingerprint", "maxWorkers")}
    forged = {**unsigned, "maxWorkers": 16, "capabilityFingerprint": canonical_fingerprint({**unsigned, "maxWorkers": 16})}
    with pytest.raises(ValueError, match="exceeds live worker capacity"):
        subject._validate_runner_capability(forged, "c" * 64)


def test_child_failure_terminates_remaining_process_groups(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    stop_event = threading.Event()
    termination_calls = []

    class FakeRuntime:
        def __init__(self, index):
            self.index = index
            self._cleanup = None

        def terminate_process_groups(self, *, force=False):
            termination_calls.append((self.index, force))
            stop_event.set()

        def cleanup(self):
            self._cleanup = {
                "status": "PASS",
                "allProcessGroupsTerminated": True,
                "leakedProcesses": [],
            }
            return dict(self._cleanup)

    def fake_run_shard(*, shard_index, fixture_root, runtime_observer, readiness_callback, start_callback, **kwargs):
        runtime = FakeRuntime(shard_index)
        runtime_observer("registered", runtime)
        try:
            readiness_callback()
            start_callback()
            if shard_index == 0:
                artifact = _one_failing_shard(
                    shard_index, fixture_root, readiness_callback=lambda: None,
                )
            else:
                stopped = stop_event.wait(timeout=1)
                artifact = _passing_shard(shard_index, fixture_root)
                if not stopped:
                    artifact["status"] = "BLOCKED"
                    artifact["metadata"] = {
                        "cleanup": {"status": "PASS", "allProcessGroupsTerminated": True},
                        "failureCode": "sibling_not_terminated",
                    }
                    unsigned = {key: value for key, value in artifact.items() if key != "evidenceFingerprint"}
                    artifact["evidenceFingerprint"] = canonical_fingerprint(unsigned)
            return artifact
        finally:
            runtime.cleanup()
            runtime_observer("unregistered", runtime)

    monkeypatch.setattr(subject, "run_pytest_shard", fake_run_shard)
    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )
    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "shard_failed"
    assert result["cleanup"]["allProcessGroupsTerminated"] is True
    assert {index for index, force in termination_calls if force} >= {1, 2, 3}


def test_runner_passes_and_requires_execution_lineage(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    lineage = {
        "runnerFingerprint": "c" * 64,
        "environmentFingerprint": "d" * 64,
        "datasetSnapshotFingerprint": "e" * 64,
    }
    observed = []

    def fake_run_shard(*, shard_index, fixture_root, lineage=None, **kwargs):
        _mark_runtime_cleanup(kwargs)
        observed.append(lineage)
        _ready_and_start(kwargs)
        result = _passing_shard(shard_index, fixture_root)
        result["lineage"] = dict(lineage or {})
        unsigned = {key: value for key, value in result.items() if key != "evidenceFingerprint"}
        result["evidenceFingerprint"] = canonical_fingerprint(unsigned)
        return result

    monkeypatch.setattr(subject, "run_pytest_shard", fake_run_shard)
    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
        execution_lineage=lineage,
    )

    assert result["status"] == "PASS"
    assert observed == [lineage] * 4


def test_runner_blocks_when_runtime_cleanup_confirmation_is_missing(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    def missing_unregistration(*, shard_index, fixture_root, runtime_observer, **kwargs):
        runtime_observer("registered", _TestRuntime())
        _ready_and_start(kwargs)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", missing_unregistration)
    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "isolation_violation"
    assert result["cleanup"]["runtimeCleanupConfirmed"] is False
    assert result["cleanup"]["cleanupConfirmationMissingShards"] == [0, 1, 2, 3]
    assert "aggregate" not in result
    assert "coverage" not in result
    assert "nodeidCount" not in result


def test_duplicate_runtime_registration_fails_closed_and_cleans_up_both_runtimes(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    runtimes = []

    class Runtime:
        def __init__(self):
            self.terminated = False
            self.cleaned = False
            self._cleanup = None

        def terminate_process_groups(self, *, force=False):
            self.terminated = force

        def cleanup(self):
            self.terminated = True
            self.cleaned = True
            self._cleanup = {
                "status": "PASS",
                "allProcessGroupsTerminated": True,
                "leakedProcesses": [],
            }
            return dict(self._cleanup)

    def fake_run_shard(*, shard_index, fixture_root, runtime_observer, **kwargs):
        runtime = Runtime()
        runtimes.append(runtime)
        runtime_observer("registered", runtime)
        if shard_index == 0:
            duplicate = Runtime()
            runtimes.append(duplicate)
            try:
                runtime_observer("registered", duplicate)
            except RuntimeError:
                duplicate.cleanup()
                runtime_observer("unregistered", duplicate)
                return subject._blocked_shard(
                    index=shard_index,
                    shard_count=4,
                    commit_sha=COMMIT,
                    source_fingerprint=SOURCE,
                    manifest_fingerprint=_manifest(16)["manifestFingerprint"],
                    fixture_root=fixture_root,
                    failure_code="runtime_allocation_failed",
                    lineage=_EXECUTION_LINEAGE,
                )
        try:
            _ready_and_start(kwargs)
            return _passing_shard(shard_index, fixture_root)
        finally:
            runtime.cleanup()
            runtime_observer("unregistered", runtime)

    monkeypatch.setattr(subject, "run_pytest_shard", fake_run_shard)
    result = _run_parallel_shards(
        subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )

    assert result["status"] == "BLOCKED"
    shard_zero = next(shard for shard in result["shards"] if shard["shardIndex"] == 0)
    assert shard_zero["metadata"]["failureCode"] == "process_registration_failure"
    assert shard_zero["metadata"]["cleanup"]["allProcessGroupsTerminated"] is True
    assert all(runtime.terminated and runtime.cleaned for runtime in runtimes)
    assert result["cleanup"]["activeRuntimeCount"] == 0
    assert result["cleanup"]["runtimeCleanupConfirmed"] is True


def test_daemon_workers_are_not_registered_for_interpreter_exit_join(monkeypatch):
    from concurrent.futures.thread import _threads_queues
    from backend.agents import acceptance_parallel_runner as subject

    executor = subject._DaemonThreadPoolExecutor(max_workers=1)
    try:
        assert executor.submit(lambda: None).result(timeout=1) is None
        assert all(thread not in _threads_queues for thread in executor._threads)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def test_runner_blocks_child_missing_execution_lineage(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    lineage = {
        "runnerFingerprint": "c" * 64,
        "environmentFingerprint": "d" * 64,
        "datasetSnapshotFingerprint": "e" * 64,
    }
    def missing_lineage_run(*, shard_index, fixture_root, **kwargs):
        _mark_runtime_cleanup(kwargs)
        _ready_and_start(kwargs)
        result = _passing_shard(shard_index, fixture_root)
        result.pop("lineage", None)
        unsigned = {key: value for key, value in result.items() if key != "evidenceFingerprint"}
        result["evidenceFingerprint"] = canonical_fingerprint(unsigned)
        return result

    monkeypatch.setattr(subject, "run_pytest_shard", missing_lineage_run)

    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
        execution_lineage=lineage,
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "shard_lineage_mismatch"


def test_runner_fail_closes_and_cleans_up_after_base_exception(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    class Runtime:
        def __init__(self):
            self.terminated = False
            self.cleaned = False
            self._cleanup = None

        def terminate_process_groups(self, *, force=False):
            self.terminated = force

        def cleanup(self):
            self.cleaned = True
            self._cleanup = {
                "status": "PASS",
                "allProcessGroupsTerminated": self.terminated,
                "leakedProcesses": [],
            }
            return self._cleanup

    runtimes = []

    def interrupted_run(*, shard_index, runtime_observer, **kwargs):
        runtime = Runtime()
        runtimes.append(runtime)
        runtime_observer("registered", runtime)
        raise SystemExit("simulated child interruption")

    monkeypatch.setattr(subject, "run_pytest_shard", interrupted_run)
    result = _run_parallel_shards(subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )

    assert result["status"] == "BLOCKED"
    assert all(runtime.terminated is True and runtime.cleaned is True for runtime in runtimes)
    assert result["cleanup"]["allProcessGroupsTerminated"] == all(
        item["metadata"]["cleanup"]["allProcessGroupsTerminated"]
        for item in result["shards"]
    )


def test_runner_emits_bounded_timeout_artifacts(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    release = threading.Event()

    def hanging_run_shard(*, shard_index, fixture_root, **kwargs):
        _mark_runtime_cleanup(kwargs)
        _ready_and_start(kwargs)
        release.wait(timeout=10)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", hanging_run_shard)
    started = time.perf_counter()
    try:
        result = _run_parallel_shards(subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
            timeout_seconds=1,
        )
        elapsed = time.perf_counter() - started
        assert 1.0 <= elapsed < 3.0
        assert result["status"] == "BLOCKED"
        assert result["failureCode"] == "controller_timeout"
        assert len(result["shards"]) == 4
        assert all(item["metadata"]["failureCode"] == "controller_timeout" for item in result["shards"])
        # The fake worker is still alive after the bounded join window. A
        # timeout must fail closed instead of claiming worker termination.
        assert result["cleanup"]["allProcessGroupsTerminated"] is False
    finally:
        release.set()


def test_controller_timeout_preserves_a_completed_child_failure_artifact(monkeypatch, tmp_path):
    from concurrent.futures import ALL_COMPLETED
    from backend.agents import acceptance_parallel_runner as subject

    release = threading.Event()
    real_wait = subject.wait
    wait_calls = 0

    def late_failure(*, shard_index, fixture_root, **kwargs):
        _mark_runtime_cleanup(kwargs)
        _ready_and_start(kwargs)
        release.wait(timeout=5)
        artifact = _passing_shard(shard_index, fixture_root)
        if shard_index == 0:
            artifact["status"] = "BLOCKED"
            artifact["metadata"] = {
                "failureCode": "late_child_failure",
                "cleanup": {
                    "status": "BLOCKED",
                    "allProcessGroupsTerminated": False,
                    "failureCode": "late_child_cleanup_failure",
                },
            }
            unsigned = {key: value for key, value in artifact.items() if key != "evidenceFingerprint"}
            artifact["evidenceFingerprint"] = canonical_fingerprint(unsigned)
        return artifact

    def release_workers_after_timeout(futures, timeout=None, return_when=ALL_COMPLETED):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 2:
            release.set()
        return real_wait(futures, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(subject, "wait", release_workers_after_timeout)
    monkeypatch.setattr(subject, "run_pytest_shard", late_failure)
    result = _run_parallel_shards(
        subject,
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
        timeout_seconds=1,
    )

    assert result["failureCode"] == "controller_timeout"
    late = next(item for item in result["shards"] if item["shardIndex"] == 0)
    assert late["metadata"]["failureCode"] == "late_child_failure"
    assert late["metadata"]["cleanup"] == {
        "status": "BLOCKED",
        "allProcessGroupsTerminated": False,
        "failureCode": "late_child_cleanup_failure",
    }


def test_child_failure_uses_bounded_cleanup_window(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    release = threading.Event()

    def failing_or_hanging(*, shard_index, fixture_root, **kwargs):
        _ready_and_start(kwargs)
        if shard_index == 0:
            return _one_failing_shard(shard_index, fixture_root)
        release.wait(timeout=30)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", failing_or_hanging)
    started = time.perf_counter()
    try:
        result = _run_parallel_shards(subject,
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
            timeout_seconds=120,
        )
        elapsed = time.perf_counter() - started

        assert result["status"] == "BLOCKED"
        assert result["failureCode"] in {"shard_failed", "controller_timeout"}
        assert result["cleanup"]["allProcessGroupsTerminated"] is (
            result["failureCode"] == "shard_failed"
        )
        assert elapsed < 8
    finally:
        release.set()


def test_real_child_processes_complete_ready_start_and_cleanup_contract(tmp_path, monkeypatch):
    from backend.agents import acceptance_parallel_runner as subject
    from backend.agents import acceptance_shard_runtime

    project_root = Path(__file__).resolve().parents[1]
    held_sockets = []
    for _ in range(6):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reservation.bind(("127.0.0.1", 0))
        reservation.listen(1)
        held_sockets.append(reservation)
    available_ports = [reservation.getsockname()[1] for reservation in held_sockets]
    port_sets = [
        {
            name: available_ports[shard * 3 + offset]
            for offset, name in enumerate(("streamlit", "mcp", "health"))
        }
        for shard in range(2)
    ]
    monkeypatch.setattr(
        acceptance_shard_runtime,
        "_ports_for",
        lambda run_id, shard_index: dict(port_sets[shard_index]),
    )

    def reserve_held_ports(ports, allocation_id):
        return (
            {name: held_sockets[available_ports.index(port)] for name, port in ports.items()},
            {},
            {},
        )

    monkeypatch.setattr(acceptance_shard_runtime, "_reserve_ports", reserve_held_ports)
    real_port_readiness = subject._port_readiness
    readiness_probe_calls = []

    def child_owned_port_readiness(ports):
        parent_reservations = [held_sockets[available_ports.index(port)] for port in ports.values()]
        assert all(reservation.fileno() == -1 for reservation in parent_reservations), (
            "parent reservation handles must be closed before child readiness is probed"
        )
        readiness_probe_calls.append(tuple(ports.values()))
        return real_port_readiness(ports)

    monkeypatch.setattr(subject, "_port_readiness", child_owned_port_readiness)
    nodeids = sorted([
        "tests/test_full_pytest_shard.py::test_select_shard_nodeids_is_stable_and_covers_every_node_once",
        "tests/test_full_pytest_shard.py::test_run_pytest_command_uses_process_port_handoff_when_available",
    ])
    manifest = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
    }
    manifest["manifestFingerprint"] = _manifest_fingerprint(COMMIT, SOURCE, nodeids)
    before = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project_root,
        text=True,
    )

    try:
        result = _run_parallel_shards(subject,
            project_root=project_root,
            manifest=manifest,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=2,
            output_root=tmp_path / "parallel-runtime",
            timeout_seconds=120,
        )

        after = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=project_root,
            text=True,
        )
        assert result["status"] == "PASS"
        assert result["readiness"]["status"] == "PASS"
        assert result["readiness"]["expectedShardCount"] == 2
        assert result["readiness"]["readyShardCount"] == 2
        assert len(readiness_probe_calls) == 2
        assert result["startedAt"] <= result["readiness"]["releasedAt"]
        assert result["parallelWallSeconds"] > 0
        assert all(item["status"] == "PASS" for item in result["shards"])
        assert all(item["metadata"]["cleanup"]["status"] == "PASS" for item in result["shards"])
        assert before == after
    finally:
        for reservation in held_sockets:
            try:
                reservation.close()
            except OSError:
                pass


_TIMEOUT_CHILD_ENV = "NBS_ACCEPTANCE_TEST_TIMEOUT_CHILD"


def _run_timeout_child_fixture():
    if os.environ.get(_TIMEOUT_CHILD_ENV) == "1":
        time.sleep(20)


def test_parallel_timeout_child_a():
    _run_timeout_child_fixture()


def test_parallel_timeout_child_b():
    _run_timeout_child_fixture()


def test_real_child_timeout_is_terminated_and_reported_blocked(tmp_path, monkeypatch):
    from backend.agents import acceptance_parallel_runner as subject

    project_root = Path(__file__).resolve().parents[1]
    nodeids = [
        "tests/test_acceptance_parallel_runner.py::test_parallel_timeout_child_a",
        "tests/test_acceptance_parallel_runner.py::test_parallel_timeout_child_b",
    ]
    manifest = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
        "manifestFingerprint": _manifest_fingerprint(COMMIT, SOURCE, nodeids),
    }

    with monkeypatch.context() as timeout_environment:
        timeout_environment.setenv(_TIMEOUT_CHILD_ENV, "1")
        result = _run_parallel_shards(subject,
            project_root=project_root,
            manifest=manifest,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=2,
            output_root=tmp_path / "timeout-runtime",
            timeout_seconds=5,
        )

        assert result["status"] == "BLOCKED"
        assert result["failureCode"] in {"shard_failed", "controller_timeout"}
        assert result["cleanup"]["allProcessGroupsTerminated"] is True, {
            "cleanup": result["cleanup"],
            "status": result["status"],
            "failureCode": result["failureCode"],
            "shards": [
                {"status": item.get("status"), "metadata": item.get("metadata")}
                for item in result["shards"]
            ],
        }
        assert any(
            item["metadata"]["failureCode"] in {"timeout", "controller_timeout"}
            for item in result["shards"]
        )
        assert all(
            item["metadata"]["cleanup"]["status"] == "PASS"
            for item in result["shards"]
        )
