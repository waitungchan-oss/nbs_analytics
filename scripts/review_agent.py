from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
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
from backend.agents.review_runner_profile import RunnerProfile, load_runner_profile, preflight_runner
from backend.agents.verification_evidence_writer import sha256_from_output, validate_verification_v1
from backend.agents.review_agent_service import (
    build_review_evidence_payload,
    format_review_markdown,
    run_review_batches,
)


def _merge_runner_diagnostics(
    provenance: list[str], preflight: list[str], recovery: list[str], *, limit: int = 4,
) -> list[str]:
    """Keep bounded diagnostics while retaining a concrete preflight cause."""
    if limit <= 0:
        return []
    primary = [*preflight, *recovery]
    if not primary:
        return provenance[:limit]
    if not provenance:
        return primary[:limit]
    return [*primary[: max(1, limit - 1)], *provenance[:1]][:limit]


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
    parser.add_argument(
        "--approved-brief",
        help="Approved brief identity required by strict Review; must equal --brief.",
    )
    parser.add_argument("--base", default="main")
    parser.add_argument(
        "--head",
        default="WORKTREE",
        help="Review head ref; use WORKTREE (or working-tree) for uncommitted changes.",
    )
    parser.add_argument("--context")
    parser.add_argument("--task-contract")
    parser.add_argument("--verification")
    parser.add_argument(
        "--memory-evidence",
        help="Optional precomputed Memory Hub evidence; never queried by Review Agent.",
    )
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--agent-command")
    parser.add_argument("--runner-profile", help="JSON runner profile for preflight")
    parser.add_argument("--runner-cache", help="Override Codex models cache path")
    parser.add_argument("--runner-model", help="Pinned model used for Codex preflight")
    parser.add_argument("--runner-timeout", type=int, default=300)
    parser.add_argument(
        "--preserve-dirty-path",
        action="append",
        default=[],
        help="Explicit process-only dirty path to omit from review evidence; repeatable.",
    )
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
    return list(validate_verification_v1(Path(path)))


