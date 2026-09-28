import os
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from backend.agents import acceptance_port_lock, acceptance_shard_runtime
from backend.agents.acceptance_shard_runtime import ShardRuntime, allocate_shard_runtime


def _project_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return project


def test_parallel_listener_port_pool_stays_outside_default_ephemeral_range():
    ports_by_shard = [
        acceptance_shard_runtime._ports_for("stable-port-pool-test", shard_index)
        for shard_index in range(16)
    ]

    assert all(set(ports) == {"streamlit", "mcp", "health"} for ports in ports_by_shard)
    assert all(30_000 <= port <= 32_767 for ports in ports_by_shard for port in ports.values())
    assert len({port for ports in ports_by_shard for port in ports.values()}) == 16 * 3


def test_two_shards_get_distinct_canonical_roots_and_databases(tmp_path):
    first = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )
    second = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=1, platform_name="darwin"
    )

    assert first.root != second.root
    assert first.root == first.root.resolve()
    assert second.root == second.root.resolve()
    assert first.environment()["NBS_ANALYTICS_DB_FILE"] != second.environment()["NBS_ANALYTICS_DB_FILE"]
    assert first.environment()["NBS_ANALYTICS_CACHE_DIR"] != second.environment()["NBS_ANALYTICS_CACHE_DIR"]
    assert set(first.profile_ports().values()).isdisjoint(second.profile_ports().values())
    assert first.process_profile != second.process_profile

    first.cleanup()
    second.cleanup()


def test_sanitized_run_id_collision_still_gets_unique_process_profiles(tmp_path):
    first = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run/a", shard_index=0, platform_name="darwin"
    )
    second = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run?a", shard_index=0, platform_name="darwin"
    )

    assert first.run_id == second.run_id
    assert first.process_profile != second.process_profile
    assert first.environment()["NBS_ANALYTICS_PROCESS_PROFILE"] != second.environment()["NBS_ANALYTICS_PROCESS_PROFILE"]

    first.cleanup()
    second.cleanup()


def test_overlapping_same_run_and_shard_cannot_reserve_same_ports(monkeypatch, tmp_path):
    reservations = []
    try:
        for _ in acceptance_shard_runtime._PORT_NAMES:
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservation.bind(("127.0.0.1", 0))
            reservations.append(reservation)
        ports = {
            name: reservation.getsockname()[1]
            for name, reservation in zip(acceptance_shard_runtime._PORT_NAMES, reservations)
        }
    finally:
        for reservation in reservations:
            reservation.close()

    monkeypatch.setattr(acceptance_shard_runtime, "_ports_for", lambda *_args: dict(ports))
    first = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-overlap", shard_index=0, platform_name="darwin"
    )

    try:
        with pytest.raises(RuntimeError, match="port"):
            allocate_shard_runtime(
                project_root=_project_root(tmp_path), run_id="run-overlap", shard_index=0, platform_name="darwin"
            )
    finally:
        first.cleanup()


def test_legacy_handoff_is_fail_closed_without_releasing_reservations(tmp_path):
    first = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-handoff", shard_index=0, platform_name="darwin"
    )
    try:
        with pytest.raises(RuntimeError, match="legacy port handoff is disabled"):
            first.handoff_ports_with_readiness(lambda ports: True)
        assert first._port_reservations
    finally:
        report = first.cleanup()
    assert report["status"] == "PASS"

    second = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-handoff", shard_index=0, platform_name="darwin"
    )
    assert second.profile_ports() == first.profile_ports()
    second.cleanup()


