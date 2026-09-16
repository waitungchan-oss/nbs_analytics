from __future__ import annotations

import json
import errno
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from backend.agents.acceptance_paths import is_temporary_path
from backend.agents.evidence_models import canonical_fingerprint
from scripts.full_pytest_shard import _validate_manifest, run_pytest_shard
from scripts.full_pytest_shard_aggregate import aggregate_pytest_shards, validate_shard_set


ALLOWED_SHARD_COUNTS = frozenset({2, 4, 8, 16})
DEFAULT_TIMEOUT_SECONDS = 1800
EXECUTION_LINEAGE_FIELDS = (
    "runnerFingerprint",
    "environmentFingerprint",
    "datasetSnapshotFingerprint",
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_output_root(project_root: Path, output_root: Path) -> Path:
    root = Path(output_root).expanduser()
    if root.is_symlink():
        raise ValueError("output root must not be a symlink")
    if not is_temporary_path(root):
        raise ValueError("output root must be inside a temporary root")
    project = Path(project_root).expanduser().resolve()
    resolved = root.resolve()
    if resolved == project or project in resolved.parents:
        raise ValueError("output root must not be inside the project root")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("output root is not a regular directory")
    return root.resolve()


def _port_readiness(ports: Mapping[str, int]) -> bool:
    if not isinstance(ports, Mapping) or not ports:
        return False
    values = list(ports.values())
    if any(isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port < 65536 for port in values):
        return False
    if len(values) != len(set(values)):
        return False
    reservations = getattr(ports, "reserved_sockets", None)
    if not isinstance(reservations, Mapping) or set(reservations) != set(ports):
        return False
    for name, port in ports.items():
        reservation = reservations.get(name)
        if not isinstance(reservation, socket.socket) or reservation.fileno() < 0:
            return False
        try:
            if reservation.getsockname()[:2] != ("127.0.0.1", port):
                return False
            try:
                accepting = reservation.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            except OSError as exc:
                if exc.errno in {errno.ENOPROTOOPT, errno.EINVAL, errno.ENOTSUP}:
                    accepting = None
                else:
                    return False
            if accepting not in {None, 1}:
                return False
        except OSError:
            return False
    return True


def _validate_execution_lineage(lineage: Mapping[str, str] | None) -> dict[str, str]:
    if not isinstance(lineage, Mapping):
        raise ValueError("execution lineage is required")
    result = {}
    for field in EXECUTION_LINEAGE_FIELDS:
        value = lineage.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(f"execution lineage {field} is invalid")
        result[field] = value
    return result


def _child_lineage_matches(artifact: Mapping[str, Any], expected: Mapping[str, str]) -> bool:
    value = artifact.get("lineage")
    return isinstance(value, Mapping) and all(value.get(field) == expected[field] for field in EXECUTION_LINEAGE_FIELDS)


def _blocked_shard(
    *,
    index: int,
    shard_count: int,
    commit_sha: str,
    source_fingerprint: str,
    manifest_fingerprint: str,
    fixture_root: Path,
    failure_code: str,
    lineage: Mapping[str, str] | None = None,
    all_process_groups_terminated: bool = True,
) -> dict[str, Any]:
    unsigned = {
        "schemaVersion": "full-pytest-shard-v1",
        "status": "BLOCKED",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "shardIndex": index,
        "shardCount": shard_count,
        "assignedNodeids": [],
        "executedNodeids": [],
        "result": {"passed": 0, "failed": 0, "skipped": 0},
        "startedAt": _timestamp(),
        "finishedAt": _timestamp(),
        "metadata": {
            "failureCode": failure_code,
            "cleanup": {
                "status": "PASS" if all_process_groups_terminated else "BLOCKED",
                "allProcessGroupsTerminated": all_process_groups_terminated,
            },
        },
        "fixtureRoot": str(fixture_root),
        "lineage": dict(lineage or {}),
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def _write_artifact(output_root: Path, index: int, artifact: Mapping[str, Any]) -> Path:
    target = output_root / f"shard-{index}.json"
    if target.is_symlink() or target.exists():
        raise ValueError("shard artifact path must be new and non-symlink")
    target.write_text(json.dumps(dict(artifact), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _cleanup_ok(artifact: Mapping[str, Any]) -> bool:
    metadata = artifact.get("metadata")
    cleanup = metadata.get("cleanup") if isinstance(metadata, Mapping) else None
    return (
        isinstance(cleanup, Mapping)
        and cleanup.get("status") == "PASS"
        and cleanup.get("allProcessGroupsTerminated") is True
    )


def run_parallel_shards(
    *,
    project_root: Path,
    manifest: Mapping[str, Any],
    commit_sha: str,
    source_fingerprint: str,
    shard_count: int,
    output_root: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    execution_lineage: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if isinstance(shard_count, bool) or shard_count not in ALLOWED_SHARD_COUNTS:
        raise ValueError("shard count must be one of 2, 4, 8, or 16")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    try:
        manifest_commit, manifest_source, nodeids, manifest_fingerprint = _validate_manifest(manifest)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"manifest identity is invalid: {exc}") from exc
    if manifest_commit != commit_sha or manifest_source != source_fingerprint:
        raise ValueError("manifest identity mismatch")
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("project root must be an existing directory")
    output = _validate_output_root(root, Path(output_root))
    lineage = _validate_execution_lineage(execution_lineage) if execution_lineage is not None else None

    start_lock = threading.Lock()
    launch_monotonic: list[float] = []
    launch_timestamp: list[str] = []
    controller_started = time.perf_counter()
    controller_deadline = controller_started + float(timeout_seconds)

    def mark_coordinated_start() -> None:
        with start_lock:
            launch_monotonic.append(time.perf_counter())
            launch_timestamp.append(_timestamp())

    readiness_barrier = threading.Barrier(shard_count, action=mark_coordinated_start)
    runtime_lock = threading.Lock()
    active_runtimes: dict[int, Any] = {}
    runtime_seen: set[int] = set()
    cleanup_confirmed: dict[int, bool] = {}
    ready_indexes: set[int] = set()
    cancel_requested = threading.Event()

    def abort_readiness_barrier() -> None:
        try:
            readiness_barrier.abort()
        except (threading.BrokenBarrierError, RuntimeError):
            pass

    def observe_runtime(index: int, event: str, runtime: Any) -> None:
        with runtime_lock:
            if event == "registered":
                active_runtimes[index] = runtime
                runtime_seen.add(index)
            elif event == "unregistered":
                active_runtimes.pop(index, None)
                report = getattr(runtime, "_cleanup", None)
                cleanup_confirmed[index] = (
                    isinstance(report, Mapping)
                    and report.get("status") == "PASS"
                    and report.get("allProcessGroupsTerminated") is True
                    and not report.get("leakedProcesses")
                )

    def force_cleanup(index: int) -> bool:
        with runtime_lock:
            if cleanup_confirmed.get(index) is True:
                return True
            runtime = active_runtimes.get(index)
            was_seen = index in runtime_seen
        if runtime is None:
            return not was_seen
        termination_ok = True
        try:
            runtime.terminate_process_groups(force=True)
        except Exception:
            termination_ok = False
        try:
            report = runtime.cleanup()
        except Exception:
            return False
        confirmed = (
            termination_ok
            and isinstance(report, Mapping)
            and report.get("status") == "PASS"
            and report.get("allProcessGroupsTerminated") is True
            and not report.get("leakedProcesses")
        )
        with runtime_lock:
            active_runtimes.pop(index, None)
            cleanup_confirmed[index] = confirmed
        return confirmed

    def readiness_evidence() -> dict[str, Any]:
        with runtime_lock:
            ready_count = len(ready_indexes)
        return {
            "status": "PASS" if ready_count == shard_count and launch_timestamp else "BLOCKED",
            "expectedShardCount": shard_count,
            "readyShardCount": ready_count,
            "releasedAt": launch_timestamp[0] if launch_timestamp else None,
        }

    def one_shard(index: int, fixture_root: Path) -> tuple[int, dict[str, Any]]:
        readiness_called = False

        def child_ready() -> None:
            nonlocal readiness_called
            readiness_called = True
            with runtime_lock:
                ready_indexes.add(index)
            try:
                remaining = controller_deadline - time.perf_counter()
                readiness_barrier.wait(timeout=max(min(remaining, 60.0), 0.001))
            except threading.BrokenBarrierError:
                raise TimeoutError("readiness barrier aborted")

        try:
            if cancel_requested.is_set():
                raise TimeoutError("controller timeout")
            remaining = controller_deadline - time.perf_counter()
            if remaining <= 0:
                artifact = _blocked_shard(
                    index=index, shard_count=shard_count, commit_sha=commit_sha,
                    source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                    fixture_root=fixture_root, failure_code="controller_timeout", lineage=lineage,
                    all_process_groups_terminated=True,
                )
            else:
                artifact = run_pytest_shard(
                    project_root=root,
                    manifest=manifest,
                    shard_index=index,
                    shard_count=shard_count,
                    fixture_root=fixture_root,
                    run_id=f"parallel-{uuid.uuid4().hex}-{index}",
                    timeout_seconds=max(1, min(timeout_seconds, int(remaining))),
                    port_readiness_probe=_port_readiness,
                    lineage=lineage,
                    runtime_observer=lambda event, runtime: observe_runtime(index, event, runtime),
                    readiness_callback=child_ready,
                )
                if not readiness_called:
                    raise RuntimeError("child readiness callback is required")
        except TimeoutError:
            abort_readiness_barrier()
            artifact = _blocked_shard(
                index=index, shard_count=shard_count, commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_root, failure_code="controller_timeout", lineage=lineage,
                all_process_groups_terminated=force_cleanup(index),
            )
        except threading.BrokenBarrierError:
            abort_readiness_barrier()
            artifact = _blocked_shard(
                index=index, shard_count=shard_count, commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_root, failure_code="start_barrier_failed", lineage=lineage,
                all_process_groups_terminated=force_cleanup(index),
            )
        except BaseException as exc:
            abort_readiness_barrier()
            artifact = _blocked_shard(
                index=index,
                shard_count=shard_count,
                commit_sha=commit_sha,
                source_fingerprint=source_fingerprint,
                manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_root,
                failure_code="pytest_runner_error" if isinstance(exc, Exception) else "pytest_runner_interrupted",
                lineage=lineage,
                all_process_groups_terminated=force_cleanup(index),
            )
        return index, artifact

    fixture_roots = {
        index: Path(tempfile.gettempdir()) / f"nbs-parallel-{uuid.uuid4().hex}-{index}"
        for index in range(shard_count)
    }
    if any(root.exists() or root.is_symlink() for root in fixture_roots.values()):
        raise ValueError("shard fixture root collision")
    results: list[tuple[int, dict[str, Any]]] = []
    timed_out = False
    timed_out_indexes: set[int] = set()
    executor_shutdown = False
    termination_errors: list[str] = []
    executor = ThreadPoolExecutor(max_workers=shard_count, thread_name_prefix="acceptance-shard")
    try:
        futures = {
            index: executor.submit(one_shard, index, fixture_roots[index])
            for index in range(shard_count)
        }
        done, pending = wait(
            tuple(futures.values()),
            timeout=max(controller_deadline - time.perf_counter(), 0.0),
        )
        for index, future in futures.items():
            if future in pending:
                future.cancel()
                results.append((
                    index,
                    _blocked_shard(
                        index=index, shard_count=shard_count, commit_sha=commit_sha,
                        source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                        fixture_root=fixture_roots[index], failure_code="controller_timeout", lineage=lineage,
                        all_process_groups_terminated=False,
                    ),
                ))
                timed_out = True
                continue
            try:
                results.append(future.result())
            except BaseException as exc:
                cleaned = force_cleanup(index)
                results.append((
                    index,
                    _blocked_shard(
                        index=index, shard_count=shard_count, commit_sha=commit_sha,
                        source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                        fixture_root=fixture_roots[index], failure_code="pytest_runner_error",
                        lineage=lineage, all_process_groups_terminated=cleaned,
                    ),
                ))

        if timed_out:
            timed_out_indexes = {index for index, future in futures.items() if future in pending}
            cancel_requested.set()
            abort_readiness_barrier()
            with runtime_lock:
                runtimes = list(active_runtimes.values())
            for runtime in runtimes:
                try:
                    runtime.terminate_process_groups(force=True)
                except Exception as exc:
                    termination_errors.append(str(exc)[:256])
            executor.shutdown(wait=True, cancel_futures=True)
            executor_shutdown = True
            futures_completed = all(future.done() for future in futures.values())
            if not futures_completed: termination_errors += ["future_not_done"]
            post_cleanup = {index: force_cleanup(index) for index in range(shard_count)}
            results = []
            for index, future in futures.items():
                if future.cancelled():
                    results.append((
                        index,
                        _blocked_shard(
                            index=index, shard_count=shard_count, commit_sha=commit_sha,
                            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                            fixture_root=fixture_roots[index], failure_code="controller_timeout", lineage=lineage,
                            all_process_groups_terminated=post_cleanup[index],
                        ),
                    ))
                else:
                    late_index, late_artifact = future.result()
                    if late_index != index:
                        raise RuntimeError("parallel shard future index mismatch")
                    if index in timed_out_indexes:
                        results.append((
                            index,
                            _blocked_shard(
                                index=index, shard_count=shard_count, commit_sha=commit_sha,
                                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                                fixture_root=fixture_roots[index], failure_code="controller_timeout", lineage=lineage,
                                all_process_groups_terminated=post_cleanup[index] and _cleanup_ok(late_artifact),
                            ),
                        ))
                    else:
                        results.append((index, late_artifact))
            results.sort(key=lambda item: item[0])
            artifacts = [item[1] for item in results]
            artifact_paths = [str(_write_artifact(output, item[0], item[1])) for item in results]
            return {
                "status": "BLOCKED",
                "failureCode": "controller_timeout",
                "commitSha": commit_sha,
                "sourceFingerprint": source_fingerprint,
                "manifestFingerprint": manifest_fingerprint,
                "shardCount": shard_count,
                "shards": artifacts,
                "shardArtifactPaths": artifact_paths,
                "parallelWallSeconds": round(time.perf_counter() - controller_started, 6),
                "startedAt": launch_timestamp[0] if launch_timestamp else None,
                "finishedAt": _timestamp(),
                "readiness": readiness_evidence(),
                "cleanup": {
                    "allProcessGroupsTerminated": futures_completed and not termination_errors and all(_cleanup_ok(artifact) for artifact in artifacts),
                    "controllerTimeout": True,
                    "shardCount": shard_count,
                    "terminationErrors": termination_errors,
                },
            }
    finally:
        if not executor_shutdown:
            executor.shutdown(wait=True, cancel_futures=False)

    results.sort(key=lambda item: item[0])
    artifacts = [item[1] for item in results]
    artifact_paths = [str(_write_artifact(output, item[0], item[1])) for item in results]
    wall_seconds = round(time.perf_counter() - launch_monotonic[0], 6) if launch_monotonic else None
    cleanup_ok = all(_cleanup_ok(artifact) for artifact in artifacts)
    failed = [artifact for artifact in artifacts if artifact.get("status") != "PASS"]
    cleanup = {
        "allProcessGroupsTerminated": cleanup_ok,
        "shardCount": shard_count,
        "failedShardCount": len(failed),
    }
    if failed:
        return {
            "status": "BLOCKED",
            "failureCode": "shard_failed",
            "commitSha": commit_sha,
            "sourceFingerprint": source_fingerprint,
            "manifestFingerprint": manifest_fingerprint,
            "shardCount": shard_count,
            "shards": artifacts,
            "shardArtifactPaths": artifact_paths,
            "parallelWallSeconds": wall_seconds,
            "startedAt": launch_timestamp[0] if launch_timestamp else None,
            "finishedAt": _timestamp(),
            "readiness": readiness_evidence(),
            "cleanup": cleanup,
        }
    if lineage is not None and any(not _child_lineage_matches(artifact, lineage) for artifact in artifacts):
        return {
            "status": "BLOCKED",
            "failureCode": "shard_lineage_mismatch",
            "commitSha": commit_sha,
            "sourceFingerprint": source_fingerprint,
            "manifestFingerprint": manifest_fingerprint,
            "shardCount": shard_count,
            "shards": artifacts,
            "shardArtifactPaths": artifact_paths,
            "parallelWallSeconds": wall_seconds,
            "startedAt": launch_timestamp[0] if launch_timestamp else None,
            "finishedAt": _timestamp(),
            "readiness": readiness_evidence(),
            "cleanup": cleanup,
        }
    validation = validate_shard_set(manifest, artifacts, commit_sha, source_fingerprint)
    if validation["status"] != "PASS":
        return {
            "status": "BLOCKED",
            "failureCode": validation.get("failureCode", "nodeid_coverage_mismatch"),
            "commitSha": commit_sha,
            "sourceFingerprint": source_fingerprint,
            "manifestFingerprint": manifest_fingerprint,
            "shardCount": shard_count,
            "shards": artifacts,
            "shardArtifactPaths": artifact_paths,
            "parallelWallSeconds": wall_seconds,
            "startedAt": launch_timestamp[0] if launch_timestamp else None,
            "finishedAt": _timestamp(),
            "cleanup": cleanup,
            "coverage": validation,
        }
    aggregate = aggregate_pytest_shards(
        manifest,
        artifacts,
        expected_commit_sha=commit_sha,
        expected_source_fingerprint=source_fingerprint,
    )
    return {
        "status": "PASS" if cleanup_ok else "BLOCKED",
        "failureCode": None if cleanup_ok else "isolation_violation",
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "shardCount": shard_count,
        "shards": artifacts,
        "shardArtifactPaths": artifact_paths,
        "aggregate": aggregate,
        "parallelWallSeconds": wall_seconds,
        "startedAt": launch_timestamp[0] if launch_timestamp else None,
        "finishedAt": _timestamp(),
        "readiness": readiness_evidence(),
        "cleanup": cleanup,
        "nodeidCount": len(nodeids),
    }
