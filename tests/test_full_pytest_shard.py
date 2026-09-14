import os
import signal
import subprocess

import pytest

from scripts.full_pytest_shard import _run_pytest_command, run_pytest_shard, select_shard_nodeids
from backend.agents.evidence_models import canonical_fingerprint


COMMIT = "a" * 40
SOURCE = "b" * 64


def _project_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return project


def _manifest(nodeids):
    payload = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": sorted(nodeids),
    }
    payload["manifestFingerprint"] = canonical_fingerprint({
        "schemaVersion": payload["schemaVersion"],
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": payload["nodeids"],
    })
    return payload


def test_select_shard_nodeids_is_stable_and_covers_every_node_once():
    nodeids = ["tests/test_c.py::test_3", "tests/test_a.py::test_1", "tests/test_b.py::test_2"]

    shards = [select_shard_nodeids(nodeids, index, 2) for index in range(2)]

    assert sorted(item for shard in shards for item in shard) == sorted(nodeids)
    assert set(shards[0]).isdisjoint(shards[1])


def test_run_pytest_shard_requires_exact_collection_before_pass(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])
    captured_env = {}

    def run(argv, **kwargs):
        captured_env.update(kwargs["env"])
        if "--collect-only" in argv:
            return subprocess.CompletedProcess(argv, 0, "tests/test_a.py::test_one\n", "")
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr("scripts.full_pytest_shard._run_pytest_command", run)
    result = run_pytest_shard(
        _project_root(tmp_path),
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
        port_readiness_probe=lambda ports: True,
    )

    assert result["schemaVersion"] == "full-pytest-shard-v1"
    assert result["status"] == "PASS"
    assert result["authority"] == "prototype"
    assert result["formalReleaseEnabled"] is False
    assert result["executedNodeids"] == manifest["nodeids"]
    assert result["metadata"]["cleanup"]["status"] == "PASS"
    assert captured_env["NBS_ANALYTICS_DB_FILE"].startswith(str(tmp_path / "shard-0"))


def test_run_pytest_shard_blocks_collection_mismatch(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])

    monkeypatch.setattr(
        "scripts.full_pytest_shard._run_pytest_command",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "tests/test_other.py::test_two\n", ""),
    )
    result = run_pytest_shard(
        _project_root(tmp_path),
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
        port_readiness_probe=lambda ports: True,
    )

    assert result["status"] == "FAIL"
    assert result["metadata"]["failureCode"] == "collection_mismatch"


def test_run_pytest_shard_rejects_existing_fixture_root(tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])
    fixture_root = tmp_path / "already-used"
    fixture_root.mkdir()

    with pytest.raises(ValueError, match="unique"):
        run_pytest_shard(
            _project_root(tmp_path),
            manifest,
            shard_index=0,
            shard_count=1,
            fixture_root=fixture_root,
        )


def test_run_pytest_shard_records_distinct_finish_timestamp(monkeypatch, tmp_path):
    manifest = _manifest([])
    timestamps = iter(["2026-09-11T08:00:00Z", "2026-09-11T08:00:01Z"])
    monkeypatch.setattr("scripts.full_pytest_shard._timestamp", lambda: next(timestamps))

    result = run_pytest_shard(
        tmp_path,
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
    )

    assert result["startedAt"] == "2026-09-11T08:00:00Z"
    assert result["finishedAt"] == "2026-09-11T08:00:01Z"


def test_run_pytest_shard_uses_allocator_when_fixture_root_is_omitted(monkeypatch, tmp_path):
    manifest = _manifest([])
    calls = []

    class FakeRuntime:
        root = tmp_path / "runtime"
        db_path = root / "shard.db"
        cache_dir = root / "cache"
        coordination_db_path = root / "coordination.db"

        def environment(self):
            calls.append("environment")
            return {
                "NBS_ANALYTICS_DB_FILE": str(self.db_path),
                "NBS_ANALYTICS_CACHE_DIR": str(self.cache_dir),
                "NBS_ANALYTICS_COORDINATION_DB": str(self.coordination_db_path),
                "NBS_ANALYTICS_PROCESS_PROFILE": "acceptance-shard-test",
            }

        def validate_isolation(self):
            calls.append("validate")

        def cleanup(self):
            calls.append("cleanup")
            return {"status": "PASS", "failureCode": None, "leakedFiles": [], "leakedLocks": [], "leakedProcesses": []}

    monkeypatch.setattr(
        "scripts.full_pytest_shard.allocate_shard_runtime",
        lambda **kwargs: FakeRuntime(),
    )
    result = run_pytest_shard(
        tmp_path,
        manifest,
        shard_index=0,
        shard_count=1,
        run_id="run-1",
    )

    assert result["status"] == "PASS"
    assert calls == ["validate", "cleanup"]




def test_run_pytest_shard_returns_bounded_blocker_when_runtime_allocation_fails(monkeypatch, tmp_path):
    manifest = _manifest([])
    monkeypatch.setattr(
        "scripts.full_pytest_shard.allocate_shard_runtime",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("port allocation failed")),
    )

    result = run_pytest_shard(
        tmp_path,
        manifest,
        shard_index=0,
        shard_count=1,
        run_id="run-1",
    )

    assert result["status"] == "BLOCKED"
    assert result["metadata"]["failureCode"] == "runtime_allocation_failed"
    assert result["metadata"]["cleanup"]["status"] == "PASS"