def test_process_handoff_transfers_reserved_socket_fds_to_child(monkeypatch, tmp_path):
    probes = []
    try:
        for _ in acceptance_shard_runtime._PORT_NAMES:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
        ports = {
            name: probe.getsockname()[1]
            for name, probe in zip(acceptance_shard_runtime._PORT_NAMES, probes)
        }
    finally:
        for probe in probes:
            probe.close()
    monkeypatch.setattr(acceptance_shard_runtime, "_ports_for", lambda *_args: dict(ports))
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id=f"run-process-handoff-{uuid.uuid4().hex}", shard_index=0
    )
    reserved_fds = {reservation.fileno() for reservation in runtime._port_reservations.values()}
    captured = {}

    class FakeProcess:
        pid = 456

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(acceptance_shard_runtime.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runtime, "register_process_group", lambda process_id: None)
    evidence_reader, evidence_writer = os.pipe()
    try:
        process = runtime.launch_process_with_port_handoff(
            ["pytest"], cwd=tmp_path, env={
                "NBS_ACCEPTANCE_PROFILE_PORTS": "",
                "NBS_ACCEPTANCE_EXECUTION_EVIDENCE_FD": str(evidence_writer),
                "NBS_ACCEPTANCE_EXECUTION_BINDING": "{}",
            }
        )

        assert process.pid == 456
        assert captured["env"]["NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL"] == "reserved-fd-v1"
        readiness_fd = int(captured["env"]["NBS_ACCEPTANCE_CHILD_READY_FD"])
        start_fd = int(captured["env"]["NBS_ACCEPTANCE_CHILD_START_FD"])
        assert set(captured["pass_fds"]) == reserved_fds | {readiness_fd, start_fd, evidence_writer}
        metadata = {}
        for item in captured["env"]["NBS_ACCEPTANCE_RESERVED_PORT_FDS"].split(","):
            name, descriptor_spec = item.split("=", 1)
            descriptor, port = descriptor_spec.split(":", 1)
            metadata[name] = (int(descriptor), int(port))
        assert set(metadata) == set(runtime.profile_ports())
        assert {descriptor for descriptor, _ in metadata.values()} == reserved_fds
        assert {name: port for name, (_, port) in metadata.items()} == runtime.profile_ports()
        assert runtime._port_reservations == {}
        assert runtime.cleanup()["status"] == "PASS"
    finally:
        os.close(evidence_reader)
        os.close(evidence_writer)


def test_runtime_completes_bounded_ready_start_handoff(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-ready-start", shard_index=0
    )
    readiness_reader, readiness_writer = os.pipe()
    start_reader, start_writer = os.pipe()

    class Process:
        _nbs_readiness_reader = readiness_reader
        _nbs_start_writer = start_writer

    callbacks = []
    observed = {}
    process = Process()
    try:
        identity = ",".join(
            f"{name}={runtime.profile_ports()[name]}"
            for name in sorted(runtime.profile_ports())
        )
        os.write(readiness_writer, f"READY {identity}\n".encode("ascii"))
        runtime.complete_port_handoff(
            process,
            timeout=1,
            readiness_callback=lambda: callbacks.append("ready"),
            start_callback=lambda: callbacks.append("start"),
            readiness_probe=lambda ports: observed.update(ports) is None and not hasattr(ports, "reserved_sockets"),
        )
        assert callbacks == ["ready", "start"]
        assert observed == runtime.profile_ports()
        assert os.read(start_reader, 6) == b"START\n"
        with pytest.raises(OSError):
            os.fstat(readiness_reader)
        assert process._nbs_readiness_reader is None
        assert process._nbs_start_writer is None
    finally:
        for descriptor in (readiness_reader, readiness_writer, start_reader, start_writer):
            try:
                os.close(descriptor)
            except OSError:
                pass
        runtime.cleanup()


def test_runtime_reads_fragmented_ready_handshake_until_newline(monkeypatch, tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-fragmented-ready", shard_index=0
    )
    readiness_reader, readiness_writer = os.pipe()
    start_reader, start_writer = os.pipe()

    class Process:
        _nbs_readiness_reader = readiness_reader
        _nbs_start_writer = start_writer

    real_read = os.read
    monkeypatch.setattr(
        acceptance_shard_runtime.os,
        "read",
        lambda descriptor, size: real_read(descriptor, min(size, 5)),
    )
    try:
        identity = ",".join(
            f"{name}={runtime.profile_ports()[name]}"
            for name in sorted(runtime.profile_ports())
        )
        os.write(readiness_writer, f"READY {identity}\n".encode("ascii"))

        runtime.complete_port_handoff(
            Process(), timeout=1, readiness_probe=lambda ports: bool(ports),
        )

        assert real_read(start_reader, 6) == b"START\n"
    finally:
        for descriptor in (readiness_reader, readiness_writer, start_reader, start_writer):
            try:
                os.close(descriptor)
            except OSError:
                pass
        runtime.cleanup()


