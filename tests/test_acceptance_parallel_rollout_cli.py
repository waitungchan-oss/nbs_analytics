from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

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


def test_canonical_session_file_needs_no_invented_source_fingerprint():
    from backend.agents.verification_session import VerificationSession
    from scripts import acceptance_parallel_rollout as subject

    session = VerificationSession.create(
        project_id="test", base_sha=COMMIT, head_sha=COMMIT,
        brief_path="brief.md", brief_fingerprint="2" * 64,
        worktree_fingerprint="d" * 64, diff_fingerprint="4" * 64,
        contract_fingerprint="3" * 64, policy_fingerprint="5" * 64,
    )
    assert "sourceFingerprint" not in session.to_dict()
    assert subject._valid_source_session(session.to_dict())


def test_source_change_after_serial_suppresses_parallel(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "run_serial_control", _passing_serial_control)
    monkeypatch.setattr(subject, "_matches_source_seal", lambda *a, **kw: False)
    monkeypatch.setattr(subject, "_current_source_identity", lambda root: (COMMIT, SOURCE, ""))
    monkeypatch.setattr(subject, "run_parallel_shards", lambda **kw: pytest.fail("stale source executed"))
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))
    assert code == 2
    result = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert result["speedup"] is None
    assert all(run["failureCode"] == "parallel_source_identity_mismatch" for run in result["measuredRuns"])


def _source_session(**overrides):
    value = {
        "schemaVersion": "source-seal-v1",
        "baseSha": COMMIT,
        "briefPath": "docs/superpowers/specs/2026-09-15-acceptance-parallel-rollout-and-speedup-design.md",
        "briefFingerprint": "2" * 64,
        "contractFingerprint": "3" * 64,
        "diffFingerprint": "4" * 64,
        "policyFingerprint": "5" * 64,
        "headSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "worktreeFingerprint": "d" * 64,
    }
    value.update(overrides)
    return value


def test_source_seal_rejects_undeclared_fields():
    from scripts import acceptance_parallel_rollout as subject

    seal = _source_session(unexpected="unbound metadata")

    assert subject._valid_source_session(seal) is False


def test_source_seal_rejects_non_repo_relative_brief_path():
    from scripts import acceptance_parallel_rollout as subject

    seal = _source_session(briefPath="../outside.md")

    assert subject._valid_source_session(seal) is False


def _probe_payload(**overrides):
    payload = {
        "head_sha": COMMIT, "brief_fingerprint": "2" * 64,
        "contract_fingerprint": "3" * 64, "diff_fingerprint": "4" * 64,
        "policy_fingerprint": "5" * 64, "worktree_fingerprint": "d" * 64,
    }
    return {**payload, **overrides}


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
    source_seal = tmp_path / "source-seal.json"
    source_session = tmp_path / "source-session.json"
    manifest.write_text(json.dumps(_manifest_payload()), encoding="utf-8")
    contract.write_text(json.dumps(_contract_payload()), encoding="utf-8")
    source_seal.write_text(json.dumps(_source_session()), encoding="utf-8")
    source_session.write_text(json.dumps(_source_session()), encoding="utf-8")
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
        "--source-seal", str(source_seal),
        "--source-session", str(source_session),
        "--output", str(tmp_path.parent / f"{tmp_path.name}-rollout.json"),
    ]


def _passing_serial_control(*, run_index: int, **kwargs):
    return {
        "status": "PASS",
        "failureCode": None,
        "result": {"passed": 16, "failed": 0, "skipped": 0},
        "serialWallSeconds": 100.0,
        "artifactFingerprint": ("1" if run_index == 0 else "2") * 64,
        "executionLineage": kwargs.get("execution_lineage", {}),
    }


