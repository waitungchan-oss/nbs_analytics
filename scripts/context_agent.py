from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.agent_runtime import SubprocessAgentRunner, resolve_runtime_output_path
from backend.agents.context_agent_service import (
    build_context_report,
    context_bundle_from_payload,
    format_context_markdown,
)
from backend.agents.context_memory_hub_adapter import query_context_memory
from backend.agents.evidence_collector import EvidenceCollector, EvidencePolicy
from backend.agents.memory_hub_models import MemoryQuery, RuntimeIdentity


def exit_code_for_status(status: str | None) -> int:
    if status in {None, "ready"}:
        return 0
    if status in {"blocked_missing_brief", "blocked_missing_evidence", "dirty_worktree"}:
        return 2
    if status == "context_overflow":
        return 4
    return 5


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect or summarize read-only context evidence")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--brief")
    source.add_argument("--bundle")
    parser.add_argument("--base", default="main")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--agent-command")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output")
    return parser


def _collect_memory_hints(query_text: str) -> dict[str, object]:
    """Query the fixed C2 composition for collect-only enrichment."""
    query = MemoryQuery.from_parts(
        query=query_text or "context governance evidence",
        consumer_id="context-agent",
        scope="project",
        memory_kinds=("governance", "evidence", "skill"),
        max_items=3,
        max_bytes=6000,
        timeout_ms=800,
    )
    identity = RuntimeIdentity.from_parts(project_id="nbs_analytics", consumer_id="context-agent")
    return query_context_memory(project_root=PROJECT_ROOT, identity=identity, query=query)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = EvidencePolicy.from_project(PROJECT_ROOT)
        if args.output:
            output_path = resolve_runtime_output_path(PROJECT_ROOT, args.output)
        else:
            output_path = None
        if args.bundle:
            bundle = context_bundle_from_payload(json.loads(Path(args.bundle).read_text(encoding="utf-8")))
        else:
            brief = (PROJECT_ROOT / args.brief).resolve()
            if not brief.is_file():
                return 2
            policy.resolve_read_path(brief)
            include_paths = tuple((PROJECT_ROOT / item).resolve() for item in args.include)
            bundle = EvidenceCollector(PROJECT_ROOT, policy=policy).collect_context(
                brief, base_ref=args.base, include_paths=include_paths, queries=tuple(args.query),
            )
        runner = None
        if not args.collect_only:
            if args.agent_command:
                command = shlex.split(args.agent_command)
                if not command:
                    raise PermissionError("Agent command cannot be empty")
                runner = SubprocessAgentRunner(command, allowed_executables=policy.agent_executables)
        instructions = (PROJECT_ROOT / "docs/agents/CONTEXT_AGENT_CONTRACT.md").read_text(encoding="utf-8")
        memory_hints = None
        if args.collect_only:
            memory_result = _collect_memory_hints(" ".join(args.query))
            if memory_result.get("status") == "ready":
                memory_hints = memory_result.get("memoryHints")
        report = build_context_report(
            bundle, runner=runner, project_root=PROJECT_ROOT, runtime_root=PROJECT_ROOT / ".nbs_agent_runtime",
            instructions=instructions, collect_only=args.collect_only, memory_hints=memory_hints,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else format_context_markdown(report)
        if output_path is None:
            sys.stdout.write(rendered)
        else:
            output_path.write_text(rendered, encoding="utf-8")
        return exit_code_for_status(report.get("status"))
    except FileNotFoundError:
        return 2
    except (PermissionError, ValueError, json.JSONDecodeError, KeyError, TypeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3 if isinstance(exc, PermissionError) else 5


if __name__ == "__main__":
    raise SystemExit(main())
