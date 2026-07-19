from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.workflow_notifications import build_notifier
from backend.agents.workflow_orchestrator import WorkflowOrchestrator
from backend.agents.workflow_retention import WorkflowRetention
from backend.agents.workflow_store import WorkflowStore
from backend.agents.documentation_workflow import DocumentationWorkflow


_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w])/(?:[^\s'\"<>]+)")
_EXIT_CODES = {
    "completed": 0,
    "awaiting_authorization": 0,
    "changes_required": 1,
    "blocked": 2,
    "context_overflow": 4,
    "invalid_agent_output": 4,
    "failed": 5,
    "runtime_error": 5,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the governed NBS agent workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", aliases=["start"])
    run.add_argument("--brief", required=True)
    run.add_argument("--context-agent-command")
    run.add_argument("--no-notify", action="store_true")

    approve = subparsers.add_parser("approve")
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--contract", required=True)
    approve.add_argument("--implementation-agent-command", required=True)
    approve.add_argument("--review-agent-command", required=True)
    approve.add_argument("--no-notify", action="store_true")

    status = subparsers.add_parser("status")
    status.add_argument("--run-id", required=True)
    subparsers.add_parser("list")

    prune = subparsers.add_parser("prune")
    mode = prune.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")

    document = subparsers.add_parser("document")
    document.add_argument("--run-id", required=True)
    document.add_argument("--agent-command")
    document.add_argument("--obsidian-vault")
    document.add_argument("--apply-brief", action="store_true")
    document.add_argument("--approve-target", action="append", default=[], choices=("system_map", "adr"))
    return parser


def _redact(value: Any) -> Any:
    secrets = sorted(
        (item for item in os.environ.values() if isinstance(item, str) and len(item) >= 4),
        key=len,
        reverse=True,
    )
    project = PROJECT_ROOT.resolve()
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, "[REDACTED]")

    def redact_path(match: re.Match[str]) -> str:
        candidate = Path(match.group(0))
        try:
            candidate.resolve().relative_to(project)
        except ValueError:
            return "[REDACTED_PATH]"
        return match.group(0)

    return _ABSOLUTE_PATH_RE.sub(redact_path, redacted)


def _render(payload: Any) -> None:
    if is_dataclass(payload):
        payload = asdict(payload)
    sys.stdout.write(json.dumps(_redact(payload), ensure_ascii=False, sort_keys=True) + "\n")


def _stderr(message: str) -> None:
    sys.stderr.write(f"{_redact(message)}\n")


def _exit_code(status: str) -> int:
    return _EXIT_CODES.get(status, 5)


def _run_housekeeping() -> None:
    retention = WorkflowRetention(PROJECT_ROOT)
    retention.apply(retention.plan(), dry_run=False)


def _orchestrator(*, notify: bool, housekeeping=None) -> WorkflowOrchestrator:
    return WorkflowOrchestrator(
        PROJECT_ROOT,
        notifier=build_notifier(enabled=notify),
        housekeeping=housekeeping,
        warning_sink=_stderr,
    )


def _safe_run_id(value: str) -> str:
    candidate = Path(value)
    if not value or value in {".", ".."} or candidate.is_absolute() or len(candidate.parts) != 1:
        raise PermissionError("run ID must name one workflow run")
    return value


def _list_runs() -> dict[str, list[dict]]:
    store = WorkflowStore(PROJECT_ROOT)
    runs = []
    for path in store.runs_root.iterdir():
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            run_id = _safe_run_id(path.name)
            status = store.load_status(run_id).to_dict()
        except (OSError, ValueError, PermissionError):
            continue
        runs.append(status)
    runs.sort(key=lambda item: (item["updatedAt"], item["runId"]), reverse=True)
    return {"runs": runs}


def _run(args: argparse.Namespace) -> tuple[dict, int]:
    if args.command in {"run", "start"}:
        status = _orchestrator(
            notify=not args.no_notify,
            housekeeping=_run_housekeeping,
        ).start(Path(args.brief), context_agent_command=args.context_agent_command, notify=not args.no_notify)
        return status.to_dict(), _exit_code(status.status)
    if args.command == "approve":
        status = _orchestrator(notify=not args.no_notify).approve(
            _safe_run_id(args.run_id),
            Path(args.contract),
            implementation_agent_command=args.implementation_agent_command,
            review_agent_command=args.review_agent_command,
            notify=not args.no_notify,
        )
        return status.to_dict(), _exit_code(status.status)
    if args.command == "status":
        status = WorkflowStore(PROJECT_ROOT).load_status(_safe_run_id(args.run_id))
        return status.to_dict(), _exit_code(status.status)
    if args.command == "list":
        return _list_runs(), 0
    if args.command == "prune":
        retention = WorkflowRetention(PROJECT_ROOT)
        report = retention.apply(retention.plan(), dry_run=not args.apply)
        payload = asdict(report) if is_dataclass(report) else report
        return {"dryRun": not args.apply, "retention": payload}, 0
    if args.command == "document":
        result = DocumentationWorkflow(PROJECT_ROOT).run(
            _safe_run_id(args.run_id),
            agent_command=args.agent_command,
            obsidian_vault=Path(args.obsidian_vault) if args.obsidian_vault else None,
            apply_brief=args.apply_brief,
            approved_targets=frozenset(args.approve_target),
        )
        documentation_exit_codes = {
            "applied": 0, "preview_ready": 0, "no_documentation_needed": 0,
            "awaiting_target_approval": 1, "blocked": 2, "context_overflow": 4,
            "invalid_agent_output": 5,
        }
        return result, documentation_exit_codes.get(result.get("status", "runtime_error"), 5)
    raise ValueError("unknown workflow command")


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code == 2:
            _render({"schemaVersion": "agent-workflow-cli-v1", "status": "blocked", "message": "invalid command-line arguments"})
            return 2
        raise
    try:
        payload, exit_code = _run(args)
        _render(payload)
        return exit_code
    except (FileNotFoundError, PermissionError) as exc:
        _stderr(str(exc))
        _render({"schemaVersion": "agent-workflow-cli-v1", "status": "blocked", "message": str(exc)})
        return 2
    except Exception as exc:
        _stderr(str(exc))
        _render({"schemaVersion": "agent-workflow-cli-v1", "status": "runtime_error", "message": str(exc)})
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
