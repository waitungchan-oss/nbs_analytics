"""Run full pytest as a bounded, commit-bound release gate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.evidence_models import canonical_fingerprint


_TAIL = 4000
_SUMMARY_SEGMENT = re.compile(
    r"(?P<count>\d+)\s+(?P<kind>failed|errors?|passed|skipped|warnings?)\b"
)
_SUMMARY_DURATION = re.compile(r"\bin\s+(?P<duration>[0-9.]+)s\b")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identity(commit_sha: str, source_fingerprint: str) -> None:
    if not _SHA40.fullmatch(commit_sha):
        raise ValueError("commit must be a 40-character SHA")
    if not _SHA64.fullmatch(source_fingerprint):
        raise ValueError("source fingerprint must be a 64-character SHA-256")


def _parse_summary(output: str) -> dict:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        segments = list(_SUMMARY_SEGMENT.finditer(line))
        if not segments:
            continue
        counts = {"failed": 0, "errors": 0, "passed": 0, "skipped": 0}
        for segment in segments:
            kind = segment.group("kind")
            if kind.startswith("error"):
                counts["errors"] += int(segment.group("count"))
            elif kind in counts:
                counts[kind] += int(segment.group("count"))
        if not any(counts.values()):
            continue
        duration = _SUMMARY_DURATION.search(line)
        return {
            "passed": counts["passed"],
            "failed": counts["failed"] + counts["errors"],
            "skipped": counts["skipped"],
            "durationSeconds": float(duration.group("duration") if duration else 0),
        }
    raise ValueError("pytest summary is malformed")


def _safe_argv(argv: list[str]) -> list[str]:
    return [Path(value).name if Path(value).is_absolute() else value for value in argv]


def _approved_argv(project_root: Path, command: list[str] | None) -> list[str]:
    argv = list(command or [sys.executable, "-m", "pytest", "-q"])
    if len(argv) not in {4, 6} or argv[1:4] != ["-m", "pytest", "-q"]:
        raise ValueError("pytest command is not allowlisted")
    if Path(argv[0]).name not in {"python", "python3", Path(sys.executable).name}:
        raise ValueError("pytest command is not allowlisted")
    if len(argv) == 6 and argv[4:] != ["--sandbox-preflight", "required"]:
        raise ValueError("pytest command is not allowlisted")
    if len(argv) == 4:
        argv += ["--sandbox-preflight", "required"]
    return argv


def run_full_pytest_gate(
    project_root: Path,
    commit_sha: str,
    source_fingerprint: str,
    command: list[str] | None = None,
    timeout_seconds: int = 1800,
) -> dict:
    _identity(commit_sha, source_fingerprint)
    if timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    root = Path(project_root).resolve()
    argv = _approved_argv(root, command)
    started = _timestamp()
    status = "FAIL"
    result = {"passed": 0, "failed": 0, "skipped": 0, "durationSeconds": 0.0}
    metadata = {"commandId": "full-pytest", "argv": _safe_argv(argv), "exitCode": None}
    stdout = stderr = ""
    try:
        completed = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        metadata["exitCode"] = completed.returncode
        combined = f"{stdout}\n{stderr}"
        if "blocked_environment" in combined or "sandbox capability" in combined.lower() and "blocked" in combined.lower():
            status = "BLOCKED"
            metadata["failureCode"] = "sandbox_capability_blocked"
        else:
            try:
                result = _parse_summary(combined)
            except ValueError:
                metadata["failureCode"] = "malformed_summary"
            status = "PASS" if completed.returncode == 0 and "failureCode" not in metadata and result["failed"] == 0 else "FAIL"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output or ""
        stderr = exc.stderr or ""
        metadata.update({"failureCode": "timeout", "timeoutSeconds": timeout_seconds})
        status = "BLOCKED"
    except OSError as exc:
        metadata.update({"failureCode": "runner_os_error", "errorType": type(exc).__name__})
        stderr = str(exc)
        status = "BLOCKED"
    finished = _timestamp()
    unsigned = {
        "schemaVersion": "full-pytest-gate-v1", "gate": "full_pytest", "status": status,
        "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
        "startedAt": started, "finishedAt": finished, "result": result,
        "metadata": {**metadata, "stdoutTail": stdout[-_TAIL:], "stderrTail": stderr[-_TAIL:]},
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--sandbox-preflight", choices=("required",), default="required")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    evidence = run_full_pytest_gate(args.project_root, args.commit_sha, args.source_fingerprint, timeout_seconds=args.timeout)
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if evidence["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
