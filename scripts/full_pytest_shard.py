"""Run one opt-in, diagnostic-only pytest shard against an isolated fixture."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_paths import is_temporary_path
from backend.agents.acceptance_telemetry import build_gate_telemetry
from backend.agents.acceptance_shard_runtime import ShardRuntime, allocate_shard_runtime
from backend.agents.evidence_models import canonical_fingerprint
from scripts.full_pytest_gate import _parse_summary
from scripts.pytest_manifest import _identity, _manifest_fingerprint, _parse_nodeids


_TAIL = 4000


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def select_shard_nodeids(nodeids: Sequence[str], shard_index: int, shard_count: int) -> list[str]:
    if isinstance(shard_index, bool) or isinstance(shard_count, bool) or shard_count <= 0:
        raise ValueError("shard count must be positive")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard index is out of range")
    ordered = sorted(nodeids)
    if len(ordered) != len(set(ordered)) or any(not isinstance(item, str) or not item for item in ordered):
        raise ValueError("manifest nodeids must be unique non-empty strings")
    return [nodeid for position, nodeid in enumerate(ordered) if position % shard_count == shard_index]


def _validate_manifest(manifest: Mapping[str, Any]) -> tuple[str, str, list[str], str]:
    if manifest.get("schemaVersion") != "pytest-test-manifest-v1" or manifest.get("status") != "PASS":
        raise ValueError("manifest is not a passing pytest manifest")
    commit_sha = manifest.get("commitSha")
    source_fingerprint = manifest.get("sourceFingerprint")
    nodeids = manifest.get("nodeids")
    fingerprint = manifest.get("manifestFingerprint")
    if not isinstance(commit_sha, str) or not isinstance(source_fingerprint, str) or not isinstance(nodeids, list) or not isinstance(fingerprint, str):
        raise ValueError("manifest identity is invalid")
    _identity(commit_sha, source_fingerprint)
    expected = _manifest_fingerprint(commit_sha, source_fingerprint, sorted(nodeids))
    if fingerprint != expected:
        raise ValueError("manifest fingerprint mismatch")
    if sorted(nodeids) != nodeids or len(nodeids) != len(set(nodeids)):
        raise ValueError("manifest nodeids are not stable and unique")
    return commit_sha, source_fingerprint, nodeids, fingerprint


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _run_pytest_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    runtime: ShardRuntime,
) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        # A post-Popen Job assignment has an unbounded race before children
        # are contained. Without an approved suspended-process launcher, fail
        # closed instead of running an unqualified Windows shard.
        raise RuntimeError("windows shard launcher is not qualified")
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    try:
        runtime.register_process_group(process.pid)
    except Exception:
        if os.name == "nt":
            try:
                process.kill()
            except OSError:
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            process.communicate(timeout=1)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.communicate(timeout=1)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                pass
        raise RuntimeError("pytest process registration failed")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except KeyboardInterrupt:
        runtime.terminate_process_groups(force=True)
        try:
            process.communicate(timeout=1)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.communicate(timeout=1)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                pass
        raise
    except subprocess.TimeoutExpired as exc:
        runtime.terminate_process_groups()
        partial_stdout = _as_text(exc.output)
        partial_stderr = _as_text(exc.stderr)
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired as grace_exc:
            runtime.terminate_process_groups(force=True)
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired as force_exc:
                process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=1)
                except subprocess.TimeoutExpired as final_exc:
                    stdout = _as_text(final_exc.output)
                    stderr = _as_text(final_exc.stderr)
            partial_stdout = partial_stdout or _as_text(grace_exc.output)
            partial_stderr = partial_stderr or _as_text(grace_exc.stderr)
        raise subprocess.TimeoutExpired(
            list(argv), timeout,
            output=_as_text(stdout) or partial_stdout,
            stderr=_as_text(stderr) or partial_stderr,
        ) from exc
    return subprocess.CompletedProcess(list(argv), process.returncode, stdout, stderr)


def _artifact(
    *,
    status: str,
    failure_code: str | None,
    commit_sha: str,
    source_fingerprint: str,
    manifest_fingerprint: str,
    shard_index: int,
    shard_count: int,
    assigned: list[str],
    executed: list[str],
    result: dict[str, Any],
    started_at: str,
    finished_at: str | None,
    monotonic_started: float,
    stdout: str,
    stderr: str,
    cleanup_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    unsigned = {
        "schemaVersion": "full-pytest-shard-v1",
        "status": status,
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "shardIndex": shard_index,
        "shardCount": shard_count,
        "assignedNodeids": assigned,
        "executedNodeids": executed,
        "result": result,
        "startedAt": started_at,
        "finishedAt": finished_at or _timestamp(),
        "metadata": {
            "commandId": "full-pytest-shard",
            "failureCode": failure_code,
            "stdoutTail": stdout[-_TAIL:],
            "stderrTail": stderr[-_TAIL:],
            "cleanup": dict(cleanup_report or {
                "status": "UNKNOWN",
                "failureCode": "cleanup_not_recorded",
                "leakedFiles": [],
                "leakedLocks": [],
                "leakedProcesses": [],
            }),
            "telemetry": build_gate_telemetry(
                duration_seconds=time.perf_counter() - monotonic_started,
                failure_code=failure_code,
                blocked_reason=failure_code if status == "BLOCKED" else None,
            ),
        },
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def run_pytest_shard(
    project_root: Path,
    manifest: Mapping[str, Any],
    *,
    shard_index: int,
    shard_count: int,
    fixture_root: Path | None = None,
    run_id: str | None = None,
    timeout_seconds: int = 1800,
    port_readiness_probe: Callable[[dict[str, int]], bool] | None = None,
) -> dict[str, Any]:
    commit_sha, source_fingerprint, nodeids, manifest_fingerprint = _validate_manifest(manifest)
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    if fixture_root is not None:
        legacy_fixture = Path(fixture_root).expanduser()
        if legacy_fixture.is_symlink() or legacy_fixture.exists():
            raise ValueError("shard fixture root must be unique and not already exist")
        if not is_temporary_path(legacy_fixture):
            raise ValueError("shard fixture root must be inside a temporary root")

    assigned = select_shard_nodeids(nodeids, shard_index, shard_count)
    started_at = _timestamp()
    monotonic_started = time.perf_counter()

    runtime: ShardRuntime | None = None
    try:
        runtime = allocate_shard_runtime(
            project_root=Path(project_root),
            run_id=run_id or f"shard-{uuid.uuid4().hex}",
            shard_index=shard_index,
            fixture_root=fixture_root,
        )
        runtime.validate_isolation()
    except RuntimeError:
        cleanup = runtime.cleanup() if runtime is not None else {
            "status": "PASS", "failureCode": None, "leakedFiles": [],
            "leakedLocks": [], "leakedProcesses": [],
        }
        return _artifact(
            status="BLOCKED", failure_code="runtime_allocation_failed", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="runtime allocation failed",
            cleanup_report=cleanup,
        )
    except ValueError:
        cleanup = runtime.cleanup() if runtime is not None else {
            "status": "PASS", "failureCode": None, "leakedFiles": [],
            "leakedLocks": [], "leakedProcesses": [],
        }
        return _artifact(
            status="BLOCKED", failure_code="isolation_violation", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="runtime isolation validation failed",
            cleanup_report=cleanup,
        )

    cleanup_finished = False

    def finish(**kwargs: Any) -> dict[str, Any]:
        nonlocal cleanup_finished
        cleanup_finished = True
        cleanup = runtime.cleanup()
        if cleanup["status"] != "PASS":
            kwargs["status"] = "BLOCKED"
            kwargs["failure_code"] = "isolation_violation"
        return _artifact(cleanup_report=cleanup, **kwargs)

    if not assigned:
        return finish(
            status="PASS", failure_code=None, commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=[], executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout="", stderr="",
        )

    if port_readiness_probe is None:
        return finish(
            status="BLOCKED", failure_code="port_handoff_requires_readiness", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="explicit child bind/readiness probe is required",
        )
    try:
        handoff = getattr(runtime, "handoff_ports_with_readiness", None)
        if not callable(handoff):
            raise RuntimeError("runtime does not implement bind/readiness handoff")
        handoff(port_readiness_probe)
    except (OSError, RuntimeError, ValueError) as exc:
        return finish(
            status="BLOCKED", failure_code="port_handoff_failed", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr=str(exc),
        )

    env = os.environ.copy()
    env.update(runtime.environment())
    collect_argv = [sys.executable, "-m", "pytest", "--collect-only", "-q", "--sandbox-preflight", "required", *assigned]
    run_argv = [sys.executable, "-m", "pytest", "-q", "--sandbox-preflight", "required", *assigned]
    stdout = stderr = ""
    try:
        collected = _run_pytest_command(
            collect_argv,
            cwd=Path(project_root).resolve(),
            env=env,
            timeout=timeout_seconds,
            runtime=runtime,
        )
        collect_stdout, collect_stderr = _as_text(collected.stdout), _as_text(collected.stderr)
        collected_nodeids = sorted(_parse_nodeids(f"{collect_stdout}\n{collect_stderr}"))
        if collected.returncode != 0 or collected_nodeids != assigned:
            return finish(
                status="FAIL", failure_code="collection_mismatch", commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
                result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
                started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
                stdout=collect_stdout, stderr=collect_stderr,
            )
        completed = _run_pytest_command(
            run_argv,
            cwd=Path(project_root).resolve(),
            env=env,
            timeout=timeout_seconds,
            runtime=runtime,
        )
        stdout, stderr = _as_text(completed.stdout), _as_text(completed.stderr)
        result = _parse_summary(f"{stdout}\n{stderr}")
        status = "PASS" if completed.returncode == 0 and result["failed"] == 0 else "FAIL"
        failure_code = None if status == "PASS" else "pytest_failed"
        return finish(
            status=status, failure_code=failure_code, commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=assigned,
            result=result, started_at=started_at, monotonic_started=monotonic_started,
            finished_at=None,
            stdout=stdout, stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _as_text(exc.output), _as_text(exc.stderr)
        return finish(
            status="BLOCKED", failure_code="timeout", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout=stdout, stderr=stderr,
        )
    except OSError as exc:
        return finish(
            status="BLOCKED", failure_code="runner_os_error", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout="", stderr=str(exc),
        )
    except RuntimeError:
        return finish(
            status="BLOCKED", failure_code="runtime_process_launch_failed", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout="", stderr="runtime process launch failed",
        )
    except Exception as exc:
        return finish(
            status="BLOCKED", failure_code="runner_unexpected_error", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
            stdout=stdout, stderr=str(exc),
        )
    except KeyboardInterrupt:
        runtime.terminate_process_groups(force=True)
        raise
    finally:
        if not cleanup_finished:
            runtime.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--port-readiness-file",
        type=Path,
        help="Temporary marker written by the external service bootstrap after all profile ports accept TCP.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        assigned = select_shard_nodeids(
            manifest["nodeids"], args.shard_index, args.shard_count
        )
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    readiness_probe = None
    if assigned:
        if args.port_readiness_file is None:
            parser.error(
                "--port-readiness-file is required for non-empty shards; "
                "the external bootstrap must prove child bind/readiness before pytest starts"
            )
        readiness_file = args.port_readiness_file.expanduser()
        if readiness_file.is_symlink() or not is_temporary_path(readiness_file):
            parser.error("--port-readiness-file must be a non-symlink path inside a temporary root")

        def readiness_probe(ports: dict[str, int]) -> bool:
            if readiness_file.is_symlink() or not readiness_file.is_file():
                return False
            for port in ports.values():
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        pass
                except OSError:
                    return False
            return True

    result = run_pytest_shard(
        args.project_root, manifest, shard_index=args.shard_index, shard_count=args.shard_count,
        fixture_root=args.fixture_root, run_id=args.run_id, timeout_seconds=args.timeout,
        port_readiness_probe=readiness_probe,
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
