"""Collect a source-bound pytest nodeid manifest for diagnostics."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_telemetry import build_gate_telemetry
from backend.agents.evidence_models import canonical_fingerprint


_TAIL = 4000
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _identity(commit_sha: str, source_fingerprint: str) -> None:
    if not _SHA40.fullmatch(commit_sha):
        raise ValueError("commit must be a 40-character SHA")
    if not _SHA64.fullmatch(source_fingerprint):
        raise ValueError("source fingerprint must be a 64-character SHA-256")


def _parse_nodeids(output: str) -> list[str]:
    nodeids = []
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith(("=", "<")) or "::" not in candidate:
            continue
        if not candidate.startswith(("tests/", "backend/", "scripts/")):
            continue
        nodeids.append(candidate)
    return nodeids


def _manifest_fingerprint(commit_sha: str, source_fingerprint: str, nodeids: list[str]) -> str:
    return canonical_fingerprint({
        "schemaVersion": "pytest-test-manifest-v1",
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "nodeids": nodeids,
    })


def collect_pytest_manifest(
    project_root: Path,
    *,
    commit_sha: str,
    source_fingerprint: str,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    _identity(commit_sha, source_fingerprint)
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    root = Path(project_root).resolve()
    argv = [sys.executable, "-m", "pytest", "--collect-only", "-q", "--sandbox-preflight", "required"]
    started_at = _timestamp()
    monotonic_started = time.perf_counter()
    status = "FAIL"
    failure_code = None
    stdout = stderr = ""
    nodeids: list[str] = []
    try:
        completed = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
        stdout, stderr = _as_text(completed.stdout), _as_text(completed.stderr)
        nodeids = _parse_nodeids(f"{stdout}\n{stderr}")
        if completed.returncode != 0:
            failure_code = "collection_failed"
        elif len(nodeids) != len(set(nodeids)):
            failure_code = "duplicate_nodeid"
        elif not nodeids:
            failure_code = "empty_manifest"
        else:
            status = "PASS"
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _as_text(exc.output), _as_text(exc.stderr)
        failure_code = "timeout"
        status = "BLOCKED"
    except OSError as exc:
        stderr = str(exc)
        failure_code = "runner_os_error"
        status = "BLOCKED"

    nodeids = sorted(nodeids)
    finished_at = _timestamp()
    unsigned = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": status,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "nodeids": nodeids,
        "manifestFingerprint": _manifest_fingerprint(commit_sha, source_fingerprint, nodeids),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "metadata": {
            "commandId": "pytest-collect-only",
            "argv": [Path(value).name if Path(value).is_absolute() else value for value in argv],
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = collect_pytest_manifest(
            args.project_root,
            commit_sha=args.commit_sha,
            source_fingerprint=args.source_fingerprint,
            timeout_seconds=args.timeout,
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"pytest manifest blocked: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