def test_execution_lineage_requires_matching_serial_and_each_parallel_shard():
    from scripts import acceptance_parallel_rollout as subject

    expected = {
        "runnerFingerprint": RUNNER,
        "environmentFingerprint": ENVIRONMENT,
        "datasetSnapshotFingerprint": DATASET,
    }
    serial = {"executionLineage": expected}
    shards = [{"lineage": expected}, {"lineage": {**expected, "datasetSnapshotFingerprint": "2" * 64}}]

    assert subject._execution_lineage_mismatch(serial, shards, expected) is True
    assert subject._execution_lineage_mismatch(serial, [{"lineage": expected}], expected) is False


def _passing_parallel_shards(*, shard_count: int, output_root, **kwargs):
    shards = []
    nodeids = sorted(_manifest_payload()["nodeids"])
    manifest_fingerprint = _manifest_payload()["manifestFingerprint"]
    execution_lineage = kwargs.get("execution_lineage", {})
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
            "lineage": execution_lineage,
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
        "executionLineage": execution_lineage,
    }
    aggregate = {**aggregate_unsigned, "evidenceFingerprint": canonical_fingerprint(aggregate_unsigned)}
    return {
        "status": "PASS",
        "failureCode": None,
        "parallelWallSeconds": 75.0,
        "aggregate": aggregate,
        "shards": shards,
        "shardArtifactPaths": [str(output_root / f"shard-{index}.json") for index in range(shard_count)],
        "cleanup": {
            "allProcessGroupsTerminated": True,
            "runtimeCleanupConfirmed": True,
            "fixtureRootsRemoved": True,
        },
    }