def _verification_freshness_diagnostics(
    bundle,
    verification: list[dict],
    *,
    brief_sha: str,
    worktree_fingerprint: str,
    current_head_sha: str,
    brief_path: str,
) -> list[str]:
    by_label = {item.get("label"): item for item in verification}
    head = by_label.get("review-head-fingerprint")
    brief = by_label.get("review-brief-fingerprint")
    worktree = by_label.get("review-worktree-fingerprint")
    if not all((head, brief, worktree)):
        return ["Strict review verification evidence lacks current provenance commands."]
    if head["argv"] != ["git", "rev-parse", "HEAD"]:
        return ["Strict review HEAD fingerprint command is not the approved command."]
    if len(brief["argv"]) < 3 or brief["argv"][:3] != ["shasum", "-a", "256"]:
        return ["Strict review brief fingerprint command is not the approved command."]
    if len(brief["argv"]) != 4 or brief["argv"][3] != brief_path:
        return ["Strict review verification brief identity does not match the requested brief."]
    if worktree["argv"] != [
        "sh", "-c",
        "git status --porcelain --untracked-files=all -- . ':(exclude)docs/superpowers' ':(exclude).superpowers' | shasum -a 256",
    ]:
        return ["Strict review worktree fingerprint command is not the approved command."]
    expected_head = bundle.repository.get("headSha") or current_head_sha
    if head["exitCode"] != 0 or head["stdoutTail"].strip() != expected_head:
        return ["Strict review verification head fingerprint is stale."]
    if brief["exitCode"] != 0 or sha256_from_output(brief["stdoutTail"]) != brief_sha.lower():
        return ["Strict review verification brief fingerprint is stale."]
    if worktree["exitCode"] != 0 or sha256_from_output(worktree["stdoutTail"]) != worktree_fingerprint.lower():
        return ["Strict review verification worktree fingerprint is stale."]
    return []


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
        approved_brief_mismatch = bool(
            args.strict and args.agent_command and args.approved_brief != args.brief
        )
        policy.resolve_read_path(brief)
        bundle = EvidenceCollector(PROJECT_ROOT, policy=policy).collect_review(
            brief,
            base_ref=args.base,
            head_ref=args.head,
            preserve_dirty_paths=tuple(args.preserve_dirty_path),
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
            memory_path = policy.resolve_input_path(Path(args.memory_evidence)) if args.memory_evidence else None
            context_summary = _read_object(str(context_path), "Context summary") if context_path else {}
            if context_summary.get("schemaVersion") == "context-evidence-v1":
                context_summary = context_summary_from_evidence_payload(context_summary)
            verification = _read_verification(str(verification_path)) if verification_path else []
            provenance_diagnostics = []
            if args.strict:
                if not verification_path:
                    provenance_diagnostics = [
                        "Strict review requires a verification-v1 evidence bundle with current provenance."
                    ]
                brief_sha = hashlib.sha256(brief.read_bytes()).hexdigest()
                current_head = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
                )
                worktree_fingerprint = bundle.repository.get("reviewLaunchWorktreeFingerprint", "")
                if current_head.returncode != 0:
                    provenance_diagnostics = [
                        "Strict review HEAD launch-state probe failed; provenance is unavailable."
                    ]
                elif bundle.repository.get("reviewLaunchWorktreeProbeExitCode") != 0:
                    provenance_diagnostics = [
                        "Strict review worktree launch-state probe failed; provenance is unavailable."
                    ]
                elif verification_path:
                    provenance_diagnostics = _verification_freshness_diagnostics(
                        bundle,
                        verification,
                        brief_sha=brief_sha,
                        worktree_fingerprint=worktree_fingerprint,
                        current_head_sha=current_head.stdout.strip(),
                        brief_path=args.brief,
                    )
            memory_evidence = _read_object(str(memory_path), "Memory Hub evidence") if memory_path else None
            runner = None
            runner_diagnostics = list(provenance_diagnostics)
            if approved_brief_mismatch:
                runner_diagnostics.append(
                    "Strict review approved brief identity does not match --brief."
                )
            if args.agent_command:
                command = shlex.split(args.agent_command)
                if not command:
                    raise PermissionError("Agent command cannot be empty")
                executable = shutil.which(command[0])
                model = args.runner_model
                if model is None:
                    for index, argument in enumerate(command[:-1]):
                        if argument in {"-m", "--model"}:
                            model = command[index + 1]
                            break
                profile_path = args.runner_profile
                if profile_path:
                    profile = load_runner_profile(policy.resolve_input_path(Path(profile_path)))
                elif executable and model:
                    cache_root = os.environ.get("CODEX_HOME")
                    cache_path = Path(args.runner_cache) if args.runner_cache else (
                        Path(cache_root) if cache_root else Path.home() / ".codex"
                    ) / "models_cache.json"
                    profile = RunnerProfile(executable, model, cache_path)
                else:
                    profile = None
                if profile is not None and profile_path:
                    profile_executable = Path(profile.executable).resolve()
                    command_executable = Path(executable).resolve() if executable else None
                    # If the command is unresolved, let preflight_runner report the
                    # bounded executable/cache failure instead of masking it as a
                    # profile mismatch. A resolved command still must match exactly.
                    if (
                        (command_executable is not None and command_executable != profile_executable)
                        or (model and profile.model != model)
                    ):
                        runner_diagnostics.append(
                            "Strict review runner profile does not match the executed command executable/model."
                        )
                        profile = None
                if profile is not None:
                    runner_identity = profile.to_runner_identity(
                        profile_name="strict-review", execution_environment="local-macos", provider="codex"
                    )
                    preflight = preflight_runner(profile)
                    if preflight.status != "ready":
                        runner_diagnostics = _merge_runner_diagnostics(
                            provenance_diagnostics,
                            list(preflight.diagnostics),
                            list(preflight.recovery),
                        )
                    else:
                        runner = SubprocessAgentRunner(
                            command, allowed_executables=policy.agent_executables,
                            timeout_seconds=args.runner_timeout,
                        )
                elif args.strict:
                    runner_diagnostics.append(
                        "Strict review runner requires an explicit runner profile or model for preflight."
                    )
                else:
                    runner = SubprocessAgentRunner(
                        command, allowed_executables=policy.agent_executables,
                        timeout_seconds=args.runner_timeout,
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
                memory_hub_evidence=memory_evidence,
                runner_diagnostics=runner_diagnostics,
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
