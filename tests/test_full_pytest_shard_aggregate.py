import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import ReleaseGateValidationError
from scripts.full_pytest_shard_aggregate import (
    aggregate_pytest_shards,
    compare_serial_and_shard,
    validate_shard_set,
)


COMMIT = "a" * 40
SOURCE = "b" * 64


def _manifest(nodeids):
    nodeids = sorted(nodeids)
    payload = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
    }
    payload["manifestFingerprint"] = canonical_fingerprint({
        "schemaVersion": payload["schemaVersion"],
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": nodeids,
    })
    return payload


def _shard(manifest, index, nodeids, status="PASS"):
    unsigned = {
        "schemaVersion": "full-pytest-shard-v1",
        "status": status,
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "manifestFingerprint": manifest["manifestFingerprint"],
        "shardIndex": index,
        "shardCount": 2,
        "assignedNodeids": nodeids,
        "executedNodeids": nodeids if status == "PASS" else [],
        "result": {"passed": len(nodeids), "failed": 0, "skipped": 0},
    }
    return {**unsigned, "evidenceFingerprint": canonical_fingerprint(unsigned)}


def test_aggregate_requires_exactly_once_manifest_coverage():
    manifest = _manifest(["tests/test_a.py::test_one", "tests/test_b.py::test_two"])
    shards = [_shard(manifest, 0, [manifest["nodeids"][0]]), _shard(manifest, 1, [manifest["nodeids"][1]])]

    result = aggregate_pytest_shards(
        manifest,
        shards,
        expected_commit_sha=COMMIT,
        expected_source_fingerprint=SOURCE,
    )

    assert result["schemaVersion"] == "full-pytest-shard-aggregate-v1"
    assert result["status"] == "PASS"
    assert result["authority"] == "prototype"
    assert result["formalReleaseEnabled"] is False


def test_validate_shard_set_returns_bounded_identity_and_coverage_result():
    manifest = _manifest(["tests/test_a.py::test_one", "tests/test_b.py::test_two"])
    shards = [_shard(manifest, 0, [manifest["nodeids"][0]]), _shard(manifest, 1, [manifest["nodeids"][1]])]

    result = validate_shard_set(manifest, shards, COMMIT, SOURCE)

    assert result["status"] == "PASS"
    assert result["coveredNodeids"] == 2
    assert result["duplicateNodeids"] == []
    assert result["missingNodeids"] == []
    assert result["unknownNodeids"] == []


def test_parity_mismatch_blocks_rollout_candidate():
    result = compare_serial_and_shard(
        {"status": "PASS", "passed": 10, "failed": 0, "skipped": 1},
        {"status": "PASS", "passed": 9, "failed": 0, "skipped": 1},
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_parity_mismatch"


@pytest.mark.parametrize("status", ["FAIL", "BLOCKED"])
def test_parity_rejects_non_passing_inputs_even_when_counts_match(status):
    result = compare_serial_and_shard(
        {"status": "PASS", "passed": 10, "failed": 0, "skipped": 1},
        {"status": status, "passed": 10, "failed": 0, "skipped": 1},
    )

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "serial_or_shard_status_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda shards, manifest: shards.pop(),
        lambda shards, manifest: shards[1]["assignedNodeids"].append(manifest["nodeids"][0]),
        lambda shards, manifest: shards[0].__setitem__("sourceFingerprint", "c" * 64),
        lambda shards, manifest: shards[0].__setitem__("status", "FAIL"),
    ],
)
def test_aggregate_rejects_missing_duplicate_cross_source_or_failed_shard(mutation):
    manifest = _manifest(["tests/test_a.py::test_one", "tests/test_b.py::test_two"])
    shards = [_shard(manifest, 0, [manifest["nodeids"][0]]), _shard(manifest, 1, [manifest["nodeids"][1]])]
    mutation(shards, manifest)

    with pytest.raises(ReleaseGateValidationError):
        aggregate_pytest_shards(
            manifest,
            shards,
            expected_commit_sha=COMMIT,
            expected_source_fingerprint=SOURCE,
        )