def test_cli_executes_real_parallel_controller_with_fresh_run_roots(tmp_path, monkeypatch, request):
    from backend.agents import acceptance_parallel_runner as parallel_runner
    from backend.agents import acceptance_shard_runtime
    from scripts import acceptance_parallel_rollout as subject
    from scripts import full_pytest_shard as shard_runner
    import socket

    completed_shards = []
    fixture_roots = []
    run_roots = []
    current_barriers = []
    nodeids_by_run = {}
    runtime_namespaces = []
    process_group_ids = []
    real_run_parallel_shards = subject.run_parallel_shards
    real_run_pytest_shard = parallel_runner.run_pytest_shard
    real_run_pytest_command = shard_runner._run_pytest_command
    held_sockets = []
    for _ in range(12 * 3):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reservation.bind(("127.0.0.1", 0))
        reservation.listen(1)
        held_sockets.append(reservation)
    available_ports = [reservation.getsockname()[1] for reservation in held_sockets]
    sockets_by_port = dict(zip(available_ports, held_sockets))
    port_sets = [
        {
            name: available_ports[allocation * 3 + offset]
            for offset, name in enumerate(("streamlit", "mcp", "health"))
        }
        for allocation in range(12)
    ]
    next_port_set = 0
    allocation_lock = threading.Lock()

    def deterministic_ports(run_id, shard_index):
        nonlocal next_port_set
        with allocation_lock:
            ports = port_sets[next_port_set]
            next_port_set += 1
        return dict(ports)

    def reserve_held_ports(ports, allocation_id):
        return ({name: sockets_by_port[port] for name, port in ports.items()}, {}, {})

    def close_held_sockets():
        for reservation in held_sockets:
            if reservation.fileno() >= 0:
                reservation.close()

    request.addfinalizer(close_held_sockets)
    monkeypatch.setattr(acceptance_shard_runtime, "_ports_for", deterministic_ports)
    monkeypatch.setattr(acceptance_shard_runtime, "_reserve_ports", reserve_held_ports)
    nodeids = [f"tests/test_parallel.py::test_case_{index}" for index in range(4)]
    test_root = tmp_path / "tests"
    test_root.mkdir()
    (tmp_path / "conftest.py").write_text(
        "def pytest_addoption(parser):\n"
        "    parser.addoption('--sandbox-preflight', choices=('required',))\n",
        encoding="utf-8",
    )
    test_definitions = ["from pathlib import Path", "import os", "from scripts import full_pytest_shard"]
    for index in range(4):
        test_definitions.extend([
            f"def test_case_{index}():",
            "    runtime = Path(os.environ['NBS_ACCEPTANCE_RUNTIME_ROOT'])",
            "    assert Path(os.environ['NBS_ANALYTICS_DB_FILE']).parent == runtime",
            "    assert Path(os.environ['NBS_ANALYTICS_CACHE_DIR']) == runtime / 'cache'",
            "    assert Path(os.environ['NBS_ANALYTICS_COORDINATION_DB']).parent == runtime",
            "    assert os.environ['NBS_ACCEPTANCE_PORT_HANDOFF_PROTOCOL'] == 'reserved-fd-v1'",
            "    assert full_pytest_shard._ACTIVATED_PORTS",
        ])
    (test_root / "test_parallel.py").write_text("\n".join(test_definitions) + "\n", encoding="utf-8")
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(part for part in (str(shard_runner.PROJECT_ROOT), existing_pythonpath) if part),
    )

    def serial_control(*, run_index, nodeids, execution_lineage, **kwargs):
        return {
            "status": "PASS",
            "failureCode": None,
            "result": {"passed": len(nodeids), "failed": 0, "skipped": 0},
            "serialWallSeconds": 100.0,
            "artifactFingerprint": str(run_index + 3) * 64,
            "executionLineage": execution_lineage,
        }

    def track_shard(**kwargs):
        fixture_roots.append(Path(kwargs["fixture_root"]))
        result = real_run_pytest_shard(**kwargs)
        completed_shards.append(kwargs["shard_index"])
        return result

    def capture_real_child_command(argv, **kwargs):
        environment = kwargs["env"]
        shard_index = int(environment["NBS_ACCEPTANCE_SHARD_INDEX"])
        expected = [
            nodeid for position, nodeid in enumerate(nodeids)
            if position % 4 == shard_index
        ]
        assert argv[-(len(expected) + 1):] == ["--", *expected]
        nodeids_by_run.setdefault(len(run_roots) - 1, []).extend(argv[-len(expected):])
        runtime = kwargs["runtime"]
        namespace = {
            "runtimeRoot": environment["NBS_ACCEPTANCE_RUNTIME_ROOT"],
            "db": environment["NBS_ANALYTICS_DB_FILE"],
            "cache": environment["NBS_ANALYTICS_CACHE_DIR"],
            "coordination": environment["NBS_ANALYTICS_COORDINATION_DB"],
            "processProfile": environment["NBS_ANALYTICS_PROCESS_PROFILE"],
            "ports": environment["NBS_ACCEPTANCE_PROFILE_PORTS"],
        }
        runtime_namespaces.append(namespace)
        register = runtime.register_process_group

        def capture_process_group(pid):
            process_group_ids.append(pid)
            return register(pid)

        monkeypatch.setattr(runtime, "register_process_group", capture_process_group)
        # The actual child-process launch calls from all four shards must
        # overlap before any one of them is allowed to proceed.
        current_barriers[-1].wait(timeout=3)
        return real_run_pytest_command(argv, **kwargs)

    def track_parallel_run(**kwargs):
        output_root = Path(kwargs["output_root"])
        assert not output_root.exists() and not output_root.is_symlink()
        assert tmp_path not in output_root.parents
        run_roots.append(output_root)
        current_barriers.append(threading.Barrier(kwargs["shard_count"], timeout=3))
        try:
            return real_run_parallel_shards(**kwargs)
        finally:
            current_barriers.pop()

    monkeypatch.setattr(subject, "_source_is_current", lambda *args, **kwargs: True)
    monkeypatch.setattr(subject, "run_serial_control", serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", track_parallel_run)
    monkeypatch.setattr(parallel_runner, "run_pytest_shard", track_shard)
    monkeypatch.setattr(shard_runner, "_run_pytest_command", capture_real_child_command)
    monkeypatch.setattr(parallel_runner, "_live_worker_capacity", lambda: 4)

    arguments = _argv(tmp_path, shard_count=4, repeats=3)
    manifest_path = Path(arguments[arguments.index("--manifest") + 1])
    manifest_path.write_text(json.dumps({
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
        "manifestFingerprint": _manifest_fingerprint(COMMIT, SOURCE, nodeids),
    }), encoding="utf-8")
    code = subject.main(arguments)

    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert code == 0
    assert rollout["status"] == "PASS"
    assert len(rollout["measuredRuns"]) == 3
    assert len(completed_shards) == 12
    assert len(run_roots) == len(set(run_roots)) == 3
    assert len(fixture_roots) == len(set(fixture_roots)) == 12
    assert all(not root.exists() and not root.is_symlink() for root in fixture_roots)
    assert len(runtime_namespaces) == len(process_group_ids) == 12
    assert len(set(process_group_ids)) == 12
    for field in ("runtimeRoot", "db", "cache", "coordination", "processProfile", "ports"):
        assert len({item[field] for item in runtime_namespaces}) == 12
    assert set(nodeids_by_run) == {0, 1, 2}
    for actual in nodeids_by_run.values():
        assert sorted(actual) == nodeids
        assert len(actual) == len(set(actual)) == len(nodeids)


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

    validated_roles = []
    validate_baseline = subject.validate_performance_baseline_v2

    def track_baseline(payload):
        validate_baseline(payload)
        validated_roles.append(payload["baselineRole"])

    monkeypatch.setattr(subject, "_source_is_current", lambda *a: True)
    monkeypatch.setattr(subject, "run_serial_control", _passing_serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", _passing_parallel_shards)
    monkeypatch.setattr(subject, "validate_performance_baseline_v2", track_baseline)
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))
    assert code == 0
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert rollout["status"] == "PASS"
    assert rollout["rolloutCandidate"] == "eligible"
    assert len(rollout["measuredRuns"]) == 3
    assert all(run["parity"]["status"] == "PASS" for run in rollout["measuredRuns"])
    assert validated_roles.count("serial_control") == 3
    assert validated_roles.count("parallel_candidate") == 3
    assert all(
        run["serialRuntimeArtifactFingerprint"] == run["serialLineage"]["runtimeArtifactFingerprint"]
        and run["parallelRuntimeArtifactFingerprint"] == run["parallelLineage"]["runtimeArtifactFingerprint"]
        for run in rollout["measuredRuns"]
    )