def test_runtime_rejects_missing_readiness_probe_before_starting_child(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-missing-readiness-probe", shard_index=0
    )
    readiness_reader, readiness_writer = os.pipe()
    start_reader, start_writer = os.pipe()

    class Process:
        _nbs_readiness_reader = readiness_reader
        _nbs_start_writer = start_writer

    callbacks = []
    try:
        with pytest.raises(ValueError, match="readiness_probe is required"):
            runtime.complete_port_handoff(
                Process(), timeout=1, start_callback=lambda: callbacks.append("start"),
            )
        assert callbacks == []
        assert os.read(start_reader, 6) == b""
    finally:
        for descriptor in (readiness_reader, readiness_writer, start_reader, start_writer):
            try:
                os.close(descriptor)
            except OSError:
                pass
        runtime.cleanup()


def test_runtime_rejects_mismatched_child_socket_identity_before_readiness_probe(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-mismatched-ready", shard_index=0
    )
    readiness_reader, readiness_writer = os.pipe()
    start_reader, start_writer = os.pipe()

    class Process:
        _nbs_readiness_reader = readiness_reader
        _nbs_start_writer = start_writer

    ports = runtime.profile_ports()
    wrong_ports = dict(ports)
    wrong_ports["mcp"] += 1
    identity = ",".join(f"{name}={wrong_ports[name]}" for name in sorted(wrong_ports))
    probe_calls = []
    try:
        os.write(readiness_writer, f"READY {identity}\n".encode("ascii"))
        with pytest.raises(RuntimeError, match="child readiness handshake failed"):
            runtime.complete_port_handoff(
                Process(), timeout=1,
                readiness_probe=lambda observed: probe_calls.append(dict(observed)) or True,
            )
        assert probe_calls == []
        assert os.read(start_reader, 6) == b""
    finally:
        for descriptor in (readiness_reader, readiness_writer, start_reader, start_writer):
            try:
                os.close(descriptor)
            except OSError:
                pass
        runtime.cleanup()


