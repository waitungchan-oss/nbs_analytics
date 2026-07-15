from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.agent_runtime import SubprocessAgentRunner
from backend.agents.evidence_models import EvidenceBundle
from backend.agents.implementation_agent_service import ImplementationAgentService
from backend.agents.implementation_models import ImplementationTaskContract


_EXIT_CODES = {
    "completed": 0,
    "collect_only": 0,
    "changes_required": 3,
    "validation_failed": 3,
    "blocked_invalid_contract": 2,
    "blocked_dirty_worktree": 2,
    "blocked_wrong_branch": 2,
    "blocked_head_mismatch": 2,
    "blocked_scope": 2,
    "blocked_high_risk": 2,
    "blocked_diff_limit": 2,
    "invalid_agent_output": 4,
    "context_overflow": 4,
    "runtime_error": 5,
}

_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w])/(?:[^\s'\"<>]+)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one approved implementation task")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--agent-command", nargs="+")
    parser.add_argument("--collect-only", action="store_true")
    return parser


def _load_contract(raw_path: str) -> ImplementationTaskContract:
    path = Path(raw_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    contract = ImplementationTaskContract.from_dict(payload)
    approved = Path(contract.approved_worktree).resolve()
    if approved != PROJECT_ROOT:
        raise PermissionError("approvedWorktree must be the implementation worktree")
    return contract


def _load_agent_runner(argv: list[str]):
    policy_path = PROJECT_ROOT / "agent_config/evidence_allowlist.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    allowed = policy.get("agentExecutables", [])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ValueError("agent executable policy is invalid")
    return SubprocessAgentRunner(argv, allowed_executables=tuple(allowed)).run


def _redact(value: Any) -> Any:
    environment_values = {
        item for item in os.environ.values()
        if isinstance(item, str) and len(item) >= 4
    }
    project = PROJECT_ROOT.resolve()

    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if not isinstance(value, str):
        return value

    redacted = value
    for secret in sorted(environment_values, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")

    def redact_path(match: re.Match[str]) -> str:
        token = match.group(0)
        suffix = ""
        while token and token[-1] in ":;,.)]}":
            suffix = token[-1] + suffix
            token = token[:-1]
        candidate = Path(token)
        if not candidate.is_absolute():
            return match.group(0)
        try:
            candidate.resolve().relative_to(project)
        except ValueError:
            return "[REDACTED_PATH]" + suffix
        return match.group(0)

    return _ABSOLUTE_PATH_RE.sub(redact_path, redacted)


def _render(payload: Any) -> None:
    sys.stdout.write(json.dumps(_redact(payload), ensure_ascii=False, indent=2) + "\n")


def _status_payload(status: str, message: str = "") -> dict[str, str]:
    payload = {"schemaVersion": "implementation-run-report-v1", "status": status}
    if message:
        payload["message"] = message
    return payload


def _exit_code(status: str) -> int:
    return _EXIT_CODES.get(status, 5)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code == 2:
            _render(_status_payload("blocked_invalid_contract", "invalid command-line arguments"))
            return 2
        raise
    try:
        contract = _load_contract(args.contract)
        service = ImplementationAgentService(PROJECT_ROOT)
        if args.collect_only:
            bundle = service.collect(contract)
            if not isinstance(bundle, EvidenceBundle):
                raise TypeError("context collector returned an invalid evidence bundle")
            payload = bundle.to_dict()
            payload["schemaVersion"] = "evidence-bundle-v1"
            _render(payload)
            return 0
        if not args.agent_command:
            _render(_status_payload("blocked_invalid_contract", "explicit --agent-command is required"))
            return 2
        report = service.execute(contract, _load_agent_runner(args.agent_command))
        payload = report.to_dict() if hasattr(report, "to_dict") else report
        _render(payload)
        return _exit_code(payload.get("status"))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, PermissionError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        _render(_status_payload("blocked_invalid_contract", str(exc)))
        return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        _render(_status_payload("runtime_error", str(exc)))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