def test_cli_refuses_to_overwrite_existing_output(tmp_path):
    import pytest
    from scripts import acceptance_parallel_rollout as subject

    output = tmp_path / "rollout.json"
    output.write_text("keep this evidence", encoding="utf-8")
    with pytest.raises(ValueError, match="new regular file"):
        subject._write_output(output, {"status": "PASS"}, diagnostics_root=tmp_path, project_root=tmp_path / "repo")
    assert output.read_text(encoding="utf-8") == "keep this evidence"


def test_cli_refuses_dangling_symlink_output(tmp_path):
    from scripts import acceptance_parallel_rollout as subject

    output = tmp_path / "rollout.json"
    output.symlink_to(tmp_path / "missing-rollout.json")

    with pytest.raises(ValueError, match="new regular file"):
        subject._write_output(output, {"status": "PASS"}, diagnostics_root=tmp_path, project_root=tmp_path / "repo")
    assert output.is_symlink()
    assert not (tmp_path / "missing-rollout.json").exists()


def test_cli_refuses_symlinked_output_parent(tmp_path):
    from scripts import acceptance_parallel_rollout as subject

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="output parent"):
        subject._write_output(linked_parent / "rollout.json", {"status": "PASS"}, diagnostics_root=tmp_path, project_root=tmp_path / "repo")

    assert not (real_parent / "rollout.json").exists()


def test_cli_rejects_project_output_before_creating_runtime_directory(tmp_path):
    from scripts import acceptance_parallel_rollout as subject

    output = tmp_path / "nested" / "rollout.json"
    argv = _argv(tmp_path, shard_count=4, repeats=3)
    argv[argv.index("--output") + 1] = str(output)

    code = subject.main(argv)

    assert code == 2
    assert not output.exists()
    assert not (output.parent / f".{output.stem}-runtime").exists()


