"""Run one opt-in, diagnostic-only pytest shard against an isolated fixture."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_paths import is_temporary_path
from backend.agents.acceptance_telemetry import build_gate_telemetry
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
    fixture_root: Path,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    commit_sha, source_fingerprint, nodeids, manifest_fingerprint = _validate_manifest(manifest)
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    fixture = Path(fixture_root).expanduser().resolve()
    if fixture.is_symlink() or not is_temporary_path(fixture):
        raise ValueError("shard fixture must be a non-symlink temporary path")
    if fixture.exists():
        raise ValueError("shard fixture root must be unique and not already exist")
    fixture.mkdir(parents=True, exist_ok=True)
    assigned = select_shard_nodeids(nodeids, shard_index, shard_count)
    started_at = _timestamp()
    monotonic_started = time.perf_counter()
    if not assigned:
        return _artifact(
            status="PASS", failure_code=None, commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=[], executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout="", stderr="",
        )

    env = os.environ.copy()
    env.update({
        "NBS_ANALYTICS_DB_FILE": str(fixture / "shard.db"),
        "NBS_ANALYTICS_CACHE_DIR": str(fixture / "cache"),
        "NBS_ANALYTICS_COORDINATION_DB": str(fixture / "coordination.db"),
    })
    collect_argv = [sys.executable, "-m", "pytest", "--collect-only", "-q", "--sandbox-preflight", "required", *assigned]
    run_argv = [sys.executable, "-m", "pytest", "-q", "--sandbox-preflight", "required", *assigned]
    stdout = stderr = ""
    try:
        collected = subprocess.run(collect_argv, cwd=Path(project_root).resolve(), env=env, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        collect_stdout, collect_stderr = _as_text(collected.stdout), _as_text(collected.stderr)
        collected_nodeids = sorted(_parse_nodeids(f"{collect_stdout}\n{collect_stderr}"))
        if collected.returncode != 0 or collected_nodeids != assigned:
            return _artifact(
                status="FAIL", failure_code="collection_mismatch", commit_sha=commit_sha,
                source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
                shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
                result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
                started_at=started_at, finished_at=None, monotonic_started=monotonic_started,
                stdout=collect_stdout, stderr=collect_stderr,
            )
        completed = subprocess.run(run_argv, cwd=Path(project_root).resolve(), env=env, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        stdout, stderr = _as_text(completed.stdout), _as_text(completed.stderr)
        result = _parse_summary(f"{stdout}\n{stderr}")
        status = "PASS" if completed.returncode == 0 and result["failed"] == 0 else "FAIL"
        failure_code = None if status == "PASS" else "pytest_failed"
        return _artifact(
            status=status, failure_code=failure_code, commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=assigned,
            result=result, started_at=started_at, monotonic_started=monotonic_started,
            finished_at=None,
            stdout=stdout, stderr=stderr,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _as_text(exc.output), _as_text(exc.stderr)
        return _artifact(
            status="BLOCKED", failure_code="timeout", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout=stdout, stderr=stderr,
        )
    except OSError as exc:
        return _artifact(
            status="BLOCKED", failure_code="runner_os_error", commit_sha=commit_sha,
            source_fingerprint=source_fingerprint, manifest_fingerprint=manifest_fingerprint,
            shard_index=shard_index, shard_count=shard_count, assigned=assigned, executed=[],
            result={"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0},
            started_at=started_at, finished_at=None, monotonic_started=monotonic_started, stdout="", stderr=str(exc),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = run_pytest_shard(
        args.project_root, manifest, shard_index=args.shard_index, shard_count=args.shard_count,
        fixture_root=args.fixture_root, timeout_seconds=args.timeout,
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
