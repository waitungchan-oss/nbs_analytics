import argparse
import hashlib
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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents.acceptance_contract import contract_fingerprint, validate_acceptance_contract
from backend.agents.acceptance_parallel_rollout import (
    build_parallel_rollout_evidence,
    validate_parallel_rollout_evidence,
)
from backend.agents.acceptance_performance import MAX_COUNT, MAX_DURATION_SECONDS
from backend.agents.acceptance_performance_v2 import build_performance_baseline_v2
from backend.agents.acceptance_shard_runtime import allocate_shard_runtime
from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.verification_chain import git_source_probe
from scripts.full_pytest_gate import _parse_summary
from scripts.full_pytest_shard_aggregate import aggregate_pytest_shards, compare_serial_and_shard, validate_shard_set
from scripts.full_pytest_shard import _validate_manifest
from backend.agents.acceptance_parallel_runner import run_parallel_shards


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_SHARDS = frozenset({2, 4, 8, 16})
_MAX_JSON_BYTES = 1024 * 1024
_SOURCE_BRIEF = "docs/superpowers/specs/2026-09-15-acceptance-parallel-rollout-and-speedup-design.md"


def _read_json(path: Path) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file(): raise ValueError("input must be a regular file")
    if candidate.stat().st_size > _MAX_JSON_BYTES: raise ValueError("input exceeds size cap")
    value = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError("input JSON must be an object")
    return value


def _require_sha(value: Any, pattern: re.Pattern[str], field: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None: raise ValueError(f"{field} is invalid")


def _safe_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError("pytest counts are invalid")
    result: dict[str, int] = {}
    for field in ("passed", "failed", "skipped"):
        item = value.get(field, 0)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_COUNT:
            raise ValueError("pytest counts are invalid")
        result[field] = item
    return result


def _serial_fixture_root() -> Path:
    return Path(tempfile.gettempdir()) / f"nbs-parallel-serial-{uuid.uuid4().hex}"


def _serial_artifact(*, run_index: int, status: str, failure_code: str | None, commit_sha: str, source_fingerprint: str, result: Mapping[str, int], wall_seconds: float, cleanup: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = {"schemaVersion": "parallel-serial-control-v1", "status": status, "runIndex": run_index,
                "commitSha": commit_sha, "sourceFingerprint": source_fingerprint, "result": dict(result),
                "serialWallSeconds": round(wall_seconds, 6), "failureCode": failure_code, "cleanup": dict(cleanup)}
    return {**unsigned, "artifactFingerprint": canonical_fingerprint(unsigned)}


def _current_source_identity(project_root: Path) -> tuple[str, str, str]:
    root = Path(project_root).resolve()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=False)
    commit = head.stdout.strip()
    if head.returncode != 0 or not _SHA40.fullmatch(commit):
        raise RuntimeError("current commit identity is unavailable")
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", ".", ":(exclude)docs/superpowers", ":(exclude).superpowers"], cwd=root, capture_output=True, text=True, check=False)
    if status.returncode != 0:
        raise RuntimeError("current source status is unavailable")
    archive = subprocess.run(["git", "archive", "--format=tar", commit], cwd=root, capture_output=True, check=False)
    if archive.returncode != 0:
        raise RuntimeError("current source archive is unavailable")
    return commit, hashlib.sha256(archive.stdout).hexdigest(), status.stdout


def _current_worktree_fingerprint(project_root: Path) -> str:
    root = Path(project_root).resolve()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True, check=True).stdout.strip()
    return git_source_probe(root, brief_path=_SOURCE_BRIEF, base_sha=head)["worktree_fingerprint"]