def test_cli_blocks_and_suppresses_speedup_when_one_shard_is_missing(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "_source_is_current", lambda *a: True)
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


def test_cli_skips_parallel_when_serial_population_is_incomplete(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    def incomplete_serial_control(*, run_index: int, **kwargs):
        return {
            "status": "FAIL",
            "failureCode": "serial_population_count_mismatch",
            "result": {"passed": 1, "failed": 0, "skipped": 0},
            "serialWallSeconds": 0.001,
            "artifactFingerprint": ("3" if run_index == 0 else "4") * 64,
        }

    def unexpected_parallel(**kwargs):
        raise AssertionError("parallel execution must be skipped for incomplete serial coverage")

    monkeypatch.setattr(subject, "run_serial_control", incomplete_serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", unexpected_parallel)
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))

    assert code == 2
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert rollout["status"] == "BLOCKED"
    assert all(
        run["failureCode"] == "serial_population_count_mismatch"
        for run in rollout["measuredRuns"]
    )
    assert rollout["speedup"] is None


def test_cli_blocks_unsupported_platform_before_selecting_parallel_runner(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject.sys, "platform", "win32")
    monkeypatch.setattr(subject, "_source_is_current", lambda *a: True)
    monkeypatch.setattr(subject, "run_serial_control", _passing_serial_control)
    monkeypatch.setattr(
        subject, "run_parallel_shards",
        lambda **kwargs: pytest.fail("unsupported platforms must not select the parallel runner"),
    )

    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())

    assert code == 2
    assert rollout["status"] == "BLOCKED"
    assert rollout["speedup"] is None
    assert "unsupported_platform" in rollout["blockers"]
    assert "duration_invalid" not in rollout["blockers"]
    assert all(run["parallelStatus"] == "BLOCKED" for run in rollout["measuredRuns"])


@pytest.mark.parametrize(
    ("target", "invalid_value"),
    [
        ("serial", 0), ("serial", None), ("serial", float("nan")),
        ("serial", float("inf")), ("parallel", 0), ("parallel", None),
        ("parallel", float("nan")), ("parallel", float("inf")),
    ],
)
def test_cli_blocks_missing_or_nonpositive_timing_without_fabricating_duration(
    tmp_path, monkeypatch, target, invalid_value,
):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "_source_is_current", lambda *a: True)

    def serial_control(*, run_index: int, **kwargs):
        result = _passing_serial_control(run_index=run_index, **kwargs)
        if target == "serial":
            result["serialWallSeconds"] = invalid_value
        return result

    def parallel_shards(**kwargs):
        result = _passing_parallel_shards(**kwargs)
        if target == "parallel":
            if invalid_value is None:
                result.pop("parallelWallSeconds")
            else:
                result["parallelWallSeconds"] = invalid_value
        return result

    monkeypatch.setattr(subject, "run_serial_control", serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", parallel_shards)
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))

    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert code == 2
    assert rollout["status"] == "BLOCKED"
    assert rollout["speedup"] is None
    assert "duration_invalid" in rollout["blockers"]
    for run in rollout["measuredRuns"]:
        field = "serialWallSeconds" if target == "serial" else "parallelWallSeconds"
        status_field = "serialStatus" if target == "serial" else "parallelStatus"
        assert run[field] is None
        assert run[status_field] == "BLOCKED"
        assert run["parity"]["status"] == "BLOCKED"
        assert run["failureCode"] == "duration_invalid"


