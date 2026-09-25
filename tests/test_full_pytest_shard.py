import os
import json
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.full_pytest_shard import _run_pytest_command, run_pytest_shard, select_shard_nodeids
from backend.agents.evidence_models import canonical_fingerprint


COMMIT = "a" * 40
SOURCE = "b" * 64


def _close_raw_fd(descriptor):
    try:
        os.close(descriptor)
    except OSError:
        pass


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


def _write_execution_evidence(env, *, collected, started):
    report_path = Path(env["NBS_ACCEPTANCE_EXECUTION_EVIDENCE"])
    unsigned = {
        "schemaVersion": "pytest-shard-execution-v1",
        "collectedNodeids": list(collected),
        "startedNodeids": list(started),
    }
    report_path.write_text(
        json.dumps({**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}),
        encoding="utf-8",
    )


def _shard_cli_args(project_root, manifest_path, output_path):
    return [
        "--project-root", str(project_root),
        "--manifest", str(manifest_path),
        "--shard-index", "0",
        "--shard-count", "1",
        "--output", str(output_path),
    ]


def test_shard_cli_rejects_existing_output_before_running_shard(monkeypatch, tmp_path):
    from scripts import full_pytest_shard as subject

    project_root = _project_root(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest([])), encoding="utf-8")
    output_path = tmp_path / "existing-shard.json"
    output_path.write_text("keep existing evidence", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        subject, "run_pytest_shard",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "PASS"},
    )

    exit_code = subject.main(_shard_cli_args(project_root, manifest_path, output_path))

    assert exit_code == 2
    assert calls == []
    assert output_path.read_text(encoding="utf-8") == "keep existing evidence"


def test_shard_cli_rejects_dangling_symlink_before_running_shard(monkeypatch, tmp_path):
    from scripts import full_pytest_shard as subject

    project_root = _project_root(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest([])), encoding="utf-8")
    output_path = tmp_path / "shard-link.json"
    target_path = tmp_path / "missing-shard.json"
    output_path.symlink_to(target_path)
    calls = []
    monkeypatch.setattr(
        subject, "run_pytest_shard",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "PASS"},
    )

    exit_code = subject.main(_shard_cli_args(project_root, manifest_path, output_path))

    assert exit_code == 2
    assert calls == []
    assert output_path.is_symlink()
    assert not target_path.exists()


def test_shard_cli_writes_a_new_temporary_output(monkeypatch, tmp_path):
    from scripts import full_pytest_shard as subject

    project_root = _project_root(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest([])), encoding="utf-8")
    output_path = tmp_path / "artifacts" / "shard.json"
    output_path.parent.mkdir()
    monkeypatch.setattr(
        subject, "run_pytest_shard",
        lambda *args, **kwargs: {"status": "PASS", "evidence": "source-bound"},
    )

    exit_code = subject.main(_shard_cli_args(project_root, manifest_path, output_path))

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "status": "PASS", "evidence": "source-bound"
    }


def test_shard_cli_does_not_overwrite_path_created_after_validation(monkeypatch, tmp_path):
    from scripts import full_pytest_shard as subject

    project_root = _project_root(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest([])), encoding="utf-8")
    output_path = tmp_path / "raced-shard.json"

    def create_competing_output(*args, **kwargs):
        output_path.write_text("other writer evidence", encoding="utf-8")
        return {"status": "PASS"}

    monkeypatch.setattr(subject, "run_pytest_shard", create_competing_output)

    exit_code = subject.main(_shard_cli_args(project_root, manifest_path, output_path))

    assert exit_code == 2
    assert output_path.read_text(encoding="utf-8") == "other writer evidence"


def test_select_shard_nodeids_is_stable_and_covers_every_node_once():
    nodeids = ["tests/test_c.py::test_3", "tests/test_a.py::test_1", "tests/test_b.py::test_2"]

    shards = [select_shard_nodeids(nodeids, index, 2) for index in range(2)]

    assert sorted(item for shard in shards for item in shard) == sorted(nodeids)
    assert set(shards[0]).isdisjoint(shards[1])