def test_run_pytest_shard_cleans_runtime_when_validation_fails_after_allocation(monkeypatch, tmp_path):
    manifest = _manifest([])
    calls = []

    class FakeRuntime:
        root = tmp_path / "runtime"
        db_path = root / "shard.db"
        cache_dir = root / "cache"
        coordination_db_path = root / "coordination.db"

        def validate_isolation(self):
            raise ValueError("runtime isolation validation failed")

        def cleanup(self):
            calls.append("cleanup")
            return {"status": "PASS", "failureCode": None, "leakedFiles": [], "leakedLocks": [], "leakedProcesses": []}

    monkeypatch.setattr("scripts.full_pytest_shard.allocate_shard_runtime", lambda **kwargs: FakeRuntime())

    result = run_pytest_shard(tmp_path, manifest, shard_index=0, shard_count=1, run_id="run-1")

    assert result["status"] == "BLOCKED"
    assert result["metadata"]["failureCode"] == "isolation_violation"
    assert calls == ["cleanup"]


def test_run_pytest_shard_returns_bounded_artifact_on_unexpected_error(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])
    calls = []

    class FakeRuntime:
        root = tmp_path / "runtime"
        db_path = root / "shard.db"
        cache_dir = root / "cache"
        coordination_db_path = root / "coordination.db"

        def environment(self):
            return {}

        def validate_isolation(self):
            return None

        def handoff_ports_with_readiness(self, probe):
            return probe({})

        def cleanup(self):
            calls.append("cleanup")
            return {"status": "PASS", "failureCode": None, "leakedFiles": [], "leakedLocks": [], "leakedProcesses": []}

    monkeypatch.setattr("scripts.full_pytest_shard.allocate_shard_runtime", lambda **kwargs: FakeRuntime())
    monkeypatch.setattr(
        "scripts.full_pytest_shard._run_pytest_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("unexpected runner state")),
    )

    result = run_pytest_shard(
        _project_root(tmp_path), manifest, shard_index=0, shard_count=1,
        run_id="run-1", port_readiness_probe=lambda ports: True,
    )

    assert result["status"] == "BLOCKED"
    assert result["metadata"]["failureCode"] == "runner_unexpected_error"
    assert result["metadata"]["stderrTail"] == "unexpected runner state"
    assert calls == ["cleanup"]


def test_run_pytest_command_reaps_process_when_communicate_is_interrupted(monkeypatch, tmp_path):
    events = []

    class FakeProcess:
        pid = 456

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            if len(events) == 1:
                raise KeyboardInterrupt
            return "reaped", ""

    class FakeRuntime:
        def register_process_group(self, process_id):
            assert process_id == 456

        def handoff_ports(self):
            return None

        def terminate_process_groups(self, *, force=False):
            events.append(("terminate", force))

    monkeypatch.setattr("scripts.full_pytest_shard.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    with pytest.raises(KeyboardInterrupt):
        _run_pytest_command(
            ["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime()
        )

    assert events == [("communicate", 1), ("terminate", True), ("communicate", 1)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group registration regression")
def test_run_pytest_command_cleans_process_when_registration_fails(monkeypatch, tmp_path):
    events = []

    class FakeProcess:
        pid = 789

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            return "", ""

        def kill(self):
            events.append("kill")

    class FakeRuntime:
        def register_process_group(self, process_id):
            assert process_id == 789
            raise ValueError("registration unavailable")

        def handoff_ports(self):
            return None

    monkeypatch.setattr("scripts.full_pytest_shard.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(
        "scripts.full_pytest_shard.os.killpg",
        lambda process_id, sig: events.append(("killpg", process_id, sig))
    )

    with pytest.raises(RuntimeError, match="registration"):
        _run_pytest_command(["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime())

    assert events == [("killpg", 789, signal.SIGKILL), ("communicate", 1)]


def test_run_pytest_shard_cleans_runtime_when_interrupted(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])
    calls = []

    class FakeRuntime:
        root = tmp_path / "runtime"
        db_path = root / "shard.db"
        cache_dir = root / "cache"
        coordination_db_path = root / "coordination.db"

        def environment(self):
            return {}

        def validate_isolation(self):
            return None

        def handoff_ports_with_readiness(self, probe):
            return probe({})

        def terminate_process_groups(self, *, force=False):
            calls.append(("terminate", force))

        def cleanup(self):
            calls.append("cleanup")
            return {"status": "PASS", "failureCode": None, "leakedFiles": [], "leakedLocks": [], "leakedProcesses": []}

    monkeypatch.setattr("scripts.full_pytest_shard.allocate_shard_runtime", lambda **kwargs: FakeRuntime())
    monkeypatch.setattr(
        "scripts.full_pytest_shard._run_pytest_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    with pytest.raises(KeyboardInterrupt):
        run_pytest_shard(
            _project_root(tmp_path), manifest, shard_index=0, shard_count=1,
            run_id="run-1", port_readiness_probe=lambda ports: True,
        )

    assert calls == [("terminate", True), "cleanup"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX timeout escalation regression")
def test_run_pytest_command_uses_bounded_grace_and_force_cleanup(monkeypatch, tmp_path):
    timeouts = []
    terminate_calls = []

    class FakeProcess:
        pid = 123
        calls = 0

        def communicate(self, timeout=None):
            timeouts.append(timeout)
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["pytest"], timeout, output="partial", stderr="")
            if self.calls == 2:
                raise subprocess.TimeoutExpired(["pytest"], timeout, output="still-open", stderr="")
            return "forced", ""

        def kill(self):
            raise AssertionError("force process kill should be a last-resort fallback")

    class FakeRuntime:
        def register_process_group(self, process_id):
            assert process_id == 123

        def handoff_ports(self):
            return None

        def terminate_process_groups(self, *, force=False):
            terminate_calls.append(force)

    monkeypatch.setattr("scripts.full_pytest_shard.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    with pytest.raises(subprocess.TimeoutExpired):
        _run_pytest_command(
            ["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime()
        )

    assert timeouts == [1, 1, 1]
    assert terminate_calls == [False, True]