def test_cli_records_blocked_rollout_when_parallel_runner_raises(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "_source_is_current", lambda *a: True)
    monkeypatch.setattr(subject, "run_serial_control", _passing_serial_control)
    monkeypatch.setattr(subject, "run_parallel_shards", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("runner crashed")))
    code = subject.main(_argv(tmp_path, shard_count=4, repeats=3))

    assert code == 2
    rollout = json.loads((tmp_path.parent / f"{tmp_path.name}-rollout.json").read_text())
    assert rollout["status"] == "BLOCKED"
    assert len(rollout["measuredRuns"]) == 3
    assert all(run["failureCode"] == "parallel_runner_error" for run in rollout["measuredRuns"])
    assert all(
        run["parallelLineage"]["runtimeExecutionLineage"] == {
            "observation": "unobserved",
            "failureCode": "parallel_runner_error",
            "commitSha": COMMIT,
            "sourceFingerprint": SOURCE,
        }
        for run in rollout["measuredRuns"]
    )
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
    assert result["executionLineage"] == {
        "observation": "unobserved",
        "failureCode": "serial_source_identity_mismatch",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
    }


def test_serial_control_requires_a_source_seal_for_matching_clean_identity(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, ""))
    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_source_seal_required"


def test_serial_control_accepts_only_an_exact_source_seal_for_dirty_worktree(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            return {"status": "PASS", "allProcessGroupsTerminated": True}

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, " M approved.py\n"))
    monkeypatch.setattr(subject, "git_source_probe", lambda *a, **kw: _probe_payload())
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())
    commands = []

    def run_pytest(argv, **kwargs):
        commands.append(argv)
        kwargs["readiness_callback"]()
        return subject.subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr(subject, "_run_pytest_command", run_pytest)

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal=_source_session(),
        expected_source_session=_source_session(),
        execution_lineage={
            "runnerFingerprint": RUNNER,
            "environmentFingerprint": ENVIRONMENT,
            "datasetSnapshotFingerprint": DATASET,
        },
    )

    assert result["status"] == "PASS"
    assert commands[0][-2:] == ["--", "tests/test_parallel.py::test_one"]


def test_serial_control_allocates_three_fresh_fixture_roots(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    fixture_roots = []

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            return {"status": "PASS", "allProcessGroupsTerminated": True}

    def allocate(**kwargs):
        fixture_root = Path(kwargs["fixture_root"])
        assert not fixture_root.exists() and not fixture_root.is_symlink()
        fixture_roots.append(fixture_root)
        return Runtime()

    monkeypatch.setattr(subject, "_source_is_current", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        subject,
        "_current_source_identity",
        lambda project_root: (COMMIT, SOURCE, " M isolated-test.py\n"),
    )
    monkeypatch.setattr(subject, "allocate_shard_runtime", allocate)

    def run_pytest(argv, **kwargs):
        kwargs["readiness_callback"]()
        return subject.subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr(
        subject,
        "_run_pytest_command",
        run_pytest,
    )

    results = [
        subject.run_serial_control(
            project_root=tmp_path,
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            nodeids=["tests/test_parallel.py::test_one"],
            run_index=run_index,
            timeout_seconds=1,
            source_seal=_source_session(),
            expected_source_session=_source_session(),
            execution_lineage={
                "runnerFingerprint": RUNNER,
                "environmentFingerprint": ENVIRONMENT,
                "datasetSnapshotFingerprint": DATASET,
            },
        )
        for run_index in range(3)
    ]

    assert all(result["status"] == "PASS" for result in results), [
        (result["status"], result["failureCode"], result["result"])
        for result in results
    ]
    assert len(fixture_roots) == len(set(fixture_roots)) == 3
    assert all(not root.exists() and not root.is_symlink() for root in fixture_roots)


def test_serial_control_rejects_summary_that_does_not_cover_manifest_population(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            return {"status": "PASS", "allProcessGroupsTerminated": True}

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, " M approved.py\n"))
    monkeypatch.setattr(subject, "git_source_probe", lambda *a, **kw: _probe_payload())
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())

    def run_pytest(argv, **kwargs):
        kwargs["readiness_callback"]()
        return subject.subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr(
        subject,
        "_run_pytest_command",
        run_pytest,
    )

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one", "tests/test_parallel.py::test_two"],
        run_index=0,
        timeout_seconds=1,
        source_seal=_source_session(),
        expected_source_session=_source_session(),
        execution_lineage={
            "runnerFingerprint": RUNNER,
            "environmentFingerprint": ENVIRONMENT,
            "datasetSnapshotFingerprint": DATASET,
        },
    )

    assert result["status"] == "FAIL"
    assert result["failureCode"] == "serial_population_count_mismatch"
    assert result["result"]["passed"] + result["result"]["failed"] + result["result"]["skipped"] == 1


