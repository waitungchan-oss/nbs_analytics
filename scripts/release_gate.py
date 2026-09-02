"""Deterministic read-only release gate validator and aggregator."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.release_gate_models import (
    GATES,
    ReleaseGateValidationError,
    aggregate_release_gates,
    validate_release_gate_aggregate,
    validate_release_gate_evidence,
)


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseGateValidationError(f"missing or invalid evidence: {path.name}") from exc
    if not isinstance(payload, dict):
        raise ReleaseGateValidationError(f"evidence must be an object: {path.name}")
    return payload


def aggregate_from_paths(
    paths: Mapping[str, Path], expected_commit_sha: str, expected_source_fingerprint: str,
    now: datetime | None = None,
) -> dict:
    if set(paths) != set(GATES):
        raise ReleaseGateValidationError("missing or duplicate gate path")
    evidence = {gate: _load(Path(paths[gate])) for gate in GATES}
    return aggregate_release_gates(evidence, expected_commit_sha, expected_source_fingerprint, now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--gate", required=True, choices=GATES)
    validate.add_argument("--evidence", type=Path, required=True)
    validate.add_argument("--commit-sha", required=True)
    validate.add_argument("--source-fingerprint", required=True)
    aggregate = subparsers.add_parser("aggregate")
    for gate in GATES:
        aggregate.add_argument(f"--{gate.replace('_', '-')}", type=Path, required=True)
    aggregate.add_argument("--commit-sha", required=True)
    aggregate.add_argument("--source-fingerprint", required=True)
    aggregate.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            value = validate_release_gate_evidence(_load(args.evidence), args.commit_sha, args.source_fingerprint, datetime.now(timezone.utc))
            result = value.to_dict()
        else:
            paths = {gate: getattr(args, gate) for gate in GATES}
            result = aggregate_from_paths(paths, args.commit_sha, args.source_fingerprint)
            validate_release_gate_aggregate(result, args.commit_sha)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if getattr(args, "output", None):
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ReleaseGateValidationError, ValueError) as exc:
        sys.stderr.write(f"release gate blocked: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