def test_run_pytest_shard_executes_manifest_nodeids_once_under_port_handoff(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])
    captured_env = {}
    commands = []
    handoffs = []
    callbacks = []

    def run(argv, **kwargs):
        commands.append(argv)
        captured_env.update(kwargs["env"])
        handoffs.append(kwargs)
        _write_execution_evidence(
            kwargs["env"], collected=manifest["nodeids"], started=manifest["nodeids"]
        )
        kwargs["readiness_callback"]()
        kwargs["start_callback"]()
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr("scripts.full_pytest_shard._run_pytest_command", run)
    result = run_pytest_shard(
        _project_root(tmp_path),
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
        port_readiness_probe=lambda ports: True,
        readiness_callback=lambda: callbacks.append("ready"),
        start_callback=lambda: callbacks.append("start"),
    )

    assert result["schemaVersion"] == "full-pytest-shard-v1"
    assert result["status"] == "PASS", result["metadata"]
    assert result["authority"] == "prototype"
    assert result["formalReleaseEnabled"] is False
    assert result["executedNodeids"] == manifest["nodeids"]
    assert result["metadata"]["cleanup"]["status"] == "PASS"
    assert captured_env["NBS_ANALYTICS_DB_FILE"].startswith(str(tmp_path / "shard-0"))
    assert len(commands) == 1
    assert "--collect-only" not in commands[0]
    assert commands[0][-2:] == ["--", manifest["nodeids"][0]]
    assert len(handoffs) == 1
    assert handoffs[0]["readiness_probe"] is not None
    assert handoffs[0]["readiness_callback"] is not None
    assert handoffs[0]["start_callback"] is not None
    assert callbacks == ["ready", "start"]


def test_run_pytest_shard_fails_when_manifest_nodeid_is_not_found(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])

    def missing_nodeid(argv, **kwargs):
        _write_execution_evidence(kwargs["env"], collected=[], started=[])
        return subprocess.CompletedProcess(
            argv, 4, "", "ERROR: not found: tests/test_a.py::test_one\n"
        )

    monkeypatch.setattr(
        "scripts.full_pytest_shard._run_pytest_command",
        missing_nodeid,
    )
    result = run_pytest_shard(
        _project_root(tmp_path),
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
        port_readiness_probe=lambda ports: True,
    )

    assert result["metadata"]["failureCode"] == "collection_mismatch"


def test_run_pytest_shard_does_not_claim_nodeids_that_child_did_not_start(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])

    def child_omits_test(argv, **kwargs):
        _write_execution_evidence(
            kwargs["env"], collected=manifest["nodeids"], started=[]
        )
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr("scripts.full_pytest_shard._run_pytest_command", child_omits_test)
    result = run_pytest_shard(
        _project_root(tmp_path), manifest, shard_index=0, shard_count=1,
        fixture_root=tmp_path / "shard-0", port_readiness_probe=lambda ports: True,
    )

    assert result["status"] == "FAIL"
    assert result["metadata"]["failureCode"] == "collection_mismatch"
    assert result["executedNodeids"] == []


