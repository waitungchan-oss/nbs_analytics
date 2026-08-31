"""Session verification chain operator CLI.

Task 6 of the strict review verification chain. Exposes the deterministic
``VerificationChain`` controller as subcommands:

    seal        create and persist a fresh session from the live source seal
    run-review  write pre-review evidence and run Strict Review batches
    run-full    run the full pytest gate (requires ``review_passed``)
    run-hermes  run Hermes acceptance bound to this session (requires
                ``full_verification_passed``) with an explicit profile
    attest      deterministic completion attestation (no LLM)
    status      read-only view of exactly one session

Every output path stays under
``.nbs_agent_runtime/verification_sessions/<sessionId>/`` and every subcommand
emits bounded session-scoped JSON with stable exit codes:

    0 complete/pass
    1 changes/failure
    2 blocked capability
    4 context overflow
    5 invalid runtime/evidence
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.agent_runtime import SubprocessAgentRunner
from backend.agents.context_agent_service import context_summary_from_evidence_payload
from backend.agents.evidence_collector import EvidenceCollector, EvidencePolicy
from backend.agents.implementation_models import ImplementationTaskContract
from backend.agents.review_agent_service import (
    merge_review_batches,
    plan_review_batches,
    run_review_batch,
)
from backend.agents.review_runner_profile import (
    RunnerProfile,
    load_runner_profile,
    probe_runner,
)
from backend.agents.verification_chain import (
    InvalidGateTransition,
    VerificationChain,
    git_source_probe,
)
from backend.agents.verification_evidence_writer import validate_verification_v1
from backend.agents.strict_review_preflight_models import validate_preflight_result
from backend.agents.verification_session import (
    StaleVerificationSession,
    VerificationSession,
)

DEFAULT_SESSIONS_ROOT = PROJECT_ROOT / ".nbs_agent_runtime" / "verification_sessions"
HERMES_PROFILES = ("primary-runtime", "isolated-profile")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TAIL_CHARS = 4000


def _python_bin(project_root: Path = PROJECT_ROOT) -> str:
    venv_python = project_root / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def exit_code_for_status(status: str | None) -> int:
    """Stable operator exit code for a chain session status."""
    if status in {"complete", "sealed", "review_passed", "full_verification_passed", "hermes_passed"}:
        return 0
    if status in {"review_changes_required", "verification_failed", "hermes_failed"}:
        return 1
    if status in {"blocked_runner_capability", "blocked_runner_transport"}:
        return 2
    if status == "context_overflow":
        return 4
    return 5


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _emit_error(message: str, *, status: str = "blocked", session_id: str | None = None) -> int:
    payload: dict = {"status": status, "error": message}
    if session_id is not None:
        payload["sessionId"] = session_id
    _emit(payload)
    return 5


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--runtime-root",
        default=str(DEFAULT_SESSIONS_ROOT),
        help="Sessions root; must stay under .nbs_agent_runtime/verification_sessions/.",
    )
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    return parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verification_chain.py",
        description="Strict review verification chain operator CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    seal = subparsers.add_parser("seal", parents=[_common_parser()])
    seal.add_argument("--brief", required=True)
    seal.add_argument("--base", required=True, help="Base SHA or ref for the source seal.")
    seal.add_argument("--head", default="WORKTREE")

    review = subparsers.add_parser("run-review", parents=[_common_parser()])
    review.add_argument("--session", required=True)
    review.add_argument("--brief", required=True)
    review.add_argument("--base", default="main")
    review.add_argument("--head", default="WORKTREE")
    review.add_argument("--context")
    review.add_argument("--task-contract")
    review.add_argument("--verification")
    review.add_argument("--preflight", help="Validated strict-review-preflight-v1 artifact.")
    review.add_argument("--memory-evidence")
    review.add_argument("--agent-command")
    review.add_argument("--runner-profile")
    review.add_argument("--runner-cache")
    review.add_argument("--runner-model")
    review.add_argument("--runner-timeout", type=int, default=300)
    review.add_argument("--preserve-dirty-path", action="append", default=[])
    review.add_argument("--strict", action="store_true")

    full = subparsers.add_parser("run-full", parents=[_common_parser()])
    full.add_argument("--session", required=True)
    full.add_argument(
        "--full-command",
        help="Approved full verification command (shlex string); "
        "defaults to the venv pytest -q.",
    )
    full.add_argument("--full-timeout", type=int, default=1800)

    hermes = subparsers.add_parser("run-hermes", parents=[_common_parser()])
    hermes.add_argument("--session", required=True)
    hermes.add_argument("--profile", required=True, choices=HERMES_PROFILES)
    hermes.add_argument("--hermes-command", help="Hermes command override (shlex string).")
    hermes.add_argument("--verification-profile", help="Isolated verification profile path.")

    attest = subparsers.add_parser("attest", parents=[_common_parser()])
    attest.add_argument("--session", required=True)

    status = subparsers.add_parser("status", parents=[_common_parser()])
    status.add_argument("--session", required=True)

    preflight = subparsers.add_parser("run-preflight", parents=[_common_parser()])
    preflight.add_argument("--session", required=True)
    preflight.add_argument("--source-fingerprint")
    preflight.add_argument("--output")
    preflight.add_argument("--strict", action="store_true")

    return parser


def _load_chain(session_id: str, runtime_root: str):
    try:
        return VerificationChain.load(session_id, runtime_root=runtime_root)
    except (FileNotFoundError, ValueError, PermissionError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"session {session_id!r} is unavailable: {exc}") from exc


def _resolve_sha(project_root: Path, ref: str) -> str:
    if _SHA_RE.fullmatch(ref):
        return ref
    completed = subprocess.run(
        ["git", "rev-parse", ref], cwd=project_root,
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"cannot resolve base ref {ref!r}")
    return completed.stdout.strip()


def _sha256_of_file(path: Path) -> str:
    from hashlib import sha256

    return sha256(path.read_bytes()).hexdigest()


def _read_object(path: str, label: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def validate_preflight_for_session(path: str, *, source_fingerprint: str) -> dict:
    payload = validate_preflight_result(_read_object(path, "Preflight evidence"))
    if payload["status"] != "ready":
        raise ValueError("preflight evidence is not ready")
    if payload["sourceFingerprint"] != source_fingerprint:
        raise ValueError("preflight source fingerprint does not match session")
    return payload


def _read_verification(path: str) -> list[dict]:
    return list(validate_verification_v1(Path(path)))


def _review_task_from_implementation_contract(path: Path) -> dict:
    contract = ImplementationTaskContract.from_dict(_read_object(str(path), "Implementation task contract"))
    return {
        **contract.to_dict(),
        "scope": list(contract.allowed_write_paths),
        "forbidden": [
            "writes outside allowedWritePaths",
            "SQLite, baseline, revenue, business rules, export schema, or Git integration",
        ],
    }


def _run_command(argv: list[str], *, timeout: int, cwd: Path) -> dict:
    try:
        completed = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        exit_code = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nTimed out after {timeout}s"
    return {
        "label": "full pytest",
        "argv": argv,
        "exitCode": exit_code,
        "stdoutTail": stdout[-_MAX_TAIL_CHARS:],
        "stderrTail": stderr[-_MAX_TAIL_CHARS:],
    }


def _build_review_runner(args, policy: EvidencePolicy):
    """Port of review_agent.py runner wiring; returns (runner, capability, diagnostics)."""
    runner = None
    capability = None
    diagnostics: list[str] = []
    if not args.agent_command:
        if args.strict:
            diagnostics.append(
                "Strict review requires --agent-command with an explicit runner profile."
            )
        return runner, capability, diagnostics
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
    profile = None
    if args.runner_profile:
        profile = load_runner_profile(policy.resolve_input_path(Path(args.runner_profile)))
    elif executable and model:
        cache_root = os.environ.get("CODEX_HOME")
        cache_path = Path(args.runner_cache) if args.runner_cache else (
            Path(cache_root) if cache_root else Path.home() / ".codex"
        ) / "models_cache.json"
        profile = RunnerProfile(executable, model, cache_path)
    if profile is not None:
        receipt = probe_runner(profile)
        if receipt.status == "turn_ready":
            runner = SubprocessAgentRunner(
                command, allowed_executables=policy.agent_executables,
                timeout_seconds=args.runner_timeout,
            )
        capability = receipt
    elif args.strict:
        diagnostics.append(
            "Strict review requires an explicit runner profile or model for preflight."
        )
    else:
        runner = SubprocessAgentRunner(
            command, allowed_executables=policy.agent_executables,
            timeout_seconds=args.runner_timeout,
        )
    return runner, capability, diagnostics


def cmd_seal(args) -> int:
    project_root = Path(args.project_root)
    brief = (project_root / args.brief).resolve()
    if not brief.is_file():
        return _emit_error(f"brief not found: {args.brief}", status="invalid_evidence")
    base_sha = _resolve_sha(project_root, args.base)

    def probe() -> dict:
        return git_source_probe(
            project_root, brief_path=args.brief, base_sha=base_sha, head_ref=args.head
        )

    current = probe()
    contract_fingerprint = _sha256_of_file(
        project_root / "docs/agents/REVIEW_AGENT_CONTRACT.md"
    )
    token_budgets = project_root / "agent_config" / "token_budgets.json"
    policy_fingerprint = (
        _sha256_of_file(token_budgets) if token_budgets.is_file() else "0" * 64
    )
    session = VerificationSession.create(
        project_id="nbs_analytics",
        base_sha=base_sha,
        head_sha=current["head_sha"],
        brief_path=args.brief,
        brief_fingerprint=current["brief_fingerprint"],
        worktree_fingerprint=current["worktree_fingerprint"],
        diff_fingerprint=current["diff_fingerprint"],
        contract_fingerprint=contract_fingerprint,
        policy_fingerprint=policy_fingerprint,
    )
    try:
        chain = VerificationChain.seal(
            session,
            runtime_root=Path(args.runtime_root) / session.session_id,
            source_probe=probe,
        )
    except (StaleVerificationSession, ValueError, PermissionError) as exc:
        return _emit_error(f"cannot seal session: {exc}")
    _emit({
        "schemaVersion": "verification-seal-v1",
        "sessionId": chain.session_id,
        "status": "sealed",
        "sourceFingerprint": chain.session.source_fingerprint,
    })
    return 0


def cmd_run_review(args) -> int:
    project_root = Path(args.project_root)
    try:
        chain = _load_chain(args.session, args.runtime_root)
    except ValueError as exc:
        return _emit_error(str(exc), session_id=args.session)
    if chain.session.status not in {"sealed", "blocked_runner_capability", "blocked_runner_transport"}:
        return _emit_error(
            f"run-review requires a sealed session; current status is {chain.session.status}",
            session_id=args.session,
        )
    try:
        policy = EvidencePolicy.from_project(project_root)
        if args.preflight:
            try:
                validate_preflight_for_session(args.preflight, source_fingerprint=chain.session.source_fingerprint)
            except ValueError as exc:
                return _emit_error(str(exc), status="invalid_evidence", session_id=args.session)
        brief = (project_root / args.brief).resolve()
        if not brief.is_file():
            return _emit_error(f"brief not found: {args.brief}", status="invalid_evidence", session_id=args.session)
        policy.resolve_read_path(brief)
        bundle = EvidenceCollector(project_root, policy=policy).collect_review(
            brief,
            base_ref=args.base,
            head_ref=args.head,
            preserve_dirty_paths=tuple(args.preserve_dirty_path),
        )
        if args.task_contract:
            bundle = replace(
                bundle,
                task=_review_task_from_implementation_contract(
                    policy.resolve_input_path(Path(args.task_contract))
                ),
            )
        context_summary = {}
        if args.context:
            context_path = policy.resolve_input_path(Path(args.context))
            context_summary = _read_object(str(context_path), "Context summary")
            if context_summary.get("schemaVersion") == "context-evidence-v1":
                context_summary = context_summary_from_evidence_payload(context_summary)
        verification = []
        if args.verification:
            verification_path = policy.resolve_input_path(Path(args.verification))
            verification = _read_verification(str(verification_path))
        if args.strict and not verification:
            return _emit_error(
                "Strict review requires a verification-v1 evidence bundle.",
                status="invalid_evidence",
                session_id=args.session,
            )
        memory_evidence = None
        if args.memory_evidence:
            memory_path = policy.resolve_input_path(Path(args.memory_evidence))
            memory_evidence = _read_object(str(memory_path), "Memory Hub evidence")
        runner, capability, diagnostics = _build_review_runner(args, policy)

        pre = chain.run_pre_review(verification)
        if pre.status != "sealed":
            _emit(pre.to_dict())
            return exit_code_for_status(pre.status)

        instructions = (project_root / "docs/agents/REVIEW_AGENT_CONTRACT.md").read_text(
            encoding="utf-8"
        )

        def review_callback(session: VerificationSession) -> dict:
            batches = plan_review_batches(session, bundle)
            reports = [
                run_review_batch(
                    batch,
                    runner,
                    runtime_root=project_root / ".nbs_agent_runtime",
                    context_summary=context_summary,
                    verification=verification,
                    instructions=instructions,
                    strict=args.strict,
                    input_token_limit=policy.review_input_tokens,
                    output_token_limit=policy.review_output_tokens,
                    memory_hub_evidence=memory_evidence,
                    runner_diagnostics=diagnostics,
                )
                for batch in batches
            ]
            return merge_review_batches(
                reports,
                session_fingerprint=session.source_fingerprint,
                expected_batch_ids=tuple(batch.batch_id for batch in batches),
            )

        result = chain.run_strict_review(runner=review_callback, capability=capability)
    except InvalidGateTransition as exc:
        return _emit_error(str(exc), session_id=args.session)
    except (PermissionError, ValueError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return _emit_error(str(exc), session_id=args.session)
    _emit(result.to_dict())
    return exit_code_for_status(result.status)


def cmd_run_preflight(args) -> int:
    from scripts.strict_review_evidence_preflight import main as preflight_main
    forwarded = ["--project-root", args.project_root, "--session", args.session]
    if args.source_fingerprint:
        forwarded += ["--source-fingerprint", args.source_fingerprint]
    if args.output:
        forwarded += ["--output", args.output]
    if args.strict:
        forwarded.append("--strict")
    return preflight_main(forwarded)


def cmd_run_full(args) -> int:
    project_root = Path(args.project_root)
    try:
        chain = _load_chain(args.session, args.runtime_root)
    except ValueError as exc:
        return _emit_error(str(exc), session_id=args.session)
    if chain.session.status != "review_passed":
        return _emit_error(
            f"run-full requires review_passed; current status is {chain.session.status}",
            session_id=args.session,
        )
    argv = (
        shlex.split(args.full_command)
        if args.full_command
        else [_python_bin(project_root), "-m", "pytest", "-q"]
    )
    command = _run_command(argv, timeout=args.full_timeout, cwd=project_root)
    result = chain.run_full_verification([command])
    _emit(result.to_dict())
    return exit_code_for_status(result.status)


def cmd_run_hermes(args) -> int:
    project_root = Path(args.project_root)
    try:
        chain = _load_chain(args.session, args.runtime_root)
    except ValueError as exc:
        return _emit_error(str(exc), session_id=args.session)
    if chain.session.status not in {"full_verification_passed", "hermes_failed"}:
        return _emit_error(
            f"run-hermes requires full_verification_passed; current status is {chain.session.status}",
            session_id=args.session,
        )
    if args.hermes_command:
        argv = shlex.split(args.hermes_command)
    else:
        argv = [
            _python_bin(project_root),
            str(project_root / "scripts" / "hermes_post_change_check.py"),
            "--json",
            "--skip-monitor",
        ]
        if args.verification_profile and args.profile == "isolated-profile":
            argv += ["--verification-profile", args.verification_profile]
    argv += ["--session", args.session, "--profile", args.profile, "--session-root", args.runtime_root]
    try:
        completed = subprocess.run(
            argv, cwd=project_root, capture_output=True, text=True,
            timeout=1800, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _emit({
            "sessionId": chain.session_id,
            "status": "hermes_failed",
            "error": "hermes acceptance timed out",
            "stdoutTail": (exc.stdout or "")[-_MAX_TAIL_CHARS:],
            "stderrTail": ((exc.stderr or "") + "\nTimed out after 1800s")[-_MAX_TAIL_CHARS:],
        })
        return 1
    if completed.returncode != 0:
        _emit({
            "sessionId": chain.session_id,
            "status": "hermes_failed",
            "error": f"hermes command exited {completed.returncode}",
            "stderrTail": completed.stderr[-_MAX_TAIL_CHARS:],
        })
        return 1
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        _emit({
            "sessionId": chain.session_id,
            "status": "hermes_failed",
            "error": "hermes output is not valid JSON",
        })
        return 1
    if not isinstance(report, dict):
        _emit({
            "sessionId": chain.session_id,
            "status": "hermes_failed",
            "error": "hermes output must be a JSON object",
        })
        return 1
    binding_ok = (
        report.get("sessionId") == chain.session_id
        and report.get("sourceFingerprint") == chain.session.source_fingerprint
        and report.get("profile") == args.profile
    )
    if not binding_ok:
        _emit({
            "sessionId": chain.session_id,
            "status": "hermes_failed",
            "error": "hermes evidence binding does not match the session/profile (mixed evidence)",
            "binding": {
                "sessionId": report.get("sessionId"),
                "sourceFingerprint": report.get("sourceFingerprint"),
                "profile": report.get("profile"),
            },
        })
        return 1
    result = chain.run_hermes(result=report, profile=args.profile)
    _emit(result.to_dict())
    return exit_code_for_status(result.status)


def cmd_attest(args) -> int:
    try:
        chain = _load_chain(args.session, args.runtime_root)
    except ValueError as exc:
        return _emit_error(str(exc), session_id=args.session)
    attestation = chain.attest()
    _emit(attestation.to_dict())
    return 0 if attestation.status == "complete" else 1


def cmd_status(args) -> int:
    try:
        chain = _load_chain(args.session, args.runtime_root)
    except ValueError as exc:
        return _emit_error(str(exc), session_id=args.session, status="not_found")
    terminal = None
    terminal_path = chain.session_dir / "terminal.json"
    if terminal_path.is_file() and not terminal_path.is_symlink():
        try:
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            terminal = None
    _emit({
        "schemaVersion": "verification-status-v1",
        "sessionId": chain.session_id,
        "status": chain.session.status,
        "sourceFingerprint": chain.session.source_fingerprint,
        "gates": chain.session.gates,
        "terminal": terminal,
    })
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv and argv[0] in {
        "seal", "run-review", "run-full", "run-hermes", "attest", "status", "run-preflight",
    } else None
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise  # --help / --version printed cleanly; let it exit 0
        # argparse errors exit 2; keep the operator contract stable at 5.
        _emit({
            "status": "blocked",
            "error": f"{command or 'verification_chain'}: missing or invalid required arguments",
            "hint": "required options include --session / --brief / --base / --profile where applicable",
        })
        return 5
    handlers = {
        "seal": cmd_seal,
        "run-review": cmd_run_review,
        "run-full": cmd_run_full,
        "run-hermes": cmd_run_hermes,
        "attest": cmd_attest,
        "status": cmd_status,
        "run-preflight": cmd_run_preflight,
    }
    try:
        return handlers[args.command](args)
    except (PermissionError, ValueError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return _emit_error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
