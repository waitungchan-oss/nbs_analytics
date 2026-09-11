"""Validate diagnostic pytest shards with exactly-once manifest coverage."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import ReleaseGateValidationError
from scripts.full_pytest_shard import _validate_manifest


def _fail(message: str) -> None:
    raise ReleaseGateValidationError(message)


def aggregate_pytest_shards(
    manifest: Mapping[str, Any],
    shards: Sequence[Mapping[str, Any]],
    *,
    expected_commit_sha: str,
    expected_source_fingerprint: str,
) -> dict[str, Any]:
    commit_sha, source_fingerprint, nodeids, manifest_fingerprint = _validate_manifest(manifest)
    if commit_sha != expected_commit_sha:
        _fail("shard aggregate commit mismatch")
    if source_fingerprint != expected_source_fingerprint:
        _fail("shard aggregate source mismatch")
    if not shards:
        _fail("shards are missing")
    try:
        shard_count = int(shards[0]["shardCount"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseGateValidationError("shard count is invalid") from exc
    if shard_count <= 0 or len(shards) != shard_count:
        _fail("shard set is incomplete")

    indexes = []
    assigned: list[str] = []
    fingerprints: dict[str, str] = {}
    totals = {"passed": 0, "failed": 0, "skipped": 0}
    for shard in shards:
        if shard.get("schemaVersion") != "full-pytest-shard-v1" or shard.get("authority") != "prototype" or shard.get("formalReleaseEnabled") is not False:
            _fail("shard authority is invalid")
        if shard.get("status") != "PASS":
            _fail("non-passing shard cannot aggregate")
        if shard.get("commitSha") != expected_commit_sha or shard.get("sourceFingerprint") != expected_source_fingerprint:
            _fail("shard identity mismatch")
        if shard.get("manifestFingerprint") != manifest_fingerprint or shard.get("shardCount") != shard_count:
            _fail("shard manifest identity mismatch")
        index = shard.get("shardIndex")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= shard_count or index in indexes:
            _fail("shard index is invalid or duplicated")
        indexes.append(index)
        assigned_nodeids = shard.get("assignedNodeids")
        executed_nodeids = shard.get("executedNodeids")
        if not isinstance(assigned_nodeids, list) or not isinstance(executed_nodeids, list) or executed_nodeids != assigned_nodeids:
            _fail("shard execution coverage is invalid")
        assigned.extend(assigned_nodeids)
        fingerprint = shard.get("evidenceFingerprint")
        unsigned = {key: value for key, value in shard.items() if key != "evidenceFingerprint"}
        if not isinstance(fingerprint, str) or canonical_fingerprint(unsigned) != fingerprint:
            _fail("shard evidence fingerprint is invalid")
        fingerprints[str(index)] = fingerprint
        result = shard.get("result")
        if not isinstance(result, dict):
            _fail("shard result is invalid")
        for key in totals:
            value = result.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                _fail("shard result count is invalid")
            totals[key] += value

    if sorted(indexes) != list(range(shard_count)):
        _fail("shard indexes are incomplete")
    if len(assigned) != len(set(assigned)) or sorted(assigned) != nodeids:
        _fail("manifest nodeids are not covered exactly once")

    unsigned = {
        "schemaVersion": "full-pytest-shard-aggregate-v1",
        "status": "PASS",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": expected_commit_sha,
        "sourceFingerprint": expected_source_fingerprint,
        "manifestFingerprint": manifest_fingerprint,
        "shardCount": shard_count,
        "coveredNodeids": len(assigned),
        "shards": fingerprints,
        "result": totals,
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}
