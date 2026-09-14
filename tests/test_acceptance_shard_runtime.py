import os
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from backend.agents import acceptance_shard_runtime
from backend.agents.acceptance_shard_runtime import ShardRuntime, allocate_shard_runtime


def _project_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return project


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


def test_overlapping_same_run_and_shard_cannot_reserve_same_ports(tmp_path):
    first = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-overlap", shard_index=0, platform_name="darwin"
    )

    with pytest.raises(RuntimeError, match="port"):
        allocate_shard_runtime(
            project_root=_project_root(tmp_path), run_id="run-overlap", shard_index=0, platform_name="darwin"
        )

    first.cleanup()


def test_handoff_allows_child_to_bind_port_and_cleanup_allows_reallocation(tmp_path):
    first = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-handoff", shard_index=0, platform_name="darwin"
    )
    port = first.profile_ports()["streamlit"]
    first.handoff_ports_with_readiness(lambda ports: True)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    listener.close()

    report = first.cleanup()
    assert report["status"] == "PASS"

    second = allocate_shard_runtime(
        project_root=_project_root(tmp_path), run_id="run-handoff", shard_index=0, platform_name="darwin"
    )
    assert second.profile_ports() == first.profile_ports()
    second.cleanup()


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
    ports = acceptance_shard_runtime._ports_for(run_id, 0)

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

    monkeypatch.undo()
    retry = allocate_shard_runtime(
        project_root=_project_root(tmp_path),
        run_id=run_id,
        shard_index=0,
        fixture_root=tmp_path / "write-failure-retry",
    )
    assert retry.cleanup()["status"] == "PASS"


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
