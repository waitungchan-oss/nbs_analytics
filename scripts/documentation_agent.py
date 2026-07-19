from __future__ import annotations

import argparse
import re
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.documentation_workflow import DocumentationWorkflow


DOCUMENTATION_EXIT_CODES = {
    "applied": 0, "preview_ready": 0, "no_documentation_needed": 0,
    "awaiting_target_approval": 1, "blocked": 2, "context_overflow": 4,
    "invalid_agent_output": 5,
}
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w])/(?:[^\s'\"<>]+)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the NBS documentation sidecar.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agent-command")
    parser.add_argument("--obsidian-vault")
    parser.add_argument("--apply-brief", action="store_true")
    parser.add_argument("--approve-target", action="append", default=[], choices=("system_map", "adr"))
    return parser


def _redact(value: Any) -> Any:
    project = PROJECT_ROOT.resolve()
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if not isinstance(value, str):
        return value
    def redact_path(match: re.Match[str]) -> str:
        try:
            Path(match.group(0)).resolve().relative_to(project)
        except ValueError:
            return "[REDACTED_PATH]"
        return match.group(0)
    return _ABSOLUTE_PATH_RE.sub(redact_path, value)


def _render(payload: Any) -> None:
    if is_dataclass(payload):
        payload = asdict(payload)
    sys.stdout.write(json.dumps(_redact(payload), ensure_ascii=False, sort_keys=True) + "\n")


def _exit_code(status: str) -> int:
    return DOCUMENTATION_EXIT_CODES.get(status, 5)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = DocumentationWorkflow(PROJECT_ROOT).run(
            args.run_id,
            agent_command=args.agent_command,
            obsidian_vault=Path(args.obsidian_vault) if args.obsidian_vault else None,
            apply_brief=args.apply_brief,
            approved_targets=frozenset(args.approve_target),
        )
        _render(result)
        return _exit_code(result.get("status", "runtime_error"))
    except SystemExit as exc:
        if exc.code == 2:
            _render({"status": "blocked", "message": "invalid command-line arguments"})
            return 2
        raise
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        sys.stderr.write(_redact(str(exc)) + "\n")
        _render({"status": "blocked", "message": str(exc)})
        return 2
    except Exception as exc:
        sys.stderr.write(_redact(str(exc)) + "\n")
        _render({"status": "runtime_error", "message": str(exc)})
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
