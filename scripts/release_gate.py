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
from backend.agents.release_readiness import classify_acceptance_stage
from backend.agents.verification_session import VerificationSession


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


def readiness_from_paths(session_path: Path, release_path: Path | None = None) -> dict:
    session = _load(session_path)
    if session.get("schemaVersion") == "verification-status-v1":
        embedded = session.get("session")
        if not isinstance(embedded, dict):
            raise ReleaseGateValidationError(
                "verification-status-v1 must include an embedded session manifest"
            )
        session = embedded
    if session.get("schemaVersion") == "verification-session-v1":
        completion = session.get("completion")
        manifest = {
            key: value for key, value in session.items() if key != "completion"
        }
        parsed = VerificationSession.from_dict(manifest)
        session = {**manifest, "sourceFingerprint": parsed.source_fingerprint}
        if not isinstance(completion, dict):
            completion_path = session_path.parent / "completion.json"
            if completion_path.is_file() and not completion_path.is_symlink():
                completion = _load(completion_path)
        if isinstance(completion, dict):
            session["completion"] = completion
    release = _load(release_path) if release_path is not None else None
    stage = classify_acceptance_stage(session, release)
    return {
        "schemaVersion": "release-readiness-status-v1",
        "sessionId": session.get("sessionId"),
        "acceptanceStage": stage,
        "commitSha": session.get("headSha") or session.get("commitSha"),
        "sourceFingerprint": session.get("sourceFingerprint"),
    }


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
    stage = subparsers.add_parser("stage", help="Show read-only verification/release readiness stage.")
    stage.add_argument("--session", type=Path, required=True)
    stage.add_argument("--release", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            value = validate_release_gate_evidence(_load(args.evidence), args.commit_sha, args.source_fingerprint, datetime.now(timezone.utc))
            result = value.to_dict()
        elif args.command == "aggregate":
            paths = {gate: getattr(args, gate) for gate in GATES}
            result = aggregate_from_paths(paths, args.commit_sha, args.source_fingerprint)
            validate_release_gate_aggregate(result, args.commit_sha)
        else:
            result = readiness_from_paths(args.session, args.release)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if getattr(args, "output", None):
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        if args.command == "stage":
            return 0 if result["acceptanceStage"] == "release_ready" else 1
        return 0 if result["status"] == "PASS" else 2
    except (OSError, ReleaseGateValidationError, ValueError) as exc:
        sys.stderr.write(f"release gate blocked: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