def test_process_registration_failure_closes_parent_pipe_descriptors(monkeypatch, tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-registration-failure", shard_index=0
    )
    captured = {}

    class FakeProcess:
        pid = 457

        def communicate(self, timeout=None):
            return (b"", b"")

        def kill(self):
            return None

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(acceptance_shard_runtime.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(acceptance_shard_runtime.os, "killpg", lambda *_args: None)
    monkeypatch.setattr(runtime, "register_process_group", lambda _pid: (_ for _ in ()).throw(RuntimeError("register failed")))

    with pytest.raises(RuntimeError, match="register failed"):
        runtime.launch_process_with_port_handoff(
            ["pytest"], cwd=tmp_path, env={"NBS_ACCEPTANCE_PROFILE_PORTS": ""}
        )

    for descriptor in captured["pass_fds"][-2:]:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    runtime.cleanup()


@pytest.mark.parametrize("fail_on_pipe", [1, 2])
def test_process_handoff_releases_partial_resources_when_pipe_allocation_fails(
    monkeypatch, tmp_path, fail_on_pipe
):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path),
        run_id=f"run-pipe-failure-{fail_on_pipe}",
        shard_index=0,
        platform_name="darwin",
    )
    real_open_namespace_lock = acceptance_shard_runtime._open_namespace_lock
    namespace_fds = []
    pipe_fds = []
    real_pipe = os.pipe
    pipe_calls = 0

    def track_namespace_lock():
        descriptor = real_open_namespace_lock()
        namespace_fds.append(descriptor)
        return descriptor

    def fail_selected_pipe():
        nonlocal pipe_calls
        pipe_calls += 1
        if pipe_calls == fail_on_pipe:
            raise OSError("injected pipe allocation failure")
        descriptors = real_pipe()
        pipe_fds.extend(descriptors)
        return descriptors

    monkeypatch.setattr(acceptance_shard_runtime, "_open_namespace_lock", track_namespace_lock)
    monkeypatch.setattr(acceptance_shard_runtime.os, "pipe", fail_selected_pipe)
    reservations = tuple(runtime._port_reservations.values())

    try:
        with pytest.raises(OSError, match="injected pipe allocation failure"):
            runtime.launch_process_with_port_handoff(
                [sys.executable, "-c", "pass"], cwd=tmp_path, env={}
            )

        assert namespace_fds and all(_descriptor_is_closed(fd) for fd in namespace_fds)
        assert all(_descriptor_is_closed(fd) for fd in pipe_fds)
        assert all(reservation.fileno() == -1 for reservation in reservations)
        assert runtime._port_reservations == {}
    finally:
        # Prevent a regression from leaving the process-wide namespace lock
        # held and blocking runtime cleanup for its timeout.
        for descriptor in (*pipe_fds, *namespace_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
        runtime.cleanup()


def _descriptor_is_closed(descriptor):
    try:
        os.fstat(descriptor)
    except OSError:
        return True
    return False


def test_runtime_rejects_non_positive_handoff_timeout(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-invalid-timeout", shard_index=0
    )
    readiness_reader, readiness_writer = os.pipe()
    start_reader, start_writer = os.pipe()

    class Process:
        _nbs_readiness_reader = readiness_reader
        _nbs_start_writer = start_writer

    try:
        with pytest.raises(ValueError, match="handoff timeout"):
            runtime.complete_port_handoff(Process(), timeout=0)
    finally:
        for descriptor in (readiness_reader, readiness_writer, start_reader, start_writer):
            try:
                os.close(descriptor)
            except OSError:
                pass
        runtime.cleanup()


@pytest.mark.skipif(os.name == "nt", reason="POSIX reserved-fd integration contract")
def test_real_child_wrapper_adopts_reserved_fds_and_exposes_socket_identity(tmp_path):
    from scripts.full_pytest_shard import (
        _ExecutionEvidenceChannel,
        _build_execution_binding,
        _run_pytest_command,
    )

    project_root = Path(__file__).resolve().parents[1]
    child_test = tmp_path / f"test_reserved_fd_child_{uuid.uuid4().hex}.py"
    assert child_test.is_relative_to(tmp_path)
    nodeid = f"{child_test}::test_child_sees_reserved_socket_identity"
    child_test.write_text(
        "from scripts import full_pytest_shard as wrapper\n\n"
        "import os\n\n"
        "def test_child_sees_reserved_socket_identity():\n"
        "    assert 'NBS_ACCEPTANCE_EXECUTION_EVIDENCE_FD' not in os.environ\n"
        "    assert 'NBS_ACCEPTANCE_EXECUTION_BINDING' not in os.environ\n"
        "    ports = dict(wrapper.activated_ports())\n"
        "    assert set(ports) == {'health', 'mcp', 'streamlit'}\n"
        "    for name, port in ports.items():\n"
        "        service_socket = wrapper.activated_socket(name)\n"
        "        assert service_socket.getsockname()[:2] == ('127.0.0.1', port)\n",
        encoding="utf-8",
    )
    runtime = allocate_shard_runtime(
        project_root=project_root,
        run_id=f"run-real-adoption-{uuid.uuid4().hex}",
        shard_index=0,
        fixture_root=tmp_path / "child-fixture",
    )
    events = []
    probed_ports = []
    env = os.environ.copy()
    env.update(runtime.environment())
    binding = _build_execution_binding(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        manifest_fingerprint="c" * 64,
        shard_index=0,
        shard_count=1,
        assigned_nodeids=[nodeid],
    )
    evidence_channel = _ExecutionEvidenceChannel()
    evidence_channel.configure(env, binding)

    def child_owned_readiness(ports):
        for port in ports.values():
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    pass
            except OSError:
                return False
            probed_ports.append(port)
        return True

    try:
        runtime.validate_reserved_ports(lambda ports: True)
        completed = _run_pytest_command(
            [sys.executable, "-m", "pytest", "-q", nodeid],
            cwd=project_root,
            env=env,
            timeout=15,
            runtime=runtime,
            readiness_callback=lambda: events.append("ready"),
            start_callback=lambda: events.append("start"),
            readiness_probe=child_owned_readiness,
        )
        assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
        assert events == ["ready", "start"]
        assert set(probed_ports) == set(runtime.profile_ports().values())
        evidence = evidence_channel.read(binding)
        child_nodeid = f"{child_test.name}::test_child_sees_reserved_socket_identity"
        assert evidence["collectedNodeids"] == [child_nodeid]
        assert evidence["startedNodeids"] == [child_nodeid]
    finally:
        evidence_channel.close()
        runtime.cleanup()
        try:
            child_test.unlink()
        except FileNotFoundError:
            pass


def test_reserved_port_validation_proves_socket_ownership_and_listening(tmp_path):
    reservations = []
    try:
        for _ in acceptance_shard_runtime._PORT_NAMES:
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservation.bind(("127.0.0.1", 0))
            reservations.append(reservation)
        ports = {
            name: reservation.getsockname()[1]
            for name, reservation in zip(acceptance_shard_runtime._PORT_NAMES, reservations)
        }
    finally:
        for reservation in reservations:
            reservation.close()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(acceptance_shard_runtime, "_ports_for", lambda *_args: dict(ports))
    try:
        runtime = allocate_shard_runtime(
            project_root=_project_root(tmp_path), run_id="run-readiness-contract", shard_index=0
        )
    finally:
        monkeypatch.undo()
    observed = {}

    def probe(ports):
        observed["ports"] = dict(ports)
        observed["has_reservations"] = hasattr(ports, "reserved_sockets")
        return True

    runtime.validate_reserved_ports(probe)

    assert observed["ports"] == runtime.profile_ports()
    assert observed["has_reservations"] is True
    assert runtime.cleanup()["status"] == "PASS"


@pytest.mark.parametrize(
    ("readiness_results", "expected"),
    [([True, True, True], True), ([True, False, True], False)],
)
def test_canary_readiness_probes_handed_off_endpoints_without_starting_binder(
    monkeypatch, readiness_results, expected,
):
    from scripts import acceptance_shard_canary

    ports = {"health": 45101, "mcp": 45102, "streamlit": 45103}
    observed = []

    def tcp_ready(port):
        observed.append(port)
        return readiness_results[len(observed) - 1]

    monkeypatch.setattr(acceptance_shard_canary, "_tcp_ready", tcp_ready)
    monkeypatch.setattr(
        acceptance_shard_canary.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("readiness must not start a port binder"),
    )

    assert acceptance_shard_canary._port_readiness(ports) is expected
    assert observed == ([45101, 45102, 45103] if expected else [45101, 45102])


def test_getsockname_failure_is_bounded_not_unboundlocalerror():
    import errno
    from types import SimpleNamespace
    from backend.agents.acceptance_shard_runtime import ShardRuntime

    def unavailable():
        raise OSError(errno.EINVAL, "endpoint unavailable")

    runtime = object.__new__(ShardRuntime)
    runtime._ports = {"health": 45000}
    runtime._port_reservations = {"health": SimpleNamespace(getsockname=unavailable)}
    with pytest.raises(RuntimeError, match="ownership is unavailable"):
        runtime.validate_reserved_ports(lambda ports: True)


def test_port_handoff_requires_explicit_bind_readiness_protocol(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-handoff-contract", shard_index=0
    )

    with pytest.raises(RuntimeError, match="bind/readiness"):
        runtime.handoff_ports()

    runtime.cleanup()


def test_runtime_rejects_production_paths(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )

    runtime.validate_isolation()

    assert "nbs_marketing_data.db" not in str(runtime.db_path)
    assert runtime.environment()["NBS_ANALYTICS_PROCESS_PROFILE"].startswith("acceptance-shard-")
    report = runtime.cleanup()
    assert report["status"] == "PASS"
    assert runtime.root.exists() is False


def test_runtime_rejects_non_integer_shard_index(tmp_path):
    with pytest.raises(ValueError, match="shard_index"):
        allocate_shard_runtime(
            project_root=_project_root(tmp_path),
            run_id="run-1",
            shard_index=1.5,
        )


def test_runtime_rejects_symlinked_or_existing_explicit_root(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="unique"):
        allocate_shard_runtime(
            project_root=_project_root(tmp_path),
            run_id="run-1",
            shard_index=0,
            fixture_root=existing,
        )

    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        allocate_shard_runtime(
            project_root=_project_root(tmp_path),
            run_id="run-1",
            shard_index=0,
            fixture_root=alias,
        )


def test_runtime_rejects_explicit_root_inside_temporary_project(tmp_path):
    project = _project_root(tmp_path)
    with pytest.raises(ValueError, match="inside the project root"):
        allocate_shard_runtime(
            project_root=project,
            run_id="run-nested-fixture",
            shard_index=0,
            fixture_root=project / "nested-runtime",
        )


def test_cleanup_classifies_unexpected_files_as_isolation_violation(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )
    (runtime.root / "unexpected-leak.txt").write_text("leak", encoding="utf-8")

    report = runtime.cleanup()

    assert report["status"] == "BLOCKED"
    assert report["failureCode"] == "isolation_violation"
    assert "unexpected-leak.txt" in report["leakedFiles"]
    assert runtime.root.exists() is False


def test_cleanup_report_is_fresh_before_removing_runtime(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )
    assert runtime.cleanup_report()["status"] == "PASS"
    (runtime.root / "late-leak.txt").write_text("leak", encoding="utf-8")

    report = runtime.cleanup()

    assert report["status"] == "BLOCKED"
    assert report["failureCode"] == "isolation_violation"
    assert "late-leak.txt" in report["leakedFiles"]
    assert runtime.root.exists() is False


def test_cleanup_detects_nested_cache_locks(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )
    (runtime.cache_dir / "workers").mkdir(parents=True)
    (runtime.cache_dir / "workers" / "worker.lock").write_text("lock", encoding="utf-8")

    report = runtime.cleanup()

    assert report["status"] == "BLOCKED"
    assert report["failureCode"] == "isolation_violation"
    assert report["leakedLocks"] == ["cache/workers/worker.lock"]
    assert runtime.root.exists() is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX detached process regression")
def test_cleanup_detects_detached_descendant_by_runtime_profile(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-detached", shard_index=0, platform_name="darwin"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; child = os.fork();\n"
            "if child: os._exit(0)\n"
            "os.setsid(); time.sleep(30)",
        ],
        env=runtime.environment(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    runtime.register_process_group(process.pid)
    process.wait(timeout=2)

    report = runtime.cleanup()

    assert report["status"] == "PASS"
    assert report["failureCode"] is None
    assert report["leakedProcesses"] == []


def test_process_profile_match_does_not_cross_match_another_shard(monkeypatch, tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )
    runtime._process_groups.add(123)
    monkeypatch.setattr(
        acceptance_shard_runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args,
            0,
            "999 python NBS_ANALYTICS_PROCESS_PROFILE=acceptance-shard-run-10-0\n",
            "",
        ),
    )

    assert runtime._detached_process_ids() == []
    runtime.cleanup()


def test_process_inspection_nonzero_exit_fails_closed(monkeypatch, tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )
    runtime._process_groups.add(123)
    monkeypatch.setattr(
        acceptance_shard_runtime.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "ps failed"),
    )

    assert runtime._detached_process_ids() is None
    report = runtime.cleanup()
    assert report["status"] == "BLOCKED"
    assert report["leakedProcesses"] == ["process_tree_unknown"]


