import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from collections.abc import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents.acceptance_contract import contract_fingerprint, validate_acceptance_contract
from backend.agents.acceptance_parallel_rollout import (
    build_parallel_rollout_evidence,
    validate_parallel_rollout_evidence,
)
from backend.agents.acceptance_performance import MAX_COUNT, MAX_DURATION_SECONDS
from backend.agents.acceptance_performance_v2 import (
    build_execution_performance_v2 as _build_v2_artifact,
    build_unobserved_runtime_lineage,
    validate_performance_baseline_v2,
)
from backend.agents.acceptance_shard_runtime import allocate_shard_runtime
from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.verification_chain import git_source_probe
from backend.agents.verification_session import VerificationSession
from scripts.full_pytest_gate import _parse_summary
from scripts.full_pytest_shard_aggregate import aggregate_pytest_shards, compare_serial_and_shard, validate_shard_set
from scripts.full_pytest_shard import (
    _ExecutionEvidenceChannel,
    _build_execution_binding,
    _read_child_execution_evidence,
    _run_pytest_command,
    _validate_manifest,
)
from scripts.acceptance_shard_canary import _port_readiness
from scripts.pytest_manifest import _manifest_fingerprint
from backend.agents.acceptance_parallel_runner import (
    MAX_TIMEOUT_SECONDS, _validate_output_root, _validate_runner_capability, build_runner_capability_receipt,
    observe_runtime_fingerprints, run_parallel_shards,
)


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_SHARDS = frozenset({2, 4, 8, 16})
_MAX_JSON_BYTES = 1024 * 1024
_SUPPORTED_PARALLEL_PLATFORMS = frozenset({"darwin", "linux"})
_SOURCE_BRIEF = "docs/agents/ACCEPTANCE_PARALLEL_ROLLOUT_RUNBOOK.md"


def _read_json(path):
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file(): raise ValueError("input must be a regular file")
    if candidate.stat().st_size > _MAX_JSON_BYTES: raise ValueError("input exceeds size cap")
    value = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError("input JSON must be an object")
    return value


def _require_sha(value, pattern, field):
    if not isinstance(value, str) or pattern.fullmatch(value) is None: raise ValueError(f"{field} is invalid")


def _safe_counts(value):
    if not isinstance(value, Mapping):
        raise ValueError("pytest counts are invalid")
    result = {}
    for field in ("passed", "failed", "skipped"):
        item = value.get(field, 0)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_COUNT:
            raise ValueError("pytest counts are invalid")
        result[field] = item
    return result


def _valid_measured_duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    if not math.isfinite(seconds) or not 0.0 < seconds <= MAX_DURATION_SECONDS:
        return None
    return seconds


def _serial_fixture_root():
    return Path(tempfile.gettempdir()) / f"nbs-parallel-serial-{uuid.uuid4().hex}"


def _serial_artifact(*, run_index, status, failure_code, commit_sha, source_fingerprint, result, wall_seconds, cleanup, execution_lineage=None):
    measured_seconds = _valid_measured_duration(wall_seconds)
    if measured_seconds is None:
        status = "BLOCKED"
        failure_code = failure_code or "duration_invalid"
    unsigned = {"schemaVersion": "parallel-serial-control-v1", "status": status, "runIndex": run_index,
                "commitSha": commit_sha, "sourceFingerprint": source_fingerprint, "result": dict(result),
                "serialWallSeconds": measured_seconds, "failureCode": failure_code, "cleanup": dict(cleanup),
                "executionLineage": dict(execution_lineage or {})}
    return {**unsigned, "artifactFingerprint": canonical_fingerprint(unsigned)}


def _blocked_parity(failure_code):
    unsigned = {
        "schemaVersion": "serial-shard-parity-v1",
        "status": "BLOCKED",
        "failureCode": failure_code,
        "serial": {},
        "shard": {},
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _current_source_identity(project_root, *, base_sha=None, brief_path=None):
    root = Path(project_root).resolve()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=False)
    commit = head.stdout.strip()
    if head.returncode != 0 or not _SHA40.fullmatch(commit):
        raise RuntimeError("current commit identity is unavailable")
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", ".", ":(exclude)docs/superpowers", ":(exclude).superpowers"], cwd=root, capture_output=True, text=True, check=False)
    if status.returncode != 0:
        raise RuntimeError("current source status is unavailable")
    source_fingerprint = None
    if base_sha is not None and brief_path is not None:
        probe = git_source_probe(
            root,
            brief_path=brief_path,
            base_sha=base_sha,
            contract_path="docs/agents/REVIEW_AGENT_CONTRACT.md",
            policy_path="agent_config/token_budgets.json",
        )
        source_session = VerificationSession.create(
            project_id="acceptance-parallel-rollout-live",
            base_sha=base_sha,
            brief_path=brief_path,
            **{key: value for key, value in probe.items() if key != "source_probe_version"},
        )
        source_fingerprint = source_session.source_fingerprint
    return commit, source_fingerprint, status.stdout


