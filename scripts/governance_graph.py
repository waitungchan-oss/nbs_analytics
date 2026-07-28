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

from backend.agents.governance_graph_service import GovernanceGraphBuilder
from backend.agents.governance_graph_query_service import GovernanceGraphQueryService


CLI_SCHEMA = "nbs-governance-graph-cli-v1"
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w])/(?:[^\s'\"<>]+)")
_EXIT_CODES = {
    "completed": 0,
    "ready_for_integration": 0,
    "awaiting_authorization": 0,
    "not_started": 0,
    "awaiting_documentation": 1,
    "blocked_user_decision": 1,
    "diagnosis_required": 1,
    "blocked_missing_runner": 2,
    "protected_incident": 2,
    "blocked": 2,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only NBS Governance Graph projection CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "validate", "status"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--run-id", required=True)
    query = subparsers.add_parser("query")
    query.add_argument("--run-id", required=True)
    query.add_argument("--node-type")
    query.add_argument("--node-status")
    query.add_argument("--node-id")
    query.add_argument("--edge-type")
    query.add_argument("--artifact-kind")
    query.add_argument("--evidence-status")
    query.add_argument("--snapshot-fingerprint")
    return parser


def _safe_run_id(value: str) -> str:
    candidate = Path(value)
    if not value or value in {".", ".."} or candidate.is_absolute() or len(candidate.parts) != 1:
        raise PermissionError("run ID must name one workflow run")
    return value


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


def _runtime_layout_exists() -> bool:
    runtime = PROJECT_ROOT / ".nbs_agent_runtime"
    runs = runtime / "runs"
    return runtime.is_dir() and not runtime.is_symlink() and runs.is_dir() and not runs.is_symlink()


def _envelope(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"schemaVersion": CLI_SCHEMA, "command": command, **payload}


def _run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run_id = _safe_run_id(args.run_id)
    if args.command in {"validate", "status", "query"} and not _runtime_layout_exists():
        raise FileNotFoundError("workflow runtime is not available")
    builder = GovernanceGraphBuilder(PROJECT_ROOT)
    if args.command == "build":
        snapshot = builder.persist(run_id)
        payload = snapshot.to_dict()
        return _envelope(args.command, {
            "runId": snapshot.run_id,
            "overallStatus": snapshot.overall_status,
            "graphFingerprint": snapshot.graph_fingerprint,
            "snapshot": payload,
        }), _exit_code(snapshot.overall_status)
    if args.command == "validate":
        snapshot = builder.validate(run_id)
        payload = snapshot.to_dict()
        return _envelope(args.command, {
            "runId": snapshot.run_id,
            "overallStatus": snapshot.overall_status,
            "graphFingerprint": snapshot.graph_fingerprint,
            "snapshot": payload,
        }), _exit_code(snapshot.overall_status)
    if args.command == "status":
        payload = builder.status(run_id)
        return _envelope(args.command, payload), _exit_code(payload["overallStatus"])
    if args.command == "query":
        result = GovernanceGraphQueryService(PROJECT_ROOT).query(
            run_id=args.run_id,
            node_type=args.node_type,
            node_status=args.node_status,
            node_id=args.node_id,
            edge_type=args.edge_type,
            artifact_kind=args.artifact_kind,
            evidence_status=args.evidence_status,
            snapshot_fingerprint=args.snapshot_fingerprint,
        )
        return _envelope(args.command, {"result": result.to_dict()}), _exit_code(result.status)
    raise ValueError("unknown governance graph command")


def _exit_code(status: str) -> int:
    return _EXIT_CODES.get(status, {"available": 0, "unavailable": 0, "unknown": 0, "invalid": 2}.get(status, 5))


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code == 2:
            _render(_envelope("error", {"status": "blocked", "message": "invalid command-line arguments"}))
            return 2
        raise
    try:
        payload, exit_code = _run(args)
        _render(payload)
        return exit_code
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        _stderr(str(exc))
        _render(_envelope(args.command, {"status": "blocked", "message": str(exc)}))
        return 2
    except Exception as exc:
        _stderr(str(exc))
        _render(_envelope(args.command, {"status": "runtime_error", "message": str(exc)}))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
