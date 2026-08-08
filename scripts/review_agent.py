from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.agent_runtime import SubprocessAgentRunner, resolve_runtime_output_path
from backend.agents.context_agent_service import context_summary_from_evidence_payload
from backend.agents.evidence_collector import EvidenceCollector, EvidencePolicy
from backend.agents.implementation_models import ImplementationTaskContract
from backend.agents.review_agent_service import (
    build_review_evidence_payload,
    format_review_markdown,
    run_review_batches,
)


def exit_code_for_verdict(verdict: str | None) -> int:
    if verdict in {None, "pass"}:
        return 0
    if verdict == "changes_required":
        return 1
    if verdict == "blocked":
        return 2
    if verdict == "context_overflow":
        return 4
    return 5


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect or run read-only Review Agent evidence")
    parser.add_argument("--brief", required=True)
    parser.add_argument("--base", default="main")
    parser.add_argument(
        "--head",
        default="WORKTREE",
        help="Review head ref; use WORKTREE (or working-tree) for uncommitted changes.",
    )
    parser.add_argument("--context")
    parser.add_argument("--task-contract")
    parser.add_argument("--verification")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--agent-command")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output")
    return parser


def _read_object(path: str, label: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _read_verification(path: str) -> list[dict]:
    value = _read_object(path, "Verification evidence")
    if set(value) != {"commands"} or not isinstance(value["commands"], list):
        raise ValueError("Verification evidence must contain only a commands list")
    commands = value["commands"]
    expected = {"label", "argv", "exitCode", "stdoutTail", "stderrTail"}
    for item in commands:
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("Verification command schema is invalid")
        if not isinstance(item["label"], str) or not item["label"]:
            raise ValueError("Verification command label is invalid")
        if not isinstance(item["argv"], list) or not all(
            isinstance(argument, str) for argument in item["argv"]
        ):
            raise ValueError("Verification command argv is invalid")
        if not isinstance(item["exitCode"], int) or isinstance(item["exitCode"], bool):
            raise ValueError("Verification command exitCode is invalid")
        if not isinstance(item["stdoutTail"], str) or not isinstance(item["stderrTail"], str):
            raise ValueError("Verification command output tails are invalid")
    return commands


def _review_task_from_implementation_contract(path: Path) -> dict:
    payload = _read_object(str(path), "Implementation task contract")
    contract = ImplementationTaskContract.from_dict(payload)
    return {
        **contract.to_dict(),
        "scope": list(contract.allowed_write_paths),
        "forbidden": [
            "writes outside allowedWritePaths",
            "SQLite, baseline, revenue, business rules, export schema, or Git integration",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = EvidencePolicy.from_project(PROJECT_ROOT)
        output_path = (
            resolve_runtime_output_path(PROJECT_ROOT, args.output) if args.output else None
        )
        brief = (PROJECT_ROOT / args.brief).resolve()
        if not brief.is_file():
            return 2
        policy.resolve_read_path(brief)
        bundle = EvidenceCollector(PROJECT_ROOT, policy=policy).collect_review(
            brief, base_ref=args.base, head_ref=args.head,
        )
        if args.task_contract:
            task_contract_path = policy.resolve_input_path(Path(args.task_contract))
            bundle = replace(
                bundle,
                task=_review_task_from_implementation_contract(task_contract_path),
            )
        if args.collect_only:
            report = build_review_evidence_payload(
                bundle, context_summary={}, verification=[],
            )
        else:
            context_path = policy.resolve_input_path(Path(args.context)) if args.context else None
            verification_path = policy.resolve_input_path(Path(args.verification)) if args.verification else None
            context_summary = _read_object(str(context_path), "Context summary") if context_path else {}
            if context_summary.get("schemaVersion") == "context-evidence-v1":
                context_summary = context_summary_from_evidence_payload(context_summary)
            verification = _read_verification(str(verification_path)) if verification_path else []
            runner = None
            if args.agent_command:
                command = shlex.split(args.agent_command)
                if not command:
                    raise PermissionError("Agent command cannot be empty")
                runner = SubprocessAgentRunner(
                    command, allowed_executables=policy.agent_executables,
                )
            instructions = (PROJECT_ROOT / "docs/agents/REVIEW_AGENT_CONTRACT.md").read_text(
                encoding="utf-8"
            )
            report = run_review_batches(
                bundle,
                project_root=PROJECT_ROOT,
                context_summary=context_summary,
                verification=verification,
                runner=runner,
                runtime_root=PROJECT_ROOT / ".nbs_agent_runtime",
                instructions=instructions,
                strict=args.strict,
                input_token_limit=policy.review_input_tokens,
                output_token_limit=policy.review_output_tokens,
            )
        rendered = (
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.format == "json"
            else format_review_markdown(report)
        )
        if output_path is None:
            sys.stdout.write(rendered)
        else:
            output_path.write_text(rendered, encoding="utf-8")
        return 0 if args.collect_only else exit_code_for_verdict(report.get("verdict"))
    except FileNotFoundError:
        return 2
    except (PermissionError, ValueError, json.JSONDecodeError, KeyError, TypeError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3 if isinstance(exc, PermissionError) else 5


if __name__ == "__main__":
    raise SystemExit(main())
