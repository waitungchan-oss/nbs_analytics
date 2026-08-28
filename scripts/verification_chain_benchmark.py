"""Bounded benchmark reporter for a persisted verification-chain session.

This command measures the already-produced gate evidence without rerunning any
business data, SQLite query, runner prompt, or full log.  It is intentionally a
read-only diagnostic artifact: the benchmark never changes the session or
project state and emits only bounded metadata and SHA-256 fingerprints.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.verification_chain import VerificationChain


BENCHMARK_SCHEMA = "verification-chain-benchmark-v1"
GATES = ("pre_review", "strict_review", "full_pytest", "hermes")
MAX_ARTIFACTS = 32


def _duration_ms(started_at: str, finished_at: str) -> int:
    """Return a non-negative bounded duration from RFC3339 gate timestamps."""
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0
    return max(0, round((finished - started).total_seconds() * 1000))


def _fingerprint_files(session_dir: Path) -> dict[str, str]:
    """Fingerprint at most 32 regular session files, never expose contents."""
    result: dict[str, str] = {}
    for path in sorted(session_dir.rglob("*")):
        if len(result) >= MAX_ARTIFACTS:
            break
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(session_dir).as_posix()
        result[relative] = sha256(path.read_bytes()).hexdigest()
    return result


def benchmark_session(
    session_dir: Path | str,
    *,
    reused_batch_count: int = 0,
    runner_probe_count: int = 0,
) -> dict:
    """Build a deterministic, bounded benchmark manifest for one session.

    ``reused_batch_count`` and ``runner_probe_count`` are explicit caller
    metadata because the session contract deliberately does not store runner
    prompts, transport traces, or batch payloads.  Negative values are rejected
    rather than silently producing misleading performance evidence.
    """
    if isinstance(reused_batch_count, bool) or reused_batch_count < 0:
        raise ValueError("reused_batch_count must be a non-negative integer")
    if isinstance(runner_probe_count, bool) or runner_probe_count < 0:
        raise ValueError("runner_probe_count must be a non-negative integer")

    directory = Path(session_dir).resolve()
    session_path = directory / "session.json"
    session_id = directory.name
    chain = VerificationChain.load(
        session_id,
        runtime_root=directory.parent,
    )
    session = chain.session
    if session_path != chain.session_dir / "session.json":
        raise ValueError("session directory does not match its session manifest")

    gate_rows: list[dict] = []
    for gate in GATES:
        gate_name = gate
        entry_name = {
            "pre_review": "preReview",
            "strict_review": "strictReview",
            "full_pytest": "fullPytest",
            "hermes": "hermes",
        }[gate]
        entry = session.gates.get(entry_name, {})
        metadata_path = directory / "gates" / gate / "gate.json"
        metadata = {}
        if metadata_path.is_file() and not metadata_path.is_symlink():
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = loaded
        started_at = metadata.get("startedAt") or ""
        finished_at = metadata.get("finishedAt") or ""
        gate_rows.append({
            "gate": gate_name,
            "gateStatus": entry.get("gateStatus") or metadata.get("status") or "missing",
            "durationMs": _duration_ms(started_at, finished_at),
            "reusedBatchCount": reused_batch_count if gate == "strict_review" else 0,
            "runnerProbeCount": runner_probe_count if gate in {"strict_review", "hermes"} else 0,
            "evidenceFingerprint": entry.get("evidenceFingerprint") or metadata.get("evidenceFingerprint") or "",
        })

    return {
        "schemaVersion": BENCHMARK_SCHEMA,
        "sessionId": session.session_id,
        "sourceFingerprint": session.source_fingerprint,
        "status": session.status,
        "gates": gate_rows,
        "artifactFingerprints": _fingerprint_files(directory),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit bounded verification-chain benchmark JSON.")
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--runtime-root",
        default=str(PROJECT_ROOT / ".nbs_agent_runtime" / "verification_sessions"),
    )
    parser.add_argument("--reused-batch-count", type=int, default=0)
    parser.add_argument("--runner-probe-count", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        session_dir = Path(args.runtime_root).resolve() / args.session
        payload = benchmark_session(
            session_dir,
            reused_batch_count=args.reused_batch_count,
            runner_probe_count=args.runner_probe_count,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"schemaVersion": BENCHMARK_SCHEMA, "status": "invalid", "error": str(exc)}))
        return 5
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] in {"complete", "hermes_passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