_SOURCE_SESSION_FIELDS = (
    "baseSha", "briefPath", "briefFingerprint", "contractFingerprint",
    "diffFingerprint", "policyFingerprint", "headSha", "sourceFingerprint",
    "worktreeFingerprint",
)
_SOURCE_SESSION_SHA64_FIELDS = (
    "briefFingerprint", "contractFingerprint", "diffFingerprint",
    "policyFingerprint", "sourceFingerprint", "worktreeFingerprint",
)


def _normalized_source_session(value):
    if not isinstance(value, Mapping):
        raise ValueError("source session is required")
    if value.get("schemaVersion") == "verification-session-v1":
        session = VerificationSession.from_dict(dict(value))
        return {**session.to_dict(), "sourceFingerprint": session.source_fingerprint}
    if value.get("schemaVersion") != "source-seal-v1":
        raise ValueError("source session schema is invalid")
    if set(value) != {"schemaVersion", *_SOURCE_SESSION_FIELDS}:
        raise ValueError("source seal schema keys are invalid")
    return dict(value)


def _valid_source_session(value):
    try:
        value = _normalized_source_session(value)
    except (TypeError, ValueError):
        return False
    if not all(isinstance(value.get(field), str) and value[field] for field in _SOURCE_SESSION_FIELDS):
        return False
    brief_path = value["briefPath"]
    path = Path(brief_path)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != brief_path or "\\" in brief_path:
        return False
    return (
        _SHA40.fullmatch(value["baseSha"]) is not None
        and _SHA40.fullmatch(value["headSha"]) is not None
        and all(_SHA64.fullmatch(value[field]) is not None for field in _SOURCE_SESSION_SHA64_FIELDS)
    )


def _matches_source_seal(project_root, source_seal, *, expected_source_session, commit_sha, source_fingerprint, actual_commit, actual_source_fingerprint=None):
    if not _valid_source_session(source_seal) or not _valid_source_session(expected_source_session):
        return False
    seal = _normalized_source_session(source_seal)
    expected = _normalized_source_session(expected_source_session)
    if any(seal[field] != expected[field] for field in _SOURCE_SESSION_FIELDS):
        return False
    if (seal["headSha"] != commit_sha or commit_sha != actual_commit
            or seal["sourceFingerprint"] != source_fingerprint
            or (actual_source_fingerprint is not None and actual_source_fingerprint != source_fingerprint)):
        return False
    probe = git_source_probe(
        project_root, brief_path=seal["briefPath"], base_sha=seal["baseSha"],
        head_ref="WORKTREE", contract_path="docs/agents/REVIEW_AGENT_CONTRACT.md",
        policy_path="agent_config/token_budgets.json",
    )
    fields = {
        "headSha": "head_sha", "briefFingerprint": "brief_fingerprint",
        "worktreeFingerprint": "worktree_fingerprint", "diffFingerprint": "diff_fingerprint",
        "contractFingerprint": "contract_fingerprint", "policyFingerprint": "policy_fingerprint",
    }
    return all(seal[field] == probe[key] for field, key in fields.items())


def _source_is_current(root, seal, expected, commit_sha, source_fingerprint):
    try:
        expected_session = _normalized_source_session(expected)
        try:
            actual_commit, actual_source_fingerprint, _ = _current_source_identity(
                root,
                base_sha=expected_session["baseSha"],
                brief_path=expected_session["briefPath"],
            )
        except TypeError:
            # Keep test doubles and older injected probes source-bound; the
            # production implementation above always recomputes the fingerprint.
            actual_commit, actual_source_fingerprint, _ = _current_source_identity(root)
        return _matches_source_seal(
            root, seal, expected_source_session=expected, commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, actual_commit=actual_commit,
            actual_source_fingerprint=actual_source_fingerprint,
        )
    except (
        OSError, ValueError, RuntimeError, subprocess.SubprocessError,
        KeyError, TypeError, AttributeError, IndexError,
    ):
        return False


