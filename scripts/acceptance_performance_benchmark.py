"""Compose bounded acceptance performance evidence from existing artifacts."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.agents.acceptance_performance import (
    MAX_COUNT,
    MAX_DURATION_SECONDS,
    build_performance_baseline,
)
from backend.agents.acceptance_contract import (
    contract_fingerprint,
    validate_acceptance_contract,
)
from backend.agents.acceptance_performance_v2 import build_performance_baseline_v2
from backend.agents.agent_runtime import resolve_runtime_output_path
from backend.agents.evidence_models import canonical_fingerprint


MAX_JSON_BYTES = 256 * 1024
MAX_MANIFEST_JSON_BYTES = 512 * 1024
MAX_FAILURE_DETAIL_CHARS = 256
FAILURE_SCHEMA_VERSION = "acceptance-performance-failure-v1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _assert_no_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"input path is a symlink: {path.name}")
    current = path.parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError(f"input path contains a symlink: {current.name}")
        current = current.parent


def read_bounded_json(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    """Read one bounded regular JSON file without following artifact symlinks."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_MANIFEST_JSON_BYTES:
        raise ValueError("size cap is invalid")
    candidate = Path(path)
    _assert_no_symlink(candidate)
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("input must be a regular file")
    if candidate.stat().st_size > max_bytes:
        raise ValueError("input exceeds size cap")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {candidate.name}") from exc
    if not isinstance(payload, dict):
        raise ValueError("input JSON must be an object")
    return payload


def _require_identity(value: Any, pattern: re.Pattern[str], field: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} is invalid")


def _duration_at(payload: Mapping[str, Any], path: Sequence[str]) -> float | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        raise ValueError(f"{'.'.join(path)} duration is invalid")
    value = float(current)
    if not math.isfinite(value) or not 0 <= value <= MAX_DURATION_SECONDS:
        raise ValueError(f"{'.'.join(path)} duration is out of range")
    return round(value, 6)


def _counts(full_pytest: Mapping[str, Any], collected: int) -> dict[str, int]:
    result = full_pytest.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("Full pytest result is invalid")
    counts = {"collected": collected}
    for key in ("passed", "failed", "skipped"):
        value = result.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
            raise ValueError(f"Full pytest result.{key} is invalid")
        counts[key] = value
    if sum(counts[key] for key in ("passed", "failed", "skipped")) > collected:
        raise ValueError("Full pytest test counts exceed collected")
    return counts


def _require_child_identity(
    payload: Mapping[str, Any], *, schema: str, commit_sha: str, source_fingerprint: str, label: str,
) -> None:
    if payload.get("schemaVersion") != schema or payload.get("status") != "PASS":
        raise ValueError(f"{label} status or schema is invalid")
    if payload.get("commitSha") != commit_sha:
        raise ValueError(f"{label} commit mismatch")
    if payload.get("sourceFingerprint") != source_fingerprint:
        raise ValueError(f"{label} source mismatch")


def _test_population_fingerprint(nodeids: list[str]) -> str:
    if len(nodeids) != len(set(nodeids)):
        raise ValueError("manifest nodeids contain duplicates")
    if any(not nodeid.strip() for nodeid in nodeids):
        raise ValueError("manifest nodeids contain an empty value")
    return canonical_fingerprint({"nodeids": sorted(nodeids)})


def _aggregate_durations(
    aggregate: Mapping[str, Any] | None, *, commit_sha: str, source_fingerprint: str,
) -> tuple[float | None, float | None]:
    if aggregate is None:
        return None, None
    _require_child_identity(
        aggregate,
        schema="release-gate-result-v1",
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        label="aggregate",
    )
    aggregation = _duration_at(
        aggregate, ("freshness", "telemetry", "aggregationDurationSeconds")
    )
    total_wall = _duration_at(
        aggregate, ("freshness", "telemetry", "totalWallDurationSeconds")
    )
    return aggregation, total_wall


