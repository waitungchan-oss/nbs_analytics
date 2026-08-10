from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.runner_capability_evidence import (
    RunnerCapabilityEvidenceError,
    RunnerCapabilityRun,
    build_capability_evidence,
)


MAX_INPUT_BYTES = 64 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build bounded runner capability evidence from two runtime inputs")
    parser.add_argument("--control", required=True)
    parser.add_argument("--treatment", required=True)
    parser.add_argument("--git-head", required=True)
    parser.add_argument("--task-fingerprint", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _runtime_path(project_root: Path, raw_path: str, *, output: bool) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise RunnerCapabilityEvidenceError("runtime paths must be relative")
    runtime_root = project_root.resolve(strict=False) / ".nbs_agent_runtime"
    if runtime_root.is_symlink():
        raise RunnerCapabilityEvidenceError("runtime root must not be a symlink")
    path = runtime_root / candidate
    current = runtime_root
    for part in candidate.parts:
        if part in {"", ".", ".."}:
            raise RunnerCapabilityEvidenceError("runtime path traversal is not allowed")
        current = current / part
        if current.exists() and current.is_symlink():
            raise RunnerCapabilityEvidenceError("runtime path must not contain symlinks")
    try:
        path.resolve(strict=False).relative_to(runtime_root.resolve(strict=False))
    except ValueError as exc:
        raise RunnerCapabilityEvidenceError("runtime path is out of root") from exc
    if output:
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise RunnerCapabilityEvidenceError("output must be a regular file")
    elif not path.exists() or path.is_symlink() or not path.is_file():
        raise RunnerCapabilityEvidenceError("input must be an existing regular file")
    return path


def _read_run(project_root: Path, raw_path: str) -> RunnerCapabilityRun:
    path = _runtime_path(project_root, raw_path, output=False)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise RunnerCapabilityEvidenceError("runtime input exceeds byte cap")
    try:
        payload: Any = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerCapabilityEvidenceError("runtime input is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RunnerCapabilityEvidenceError("runtime input must be a JSON object")
    return RunnerCapabilityRun.from_dict(payload)


def run(args: argparse.Namespace, *, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    root = Path(project_root)
    control = _read_run(root, args.control)
    treatment = _read_run(root, args.treatment)
    evidence = build_capability_evidence(
        control.unsigned_dict(), treatment.unsigned_dict(),
        expected_git_head=args.git_head, expected_task_fingerprint=args.task_fingerprint,
    )
    output = _runtime_path(root, args.output, output=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise RunnerCapabilityEvidenceError("output must not be a symlink")
    output.write_text(
        json.dumps(evidence.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return evidence.to_dict()


def main(argv: list[str] | None = None, *, project_root: Path = PROJECT_ROOT) -> int:
    try:
        run(_parser().parse_args(argv), project_root=project_root)
    except (OSError, RunnerCapabilityEvidenceError, ValueError) as exc:
        print(f"runner capability evidence: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
