from __future__ import annotations

import json
import hashlib
import os
import platform
import socket
import sys
import subprocess
import tempfile
import threading
import time
import weakref
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures.thread import _worker
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping

from backend.agents.acceptance_paths import is_temporary_path
from backend.agents.evidence_models import canonical_fingerprint
from scripts.full_pytest_shard import _validate_manifest, run_pytest_shard
from scripts.full_pytest_shard_aggregate import aggregate_pytest_shards, validate_shard_set


ALLOWED_SHARD_COUNTS = frozenset({2, 4, 8, 16})
DEFAULT_TIMEOUT_SECONDS = 1800
MAX_TIMEOUT_SECONDS = 1800
CHILD_FAILURE_CLEANUP_SECONDS = 5
EXECUTION_LINEAGE_FIELDS = (
    "runnerFingerprint",
    "environmentFingerprint",
    "datasetSnapshotFingerprint",
)


class _DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """Keep controller return bounded without interpreter-exit thread joins."""

    def _adjust_thread_count(self):
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        if len(self._threads) < self._max_workers:
            thread_name = "%s_%d" % (
                self._thread_name_prefix or self,
                len(self._threads),
            )
            thread = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(weakref.ref(self, weakref_cb), self._work_queue, self._initializer, self._initargs),
                daemon=True,
            )
            thread.start()
            self._threads.add(thread)


def _join_executor_workers(executor, *, timeout_seconds=1.0):
    """Join controller workers for a bounded interval after cancellation."""
    deadline = time.perf_counter() + max(float(timeout_seconds), 0.0)
    for thread in tuple(executor._threads):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        thread.join(timeout=remaining)
    return all(not thread.is_alive() for thread in tuple(executor._threads))


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_output_root(project_root, output_root, *, require_fresh=True):
    root = Path(output_root).expanduser()
    if root.is_symlink():
        raise ValueError("output root must not be a symlink")
    if not is_temporary_path(root):
        raise ValueError("output root must be inside a temporary root")
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    current = root.parent
    while True:
        if current.is_symlink():
            resolved_component = current.resolve(strict=False)
            if resolved_component not in temporary_root.parents:
                raise ValueError("output root parent must not be a symlink")
        if current.parent == current:
            break
        current = current.parent
    project = Path(project_root).expanduser().resolve()
    resolved = root.resolve()
    if resolved == project or project in resolved.parents:
        raise ValueError("output root must not be inside the project root")
    if root.is_symlink():
        raise ValueError("output root must not be a symlink")
    if root.exists() and require_fresh:
        raise ValueError("output root must be fresh and not already exist")
    if not root.exists():
        try:
            root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ValueError("output root must be fresh and not already exist") from exc
    if root.is_symlink() or not root.is_dir():
        raise ValueError("output root is not a regular directory")
    return root.resolve()


def _port_readiness(ports):
    """Probe the ports after the child has adopted the inherited listeners."""
    if not isinstance(ports, Mapping) or not ports:
        return False
    values = list(ports.values())
    if any(isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port < 65536 for port in values):
        return False
    if len(values) != len(set(values)):
        return False
    for name, port in ports.items():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass
        except OSError:
            return False
    return True


def _validate_execution_lineage(lineage):
    if not isinstance(lineage, Mapping):
        raise ValueError("execution lineage is required")
    result = {}
    for field in EXECUTION_LINEAGE_FIELDS:
        value = lineage.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(f"execution lineage {field} is invalid")
        result[field] = value
    return result


def _live_worker_capacity():
    affinity = getattr(os, "sched_getaffinity", None)
    if callable(affinity):
        try:
            return max(1, len(affinity(0)))
        except OSError:
            pass
    return max(1, os.cpu_count() or 1)


def build_runner_capability_receipt(runner_fingerprint):
    if not isinstance(runner_fingerprint, str) or len(runner_fingerprint) != 64 or any(
        char not in "0123456789abcdef" for char in runner_fingerprint
    ):
        raise ValueError("runner capability fingerprint is invalid")
    unsigned = {
        "schemaVersion": "acceptance-runner-capability-v1",
        "runnerFingerprint": runner_fingerprint,
        "maxWorkers": _live_worker_capacity(),
    }
    return {**unsigned, "capabilityFingerprint": canonical_fingerprint(unsigned)}