def build_performance_from_artifacts(
    *,
    manifest: Mapping[str, Any],
    full_pytest: Mapping[str, Any],
    fixture_timing: Mapping[str, Any] | None,
    aggregate: Mapping[str, Any] | None,
    commit_sha: str,
    source_fingerprint: str,
    runner_fingerprint: str,
    selection_mode: str,
    contract: Mapping[str, Any] | None = None,
    output_schema: str = "v1",
    environment_fingerprint: str | None = None,
    baseline_family_id: str | None = None,
    baseline_role: str = "serial_control",
    lifecycle: str = "draft",
) -> dict[str, Any]:
    """Compose diagnostics only; never starts tests, services or models."""
    if output_schema not in {"v1", "v2"}:
        raise ValueError("output schema is invalid")
    _require_identity(commit_sha, _SHA40, "commitSha")
    _require_identity(source_fingerprint, _SHA64, "sourceFingerprint")
    _require_identity(runner_fingerprint, _SHA64, "runnerFingerprint")
    _require_child_identity(
        manifest,
        schema="pytest-test-manifest-v1",
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        label="manifest",
    )
    _require_child_identity(
        full_pytest,
        schema="full-pytest-gate-v1",
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        label="Full pytest",
    )
    nodeids = manifest.get("nodeids")
    if not isinstance(nodeids, list) or len(nodeids) > MAX_COUNT or any(not isinstance(item, str) for item in nodeids):
        raise ValueError("manifest nodeids are invalid")

    collection = _duration_at(manifest, ("metadata", "telemetry", "durationSeconds"))
    fixture = None
    if fixture_timing is not None:
        if fixture_timing.get("schemaVersion") != "acceptance-fixture-timing-v1":
            raise ValueError("fixture timing schema is invalid")
        if fixture_timing.get("commitSha") != commit_sha:
            raise ValueError("fixture timing commit mismatch")
        if fixture_timing.get("sourceFingerprint") != source_fingerprint:
            raise ValueError("fixture timing source mismatch")
        fixture = _duration_at(fixture_timing, ("durationSeconds",))
    pytest_execution = _duration_at(
        full_pytest, ("metadata", "performanceTiming", "pytestExecutionSeconds"),
    )
    if pytest_execution is None:
        pytest_execution = _duration_at(full_pytest, ("metadata", "telemetry", "durationSeconds"))
    aggregate_duration, aggregate_wall = _aggregate_durations(
        aggregate, commit_sha=commit_sha, source_fingerprint=source_fingerprint,
    )
    counts = _counts(full_pytest, len(nodeids))
    stages = {
        "collectionSeconds": collection or 0.0,
        "fixturePreparationSeconds": fixture or 0.0,
        "pytestExecutionSeconds": pytest_execution or 0.0,
        "aggregateSeconds": aggregate_duration or 0.0,
        "totalWallSeconds": aggregate_wall or 0.0,
    }
    missing_stage = any(
        value is None
        for value in (collection, fixture, pytest_execution, aggregate_duration, aggregate_wall)
    )
    comparison = {
        "status": "not_compared",
        "baselineArtifact": None,
        "cacheHit": False,
        "cacheMiss": False,
        "failureRate": round(min(1.0, counts["failed"] / max(counts["collected"], 1)), 6),
    }
    if missing_stage:
        comparison["reason"] = "stage_evidence_missing"
    if output_schema == "v2":
        if contract is None:
            raise ValueError("v2 performance output requires a contract")
        validate_acceptance_contract(contract)
        manifest_fingerprint = manifest.get("manifestFingerprint")
        _require_identity(manifest_fingerprint, _SHA64, "manifestFingerprint")
        if environment_fingerprint is None:
            raise ValueError("v2 performance output requires an environment fingerprint")
        _require_identity(environment_fingerprint, _SHA64, "environmentFingerprint")
        if baseline_family_id is None:
            raise ValueError("v2 performance output requires a baseline family")
        return build_performance_baseline_v2(
            baseline_family_id=baseline_family_id,
            baseline_role=baseline_role,
            lifecycle=lifecycle,
            contract_fingerprint=contract_fingerprint(contract),
            commit_sha=commit_sha,
            source_fingerprint=source_fingerprint,
            manifest_fingerprint=manifest_fingerprint,
            test_population_fingerprint=_test_population_fingerprint(nodeids),
            runner_fingerprint=runner_fingerprint,
            environment_fingerprint=environment_fingerprint,
            dataset_snapshot_fingerprint=contract["datasetSnapshotFingerprint"],
            selection_mode=selection_mode,
            stages=stages,
            test_count=counts,
        )
    return build_performance_baseline(
        commit_sha=commit_sha,
        source_fingerprint=source_fingerprint,
        runner_fingerprint=runner_fingerprint,
        stages=stages,
        test_count=counts,
        selection_mode=selection_mode,
        comparison=comparison,
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    raw_output = Path(path)
    if raw_output.is_symlink():
        raise ValueError("output must not be a symlink")
    output = resolve_runtime_output_path(PROJECT_ROOT, str(path))
    if output.is_symlink():
        raise ValueError("output must not be a symlink")
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _failure_artifact(
    *, commit_sha: str, source_fingerprint: str, runner_fingerprint: str,
    selection_mode: str, error: Exception,
) -> dict[str, Any]:
    detail = " ".join(str(error).split())[:MAX_FAILURE_DETAIL_CHARS]
    unsigned = {
        "schemaVersion": FAILURE_SCHEMA_VERSION,
        "authority": "diagnostic",
        "fullGateRequired": True,
        "status": "BLOCKED",
        "failureCode": "benchmark_blocked",
        "error": detail or "acceptance performance benchmark blocked",
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "runnerFingerprint": runner_fingerprint,
        "selectionMode": selection_mode,
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--full-pytest", type=Path, required=True)
    parser.add_argument("--fixture-timing", type=Path)
    parser.add_argument("--aggregate", type=Path)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--runner-fingerprint", required=True)
    parser.add_argument("--selection-mode", choices=("full", "fast", "shard"), required=True)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--output-schema", choices=("v1", "v2"), default="v1")
    parser.add_argument("--environment-fingerprint")
    parser.add_argument("--baseline-family-id")
    parser.add_argument(
        "--baseline-role",
        choices=("serial_control", "parallel_candidate", "fast_diagnostic"),
        default="serial_control",
    )
    parser.add_argument(
        "--lifecycle",
        choices=("draft", "qualified", "retired", "invalidated"),
        default="draft",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_performance_from_artifacts(
            manifest=read_bounded_json(args.manifest, max_bytes=MAX_MANIFEST_JSON_BYTES),
            full_pytest=read_bounded_json(args.full_pytest),
            fixture_timing=read_bounded_json(args.fixture_timing) if args.fixture_timing else None,
            aggregate=read_bounded_json(args.aggregate) if args.aggregate else None,
            commit_sha=args.commit_sha,
            source_fingerprint=args.source_fingerprint,
            runner_fingerprint=args.runner_fingerprint,
            selection_mode=args.selection_mode,
            contract=read_bounded_json(args.contract) if args.contract else None,
            output_schema=args.output_schema,
            environment_fingerprint=args.environment_fingerprint,
            baseline_family_id=args.baseline_family_id,
            baseline_role=args.baseline_role,
            lifecycle=args.lifecycle,
        )
        _write_json(args.output, result)
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if args.output:
            try:
                _write_json(
                    args.output,
                    _failure_artifact(
                        commit_sha=args.commit_sha,
                        source_fingerprint=args.source_fingerprint,
                        runner_fingerprint=args.runner_fingerprint,
                        selection_mode=args.selection_mode,
                        error=exc,
                    ),
                )
            except (OSError, PermissionError, ValueError, TypeError) as write_error:
                sys.stderr.write(f"acceptance performance failure artifact blocked: {write_error}\n")
        sys.stderr.write(f"acceptance performance benchmark blocked: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
