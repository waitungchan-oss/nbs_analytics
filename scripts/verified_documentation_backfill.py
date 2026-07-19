from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from backend.agents.verified_backfill_service import VerifiedBackfillService
from backend.agents.workflow_store import WorkflowStore


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a verified documentation backfill run")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--no-notify", action="store_true")
    return parser.parse_args(argv)


def build_service(no_notify: bool) -> VerifiedBackfillService:
    root = Path.cwd()
    return VerifiedBackfillService(root, store=WorkflowStore(root), notify=not no_notify)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_service(args.no_notify).create(source_commit=args.source_commit, reason=args.reason)
    except (OSError, subprocess.TimeoutExpired):
        result = {"status": "blocked", "reason": "service_io_failed"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