def observe_runtime_fingerprints(project_root):
    """Fingerprint the runtime actually executing this rollout, matching CI identity inputs."""
    root = Path(project_root).expanduser().resolve()
    requirements = root / "requirements.txt"
    if not root.is_dir() or not requirements.is_file() or requirements.is_symlink():
        raise ValueError("live runtime identity inputs are unavailable")
    try:
        packages = subprocess.check_output(
            [sys.executable, "-m", "pip", "freeze", "--all"],
            cwd=root,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("installed package identity could not be observed") from exc
    runner_image = {
        "imageOS": os.environ.get("ImageOS", "unknown"),
        "imageVersion": os.environ.get("ImageVersion", "unknown"),
        "osVersion": platform.mac_ver()[0] or platform.release(),
    }
    identity = {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version_info[:3],
        "requirementsSha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "installedPackages": packages.splitlines(),
        "runnerImage": runner_image,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    runner_fingerprint = hashlib.sha256(encoded).hexdigest()
    environment_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "runnerFingerprint": runner_fingerprint,
                "profile": "acceptance-parallel-rollout-v1",
                "runnerImage": runner_image,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "runnerFingerprint": runner_fingerprint,
        "environmentFingerprint": environment_fingerprint,
    }


def _validate_runner_capability(capability, runner_fingerprint):
    if not isinstance(capability, Mapping):
        raise ValueError("runner capability is required")
    expected_keys = {"schemaVersion", "runnerFingerprint", "maxWorkers", "capabilityFingerprint"}
    if set(capability) != expected_keys or capability.get("schemaVersion") != "acceptance-runner-capability-v1":
        raise ValueError("runner capability receipt schema is invalid")
    unsigned = {key: capability[key] for key in ("schemaVersion", "runnerFingerprint", "maxWorkers")}
    if capability.get("capabilityFingerprint") != canonical_fingerprint(unsigned):
        raise ValueError("runner capability receipt fingerprint is invalid")
    if capability.get("runnerFingerprint") != runner_fingerprint:
        raise ValueError("runner capability receipt fingerprint binding mismatch")
    max_workers = capability.get("maxWorkers")
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("runner capability maxWorkers is invalid")
    if max_workers > _live_worker_capacity():
        raise ValueError("runner capability exceeds live worker capacity")
    return max_workers


def _child_lineage_matches(artifact, expected):
    value = artifact.get("lineage")
    return isinstance(value, Mapping) and all(value.get(field) == expected[field] for field in EXECUTION_LINEAGE_FIELDS)


def _blocked_shard(
    *, index, shard_count, commit_sha, source_fingerprint, manifest_fingerprint,
    fixture_root, failure_code, lineage=None, all_process_groups_terminated=True,
):
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


def _write_artifact(output_root, index, artifact):
    target = output_root / f"shard-{index}.json"
    if target.is_symlink() or target.exists():
        raise ValueError("shard artifact path must be new and non-symlink")
    payload = json.dumps(dict(artifact), ensure_ascii=False, indent=2) + "\n"
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise ValueError("shard artifact path must be new and non-symlink") from exc
    return target


def _cleanup_ok(artifact):
    metadata = artifact.get("metadata")
    cleanup = metadata.get("cleanup") if isinstance(metadata, Mapping) else None
    return (
        isinstance(cleanup, Mapping)
        and cleanup.get("status") == "PASS"
        and cleanup.get("allProcessGroupsTerminated") is True
    )


def run_parallel_shards(
    *, project_root, manifest, commit_sha, source_fingerprint, shard_count, output_root,
    timeout_seconds=DEFAULT_TIMEOUT_SECONDS, execution_lineage=None, runner_capability=None,
):
    if isinstance(shard_count, bool) or shard_count not in ALLOWED_SHARD_COUNTS:
        raise ValueError("shard count must be one of 2, 4, 8, or 16")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout must be positive")
    if timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout exceeds bounded maximum")
    lineage = _validate_execution_lineage(execution_lineage)
    observed_runtime = observe_runtime_fingerprints(project_root)
    if (
        lineage["runnerFingerprint"] != observed_runtime["runnerFingerprint"]
        or lineage["environmentFingerprint"] != observed_runtime["environmentFingerprint"]
    ):
        raise ValueError("execution lineage does not match live runtime identity")
    max_workers = _validate_runner_capability(
        runner_capability, lineage["runnerFingerprint"],
    )
    if shard_count > max_workers:
        raise ValueError("shard count exceeds runner capability maxWorkers")
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
    start_lock = threading.Lock()
    coordinated_start_monotonic = []
    coordinated_start_timestamp = []
    controller_started = time.perf_counter()
    controller_deadline = controller_started + float(timeout_seconds)

    def mark_coordinated_start():
        with start_lock:
            if not coordinated_start_monotonic:
                coordinated_start_monotonic.append(time.perf_counter())
                coordinated_start_timestamp.append(_timestamp())

    readiness_barrier = threading.Barrier(shard_count)
    start_barrier = threading.Barrier(shard_count, action=mark_coordinated_start)
    runtime_lock = threading.Lock()
    active_runtimes = {}
    runtime_seen = set()
    cleanup_confirmed = {}
    unregistration_observed = set()
    registration_failures = set()
    duplicate_runtimes = {}
    duplicate_cleanup_confirmed = {}
    ready_indexes = set()
    cancel_requested = threading.Event()

    def abort_readiness_barrier():
        for barrier in (readiness_barrier, start_barrier):
            try:
                barrier.abort()
            except (threading.BrokenBarrierError, RuntimeError):
                pass

    def observe_runtime(index, event, runtime):
        duplicate_registration = False
        with runtime_lock:
            if event == "registered":
                if index in runtime_seen:
                    registration_failures.add(index)
                    duplicate_runtimes.setdefault(index, []).append(runtime)
                    duplicate_cleanup_confirmed[index] = False
                    duplicate_registration = True
                else:
                    active_runtimes[index] = runtime
                    runtime_seen.add(index)
            elif event == "unregistered":
                report = getattr(runtime, "_cleanup", None)
                runtime_cleaned = (
                    isinstance(report, Mapping)
                    and report.get("status") == "PASS"
                    and report.get("allProcessGroupsTerminated") is True
                    and not report.get("leakedProcesses")
                )
                if any(runtime is duplicate for duplicate in duplicate_runtimes.get(index, ())):
                    duplicate_cleanup_confirmed[index] = runtime_cleaned
                elif active_runtimes.get(index) is runtime:
                    active_runtimes.pop(index)
                    unregistration_observed.add(index)
                    cleanup_confirmed[index] = runtime_cleaned
                else:
                    cleanup_confirmed[index] = False
                    unregistration_observed.discard(index)
        if duplicate_registration:
            raise RuntimeError("duplicate runtime registration for shard")
        if event == "registered" and cancel_requested.is_set():
            try:
                runtime.terminate_process_groups(force=True)
            finally:
                raise TimeoutError("controller timeout during runtime registration")

    def force_cleanup(index, *, worker_stopped=False, owner_thread=False):
        if not worker_stopped and not owner_thread:
            return False
        with runtime_lock:
            confirmed = cleanup_confirmed.get(index)
            if confirmed is True:
                return True
            runtime = active_runtimes.get(index)
            was_seen = index in runtime_seen
        if runtime is None:
            # A shard canceled before runtime registration has no process group
            # to terminate; only a previously observed runtime needs explicit
            # cleanup confirmation.
            safe_without_runtime = confirmed is True or not was_seen
            if safe_without_runtime:
                with runtime_lock:
                    cleanup_confirmed[index] = True
                    unregistration_observed.add(index)
            return safe_without_runtime
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
            if confirmed:
                unregistration_observed.add(index)
        return confirmed

    def readiness_evidence():
        with runtime_lock:
            ready_count = len(ready_indexes)
        return {
            "status": "PASS" if ready_count == shard_count and coordinated_start_timestamp else "BLOCKED",
            "expectedShardCount": shard_count,
            "readyShardCount": ready_count,
            "releasedAt": coordinated_start_timestamp[0] if coordinated_start_timestamp else None,
        }

    def one_shard(index, fixture_root):
        readiness_called = False
        start_called = False

        def child_ready():
            nonlocal readiness_called
            readiness_called = True
            with runtime_lock:
                ready_indexes.add(index)
            try:
                remaining = controller_deadline - time.perf_counter()
                readiness_barrier.wait(timeout=max(remaining, 0.001))
            except threading.BrokenBarrierError:
                raise TimeoutError("readiness barrier aborted")

        def child_start():
            nonlocal start_called
            start_called = True
            remaining = controller_deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("controller timeout before coordinated start")
            start_barrier.wait(timeout=remaining)

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
                    start_callback=child_start,
                )
                if artifact.get("status") == "PASS":
                    if not readiness_called:
                        raise RuntimeError("passing child must report readiness")
                    if not start_called:
                        raise RuntimeError("passing child must report coordinated start")
                elif not readiness_called or not start_called:
                    abort_readiness_barrier()
        except TimeoutError:
            abort_readiness_barrier()
            artifact = _blocked_shard(
                index=index, shard_count=shard_count, commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_root, failure_code="controller_timeout", lineage=lineage,
                all_process_groups_terminated=force_cleanup(index, owner_thread=True),
            )
        except threading.BrokenBarrierError:
            abort_readiness_barrier()
            artifact = _blocked_shard(
                index=index, shard_count=shard_count, commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_root, failure_code="start_barrier_failed", lineage=lineage,
                all_process_groups_terminated=force_cleanup(index, owner_thread=True),
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
                all_process_groups_terminated=force_cleanup(index, owner_thread=True),
            )
        with runtime_lock:
            duplicate_registration = index in registration_failures
        if duplicate_registration:
            abort_readiness_barrier()
            primary_cleanup = force_cleanup(index, owner_thread=True)
            with runtime_lock:
                all_runtimes_cleaned = (
                    primary_cleanup and duplicate_cleanup_confirmed.get(index) is True
                )
                cleanup_confirmed[index] = all_runtimes_cleaned
                if all_runtimes_cleaned:
                    unregistration_observed.add(index)
                else:
                    unregistration_observed.discard(index)
            artifact = _blocked_shard(
                index=index, shard_count=shard_count, commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_root, failure_code="process_registration_failure", lineage=lineage,
                all_process_groups_terminated=all_runtimes_cleaned,
            )
        return index, artifact

    fixture_roots = {
        index: Path(tempfile.gettempdir()) / f"nbs-parallel-{uuid.uuid4().hex}-{index}"
        for index in range(shard_count)
    }
    if any(root.exists() or root.is_symlink() for root in fixture_roots.values()):
        raise ValueError("shard fixture root collision")
    results = []
    timed_out = False
    timed_out_indexes = set()
    executor_shutdown = False
    termination_errors = []

    def terminate_active_runtimes():
        cancel_requested.set()
        abort_readiness_barrier()
        with runtime_lock:
            active = tuple(active_runtimes.items())
        for index, runtime in active:
            try:
                runtime.terminate_process_groups(force=True)
            except Exception as exc:
                termination_errors.append(f"shard_{index}:{str(exc)[:220]}")

    def resolve_future(index, future):
        if future.cancelled():
            return index, _blocked_shard(
                index=index, shard_count=shard_count, commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_roots[index], failure_code="controller_timeout", lineage=lineage,
                all_process_groups_terminated=False,
            )
        try:
            resolved_index, artifact = future.result()
            if resolved_index != index:
                raise RuntimeError("parallel shard future index mismatch")
            if not _cleanup_ok(artifact):
                force_cleanup(index, worker_stopped=True)
            return resolved_index, artifact
        except BaseException as exc:
            cleaned = force_cleanup(index, worker_stopped=True)
            return index, _blocked_shard(
                index=index, shard_count=shard_count, commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                fixture_root=fixture_roots[index], failure_code="pytest_runner_error",
                lineage=lineage, all_process_groups_terminated=cleaned,
            )

    def finish_result(failure_code, cleanup, **extra):
        # Artifact serialization and aggregation are outside the execution clock.
        finished = execution_finished[0] if execution_finished else time.perf_counter()
        ordered = sorted(results, key=lambda item: item[0])
        leaked_fixture_roots = sorted(
            str(root) for root in fixture_roots.values()
            if root.exists() or root.is_symlink()
        )
        fixture_cleanup_ok = not leaked_fixture_roots
        with runtime_lock:
            active_runtime_indexes = sorted(active_runtimes)
            cleanup_confirmation_missing = sorted(
                index for index in range(shard_count)
                if index not in unregistration_observed
                or cleanup_confirmed.get(index) is not True
            )
        runtime_cleanup_ok = not active_runtime_indexes and not cleanup_confirmation_missing
        cleanup = {
            **cleanup,
            "allProcessGroupsTerminated": cleanup.get("allProcessGroupsTerminated") is True,
            "runtimeCleanupConfirmed": runtime_cleanup_ok,
            "activeRuntimeCount": len(active_runtime_indexes),
            "cleanupConfirmationMissingShards": cleanup_confirmation_missing,
            "fixtureRootsRemoved": fixture_cleanup_ok,
            "leakedFixtureRoots": leaked_fixture_roots,
        }
        if failure_code is None and (not fixture_cleanup_ok or not runtime_cleanup_ok):
            failure_code = "isolation_violation"
        artifact_paths = []
        artifact_write_error = None
        for index, artifact in ordered:
            try:
                artifact_paths.append(str(_write_artifact(output, index, artifact)))
            except (OSError, TypeError, ValueError) as exc:
                artifact_write_error = str(exc)[:240]
                break
        if artifact_write_error is not None and failure_code is None:
            failure_code = "artifact_write_failed"
        result = {
            "status": "BLOCKED" if failure_code else "PASS",
            "failureCode": failure_code,
            "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
            "manifestFingerprint": manifest_fingerprint, "shardCount": shard_count,
            "shards": [item[1] for item in ordered],
            "shardArtifactPaths": artifact_paths,
            "parallelWallSeconds": max(round(finished - (
                coordinated_start_monotonic[0] if coordinated_start_monotonic else controller_started), 6), .001),
            "startedAt": coordinated_start_timestamp[0] if coordinated_start_timestamp else None,
            "finishedAt": _timestamp(), "readiness": readiness_evidence(),
            "cleanup": cleanup,
        }
        result.update(extra)
        if artifact_write_error is not None:
            for key in (
                "aggregate", "coverage", "parity", "speedupMultiple", "speedRatio",
                "comparison", "performance",
            ):
                result.pop(key, None)
            result["artifactWrite"] = {
                "status": "BLOCKED",
                "expectedShardArtifacts": len(ordered),
                "writtenShardArtifacts": len(artifact_paths),
                "error": artifact_write_error,
            }
        return result

    execution_finished = []
    executor = _DaemonThreadPoolExecutor(max_workers=shard_count, thread_name_prefix="acceptance-shard")
    try:
        futures = {
            index: executor.submit(one_shard, index, fixture_roots[index])
            for index in range(shard_count)
        }
        future_indexes = {future: index for index, future in futures.items()}
        pending = set(futures.values())
        child_failed = False
        while pending and not child_failed:
            remaining = controller_deadline - time.perf_counter()
            if remaining <= 0:
                timed_out = True
                break
            done, pending = wait(
                pending, timeout=remaining, return_when=FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                break
            for future in done:
                index, artifact = resolve_future(future_indexes[future], future)
                results.append((index, artifact))
                if artifact.get("status") != "PASS" and not child_failed:
                    child_failed = True
                    terminate_active_runtimes()
        if child_failed and pending:
            done, pending = wait(
                pending,
                timeout=min(
                    CHILD_FAILURE_CLEANUP_SECONDS,
                    max(controller_deadline - time.perf_counter(), 0.0),
                ),
            )
            for future in done:
                results.append(resolve_future(future_indexes[future], future))
            if pending:
                timed_out = True

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
            executor.shutdown(wait=False, cancel_futures=True)
            executor_shutdown = True
            # Give workers a bounded termination barrier. A daemon executor keeps
            # the controller from hanging forever, but unfinished workers must
            # remain explicitly unconfirmed and cannot yield aggregate evidence.
            _done_after_cleanup, pending_after_cleanup = wait(
                tuple(futures.values()), timeout=1.0,
            )
            worker_termination_confirmed = (
                not pending_after_cleanup
                and _join_executor_workers(executor, timeout_seconds=1.0)
            )
            if not worker_termination_confirmed:
                termination_errors.append("worker_termination_unconfirmed")
            futures_completed = all(future.done() for future in futures.values())
            post_cleanup = {
                index: force_cleanup(
                    index,
                    worker_stopped=(future.done() or future.cancelled()),
                )
                for index, future in futures.items()
            }
            timed_out_cleanup_ok = all(post_cleanup.values())
            if not timed_out_cleanup_ok: termination_errors += ["timed_out_shard_cleanup_unconfirmed"]
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
                elif not future.done():
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
                    # A future can become done between the timeout snapshot and
                    # this loop; resolve it through the same fail-closed path
                    # so BaseException cannot escape the controller.
                    late_index, late_artifact = resolve_future(index, future)
                    if late_index != index:
                        raise RuntimeError("parallel shard future index mismatch")
                    results.append((index, late_artifact))
            execution_finished.append(time.perf_counter())
            return finish_result("controller_timeout", {
                "allProcessGroupsTerminated": worker_termination_confirmed and not termination_errors
                    and all(_cleanup_ok(item[1]) for item in results),
                "controllerTimeout": True, "shardCount": shard_count,
                "futuresCompleted": futures_completed,
                "workerTerminationConfirmed": worker_termination_confirmed,
                "timedOutShardCleanupConfirmed": timed_out_cleanup_ok,
                "terminationErrors": termination_errors,
            })
    finally:
        if not executor_shutdown:
            executor.shutdown(wait=False, cancel_futures=True)
            _join_executor_workers(executor, timeout_seconds=1.0)

    execution_finished.append(time.perf_counter())
    artifacts = [item[1] for item in sorted(results)]
    cleanup_ok = all(_cleanup_ok(artifact) for artifact in artifacts)
    failed = [artifact for artifact in artifacts if artifact.get("status") != "PASS"]
    cleanup = {
        "allProcessGroupsTerminated": cleanup_ok,
        "shardCount": shard_count,
        "failedShardCount": len(failed),
    }
    if failed:
        return finish_result("shard_failed", cleanup)
    with runtime_lock:
        runtime_cleanup_ok = not active_runtimes and all(
            index in unregistration_observed and cleanup_confirmed.get(index) is True
            for index in range(shard_count)
        )
    fixture_cleanup_ok = all(not root.exists() and not root.is_symlink() for root in fixture_roots.values())
    if not cleanup_ok or not runtime_cleanup_ok or not fixture_cleanup_ok:
        return finish_result("isolation_violation", cleanup)
    if lineage is not None and any(not _child_lineage_matches(artifact, lineage) for artifact in artifacts):
        return finish_result("shard_lineage_mismatch", cleanup)
    validation = validate_shard_set(manifest, artifacts, commit_sha, source_fingerprint)
    if validation["status"] != "PASS":
        return finish_result(validation.get("failureCode", "nodeid_coverage_mismatch"), cleanup, coverage=validation)
    aggregate = aggregate_pytest_shards(
        manifest, artifacts, expected_commit_sha=commit_sha,
        expected_source_fingerprint=source_fingerprint,
    )
    return finish_result(None, cleanup, aggregate=aggregate, nodeidCount=len(nodeids))