def test_verification_session_source_fingerprint_drift_blocks_execution(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            return {"status": "PASS", "allProcessGroupsTerminated": True}

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, "a" * 64, " M implementation.py\n"))
    monkeypatch.setattr(subject, "git_source_probe", lambda *a, **kw: _probe_payload())
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())
    monkeypatch.setattr(
        subject,
        "_run_pytest_command",
        lambda *args, **kwargs: subject.subprocess.CompletedProcess(args[0], 0, "1 passed in 0.01s\n", ""),
    )

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal=_source_session(),
        expected_source_session=_source_session(),
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_source_identity_mismatch"


def test_serial_control_requires_an_external_expected_source_session(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, " M approved.py\n"))
    monkeypatch.setattr(subject, "git_source_probe", lambda *a, **kw: _probe_payload())

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal=_source_session(),
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_source_session_required"


def test_serial_control_rejects_mismatched_source_session_lineage(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, " M approved.py\n"))
    monkeypatch.setattr(subject, "git_source_probe", lambda *a, **kw: _probe_payload())
    expected = _source_session()
    seal = _source_session(diffFingerprint="6" * 64)

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal=seal,
        expected_source_session=expected,
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_source_identity_mismatch"


@pytest.mark.parametrize("drift", [None, "brief_fingerprint", "diff_fingerprint", "policy_fingerprint"])
def test_real_source_seal_uses_canonical_worktree_fingerprint_for_serial_control(monkeypatch, drift):
    from backend.agents.verification_chain import git_source_probe
    from backend.agents.verification_session import VerificationSession
    from scripts import acceptance_parallel_rollout as subject
    from types import SimpleNamespace

    project_root = Path(__file__).resolve().parents[1]
    commit_sha, _, _ = subject._current_source_identity(project_root)
    brief = "docs/superpowers/specs/2026-09-15-acceptance-parallel-rollout-and-speedup-design.md"
    probe = git_source_probe(
        project_root, brief_path=brief, base_sha=commit_sha,
        contract_path="docs/agents/REVIEW_AGENT_CONTRACT.md",
        policy_path="agent_config/token_budgets.json",
    )
    session = VerificationSession.create(
        project_id="test", base_sha=commit_sha, brief_path=brief,
        **{key: value for key, value in probe.items() if key != "source_probe_version"},
    )
    calls = []

    def execution(argv, **kwargs):
        calls.append(argv)
        kwargs["readiness_callback"]()
        return subject.subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\\n", "")

    runtime = SimpleNamespace(
        environment=lambda: {},
        cleanup=lambda: {"status": "PASS", "allProcessGroupsTerminated": True},
    )
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kw: runtime)
    monkeypatch.setattr(subject, "_run_pytest_command", execution)
    if drift:
        monkeypatch.setattr(subject, "git_source_probe", lambda *a, **kw: {**probe, drift: "9" * 64})
    result = subject.run_serial_control(
        project_root=project_root, commit_sha=commit_sha,
        source_fingerprint=session.source_fingerprint, nodeids=["tests/test_parallel.py::test_one"],
        run_index=0, timeout_seconds=1, source_seal=session.to_dict(),
        expected_source_session=session.to_dict(),
        execution_lineage={
            "runnerFingerprint": RUNNER,
            "environmentFingerprint": ENVIRONMENT,
            "datasetSnapshotFingerprint": DATASET,
        },
    )
    assert result["status"] == ("BLOCKED" if drift else "PASS")
    assert len(calls) == (0 if drift else 1)