def run_serial_control(*, project_root, commit_sha, source_fingerprint, nodeids, run_index, timeout_seconds, source_seal=None, expected_source_session=None, execution_lineage=None, runner_capability=None, acceptance_contract=None):
    runtime = None
    execution_channel = None
    started = time.perf_counter()
    execution_start = []
    fixture_root = _serial_fixture_root()
    status = "BLOCKED"
    failure_code = None
    result = {"passed": 0, "failed": 0, "skipped": 0}
    cleanup = {"status": "PASS", "allProcessGroupsTerminated": True}
    execution_lineage_validated = False
    try:
        root = Path(project_root).resolve()
        actual_commit, _, dirty = _current_source_identity(root)
        if source_seal is not None:
            if not _source_is_current(root, source_seal, expected_source_session, commit_sha, source_fingerprint):
                failure_code = "serial_source_session_required" if expected_source_session is None else "serial_source_identity_mismatch"
        elif dirty:
            failure_code = "serial_source_dirty"
        elif actual_commit != commit_sha:
            failure_code = "serial_source_identity_mismatch"
        else:
            failure_code = "serial_source_seal_required"
        if failure_code is None:
            try:
                validate_acceptance_contract(acceptance_contract)
                observed_runtime = observe_runtime_fingerprints(root)
                expected_execution_fields = {
                    "runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint",
                }
                if (
                    not isinstance(execution_lineage, Mapping)
                    or set(execution_lineage) != expected_execution_fields
                    or not all(
                        isinstance(value, str) and _SHA64.fullmatch(value)
                        for value in execution_lineage.values()
                    )
                    or not isinstance(observed_runtime, Mapping)
                    or not all(
                        isinstance(observed_runtime.get(field), str)
                        and _SHA64.fullmatch(observed_runtime[field])
                        for field in ("runnerFingerprint", "environmentFingerprint")
                    )
                ):
                    failure_code = "serial_execution_lineage_unobserved"
                elif (
                    execution_lineage["runnerFingerprint"] != observed_runtime["runnerFingerprint"]
                    or execution_lineage["environmentFingerprint"] != observed_runtime["environmentFingerprint"]
                    or execution_lineage["datasetSnapshotFingerprint"]
                    != acceptance_contract["datasetSnapshotFingerprint"]
                ):
                    failure_code = "serial_execution_lineage_mismatch"
                else:
                    _validate_runner_capability(
                        runner_capability, observed_runtime["runnerFingerprint"],
                    )
                    execution_lineage_validated = True
            except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                failure_code = "serial_execution_lineage_unobserved"
        if failure_code is None:
            runtime = allocate_shard_runtime(
                project_root=root,
                run_id=f"parallel-serial-{uuid.uuid4().hex}",
                shard_index=0,
                fixture_root=fixture_root,
            )
            environment = dict(os.environ)
            environment.update(runtime.environment())
            if not nodeids or len(nodeids) != len(set(nodeids)) or any(not isinstance(nodeid, str) or not nodeid for nodeid in nodeids):
                failure_code = "serial_manifest_population_invalid"
            else:
                manifest_fingerprint = _manifest_fingerprint(
                    commit_sha, source_fingerprint, sorted(nodeids),
                )
                execution_binding = _build_execution_binding(
                    commit_sha=commit_sha,
                    source_fingerprint=source_fingerprint,
                    manifest_fingerprint=manifest_fingerprint,
                    shard_index=0,
                    shard_count=1,
                    assigned_nodeids=nodeids,
                )
                execution_channel = _ExecutionEvidenceChannel()
                execution_channel.configure(environment, execution_binding)
                completed = _run_pytest_command(
                    [sys.executable, "-m", "pytest", "-q", "--sandbox-preflight", "required", "--", *nodeids],
                    cwd=root,
                    env=environment,
                    timeout=timeout_seconds, runtime=runtime,
                    readiness_callback=lambda: execution_start.append(time.perf_counter()),
                    readiness_probe=_port_readiness,
                )
                try:
                    execution_evidence = execution_channel.read(execution_binding)
                except (OSError, ValueError, TypeError):
                    failure_code = "serial_child_execution_evidence_invalid"
                else:
                    if (
                        sorted(execution_evidence["collectedNodeids"]) != sorted(nodeids)
                        or sorted(execution_evidence["startedNodeids"]) != sorted(nodeids)
                    ):
                        failure_code = "serial_collection_mismatch"
                if failure_code is None:
                    parsed = _parse_summary(f"{completed.stdout}\n{completed.stderr}")
                    result = _safe_counts(parsed)
                    if completed.returncode != 0 or result["failed"] != 0:
                        failure_code = "serial_control_failed"
                    elif sum(result.values()) != len(nodeids):
                        failure_code = "serial_population_count_mismatch"
                status = "PASS" if failure_code is None else (
                    "BLOCKED"
                    if failure_code in {
                        "serial_child_execution_evidence_invalid",
                        "serial_collection_mismatch",
                    }
                    else "FAIL"
                )
    except subprocess.TimeoutExpired:
        failure_code = "serial_control_timeout"
    except (OSError, RuntimeError, ValueError):
        failure_code = "serial_control_blocked"
    finally:
        if execution_channel is not None:
            execution_channel.close()
        if runtime is not None:
            try:
                cleanup = runtime.cleanup()
            except Exception as exc:
                cleanup = {
                    "status": "BLOCKED",
                    "failureCode": "isolation_violation",
                    "allProcessGroupsTerminated": False,
                    "error": str(exc)[:256],
                }
                failure_code = failure_code or "isolation_violation"
                status = "BLOCKED"
    elapsed = time.perf_counter() - (execution_start[0] if execution_start else started)
    if cleanup.get("status") != "PASS":
        status = "BLOCKED"
        failure_code = "isolation_violation"
    expected_execution_fields = {
        "runnerFingerprint", "environmentFingerprint", "datasetSnapshotFingerprint",
    }
    capability_observed = False
    if isinstance(execution_lineage, Mapping):
        try:
            _validate_runner_capability(
                runner_capability, execution_lineage.get("runnerFingerprint"),
            )
            capability_observed = True
        except (TypeError, ValueError):
            capability_observed = False
    execution_lineage_observed = (
        bool(execution_start)
        and execution_lineage_validated
        and capability_observed
        and isinstance(execution_lineage, Mapping)
        and set(execution_lineage) == expected_execution_fields
        and all(isinstance(value, str) and _SHA64.fullmatch(value) for value in execution_lineage.values())
    )
    if not execution_lineage_observed:
        failure_code = failure_code or (
            "serial_execution_lineage_unobserved"
            if execution_start else "serial_execution_not_started"
        )
        status = "BLOCKED"
        execution_lineage = build_unobserved_runtime_lineage(
            failure_code=failure_code,
            commit_sha=commit_sha,
            source_fingerprint=source_fingerprint,
        )
    return _serial_artifact(run_index=run_index, status=status, failure_code=failure_code,
                            commit_sha=commit_sha, source_fingerprint=source_fingerprint,
                            result=result, wall_seconds=elapsed, cleanup=cleanup,
                            execution_lineage=execution_lineage)


