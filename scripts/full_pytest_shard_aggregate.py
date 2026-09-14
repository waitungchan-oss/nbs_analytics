"""Validate diagnostic pytest shards with exactly-once manifest coverage."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import ReleaseGateValidationError
from scripts.full_pytest_shard import _validate_manifest


def _fail(message: str) -> None:
    raise ReleaseGateValidationError(message)


def _bounded_counts(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    counts: dict[str, int] = {}
    for key in ("passed", "failed", "skipped"):
        item = value.get(key, 0)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return None
        counts[key] = item
    return counts


def _validation_result(
    *, status: str, failure_code: str | None, commit_sha: str,
    source_fingerprint: str, manifest_fingerprint: str | None = None,
    shard_count: int = 0, covered: int = 0, duplicates: list[str] | None = None,
    missing: list[str] | None = None, unknown: list[str] | None = None,
    totals: Mapping[str, int] | None = None, fingerprints: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    unsigned = {
        "schemaVersion": "full-pytest-shard-set-validation-v1",
        "status": status,
        "failureCode": failure_code,
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": commit_sha,
        "sourceFingerprint": source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "shardCount": shard_count,
        "coveredNodeids": covered,
        "duplicateNodeids": sorted(duplicates or []),
        "missingNodeids": sorted(missing or []),
        "unknownNodeids": sorted(unknown or []),
        "result": dict(totals or {"passed": 0, "failed": 0, "skipped": 0}),
        "shards": dict(fingerprints or {}),
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def validate_shard_set(
    manifest: Mapping[str, Any], shards: Sequence[Mapping[str, Any]],
    expected_commit_sha: str, expected_source_fingerprint: str,
) -> dict[str, Any]:
    """Validate shard identity, exactly-once node coverage and aggregate counts."""
    try:
        commit_sha, source_fingerprint, nodeids, manifest_fingerprint = _validate_manifest(manifest)
    except (TypeError, ValueError):
        return _validation_result(
            status="BLOCKED", failure_code="manifest_invalid",
            commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint,
        )
    if commit_sha != expected_commit_sha:
        return _validation_result(status="BLOCKED", failure_code="commit_identity_mismatch", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint)
    if source_fingerprint != expected_source_fingerprint:
        return _validation_result(status="BLOCKED", failure_code="source_identity_mismatch", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint)
    if not shards:
        return _validation_result(status="BLOCKED", failure_code="shard_set_missing", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint)
    try:
        shard_count = shards[0]["shardCount"]
        if isinstance(shard_count, bool) or not isinstance(shard_count, int) or shard_count <= 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return _validation_result(status="BLOCKED", failure_code="shard_count_invalid", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint)
    if len(shards) != shard_count:
        return _validation_result(status="BLOCKED", failure_code="shard_set_incomplete", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)

    indexes: list[int] = []
    assigned: list[str] = []
    fingerprints: dict[str, str] = {}
    totals = {"passed": 0, "failed": 0, "skipped": 0}
    for shard in shards:
        if not isinstance(shard, Mapping):
            return _validation_result(status="BLOCKED", failure_code="shard_invalid", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        if shard.get("schemaVersion") != "full-pytest-shard-v1" or shard.get("authority") != "prototype" or shard.get("formalReleaseEnabled") is not False:
            return _validation_result(status="BLOCKED", failure_code="shard_authority_invalid", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        if shard.get("status") != "PASS":
            return _validation_result(status="BLOCKED", failure_code="shard_not_passing", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        if shard.get("commitSha") != expected_commit_sha or shard.get("sourceFingerprint") != expected_source_fingerprint:
            return _validation_result(status="BLOCKED", failure_code="shard_identity_mismatch", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        if shard.get("manifestFingerprint") != manifest_fingerprint or shard.get("shardCount") != shard_count:
            return _validation_result(status="BLOCKED", failure_code="shard_manifest_mismatch", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        index = shard.get("shardIndex")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= shard_count or index in indexes:
            return _validation_result(status="BLOCKED", failure_code="shard_index_invalid", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        indexes.append(index)
        assigned_nodeids = shard.get("assignedNodeids")
        executed_nodeids = shard.get("executedNodeids")
        if not isinstance(assigned_nodeids, list) or not isinstance(executed_nodeids, list) or executed_nodeids != assigned_nodeids or any(not isinstance(item, str) or not item for item in assigned_nodeids):
            return _validation_result(status="BLOCKED", failure_code="shard_execution_coverage_invalid", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        assigned.extend(assigned_nodeids)
        fingerprint = shard.get("evidenceFingerprint")
        unsigned = {key: value for key, value in shard.items() if key != "evidenceFingerprint"}
        if not isinstance(fingerprint, str) or canonical_fingerprint(unsigned) != fingerprint:
            return _validation_result(status="BLOCKED", failure_code="shard_evidence_invalid", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        fingerprints[str(index)] = fingerprint
        counts = _bounded_counts(shard.get("result"))
        if counts is None:
            return _validation_result(status="BLOCKED", failure_code="shard_result_invalid", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count)
        for key, value in counts.items():
            totals[key] += value

    duplicates = sorted(nodeid for nodeid, count in Counter(assigned).items() if count > 1)
    assigned_set = set(assigned)
    manifest_set = set(nodeids)
    missing = sorted(manifest_set - assigned_set)
    unknown = sorted(assigned_set - manifest_set)
    if sorted(indexes) != list(range(shard_count)):
        return _validation_result(status="BLOCKED", failure_code="shard_indexes_incomplete", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count, covered=len(assigned), duplicates=duplicates, missing=missing, unknown=unknown, totals=totals, fingerprints=fingerprints)
    if duplicates or missing or unknown or len(assigned) != len(nodeids):
        return _validation_result(status="BLOCKED", failure_code="nodeid_coverage_mismatch", commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count, covered=len(assigned), duplicates=duplicates, missing=missing, unknown=unknown, totals=totals, fingerprints=fingerprints)
    return _validation_result(status="PASS", failure_code=None, commit_sha=expected_commit_sha, source_fingerprint=expected_source_fingerprint, manifest_fingerprint=manifest_fingerprint, shard_count=shard_count, covered=len(assigned), totals=totals, fingerprints=fingerprints)


def compare_serial_and_shard(serial: Mapping[str, Any], aggregate: Mapping[str, Any]) -> dict[str, Any]:
    """Compare only explicit test counts from fresh serial and shard results."""
    if (
        not isinstance(serial, Mapping) or not isinstance(aggregate, Mapping)
        or serial.get("status") != "PASS" or aggregate.get("status") != "PASS"
    ):
        unsigned = {
            "schemaVersion": "serial-shard-parity-v1",
            "status": "BLOCKED",
            "failureCode": "serial_or_shard_status_invalid",
            "serial": {},
            "shard": {},
        }
        return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}
    serial_counts = _bounded_counts(serial.get("result") if isinstance(serial, Mapping) and "result" in serial else serial)
    shard_counts = _bounded_counts(aggregate.get("result") if isinstance(aggregate, Mapping) and "result" in aggregate else aggregate)
    if serial_counts is None or shard_counts is None:
        status, code = "BLOCKED", "serial_or_shard_counts_invalid"
    elif serial_counts != shard_counts:
        status, code = "BLOCKED", "serial_parity_mismatch"
    else:
        status, code = "PASS", None
    unsigned = {"schemaVersion": "serial-shard-parity-v1", "status": status, "failureCode": code, "serial": serial_counts or {}, "shard": shard_counts or {}}
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def aggregate_pytest_shards(
    manifest: Mapping[str, Any],
    shards: Sequence[Mapping[str, Any]],
    *,
    expected_commit_sha: str,
    expected_source_fingerprint: str,
) -> dict[str, Any]:
    validation = validate_shard_set(manifest, shards, expected_commit_sha, expected_source_fingerprint)
    if validation["status"] != "PASS":
        _fail(str(validation["failureCode"]))
    manifest_fingerprint = validation["manifestFingerprint"]
    shard_count = validation["shardCount"]
    fingerprints = validation["shards"]
    totals = validation["result"]
    assigned = validation["coveredNodeids"]

    unsigned = {
        "schemaVersion": "full-pytest-shard-aggregate-v1",
        "status": "PASS",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": expected_commit_sha,
        "sourceFingerprint": expected_source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "shardCount": shard_count,
        "coveredNodeids": assigned,
        "shards": fingerprints,
        "result": totals,
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}
