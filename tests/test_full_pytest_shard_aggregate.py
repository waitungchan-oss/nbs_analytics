import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import ReleaseGateValidationError
from scripts.full_pytest_shard_aggregate import aggregate_pytest_shards


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