def _blocked_aggregate(
    parallel, *, commit_sha, source_fingerprint, manifest_fingerprint, shard_count,
):
    unsigned = {"status": "BLOCKED", "failureCode": parallel.get("failureCode", "shard_set_incomplete"),
                "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
                "manifestFingerprint": manifest_fingerprint, "shardCount": shard_count,
                "result": {"passed": 0, "failed": 0, "skipped": 0}, "shards": []}
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _execution_lineage_mismatch(serial, shards, expected):
    """Fail closed unless serial and every shard independently report the expected run lineage."""
    if not isinstance(serial, Mapping) or dict(serial.get("executionLineage", {})) != dict(expected):
        return True
    if not isinstance(shards, list) or not shards:
        return True
    return any(
        not isinstance(shard, Mapping)
        or not isinstance(shard.get("lineage"), Mapping)
        or dict(shard["lineage"]) != dict(expected)
        for shard in shards
    )


def _blocked_parallel_result(*, commit_sha, source_fingerprint, manifest_fingerprint, shard_count, failure_code, wall_seconds, error=None):
    measured_seconds = _valid_measured_duration(wall_seconds)
    result = {"status": "BLOCKED", "failureCode": failure_code, "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint, "manifestFingerprint": manifest_fingerprint,
        "shardCount": shard_count, "shards": [], "parallelWallSeconds": measured_seconds,
        "cleanup": {"allProcessGroupsTerminated": False}}
    if error:
        result["error"] = error[:256]
    return result