def _sealed_worktree_fingerprint(project_root: Path, source_seal: Mapping[str, Any]) -> str:
    if source_seal.get("schemaVersion") == "verification-session-v1":
        brief_path = source_seal.get("briefPath")
        base_sha = source_seal.get("baseSha")
        if isinstance(brief_path, str) and isinstance(base_sha, str):
            probe = git_source_probe(
                project_root,
                brief_path=brief_path,
                base_sha=base_sha,
                head_ref="WORKTREE",
                contract_path="docs/agents/REVIEW_AGENT_CONTRACT.md",
                policy_path="agent_config/token_budgets.json",
            )
            return probe["worktree_fingerprint"]
    return _current_worktree_fingerprint(project_root)


def _matches_source_seal(
    project_root: Path,
    source_seal: Mapping[str, Any],
    *,
    commit_sha: str,
    source_fingerprint: str,
    actual_commit: str,
) -> bool:
    if source_seal.get("schemaVersion") not in {"verification-session-v1", "source-seal-v1"}:
        return False
    sealed_commit = source_seal.get("headSha", source_seal.get("commitSha"))
    sealed_source = source_seal.get("sourceFingerprint")
    sealed_worktree = source_seal.get("worktreeFingerprint")
    return sealed_commit == commit_sha == actual_commit and (sealed_source is None or sealed_source == source_fingerprint) and isinstance(sealed_worktree, str) and _SHA64.fullmatch(sealed_worktree) is not None and _sealed_worktree_fingerprint(project_root, source_seal) == sealed_worktree


def run_serial_control(*, project_root: Path, commit_sha: str, source_fingerprint: str,
                       nodeids: list[str], run_index: int, timeout_seconds: int,
                       source_seal: Mapping[str, Any] | None = None) -> dict[str, Any]:
    runtime = None
    started = time.perf_counter()
    fixture_root = _serial_fixture_root()
    status = "BLOCKED"
    failure_code: str | None = None
    result = {"passed": 0, "failed": 0, "skipped": 0}
    cleanup: Mapping[str, Any] = {"status": "PASS", "allProcessGroupsTerminated": True}
    try:
        root = Path(project_root).resolve()
        actual_commit, archive_fingerprint, dirty = _current_source_identity(root)
        if source_seal is not None:
            if (
                not isinstance(source_seal, Mapping)
                or not _matches_source_seal(
                    root, source_seal, commit_sha=commit_sha,
                    source_fingerprint=source_fingerprint, actual_commit=actual_commit,
                )
            ):
                failure_code = "serial_source_identity_mismatch"
        elif dirty:
            failure_code = "serial_source_dirty"
        elif (actual_commit, archive_fingerprint) != (commit_sha, source_fingerprint):
            failure_code = "serial_source_identity_mismatch"
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
                completed = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "--sandbox-preflight", "required", *nodeids],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                parsed = _parse_summary(f"{completed.stdout}\n{completed.stderr}")
                result = _safe_counts(parsed)
                failure_code = None if completed.returncode == 0 and result["failed"] == 0 else "serial_control_failed"
                status = "PASS" if failure_code is None else "FAIL"
    except subprocess.TimeoutExpired:
        failure_code = "serial_control_timeout"
    except (OSError, RuntimeError, ValueError):
        failure_code = "serial_control_blocked"
    finally:
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
    elapsed = max(time.perf_counter() - started, 0.001)
    if cleanup.get("status") != "PASS":
        status = "BLOCKED"
        failure_code = "isolation_violation"
    return _serial_artifact(run_index=run_index, status=status, failure_code=failure_code,
                            commit_sha=commit_sha, source_fingerprint=source_fingerprint,
                            result=result, wall_seconds=elapsed, cleanup=cleanup)


