#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from backend.agents.short_term_offload_policy import ShortTermOffloadPolicy
from backend.agents.short_term_offload_store import ShortTermOffloadStore


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _store(root_value: str) -> ShortTermOffloadStore:
    root = Path(root_value).resolve()
    expected = (_PROJECT_ROOT / ".nbs_agent_runtime" / "short-term-offload").resolve()
    if root != expected or root.name != "short-term-offload" or root.parent.name != ".nbs_agent_runtime":
        raise ValueError("runtime root must be <project>/.nbs_agent_runtime/short-term-offload")
    return ShortTermOffloadStore(root.parent.parent, policy=ShortTermOffloadPolicy())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only short-term offload inspection")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--runtime-root", required=True)
    inspect.add_argument("--run-id", required=True)
    inspect.add_argument("--session-id", required=True)
    inspect.add_argument("--ref-id", required=True)
    inspect.add_argument("--sha256", required=False)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--runtime-root", required=True)
    cleanup.add_argument("--now", required=True)
    args = parser.parse_args(argv)
    try:
        store = _store(args.runtime_root)
        if args.command == "inspect":
            artifact = store.read(args.run_id, args.session_id, args.ref_id)
            if artifact is None or args.sha256 and artifact.content_sha256 != args.sha256:
                print(json.dumps({"status": "missing_or_mismatch"}, sort_keys=True))
                return 2
            print(json.dumps({"status": "ready", "summary": artifact.summary, "contentSha256": artifact.content_sha256}, sort_keys=True))
            return 0
        now = datetime.fromisoformat(args.now)
        removed = store.cleanup_expired(now=now)
        print(json.dumps({"status": "ready", "removedRefIds": removed}, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)[:256]}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
