from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from scripts.pytest_manifest import _manifest_fingerprint


COMMIT = "a" * 40
SOURCE = "b" * 64


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
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _output_root(tmp_path):
    return tmp_path.parent / f"{tmp_path.name}-runtime"


def _one_failing_shard(index: int, fixture_root, **kwargs) -> dict[str, object]:
    result = _passing_shard(index, fixture_root)
    result["status"] = "BLOCKED"
    result["metadata"] = {
        "cleanup": {"status": "PASS", "allProcessGroupsTerminated": True},
        "failureCode": "pytest_failed",
    }
    unsigned = {key: value for key, value in result.items() if key != "evidenceFingerprint"}
    result["evidenceFingerprint"] = canonical_fingerprint(unsigned)
    return result


def test_run_parallel_shards_starts_all_child_jobs_before_completion(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    started = []
    release = threading.Event()

    def fake_run_shard(*, shard_index, fixture_root, **kwargs):
        started.append((shard_index, fixture_root))
        release.wait(timeout=1)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", fake_run_shard)
    result = subject.run_parallel_shards(
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )
    assert result["status"] == "PASS"
    assert len(started) == 4
    assert len({str(item[1]) for item in started}) == 4
    assert result["parallelWallSeconds"] >= 0


def test_runner_blocks_invalid_shard_count_and_manifest_identity(tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    with pytest.raises(ValueError, match="inside the project root"):
        subject.run_parallel_shards(
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=tmp_path / "runtime",
        )

    with pytest.raises(ValueError, match="shard count"):
        subject.run_parallel_shards(
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=3,
            output_root=_output_root(tmp_path),
        )

    with pytest.raises(ValueError, match="manifest identity"):
        subject.run_parallel_shards(
            project_root=tmp_path,
            manifest=_manifest(16),
            commit_sha="c" * 40,
            source_fingerprint=SOURCE,
            shard_count=4,
            output_root=_output_root(tmp_path),
        )


def test_child_failure_terminates_remaining_process_groups(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    monkeypatch.setattr(subject, "run_pytest_shard", _one_failing_shard)
    result = subject.run_parallel_shards(
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


def test_runner_passes_and_requires_execution_lineage(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    lineage = {
        "runnerFingerprint": "c" * 64,
        "environmentFingerprint": "d" * 64,
        "datasetSnapshotFingerprint": "e" * 64,
    }
    observed = []

    def fake_run_shard(*, shard_index, fixture_root, lineage=None, **kwargs):
        observed.append(lineage)
        result = _passing_shard(shard_index, fixture_root)
        result["lineage"] = dict(lineage or {})
        unsigned = {key: value for key, value in result.items() if key != "evidenceFingerprint"}
        result["evidenceFingerprint"] = canonical_fingerprint(unsigned)
        return result

    monkeypatch.setattr(subject, "run_pytest_shard", fake_run_shard)
    result = subject.run_parallel_shards(
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


def test_runner_blocks_child_missing_execution_lineage(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    lineage = {
        "runnerFingerprint": "c" * 64,
        "environmentFingerprint": "d" * 64,
        "datasetSnapshotFingerprint": "e" * 64,
    }
    monkeypatch.setattr(subject, "run_pytest_shard", lambda *, shard_index, fixture_root, **kwargs: _passing_shard(shard_index, fixture_root))

    result = subject.run_parallel_shards(
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
    result = subject.run_parallel_shards(
        project_root=tmp_path,
        manifest=_manifest(16),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        shard_count=4,
        output_root=_output_root(tmp_path),
    )

    assert result["status"] == "BLOCKED"
    assert all(runtime.terminated is True and runtime.cleaned is True for runtime in runtimes)
    assert result["cleanup"]["allProcessGroupsTerminated"] is True


def test_runner_emits_bounded_timeout_artifacts(monkeypatch, tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    release = threading.Event()

    def hanging_run_shard(*, shard_index, fixture_root, **kwargs):
        release.wait(timeout=2)
        return _passing_shard(shard_index, fixture_root)

    monkeypatch.setattr(subject, "run_pytest_shard", hanging_run_shard)
    started = time.perf_counter()
    try:
        result = subject.run_parallel_shards(
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
        assert result["cleanup"]["allProcessGroupsTerminated"] is True
    finally:
        release.set()


def test_real_child_processes_complete_ready_start_and_cleanup_contract(tmp_path, monkeypatch):
    from backend.agents import acceptance_parallel_runner as subject
    from backend.agents import acceptance_shard_runtime

    project_root = Path(__file__).resolve().parents[1]
    available_ports = []
    for _ in range(6):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            available_ports.append(probe.getsockname()[1])
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

    result = subject.run_parallel_shards(
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
    assert result["readiness"] == {
        "status": "PASS",
        "expectedShardCount": 2,
        "readyShardCount": 2,
        "releasedAt": result["startedAt"],
    }
    assert result["parallelWallSeconds"] > 0
    assert all(item["status"] == "PASS" for item in result["shards"])
    assert all(item["metadata"]["cleanup"]["status"] == "PASS" for item in result["shards"])
    assert before == after


def test_real_child_timeout_is_terminated_and_reported_blocked(tmp_path):
    from backend.agents import acceptance_parallel_runner as subject

    project_root = Path(__file__).resolve().parents[1]
    slow_test = project_root / "tests" / f"test_timeout_child_{os.getpid()}.py"
    slow_test.write_text(
        "import time\n\n"
        "def test_slow_child():\n"
        "    time.sleep(5)\n",
        encoding="utf-8",
    )
    nodeids = [f"tests/{slow_test.name}::test_slow_child"]
    manifest = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
        "manifestFingerprint": _manifest_fingerprint(COMMIT, SOURCE, nodeids),
    }

    try:
        result = subject.run_parallel_shards(
            project_root=project_root,
            manifest=manifest,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            shard_count=2,
            output_root=tmp_path / "timeout-runtime",
            timeout_seconds=1,
        )

        assert result["status"] == "BLOCKED"
        assert result["failureCode"] in {"shard_failed", "controller_timeout"}
        assert result["cleanup"]["allProcessGroupsTerminated"] is True
        assert any(
            item["metadata"]["failureCode"] in {"timeout", "controller_timeout"}
            for item in result["shards"]
        )
        assert all(
            item["metadata"]["cleanup"]["status"] == "PASS"
            for item in result["shards"]
        )
    finally:
        slow_test.unlink(missing_ok=True)