def _build_v2_artifact(*, role: str, total_seconds: float, result: Mapping[str, Any], commit_sha: str, source_fingerprint: str, baseline_family_id: str, lineage: Mapping[str, Any]) -> dict[str, Any]:
    counts = _safe_counts(result)
    collected = sum(counts.values())
    total = float(total_seconds)
    if not math.isfinite(total) or not 0.0 < total <= MAX_DURATION_SECONDS:
        raise ValueError("performance duration is invalid")
    return build_performance_baseline_v2(
        baseline_family_id=baseline_family_id,
        baseline_role=role,
        lifecycle="draft",
        contract_fingerprint=lineage["contractFingerprint"],
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        manifest_fingerprint=lineage["manifestFingerprint"],
        test_population_fingerprint=lineage["testPopulationFingerprint"],
        runner_fingerprint=lineage["runnerFingerprint"],
        environment_fingerprint=lineage["environmentFingerprint"],
        dataset_snapshot_fingerprint=lineage["datasetSnapshotFingerprint"],
        selection_mode="full",
        stages={"collectionSeconds": 0.0, "fixturePreparationSeconds": 0.0,
                "pytestExecutionSeconds": total, "aggregateSeconds": 0.0,
                "totalWallSeconds": total},
        test_count={"collected": collected, **counts},
    )


def _blocked_aggregate(parallel: Mapping[str, Any], *, shard_count: int) -> dict[str, Any]:
    unsigned = {"status": "BLOCKED", "failureCode": parallel.get("failureCode", "shard_set_incomplete"),
                "shardCount": parallel.get("shardCount", shard_count), "result": {"passed": 0, "failed": 0, "skipped": 0}, "shards": []}
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _blocked_parallel_result(
    *, commit_sha: str, source_fingerprint: str, manifest_fingerprint: str,
    shard_count: int, failure_code: str, wall_seconds: float, error: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "BLOCKED", "failureCode": failure_code, "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint, "manifestFingerprint": manifest_fingerprint,
        "shardCount": shard_count, "shards": [], "parallelWallSeconds": max(round(wall_seconds, 6), 0.001),
        "cleanup": {"allProcessGroupsTerminated": False}}
    if error:
        result["error"] = error[:256]
    return result


