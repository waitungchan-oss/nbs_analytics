"""Run the existing Hermes post-change check as a bounded release gate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.agents.evidence_models import canonical_fingerprint


_TAIL = 4000
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN = re.compile(r"(?i)(?:writes?\s*[=:]\s*[1-9]|approval\s*[=:]\s*[1-9]|dispatch\s*[=:]\s*[1-9]|gateway\s*[=:]\s*(?:start|run|write))")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identity(commit_sha: str, source_fingerprint: str) -> None:
    if not _SHA40.fullmatch(commit_sha):
        raise ValueError("commit must be a 40-character SHA")
    if not _SHA64.fullmatch(source_fingerprint):
        raise ValueError("source fingerprint must be a 64-character SHA-256")


def _safe_argv(argv: list[str]) -> list[str]:
    return [Path(value).name if Path(value).is_absolute() else value for value in argv]


def run_hermes_gate(
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
    argv = list(command or [sys.executable, str(root / "scripts" / "hermes_post_change_check.py"), "--skip-monitor", "--json"])
    started = _timestamp()
    status = "FAIL"
    report: dict = {}
    metadata: dict = {"commandId": "hermes-post-change", "argv": _safe_argv(argv), "readOnly": True, "exitCode": None}
    stdout = stderr = ""
    try:
        completed = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout_seconds, check=False)
        stdout, stderr = completed.stdout or "", completed.stderr or ""
        metadata["exitCode"] = completed.returncode
        try:
            report = json.loads(stdout)
        except json.JSONDecodeError:
            metadata["failureCode"] = "malformed_report"
        if not isinstance(report, dict):
            metadata["failureCode"] = "malformed_report"
        elif _FORBIDDEN.search(json.dumps(report, ensure_ascii=False)):
            metadata["failureCode"] = "read_only_boundary_violation"
        elif completed.returncode != 0 or report.get("overallStatus") != "pass":
            metadata["failureCode"] = "hermes_nonpass"
        else:
            status = "PASS"
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.output or "", exc.stderr or ""
        metadata.update({"failureCode": "timeout", "timeoutSeconds": timeout_seconds})
        status = "BLOCKED"
    result = {
        "overallStatus": report.get("overallStatus") if isinstance(report, dict) else None,
        "reportFingerprint": canonical_fingerprint(report) if isinstance(report, dict) else None,
        "readOnlyIndicators": {"writes": 0, "approvals": 0, "dispatches": 0},
    }
    unsigned = {
        "schemaVersion": "hermes-gate-v1", "gate": "hermes", "status": status,
        "commitSha": commit_sha, "sourceFingerprint": source_fingerprint,
        "startedAt": started, "finishedAt": _timestamp(), "result": result,
        "metadata": {**metadata, "stdoutTail": stdout[-_TAIL:], "stderrTail": stderr[-_TAIL:]},
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    evidence = run_hermes_gate(args.project_root, args.commit_sha, args.source_fingerprint, timeout_seconds=args.timeout)
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if evidence["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