def test_port_allocation_failure_removes_created_runtime(monkeypatch, tmp_path):
    allocated = tmp_path / "allocated-runtime"

    def create_runtime(**kwargs):
        allocated.mkdir()
        assert allocated.exists()
        return str(allocated)

    monkeypatch.setattr(acceptance_shard_runtime.tempfile, "mkdtemp", create_runtime)
    monkeypatch.setattr(
        acceptance_shard_runtime,
        "_reserve_ports",
        lambda *args: (_ for _ in ()).throw(RuntimeError("port allocation unavailable")),
    )

    with pytest.raises(RuntimeError, match="port allocation"):
        allocate_shard_runtime(project_root=_project_root(tmp_path), run_id="run-1", shard_index=0)

    assert allocated.exists() is False


def test_port_lock_write_failure_closes_descriptor_and_removes_lock(monkeypatch, tmp_path):
    run_id = f"write-failure-{uuid.uuid4().hex}"
    ports = {name: 45001 + index for index, name in enumerate(acceptance_shard_runtime._PORT_NAMES)}
    monkeypatch.setattr(acceptance_shard_runtime, "_ports_for", lambda *_args: dict(ports))
    lock_path = acceptance_port_lock.PORT_LOCK_ROOT / f"port-{ports['streamlit']}.lock"
    opened_lock_descriptors = []
    fake_sockets = []

    class FakeSocket:
        closed = False

        def setsockopt(self, *_args):
            pass

        def bind(self, *_args):
            pass

        def listen(self, *_args):
            pass

        def close(self):
            self.closed = True

    def make_fake_socket(*_args):
        value = FakeSocket()
        fake_sockets.append(value)
        return value

    monkeypatch.setattr(
        acceptance_port_lock,
        "socket",
        type("FakeSocketModule", (), {
            "AF_INET": socket.AF_INET,
            "SOCK_STREAM": socket.SOCK_STREAM,
            "SOL_SOCKET": socket.SOL_SOCKET,
            "SO_REUSEADDR": socket.SO_REUSEADDR,
            "socket": staticmethod(make_fake_socket),
        }),
    )
    original_open = os.open

    def track_lock_descriptor(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path) == lock_path:
            opened_lock_descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(acceptance_port_lock.os, "open", track_lock_descriptor)

    def fail_write(_descriptor, _payload):
        raise OSError("disk full")

    monkeypatch.setattr(acceptance_shard_runtime.os, "write", fail_write)

    with pytest.raises(RuntimeError, match="port reservation"):
        allocate_shard_runtime(
            project_root=_project_root(tmp_path),
            run_id=run_id,
            shard_index=0,
            fixture_root=tmp_path / "write-failure-runtime",
        )

    assert len(opened_lock_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(opened_lock_descriptors[0])
    assert not lock_path.exists()
    assert all(item.closed for item in fake_sockets)
    assert not (tmp_path / "write-failure-runtime").exists()

    monkeypatch.undo()
    retry_root = tmp_path / "write-failure-retry-runtime"
    retry_runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path),
        run_id=run_id,
        shard_index=0,
        fixture_root=retry_root,
    )
    retry_lock_paths = tuple(retry_runtime._port_lock_paths.values())
    try:
        assert retry_runtime.root == retry_root.resolve()
        assert retry_lock_paths
        assert all(path.exists() for path in retry_lock_paths)
    finally:
        retry_report = retry_runtime.cleanup()
    assert retry_report["status"] == "PASS"
    assert not retry_root.exists()
    assert all(not path.exists() for path in retry_lock_paths)