def run_parallel_rollout(
    *, project_root: Path, manifest: Mapping[str, Any], contract: Mapping[str, Any],
    commit_sha: str, source_fingerprint: str, runner_fingerprint: str,
    environment_fingerprint: str, baseline_family_id: str,
    shard_count: int = 4,
    repeats: int = 3,
    timeout_seconds: int = 1800,
    output_root: Path | None = None,
    source_seal: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _require_sha(commit_sha, _SHA40, "commitSha")
    _require_sha(source_fingerprint, _SHA64, "sourceFingerprint")
    _require_sha(runner_fingerprint, _SHA64, "runnerFingerprint")
    _require_sha(environment_fingerprint, _SHA64, "environmentFingerprint")
    if shard_count not in _ALLOWED_SHARDS or isinstance(shard_count, bool):
        raise ValueError("shard count is invalid")
    if repeats != 3 or isinstance(repeats, bool):
        raise ValueError("repeats must be exactly 3")
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
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
        "runnerFingerprint": runner_fingerprint,
        "environmentFingerprint": environment_fingerprint,
        "datasetSnapshotFingerprint": dataset_fp,
        "selectionMode": "full",
    }
    root = Path(project_root).resolve()
    diagnostics_root = Path(output_root or (Path(tempfile.gettempdir()) / f"nbs-parallel-rollout-{uuid.uuid4().hex}"))
    diagnostics_root.mkdir(parents=True, exist_ok=True)
    measured_runs: list[dict[str, Any]] = []
    for run_index in range(repeats):
        run_root = diagnostics_root / f"run-{run_index}"
        run_root.mkdir(parents=True, exist_ok=False)
        serial = run_serial_control(project_root=root, commit_sha=commit_sha,
                                    source_fingerprint=source_fingerprint, nodeids=nodeids,
                                    run_index=run_index, timeout_seconds=timeout_seconds,
                                    source_seal=source_seal)
        if serial.get("status") != "PASS":
            parallel = _blocked_parallel_result(
                commit_sha=commit_sha,
                source_fingerprint=source_fingerprint,
                manifest_fingerprint=manifest_fingerprint,
                shard_count=shard_count,
                failure_code=serial.get("failureCode") or "serial_control_failed",
                wall_seconds=0.001,
                error="parallel execution skipped because serial control did not pass",
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
                    timeout_seconds=timeout_seconds,
                    execution_lineage={
                        "runnerFingerprint": runner_fingerprint,
                        "environmentFingerprint": environment_fingerprint,
                        "datasetSnapshotFingerprint": dataset_fp,
                    },
                )
            except Exception as exc:
                parallel = _blocked_parallel_result(
                    commit_sha=commit_sha,
                    source_fingerprint=source_fingerprint,
                    manifest_fingerprint=manifest_fingerprint,
                    shard_count=shard_count,
                    failure_code="parallel_runner_error",
                    wall_seconds=time.perf_counter() - parallel_started,
                    error=str(exc),
                )
        aggregate = parallel.get("aggregate") if isinstance(parallel, Mapping) else None
        shards = parallel.get("shards") if isinstance(parallel, Mapping) else None
        if isinstance(shards, list):
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
        if not isinstance(aggregate, Mapping) or aggregate.get("status") != "PASS":
            aggregate = _blocked_aggregate(parallel, shard_count=shard_count)
        shard_coverage = []
        if isinstance(shards, list):
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
        parity = compare_serial_and_shard(serial, aggregate)
        serial_total = float(serial.get("serialWallSeconds", 0.001) or 0.001)
        parallel_total = float(parallel.get("parallelWallSeconds", 0.001) or 0.001) if isinstance(parallel, Mapping) else 0.001
        serial_v2 = _build_v2_artifact(role="serial_control", total_seconds=serial_total,
            result=serial.get("result", {}), commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, baseline_family_id=baseline_family_id,
            lineage=lineage)
        parallel_v2 = _build_v2_artifact(role="parallel_candidate", total_seconds=parallel_total,
            result=aggregate.get("result", {}) if aggregate.get("status") == "PASS" else {"passed": 0, "failed": 0, "skipped": 0},
            commit_sha=commit_sha, source_fingerprint=source_fingerprint,
            baseline_family_id=baseline_family_id, lineage=lineage)
        measured_runs.append({
            "runIndex": run_index,
            "populationKind": "full-pytest-nodeid",
            "contractFingerprint": contract_fp,
            "baselineFamilyId": baseline_family_id,
            "manifestFingerprint": manifest_fingerprint,
            "testPopulationFingerprint": population_fingerprint,
            "runnerFingerprint": runner_fingerprint,
            "environmentFingerprint": environment_fingerprint,
            "datasetSnapshotFingerprint": dataset_fp,
            "selectionMode": "full",
            "testPopulationCount": len(nodeids),
            "populationNodeids": sorted(nodeids),
            "shardCoverage": shard_coverage,
            "serialResult": dict(serial.get("result", {})),
            "serialLineage": dict(lineage),
            "parallelLineage": dict(lineage),
            "serialWallSeconds": serial_total,
            "parallelWallSeconds": parallel_total,
            "serialStatus": serial.get("status"),
            "parallelStatus": parallel.get("status") if isinstance(parallel, Mapping) else "BLOCKED",
            "parity": parity,
            "shardAggregate": aggregate,
            "serialArtifactFingerprint": serial_v2["evidenceFingerprint"],
            "parallelArtifactFingerprint": parallel_v2["evidenceFingerprint"],
            "shardAggregateFingerprint": aggregate.get("evidenceFingerprint") or canonical_fingerprint(aggregate),
            "failureCode": next((value for value in (
                serial.get("failureCode"), parallel.get("failureCode") if isinstance(parallel, Mapping) else None,
                aggregate.get("failureCode"), parity.get("failureCode"),
            ) if isinstance(value, str) and value), None),
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
    validate_parallel_rollout_evidence(rollout)
    return rollout


def _write_output(path: Path, payload: Mapping[str, Any]) -> None:
    target = Path(path).expanduser()
    if target.is_symlink() or target.exists() and not target.is_file():
        raise ValueError("output must be a new regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--source-seal", type=Path)
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
        )
        _write_output(args.output, result)
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"parallel rollout blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
