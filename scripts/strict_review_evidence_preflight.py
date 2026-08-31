"""Deterministic, read-only Strict Review evidence preflight CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.governance_graph_preflight_adapter import read_governance_observation
from backend.agents.memory_preflight_adapter import read_memory_observation, merge_non_authoritative_observations
from backend.agents.strict_review_evidence_cache import write_preflight_artifacts
from backend.agents.strict_review_evidence_service import (
    build_verification_v1, evaluate_check_results, run_preflight_checks,
)
from backend.agents.validation_runner import ValidationRunner
from backend.agents.evidence_models import canonical_fingerprint


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _source_fingerprint(project_root: Path) -> str:
    import subprocess
    completed = subprocess.run(["git", "status", "--short"], cwd=project_root, capture_output=True, text=True, check=False)
    return hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()


def _output_dir(project_root: Path, raw: str, session: str) -> Path:
    runtime = (project_root / ".nbs_agent_runtime").resolve()
    candidate = Path(raw) if raw else runtime / "verification_sessions" / session
    candidate = candidate.resolve()
    candidate.relative_to(runtime)
    if any(part == ".." for part in candidate.parts):
        raise ValueError("output path escapes runtime")
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strict Review evidence preflight")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--session", required=True)
    parser.add_argument("--source-fingerprint")
    parser.add_argument("--brief")
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--output")
    parser.add_argument("--strict", action="store_true")
    if argv == ["--help"]:
        parser.print_help()
        return 0
    try:
        args = parser.parse_args(argv)
        project_root = Path(args.project_root).resolve(strict=True)
        if not args.session or "/" in args.session or "\\" in args.session:
            raise ValueError("session must be a safe path component")
        source = args.source_fingerprint or _source_fingerprint(project_root)
        if len(source) != 64 or any(char not in "0123456789abcdef" for char in source):
            raise ValueError("source fingerprint is invalid")
        output = _output_dir(project_root, args.output or "", args.session)
        plan = (("py_compile", ("backend/agents/strict_review_evidence_service.py",)), ("py_compile", ("backend/agents/strict_review_evidence_cache.py",)))
        results = run_preflight_checks(project_root, plan, source_fingerprint=source, runner=ValidationRunner(project_root))
        verification = build_verification_v1(results)
        status = evaluate_check_results(results)
        governance = read_governance_observation(project_root, session_source=source)
        memory = read_memory_observation(project_root, session_source=source)
        payload = {
            "schemaVersion": "strict-review-preflight-v1", "status": status,
            "sessionId": args.session, "sourceFingerprint": source,
            "bundleFingerprint": canonical_fingerprint({"sessionId": args.session, "sourceFingerprint": source, "verification": verification}),
            "changedFiles": [], "coverage": {"targetedTests": "not_requested", "compileStatic": status, "diffCheck": "not_requested", "runnerCapability": "available", "contextCompatibility": "not_requested", "governanceLineage": governance["status"], "memoryReadiness": memory["status"]},
            "generatedEvidence": ["verification-v1.json"], "verificationPath": f".nbs_agent_runtime/verification_sessions/{args.session}/verification-v1.json",
            "diagnostics": [], "createdAt": "deterministic-preflight", "preflightFingerprint": canonical_fingerprint({"sessionId": args.session, "sourceFingerprint": source, "verification": verification}),
        }
        payload = merge_non_authoritative_observations(payload, governance=governance, memory=memory)
        write_preflight_artifacts(output, payload, verification)
        _emit(payload)
        return 0 if status == "ready" else 1
    except (OSError, ValueError, PermissionError, RuntimeError) as exc:
        _emit({"schemaVersion": "strict-review-preflight-v1", "status": "invalid_evidence", "error": str(exc)[:512]})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