def test_stale_port_lock_file_is_recoverable(tmp_path):
    run_id = f"stale-lock-{uuid.uuid4().hex}"
    ports = acceptance_shard_runtime._ports_for(run_id, 0)
    lock_root = acceptance_shard_runtime._PORT_LOCK_ROOT
    lock_root.mkdir(parents=True, exist_ok=True)
    for port in ports.values():
        (lock_root / f"port-{port}.lock").write_text(
            "999999999:stale", encoding="ascii"
        )

    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path),
        run_id=run_id,
        shard_index=0,
        fixture_root=tmp_path / "stale-lock-runtime",
    )
    assert runtime.cleanup()["status"] == "PASS"


def test_cleanup_unlinks_locks_when_namespace_guard_is_unavailable(monkeypatch, tmp_path):
    def available_ports(*_args):
        # This test exercises cleanup semantics, not the deterministic hash-to-port
        # mapping. Pick a short-lived free block so unrelated local services cannot
        # make the test fail before the cleanup path is reached.
        reservations = []
        try:
            for _ in acceptance_shard_runtime._PORT_NAMES:
                reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                reservation.bind(("127.0.0.1", 0))
                reservations.append(reservation)
            return {
                name: reservation.getsockname()[1]
                for name, reservation in zip(acceptance_shard_runtime._PORT_NAMES, reservations)
            }
        finally:
            for reservation in reservations:
                reservation.close()

    monkeypatch.setattr(acceptance_shard_runtime, "_ports_for", available_ports)
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id=f"guard-failure-{uuid.uuid4().hex}", shard_index=0
    )
    lock_paths = list(runtime._port_lock_paths.values())
    monkeypatch.setattr(
        acceptance_shard_runtime,
        "_open_namespace_lock",
        lambda: (_ for _ in ()).throw(RuntimeError("namespace lock unavailable")),
    )

    report = runtime.cleanup()

    assert report["status"] == "BLOCKED"
    assert report["failureCode"] == "isolation_violation"
    assert all(not path.exists() for path in lock_paths)