def run_parallel_rollout(*, project_root, manifest, contract, commit_sha, source_fingerprint, runner_fingerprint,
                         environment_fingerprint, baseline_family_id, shard_count=4, repeats=3,
                         timeout_seconds=1800, output_root=None, source_seal=None, expected_source_session=None,
                         runner_max_workers=None):
    _require_sha(commit_sha, _SHA40, "commitSha")
    _require_sha(source_fingerprint, _SHA64, "sourceFingerprint")
    _require_sha(runner_fingerprint, _SHA64, "runnerFingerprint")
    _require_sha(environment_fingerprint, _SHA64, "environmentFingerprint")
    if shard_count not in _ALLOWED_SHARDS or isinstance(shard_count, bool):
        raise ValueError("shard count is invalid")
    if repeats != 3 or isinstance(repeats, bool):
        raise ValueError("repeats must be exactly 3")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds)) or not 0.0 < float(timeout_seconds) <= MAX_TIMEOUT_SECONDS):
        raise ValueError("timeout is invalid")
    if runner_max_workers is not None:
        raise ValueError("caller-supplied runner max workers are not trusted")
    if source_seal is None or expected_source_session is None:
        raise ValueError("source seal and expected source session are required")
    if not _valid_source_session(source_seal) or not _valid_source_session(expected_source_session):
        raise ValueError("source seal and expected source session are invalid")
    observed_runtime = observe_runtime_fingerprints(project_root)
    if (
        runner_fingerprint != observed_runtime["runnerFingerprint"]
        or environment_fingerprint != observed_runtime["environmentFingerprint"]
    ):
        raise ValueError("caller lineage fingerprints do not match live runtime identity")
    initial_runner_capability = build_runner_capability_receipt(observed_runtime["runnerFingerprint"])
    if shard_count > initial_runner_capability["maxWorkers"]:
        raise ValueError("shard count exceeds verified live runner capacity")
    validate_acceptance_contract(contract)
    manifest_commit, manifest_source, nodeids, manifest_fingerprint = _validate_manifest(manifest)
    if manifest_commit != commit_sha or manifest_source != source_fingerprint:
        raise ValueError("manifest identity mismatch")
    population_fingerprint = canonical_fingerprint({"nodeids": sorted(nodeids)})
    contract_fp = contract_fingerprint(contract)
    dataset_fp = contract["datasetSnapshotFingerprint"]
    lineage = {
        "contractFingerprint": contract_fp,
        "baselineFamilyId": baseline_family_id,
        "manifestFingerprint": manifest_fingerprint,
        "testPopulationFingerprint": population_fingerprint,
        "runnerFingerprint": observed_runtime["runnerFingerprint"],
        "environmentFingerprint": observed_runtime["environmentFingerprint"],
        "datasetSnapshotFingerprint": dataset_fp,
        "selectionMode": "full",
    }
    root = Path(project_root).resolve()
    diagnostics_root = _validate_output_root(
        root,
        Path(output_root or (Path(tempfile.gettempdir()) / f"nbs-parallel-rollout-{uuid.uuid4().hex}")),
    )
    measured_runs = []
    performance_identity = dict(
        commit_sha=commit_sha, source_fingerprint=source_fingerprint,
        baseline_family_id=baseline_family_id, lineage=lineage,
    )
    source_identity = dict(commit_sha=commit_sha, source_fingerprint=source_fingerprint)
    parallel_identity = dict(**source_identity, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
    for run_index in range(repeats):
        run_root = diagnostics_root / f"run-{run_index}"
        observed_runtime = observe_runtime_fingerprints(project_root)
        runtime_lineage_matches = (
            observed_runtime.get("runnerFingerprint") == runner_fingerprint
            and observed_runtime.get("environmentFingerprint") == environment_fingerprint
        )
        runner_capability = (
            build_runner_capability_receipt(observed_runtime["runnerFingerprint"])
            if runtime_lineage_matches else dict(initial_runner_capability)
        )
        run_lineage = dict(lineage)
        run_performance_identity = {
            **performance_identity,
            "lineage": run_lineage,
        }
        if runtime_lineage_matches:
            serial = run_serial_control(project_root=root, commit_sha=commit_sha,
                                        source_fingerprint=source_fingerprint, nodeids=nodeids,
                                        run_index=run_index, timeout_seconds=timeout_seconds,
                                        source_seal=source_seal, expected_source_session=expected_source_session,
                                        acceptance_contract=contract,
                                        execution_lineage={
                                            "runnerFingerprint": observed_runtime["runnerFingerprint"],
                                            "environmentFingerprint": observed_runtime["environmentFingerprint"],
                                            "datasetSnapshotFingerprint": dataset_fp,
                                        }, runner_capability=runner_capability)
        else:
            failure_code = "runtime_lineage_mismatch"
            serial = _serial_artifact(
                run_index=run_index,
                status="BLOCKED",
                failure_code=failure_code,
                commit_sha=commit_sha,
                source_fingerprint=source_fingerprint,
                result={"passed": 0, "failed": 0, "skipped": 0},
                wall_seconds=None,
                cleanup={"status": "PASS", "allProcessGroupsTerminated": True},
                execution_lineage=build_unobserved_runtime_lineage(
                    failure_code=failure_code,
                    commit_sha=commit_sha,
                    source_fingerprint=source_fingerprint,
                ),
            )
        if serial.get("status") != "PASS":
            parallel = _blocked_parallel_result(
                **parallel_identity,
                failure_code=serial.get("failureCode") or "serial_control_failed",
                wall_seconds=None,
                error="parallel execution skipped because serial control did not pass",
            )
        elif not _source_is_current(root, source_seal, expected_source_session, commit_sha, source_fingerprint):
            parallel = _blocked_parallel_result(
                **parallel_identity,
                failure_code="parallel_source_identity_mismatch", wall_seconds=None,
            )
        elif sys.platform not in _SUPPORTED_PARALLEL_PLATFORMS:
            parallel = _blocked_parallel_result(
                **parallel_identity,
                failure_code="unsupported_platform",
                wall_seconds=None,
                error="reserved-fd-v1 parallel runner supports macOS and Linux only",
            )
        else:
            parallel_started = time.perf_counter()
            try:
                parallel = run_parallel_shards(
                    project_root=root,
                    manifest=manifest,
                    commit_sha=commit_sha,
                    source_fingerprint=source_fingerprint,
                    shard_count=shard_count,
                    output_root=run_root,
                    validated_dataset_snapshot_fingerprint=dataset_fp,
                    timeout_seconds=timeout_seconds,
                    execution_lineage={
                        "runnerFingerprint": run_lineage["runnerFingerprint"],
                        "environmentFingerprint": run_lineage["environmentFingerprint"],
                        "datasetSnapshotFingerprint": dataset_fp,
                    },
                    runner_capability=runner_capability,
                    source_session=expected_source_session,
                )
            except Exception as exc:
                parallel = _blocked_parallel_result(
                    **parallel_identity,
                    failure_code="parallel_runner_error",
                    wall_seconds=time.perf_counter() - parallel_started,
                    error=str(exc),
                )
        if parallel.get("status") == "PASS" and not _source_is_current(
            root, source_seal, expected_source_session, commit_sha, source_fingerprint,
        ):
            parallel = {**parallel, "status": "BLOCKED", "failureCode": "parallel_source_identity_mismatch"}
        aggregate = parallel.get("aggregate") if isinstance(parallel, Mapping) else None
        shards = parallel.get("shards") if isinstance(parallel, Mapping) else None
        cleanup_evidence = parallel.get("cleanup") if isinstance(parallel, Mapping) else None
        cleanup_confirmed = (
            isinstance(cleanup_evidence, Mapping)
            and cleanup_evidence.get("allProcessGroupsTerminated") is True
            and cleanup_evidence.get("runtimeCleanupConfirmed") is True
            and cleanup_evidence.get("fixtureRootsRemoved") is True
        )
        if parallel.get("status") == "PASS" and not cleanup_confirmed:
            parallel = {**parallel, "status": "BLOCKED", "failureCode": "isolation_violation"}
            aggregate = None
        if parallel.get("status") == "PASS" and isinstance(shards, list):
            validation = validate_shard_set(manifest, shards, commit_sha, source_fingerprint)
            if validation["status"] == "PASS":
                aggregate = aggregate_pytest_shards(
                    manifest,
                    shards,
                    expected_commit_sha=commit_sha,
                    expected_source_fingerprint=source_fingerprint,
                )
            else:
                aggregate = {"status": "BLOCKED", "failureCode": validation["failureCode"]}
                parallel = {
                    **parallel,
                    "status": "BLOCKED",
                    "failureCode": validation["failureCode"],
                }
        if not isinstance(aggregate, Mapping) or aggregate.get("status") != "PASS":
            aggregate = _blocked_aggregate(
                parallel,
                commit_sha=commit_sha,
                source_fingerprint=source_fingerprint,
                manifest_fingerprint=manifest_fingerprint,
                shard_count=shard_count,
            )
            if parallel.get("status") == "PASS":
                parallel = {
                    **parallel,
                    "status": "BLOCKED",
                    "failureCode": aggregate.get("failureCode") or "shard_aggregate_failed",
                }
        shard_coverage = []
        if cleanup_confirmed and isinstance(shards, list):
            shard_coverage = [
                {
                    "shardIndex": shard.get("shardIndex"),
                    "assignedNodeids": list(shard.get("assignedNodeids", [])),
                    "executedNodeids": list(shard.get("executedNodeids", [])),
                }
                for shard in sorted(
                    (item for item in shards if isinstance(item, Mapping)),
                    key=lambda item: item.get("shardIndex", -1),
                )
            ]
        expected_execution_lineage = {
            "runnerFingerprint": observed_runtime["runnerFingerprint"],
            "environmentFingerprint": observed_runtime["environmentFingerprint"],
            "datasetSnapshotFingerprint": dataset_fp,
        }
        parity = compare_serial_and_shard(serial, aggregate)
        if parity.get("status") == "PASS" and _execution_lineage_mismatch(
            serial, shards, expected_execution_lineage,
        ):
            parity = _blocked_parity("execution_lineage_mismatch")
        serial_total = _valid_measured_duration(serial.get("serialWallSeconds"))
        parallel_total = _valid_measured_duration(
            parallel.get("parallelWallSeconds") if isinstance(parallel, Mapping) else None
        )
        duration_blockers = []
        if serial_total is None and serial.get("status") == "PASS":
            duration_blockers.append("duration_invalid")
            serial = {
                **serial,
                "status": "BLOCKED",
                "failureCode": serial.get("failureCode") or "duration_invalid",
            }
        if parallel_total is None and parallel.get("status") == "PASS":
            duration_blockers.append("duration_invalid")
            parallel = {
                **parallel,
                "status": "BLOCKED",
                "failureCode": parallel.get("failureCode") or "duration_invalid",
            }
        if parity.get("status") == "PASS" and (
            serial.get("status") != "PASS"
            or parallel.get("status") != "PASS"
            or aggregate.get("status") != "PASS"
        ):
            parity = _blocked_parity("serial_or_shard_status_invalid")
        serial_runtime_fingerprint = serial.get("artifactFingerprint")
        if not isinstance(serial_runtime_fingerprint, str) or _SHA64.fullmatch(serial_runtime_fingerprint) is None:
            serial_runtime_fingerprint = canonical_fingerprint(dict(serial))
        parallel_runtime_fingerprint = aggregate.get("evidenceFingerprint")
        if not isinstance(parallel_runtime_fingerprint, str) or _SHA64.fullmatch(parallel_runtime_fingerprint) is None:
            parallel_runtime_fingerprint = canonical_fingerprint(dict(aggregate))
        if serial.get("status") == "PASS" and serial_total is not None:
            serial_v2 = _build_v2_artifact(role="serial_control", total_seconds=serial_total,
                result=serial.get("result", {}), **run_performance_identity)
            validate_performance_baseline_v2(serial_v2)
            serial_artifact_fingerprint = serial_v2["evidenceFingerprint"]
        else:
            serial_artifact_fingerprint = serial_runtime_fingerprint
        if parallel.get("status") == "PASS" and parallel_total is not None:
            parallel_v2 = _build_v2_artifact(role="parallel_candidate", total_seconds=parallel_total,
                result=aggregate.get("result", {}) if aggregate.get("status") == "PASS" else {"passed": 0, "failed": 0, "skipped": 0},
                **run_performance_identity)
            validate_performance_baseline_v2(parallel_v2)
            parallel_artifact_fingerprint = parallel_v2["evidenceFingerprint"]
        else:
            parallel_artifact_fingerprint = parallel_runtime_fingerprint
        serial_runtime_lineage = serial.get("executionLineage")
        if not isinstance(serial_runtime_lineage, Mapping):
            serial_runtime_lineage = {}
        parallel_runtime_lineage = [
            dict(shard["lineage"])
            for shard in (shards if isinstance(shards, list) else [])
            if isinstance(shard, Mapping) and isinstance(shard.get("lineage"), Mapping)
        ]
        failure_candidates = [
            value for value in (
                serial.get("failureCode"),
                parallel.get("failureCode") if isinstance(parallel, Mapping) else None,
                aggregate.get("failureCode"),
                parity.get("failureCode"),
            ) if isinstance(value, str) and value
        ]
        for value in duration_blockers:
            if value not in failure_candidates:
                failure_candidates.append(value)
        failure_code = next(iter(failure_candidates), None)
        if not serial_runtime_lineage and serial.get("status") != "PASS":
            failure_code = failure_code or "serial_execution_lineage_unobserved"
            if failure_code not in failure_candidates:
                failure_candidates.append(failure_code)
            serial_runtime_lineage = build_unobserved_runtime_lineage(
                failure_code=failure_code,
                commit_sha=commit_sha,
                source_fingerprint=source_fingerprint,
            )
        if (
            parallel.get("status") != "PASS"
            and not parallel_runtime_lineage
            and not (isinstance(shards, list) and shards)
        ):
            failure_code = failure_code or "parallel_execution_lineage_unobserved"
            if failure_code not in failure_candidates:
                failure_candidates.append(failure_code)
            parallel_runtime_lineage = build_unobserved_runtime_lineage(
                failure_code=failure_code,
                commit_sha=commit_sha,
                source_fingerprint=source_fingerprint,
            )
        serial_cleanup = serial.get("cleanup") if isinstance(serial.get("cleanup"), Mapping) else {}
        parallel_cleanup = parallel.get("cleanup") if isinstance(parallel.get("cleanup"), Mapping) else {}
        serial_groups_terminated = serial_cleanup.get("allProcessGroupsTerminated") is True
        parallel_cleanup_flags = {
            field: parallel_cleanup.get(field) is True
            for field in (
                "allProcessGroupsTerminated",
                "runtimeCleanupConfirmed",
                "fixtureRootsRemoved",
            )
        }
        cleanup_evidence = {
            "serial": {
                "status": "PASS" if serial_groups_terminated else "BLOCKED",
                "allProcessGroupsTerminated": serial_groups_terminated,
            },
            "parallel": {
                "status": "PASS" if all(parallel_cleanup_flags.values()) else "BLOCKED",
                **parallel_cleanup_flags,
            },
        }
        measured_runs.append({
            "runIndex": run_index,
            "populationKind": "full-pytest-nodeid",
            "commitSha": commit_sha,
            "sourceFingerprint": source_fingerprint,
            **lineage,
            "testPopulationCount": len(nodeids),
            "populationNodeids": sorted(nodeids),
            "runnerCapability": dict(runner_capability),
            "serialRuntimeArtifactFingerprint": serial_runtime_fingerprint,
            "parallelRuntimeArtifactFingerprint": parallel_runtime_fingerprint,
            "shardCoverage": shard_coverage,
            "serialResult": dict(serial.get("result", {})),
            "serialLineage": {
                "canonical": dict(lineage),
                "runtimeExecutionLineage": dict(serial_runtime_lineage),
                "runtimeArtifactFingerprint": serial_runtime_fingerprint,
            },
            "parallelLineage": {
                "canonical": dict(lineage),
                "runtimeExecutionLineage": parallel_runtime_lineage,
                "runtimeArtifactFingerprint": parallel_runtime_fingerprint,
            },
            "serialWallSeconds": serial_total,
            "parallelWallSeconds": parallel_total,
            "serialStatus": serial.get("status"),
            "parallelStatus": parallel.get("status") if isinstance(parallel, Mapping) else "BLOCKED",
            "cleanupEvidence": cleanup_evidence,
            "parity": parity,
            "shardAggregate": aggregate,
            "serialArtifactFingerprint": serial_artifact_fingerprint,
            "parallelArtifactFingerprint": parallel_artifact_fingerprint,
            "shardAggregateFingerprint": aggregate.get("evidenceFingerprint") or canonical_fingerprint(aggregate),
            "failureCode": failure_code,
            "blockers": failure_candidates,
        })
    rollout = build_parallel_rollout_evidence(
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        contract_fingerprint=contract_fp,
        baseline_family_id=baseline_family_id,
        manifest_fingerprint=manifest_fingerprint,
        test_population_fingerprint=population_fingerprint,
        runner_fingerprint=runner_fingerprint,
        environment_fingerprint=environment_fingerprint,
        dataset_snapshot_fingerprint=dataset_fp,
        selection_mode="full",
        shard_count=shard_count,
        measured_runs=measured_runs,
    )
    validate_parallel_rollout_evidence(
        rollout,
        expected_source_lineage={"commitSha": commit_sha, "sourceFingerprint": source_fingerprint, **lineage},
    )
    return rollout


def _write_output(path, payload, *, diagnostics_root, project_root):
    target = Path(path).expanduser()
    if target.is_symlink() or target.exists():
        raise ValueError("output must be a new regular file")
    validated_root = _validate_output_root(
        project_root, Path(diagnostics_root), require_fresh=False,
    )
    lexical_parent = Path(os.path.abspath(target.parent))
    resolved_parent = lexical_parent.resolve(strict=False)
    parent = target.parent
    if parent.is_symlink() or resolved_parent != lexical_parent:
        raise ValueError("output parent must not be a symlink")
    if resolved_parent != validated_root or target.name in {"", ".", ".."}:
        raise ValueError("output must stay inside the diagnostic root")
    if target.is_symlink() or target.exists():
        raise ValueError("output must be a new regular file")
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--runner-fingerprint", required=True)
    parser.add_argument("--environment-fingerprint", required=True)
    parser.add_argument("--baseline-family-id", required=True)
    parser.add_argument("--shard-count", type=int, choices=(2, 4, 8, 16), default=4)
    parser.add_argument("--repeats", type=int, choices=(3,), default=3)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-seal", type=Path, required=True)
    parser.add_argument("--source-session", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_parallel_rollout(
            project_root=args.project_root,
            manifest=_read_json(args.manifest),
            contract=_read_json(args.contract),
            commit_sha=args.commit_sha,
            source_fingerprint=args.source_fingerprint,
            runner_fingerprint=args.runner_fingerprint,
            environment_fingerprint=args.environment_fingerprint,
            baseline_family_id=args.baseline_family_id,
            shard_count=args.shard_count,
            repeats=args.repeats,
            timeout_seconds=args.timeout,
            output_root=args.output.parent / f".{args.output.stem}-runtime",
            source_seal=_read_json(args.source_seal) if args.source_seal else None,
            expected_source_session=_read_json(args.source_session) if args.source_session else None,
        )
        _write_output(
            args.output, result,
            diagnostics_root=args.output.parent,
            project_root=args.project_root,
        )
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"parallel rollout blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