@pytest.mark.skipif(os.name == "nt", reason="reserved-fd child handoff is POSIX-only")
def test_real_child_reports_exact_execution_after_reserved_fd_handoff(monkeypatch, tmp_path):
    from scripts.full_pytest_shard import PROJECT_ROOT

    project_root = _project_root(tmp_path)
    tests_root = project_root / "tests"
    tests_root.mkdir()
    (project_root / "conftest.py").write_text(
        "def pytest_addoption(parser):\n"
        "    parser.addoption('--sandbox-preflight', choices=('required',))\n",
        encoding="utf-8",
    )
    (tests_root / "test_child_execution.py").write_text(
        "import os\n"
        "import sys\n"
        "from scripts import full_pytest_shard\n"
        "def test_child_first():\n"
        "    assert os.environ['NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL'] == 'reserved-fd-v1'\n"
        "    assert full_pytest_shard._ACTIVATED_PORTS\n"
        "    assert full_pytest_shard is sys.modules['__main__']\n"
        "def test_child_second():\n"
        "    assert os.environ['NBS_ACCEPTANCE_EXECUTION_EVIDENCE']\n",
        encoding="utf-8",
    )
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(part for part in (str(PROJECT_ROOT), existing_pythonpath) if part),
    )
    manifest = _manifest([
        "tests/test_child_execution.py::test_child_first",
        "tests/test_child_execution.py::test_child_second",
    ])

    result = run_pytest_shard(
        project_root,
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "child-runtime",
        timeout_seconds=20,
        port_readiness_probe=lambda ports: bool(ports),
    )

    assert result["status"] == "PASS"
    assert result["executedNodeids"] == manifest["nodeids"]
    assert result["result"]["passed"] == 2
    assert result["metadata"]["cleanup"]["status"] == "PASS"
    assert not (tmp_path / "child-runtime").exists()


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
            ["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime(), port_handoff=False
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
        _run_pytest_command(["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime(), port_handoff=False)

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


def test_run_pytest_command_uses_process_port_handoff_when_available(monkeypatch, tmp_path):
    events = []

    class FakeProcess:
        pid = 456
        returncode = 0
        _nbs_readiness_reader = -1
        _nbs_start_writer = -1

        def communicate(self, timeout=None):
            return "", ""

    class FakeRuntime:
        def complete_port_handoff(self, process, *, timeout, readiness_callback):
            events.append("ready-start")

        def launch_process_with_port_handoff(self, argv, *, cwd, env):
            events.append((argv, cwd, env))
            return FakeProcess()

    monkeypatch.setattr(
        "scripts.full_pytest_shard.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("direct Popen bypassed handoff")),
    )

    result = _run_pytest_command(
        ["pytest"], cwd=tmp_path, env={"A": "B"}, timeout=1, runtime=FakeRuntime()
    )

    assert result.returncode == 0
    assert events == [(["pytest"], tmp_path, {"A": "B"}), "ready-start"]


def test_run_pytest_command_shares_one_timeout_budget_with_handoff(monkeypatch, tmp_path):
    budgets = []
    clock = iter([100.0, 100.2, 100.7])

    class FakeProcess:
        returncode = 0
        _nbs_readiness_reader = -1
        _nbs_start_writer = -1

        def communicate(self, timeout=None):
            budgets.append(timeout)
            return "", ""

    class FakeRuntime:
        def complete_port_handoff(self, process, *, timeout, readiness_callback):
            budgets.append(timeout)

        def launch_process_with_port_handoff(self, argv, *, cwd, env):
            return FakeProcess()

    monkeypatch.setattr("scripts.full_pytest_shard.time.monotonic", lambda: next(clock))

    result = _run_pytest_command(
        ["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime()
    )

    assert result.returncode == 0
    assert budgets == pytest.approx([0.8, 0.3])


def test_run_pytest_command_cleans_up_when_handoff_rejects_expired_timeout(monkeypatch, tmp_path):
    events = []

    class FakeProcess:
        pid = 457
        returncode = None
        _nbs_readiness_reader = -1
        _nbs_start_writer = -1

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            return "", ""

    class FakeRuntime:
        def complete_port_handoff(self, process, *, timeout, readiness_callback):
            assert timeout == 0
            raise ValueError("handoff timeout must be finite and strictly positive")

        def launch_process_with_port_handoff(self, argv, *, cwd, env):
            return FakeProcess()

        def terminate_process_groups(self, *, force=False):
            events.append(("terminate", force))

    clock = iter([100.0, 101.0])
    monkeypatch.setattr("scripts.full_pytest_shard.time.monotonic", lambda: next(clock))

    with pytest.raises(ValueError, match="handoff timeout"):
        _run_pytest_command(
            ["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime()
        )

    assert events == [("terminate", True), ("communicate", 1)]


@pytest.mark.parametrize("missing", ["reader", "writer", "callback"])
def test_missing_ready_start_contract_fails_closed(tmp_path, missing):
    from types import SimpleNamespace

    terminated = []
    process = SimpleNamespace(
        pid=456, returncode=0, communicate=lambda **kw: ("", ""),
        _nbs_readiness_reader=None if missing == "reader" else -1,
        _nbs_start_writer=None if missing == "writer" else -1,
    )
    runtime = SimpleNamespace(
        launch_process_with_port_handoff=lambda *a, **kw: process,
        terminate_process_groups=lambda **kw: terminated.append(True),
        complete_port_handoff=None if missing == "callback" else lambda *a, **kw: None,
    )
    with pytest.raises(RuntimeError, match="handshake"):
        _run_pytest_command(["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=runtime)
    assert terminated == [True]


def test_child_adopts_reserved_port_descriptor_contract(monkeypatch):
    from scripts import full_pytest_shard as subject

    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    reservation.listen(1)
    descriptor = os.dup(reservation.fileno())
    port = reservation.getsockname()[1]
    monkeypatch.setenv("NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL", "reserved-fd-v1")
    monkeypatch.setenv("NBS_ACCEPTANCE_RESERVED_PORT_FDS", f"health={descriptor}:{port}")

    try:
        subject._adopt_reserved_port_fds()
        adopted = subject._ADOPTED_RESERVED_PORTS.pop("health")
        assert adopted.getsockname()[:2] == ("127.0.0.1", port)
        adopted.close()
    finally:
        subject.close_activated_sockets()
        _close_raw_fd(descriptor)
        reservation.close()


def test_child_closes_socket_when_activation_registration_fails(monkeypatch):
    from scripts import full_pytest_shard as subject

    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    reservation.listen(1)
    descriptor = os.dup(reservation.fileno())
    port = reservation.getsockname()[1]
    created = []
    real_fromfd = subject.socket.fromfd

    def tracking_fromfd(*args):
        sock = real_fromfd(*args)
        created.append(sock)
        return sock

    monkeypatch.setenv("NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL", "reserved-fd-v1")
    monkeypatch.setenv("NBS_ACCEPTANCE_RESERVED_PORT_FDS", f"health={descriptor}:{port}")
    monkeypatch.setattr(subject.socket, "fromfd", tracking_fromfd)
    monkeypatch.setattr(
        subject,
        "_register_activated_socket",
        lambda *args: (_ for _ in ()).throw(RuntimeError("registration failed")),
    )

    try:
        with pytest.raises(RuntimeError, match="descriptor adoption failed"):
            subject._adopt_reserved_port_fds()
        assert created and all(sock.fileno() < 0 for sock in created)
        assert not subject._ADOPTED_RESERVED_PORTS
        assert not subject._ACTIVATED_PORTS
    finally:
        subject.close_activated_sockets()
        _close_raw_fd(descriptor)
        reservation.close()


def test_child_clears_all_socket_registries_after_late_adoption_failure(monkeypatch):
    from scripts import full_pytest_shard as subject

    reservations = []
    entries = []
    created = []
    real_fromfd = subject.socket.fromfd
    real_register = subject._register_activated_socket

    def tracking_fromfd(*args):
        sock = real_fromfd(*args)
        created.append(sock)
        return sock

    calls = 0

    def fail_on_second_registration(name, sock, port):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("late registration failed")
        return real_register(name, sock, port)

    try:
        for name in ("health", "mcp"):
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservation.bind(("127.0.0.1", 0))
            reservation.listen(1)
            descriptor = os.dup(reservation.fileno())
            reservations.append(reservation)
            entries.append(f"{name}={descriptor}:{reservation.getsockname()[1]}")
        monkeypatch.setenv("NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL", "reserved-fd-v1")
        monkeypatch.setenv("NBS_ACCEPTANCE_RESERVED_PORT_FDS", ",".join(entries))
        monkeypatch.setattr(subject.socket, "fromfd", tracking_fromfd)
        monkeypatch.setattr(subject, "_register_activated_socket", fail_on_second_registration)

        with pytest.raises(RuntimeError, match="descriptor adoption failed"):
            subject._adopt_reserved_port_fds()

        assert calls == 2
        assert created and all(sock.fileno() < 0 for sock in created)
        assert not subject._ADOPTED_RESERVED_PORTS
        assert not subject._ACTIVATED_PORTS
    finally:
        subject.close_activated_sockets()
        for entry in entries:
            _close_raw_fd(int(entry.split("=", 1)[1].split(":", 1)[0]))
        for reservation in reservations:
            reservation.close()


def test_child_rejects_duplicate_reserved_fd_entries(monkeypatch):
    from scripts import full_pytest_shard as subject

    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    reservation.listen(1)
    descriptor = os.dup(reservation.fileno())
    port = reservation.getsockname()[1]
    fromfd_calls = []
    real_fromfd = subject.socket.fromfd

    def tracking_fromfd(*args):
        fromfd_calls.append(args[0])
        return real_fromfd(*args)

    monkeypatch.setenv("NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL", "reserved-fd-v1")
    monkeypatch.setenv(
        "NBS_ACCEPTANCE_RESERVED_PORT_FDS",
        f"health={descriptor}:{port},mcp={descriptor}:{port}",
    )
    monkeypatch.setattr(subject.socket, "fromfd", tracking_fromfd)

    try:
        with pytest.raises(RuntimeError, match="descriptor adoption failed"):
            subject._adopt_reserved_port_fds()
        assert fromfd_calls == [descriptor]
        assert not subject._ADOPTED_RESERVED_PORTS
        assert not subject._ACTIVATED_PORTS
    finally:
        subject.close_activated_sockets()
        _close_raw_fd(descriptor)
        reservation.close()


def test_direct_activation_registration_is_retrievable_and_cleaned_up():
    from scripts import full_pytest_shard as subject

    service_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    service_socket.bind(("127.0.0.1", 0))
    service_socket.listen(1)
    port = service_socket.getsockname()[1]
    try:
        subject._register_activated_socket("direct", service_socket, port)
        assert subject.activated_socket("direct") is service_socket
        assert subject.activated_ports() == {"direct": port}
    finally:
        subject.close_activated_sockets()
    assert service_socket.fileno() < 0


def test_child_side_service_can_consume_every_activated_endpoint(monkeypatch):
    from scripts import full_pytest_shard as subject

    reservations = {}
    entries = []
    try:
        for name in ("health", "mcp", "streamlit"):
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservation.bind(("127.0.0.1", 0))
            reservation.listen(1)
            descriptor = os.dup(reservation.fileno())
            port = reservation.getsockname()[1]
            reservations[name] = reservation
            entries.append(f"{name}={descriptor}:{port}")
        monkeypatch.setenv("NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL", "reserved-fd-v1")
        monkeypatch.setenv("NBS_ACCEPTANCE_RESERVED_PORT_FDS", ",".join(entries))

        subject._adopt_reserved_port_fds()

        assert dict(subject.activated_ports()) == {
            name: reservations[name].getsockname()[1] for name in reservations
        }
        for name, reservation in reservations.items():
            service_socket = subject.activated_socket(name)
            assert service_socket.get_inheritable() is True
            with socket.create_connection(("127.0.0.1", reservation.getsockname()[1]), timeout=1):
                accepted, _ = service_socket.accept()
                accepted.close()
    finally:
        subject.close_activated_sockets()
        subject._ADOPTED_RESERVED_PORTS.clear()
        for entry in entries:
            _close_raw_fd(int(entry.split("=", 1)[1].split(":", 1)[0]))
        for reservation in reservations.values():
            reservation.close()


def test_pytest_execution_wraps_child_with_reserved_fd_adoption(monkeypatch, tmp_path):
    events = []

    class FakeProcess:
        pid = 456
        returncode = 0
        _nbs_readiness_reader = -1
        _nbs_start_writer = -1

        def communicate(self, timeout=None):
            return "", ""

    class FakeRuntime:
        def complete_port_handoff(self, process, **kwargs):
            pass

        def launch_process_with_port_handoff(self, argv, *, cwd, env):
            events.append((argv, cwd, env))
            return FakeProcess()

    result = _run_pytest_command(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        env={"A": "B"},
        timeout=1,
        runtime=FakeRuntime(),
    )

    assert result.returncode == 0
    assert events[0][0] == [sys.executable, "-m", "scripts.full_pytest_shard", "--child-adopt-reserved-fds", "-q"]


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
            ["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime(), port_handoff=False
        )

    assert timeouts == [1, 1, 1]
    assert terminate_calls == [False, True]


def test_run_pytest_command_blocks_unqualified_port_handoff(monkeypatch, tmp_path):
    class FakeRuntime:
        pass

    monkeypatch.setattr(
        "scripts.full_pytest_shard.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unqualified Popen must not run")),
    )

    with pytest.raises(RuntimeError, match="qualified port-handoff launcher"):
        _run_pytest_command(["pytest"], cwd=tmp_path, env={}, timeout=1, runtime=FakeRuntime())
