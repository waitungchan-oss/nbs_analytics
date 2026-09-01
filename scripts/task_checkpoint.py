"""Read-only Task checkpoint inspection and validation CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.task_checkpoint_validator import (
    CheckpointValidationError,
    inspect_git_state,
    validate_checkpoint,
)
from backend.agents.task_checkpoint_models import CHECKPOINT_CLI_SCHEMA


def _load(path: str) -> dict[str, Any]:
    candidate = Path(path).resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 1_000_000:
        raise ValueError("input must be a bounded regular file")
    value = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def _inspect(project_root: Path) -> dict[str, object]:
    state = inspect_git_state(project_root)
    return {
        "schemaVersion": CHECKPOINT_CLI_SCHEMA,
        "status": "ready" if not state.status_lines else "blocked",
        "reasons": [] if not state.status_lines else ["dirty_worktree"],
        "git": {
            "head": state.head,
            "changedFiles": list(state.changed_files),
            "stagedFiles": list(state.staged_files),
            "dirtyCount": len(state.status_lines),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("inspect")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--task-contract", required=True)
    validate.add_argument("--verification", required=True)
    validate.add_argument("--expected-parent-head", required=True)
    args = parser.parse_args(argv)
    try:
        project_root = Path(args.project_root).resolve(strict=True)
        if args.action == "inspect":
            payload = _inspect(project_root)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0 if payload["status"] == "ready" else 2
        result = validate_checkpoint(
            project_root,
            _load(args.task_contract),
            _load(args.verification),
            expected_parent_head=args.expected_parent_head,
        )
        payload = {
            "schemaVersion": CHECKPOINT_CLI_SCHEMA,
            "status": result.status,
            "reasons": list(result.reasons),
            "evidence": result.evidence.to_dict() if result.evidence else None,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if result.status in {"ready", "no_op"} else 2
    except (OSError, ValueError, CheckpointValidationError, json.JSONDecodeError) as exc:
        print(json.dumps({"schemaVersion": CHECKPOINT_CLI_SCHEMA, "status": "invalid_evidence", "reasons": [str(exc)[:256]]}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
