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
from backend.agents.governance_graph_comparison_service import GovernanceGraphComparisonService
from backend.agents.governance_graph_risk_service import GovernanceGraphRiskService
from backend.agents.governance_graph_impact_service import GovernanceGraphImpactService
from backend.agents.governance_graph_evidence_lineage_models import EvidenceLineageInput
from backend.agents.governance_graph_evidence_lineage_service import GovernanceGraphEvidenceLineageService
from backend.agents.governance_graph_catalog_service import OwnerDependencyReadService


CLI_SCHEMA = "nbs-governance-graph-cli-v1"
CATALOG_CLI_SCHEMA = "governance-graph-catalog-cli-v1"
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
    compare = subparsers.add_parser("compare")
    compare.add_argument("--left-run-id", required=True)
    compare.add_argument("--right-run-id", required=True)
    compare.add_argument("--left-snapshot-fingerprint")
    compare.add_argument("--right-snapshot-fingerprint")
    subparsers.add_parser("risk-summary")
    subparsers.add_parser("change-impact")
    subparsers.add_parser("evidence-lineage")
    subparsers.add_parser("catalog-validate")
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
    if args.command == "catalog-validate":
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Keep malformed stdin inside the catalog contract and bounded
            # rather than leaking into the generic CLI runtime-error envelope.
            result = OwnerDependencyReadService().resolve(
                snapshot_fingerprint=None,
                owner_catalog=None,
                dependency_catalog=None,
            )
            return {"schemaVersion": CATALOG_CLI_SCHEMA, "command": args.command, "result": result.to_dict()}, 2
        if not isinstance(payload, dict):
            payload = {}
        result = OwnerDependencyReadService().resolve(
            snapshot_fingerprint=payload.get("snapshotFingerprint"),
            owner_catalog=payload.get("ownerCatalog"),
            dependency_catalog=payload.get("dependencyCatalog"),
        )
        result_payload = result.to_dict()
        return {"schemaVersion": CATALOG_CLI_SCHEMA, "command": args.command, "result": result_payload}, _exit_code(result.status)
    if args.command == "risk-summary":
        payload = json.load(sys.stdin)
        result = GovernanceGraphRiskService().evaluate(payload)
        return _envelope(args.command, {"result": result.to_dict()}), _exit_code(result.status)
    if args.command == "change-impact":
        payload = json.load(sys.stdin)
        result = GovernanceGraphImpactService().evaluate(payload)
        return _envelope(args.command, {"result": result.to_dict()}), _exit_code(result.status)
    if args.command == "evidence-lineage":
        payload = json.load(sys.stdin)
        request = EvidenceLineageInput.from_dict(payload)
        result = GovernanceGraphEvidenceLineageService(PROJECT_ROOT).resolve(request)
        return _envelope(args.command, {"result": result.to_dict()}), _exit_code(result.status)
    if args.command == "compare":
        if not _runtime_layout_exists():
            raise FileNotFoundError("workflow runtime is not available")
        result = GovernanceGraphComparisonService(PROJECT_ROOT).compare(
            left_run_id=_safe_run_id(args.left_run_id),
            right_run_id=_safe_run_id(args.right_run_id),
            left_snapshot_fingerprint=args.left_snapshot_fingerprint,
            right_snapshot_fingerprint=args.right_snapshot_fingerprint,
        )
        return _envelope(args.command, {"result": result.to_dict()}), _exit_code(result.status)
    run_id = args.run_id if args.command == "query" else _safe_run_id(args.run_id)
    if args.command in {"validate", "status", "query"} and not _runtime_layout_exists():
        raise FileNotFoundError("workflow runtime is not available")
    if args.command == "build":
        builder = GovernanceGraphBuilder(PROJECT_ROOT)
        snapshot = builder.persist(run_id)
        payload = snapshot.to_dict()
        return _envelope(args.command, {
            "runId": snapshot.run_id,
            "overallStatus": snapshot.overall_status,
            "graphFingerprint": snapshot.graph_fingerprint,
            "snapshot": payload,
        }), _exit_code(snapshot.overall_status)
    if args.command == "validate":
        builder = GovernanceGraphBuilder(PROJECT_ROOT)
        snapshot = builder.validate(run_id)
        payload = snapshot.to_dict()
        return _envelope(args.command, {
            "runId": snapshot.run_id,
            "overallStatus": snapshot.overall_status,
            "graphFingerprint": snapshot.graph_fingerprint,
            "snapshot": payload,
        }), _exit_code(snapshot.overall_status)
    if args.command == "status":
        builder = GovernanceGraphBuilder(PROJECT_ROOT)
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