def test_windows_job_boundary_contract_is_exercised_without_platform_skip(monkeypatch, tmp_path):
    project = _project_root(tmp_path)
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    events = []
    monkeypatch.setattr(acceptance_shard_runtime, "_create_windows_job", lambda: 101)
    monkeypatch.setattr(
        acceptance_shard_runtime,
        "_assign_windows_process_to_job",
        lambda job, process: events.append(("assign", job, process)),
    )
    monkeypatch.setattr(
        acceptance_shard_runtime,
        "_windows_job_active_processes",
        lambda job: 0,
    )
    monkeypatch.setattr(
        acceptance_shard_runtime,
        "_windows_process_alive",
        lambda process: False,
    )
    monkeypatch.setattr(
        acceptance_shard_runtime,
        "_terminate_windows_job",
        lambda job: events.append(("terminate", job)),
    )
    monkeypatch.setattr(
        acceptance_shard_runtime.acceptance_windows_job,
        "close_job",
        lambda job: events.append(("close", job)),
    )

    runtime = ShardRuntime(
        project_root=project,
        root=runtime_root,
        run_id="windows-contract",
        shard_index=0,
        platform_name="win32",
        _ports={},
        allocation_id="allocation",
    )
    runtime._process_group_alive = lambda process_id: False
    runtime.register_process_group(456)
    runtime.terminate_process_groups(force=True)
    report = runtime.cleanup()

    assert events == [("assign", 101, 456), ("terminate", 101), ("close", 101)]
    assert report["status"] == "PASS"


def test_cleanup_detects_and_terminates_registered_process_group(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="darwin"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    runtime.register_process_group(process.pid)

    try:
        report = runtime.cleanup()

        assert report["status"] == "PASS"
        assert report["failureCode"] is None
        assert report["leakedProcesses"] == []
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


@pytest.mark.skipif(os.name != "nt", reason="Windows process handle regression")
def test_windows_cleanup_does_not_report_exited_process_as_leak(tmp_path):
    runtime = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-1", shard_index=0, platform_name="win32"
    )
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=2)
    runtime.register_process_group(process.pid)

    report = runtime.cleanup()

    assert report["status"] == "PASS"
    assert report["leakedProcesses"] == []
