"""Build a bounded, source-bound preflight for the opt-in shard rollout."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_rollout_models import RolloutConfig, build_rollout_preflight
from backend.agents.agent_runtime import resolve_runtime_output_path


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("preflight input must be a JSON object")
    return payload


def _optional_object(path: Path | None, fallback: dict[str, Any]) -> dict[str, Any]:
    return fallback if path is None else _load_object(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--runner-capability", type=Path)
    parser.add_argument("--isolation", type=Path)
    parser.add_argument("--ui-cache", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_rollout_preflight(
            config=RolloutConfig.from_environment(os.environ),
            commit_sha=args.commit_sha,
            source_fingerprint=args.source_fingerprint,
            manifest=_load_object(args.manifest),
            runner_capability=_optional_object(
                args.runner_capability,
                {"sandbox": "unavailable", "interpreter": "unqualified"},
            ),
            isolation=_optional_object(
                args.isolation,
                {"fixtureRoots": "unknown", "sqlite": "unknown", "cache": "unknown", "processes": "unknown"},
            ),
            ui_cache=_optional_object(
                args.ui_cache,
                {"status": "BLOCKED", "sourceMatched": False, "reason": "missing_ui_fixture"},
            ),
        )
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = resolve_runtime_output_path(args.project_root.resolve(), str(args.output))
            output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"acceptance shard rollout preflight blocked: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