def test_serial_control_converts_cleanup_exception_to_blocked_evidence(tmp_path, monkeypatch):
    from scripts import acceptance_parallel_rollout as subject

    class Runtime:
        def environment(self):
            return {}

        def cleanup(self):
            raise RuntimeError("cleanup unavailable")

    monkeypatch.setattr(subject, "_current_source_identity", lambda project_root: (COMMIT, SOURCE, ""))
    monkeypatch.setattr(subject, "git_source_probe", lambda *a, **kw: _probe_payload())
    monkeypatch.setattr(subject, "allocate_shard_runtime", lambda **kwargs: Runtime())

    result = subject.run_serial_control(
        project_root=tmp_path,
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        nodeids=["tests/test_parallel.py::test_one"],
        run_index=0,
        timeout_seconds=1,
        source_seal=_source_session(schemaVersion="source-seal-v1"),
        expected_source_session=_source_session(schemaVersion="source-seal-v1"),
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "isolation_violation"
    assert result["cleanup"]["status"] == "BLOCKED"


def test_parallel_rollout_rejects_unbounded_timeout(tmp_path):
    from scripts import acceptance_parallel_rollout as subject
    from backend.agents.acceptance_parallel_rollout import MAX_DURATION_SECONDS

    with pytest.raises(ValueError, match="timeout is invalid"):
        subject.run_parallel_rollout(
            project_root=tmp_path,
            manifest={},
            contract={},
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            runner_fingerprint=RUNNER,
            environment_fingerprint=ENVIRONMENT,
            baseline_family_id="acceptance-full-serial-ci-v1",
            timeout_seconds=MAX_DURATION_SECONDS + 1,
        )


def test_parallel_rollout_rejects_caller_supplied_worker_capability(tmp_path):
    from scripts import acceptance_parallel_rollout as subject

    with pytest.raises(ValueError, match="caller-supplied runner max workers are not trusted"):
        subject.run_parallel_rollout(
            project_root=tmp_path,
            manifest={},
            contract={},
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            runner_fingerprint=RUNNER,
            environment_fingerprint=ENVIRONMENT,
            baseline_family_id="acceptance-full-serial-ci-v1",
            runner_max_workers=4,
        )


def test_parallel_rollout_requires_source_seal_pair(tmp_path):
    from scripts import acceptance_parallel_rollout as subject

    with pytest.raises(ValueError, match="source seal and expected source session are required"):
        subject.run_parallel_rollout(
            project_root=tmp_path,
            manifest={},
            contract={},
            commit_sha=COMMIT,
            source_fingerprint=SOURCE,
            runner_fingerprint=RUNNER,
            environment_fingerprint=ENVIRONMENT,
            baseline_family_id="acceptance-full-serial-ci-v1",
        )


def test_rollout_output_must_stay_inside_validated_diagnostic_root(tmp_path):
    from scripts import acceptance_parallel_rollout as subject

    project = tmp_path / "repo"
    project.mkdir()
    diagnostic_root = tmp_path / "diagnostics"
    diagnostic_root.mkdir()
    with pytest.raises(ValueError, match="output must stay inside the diagnostic root"):
        subject._write_output(
            tmp_path / "outside.json", {"status": "BLOCKED"},
            diagnostics_root=diagnostic_root, project_root=project,
        )


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


def test_v2_comparison_rejects_non_positive_candidate_total():
    from backend.agents.acceptance_performance_v2 import compare_performance_baselines_v2

    baseline = _qualified_v2(role="serial_control", total=100.0)
    candidate = _qualified_v2(role="parallel_candidate", total=4.0)
    candidate["stages"]["totalWallSeconds"] = 0.0
    candidate["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in candidate.items() if key != "evidenceFingerprint"}
    )
    result = compare_performance_baselines_v2(baseline, candidate)
    assert result["status"] == "not_compared"
    assert result["reason"] == "candidate_total_non_positive"
    assert "totalSpeedRatio" not in result
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
