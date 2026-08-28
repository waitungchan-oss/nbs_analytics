from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.review_runner_profile import RunnerProfile, preflight_runner, probe_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Codex Review runner preflight")
    parser.add_argument("--model", required=True)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--cache")
    parser.add_argument(
        "--probe", action="store_true",
        help="run a short read-only live turn probe and report runner-capability-v1",
    )
    args = parser.parse_args(argv)
    executable = shutil.which(args.executable) if not Path(args.executable).is_absolute() else args.executable
    cache_root = os.environ.get("CODEX_HOME")
    cache = Path(args.cache) if args.cache else (
        Path(cache_root) if cache_root else Path.home() / ".codex"
    ) / "models_cache.json"
    profile = RunnerProfile(executable or args.executable, args.model, cache)
    if args.probe:
        receipt = probe_runner(profile)
        print(json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2))
        return 0 if receipt.status == "turn_ready" else 2
    result = preflight_runner(profile)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
