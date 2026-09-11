import subprocess

import pytest

from scripts.full_pytest_shard import run_pytest_shard, select_shard_nodeids
from backend.agents.evidence_models import canonical_fingerprint


COMMIT = "a" * 40
SOURCE = "b" * 64


def _manifest(nodeids):
    payload = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": sorted(nodeids),
    }
    payload["manifestFingerprint"] = canonical_fingerprint({
        "schemaVersion": payload["schemaVersion"],
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": payload["nodeids"],
    })
    return payload


def test_select_shard_nodeids_is_stable_and_covers_every_node_once():
    nodeids = ["tests/test_c.py::test_3", "tests/test_a.py::test_1", "tests/test_b.py::test_2"]

    shards = [select_shard_nodeids(nodeids, index, 2) for index in range(2)]

    assert sorted(item for shard in shards for item in shard) == sorted(nodeids)
    assert set(shards[0]).isdisjoint(shards[1])


def test_run_pytest_shard_requires_exact_collection_before_pass(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])

    def run(argv, **kwargs):
        if "--collect-only" in argv:
            return subprocess.CompletedProcess(argv, 0, "tests/test_a.py::test_one\n", "")
        return subprocess.CompletedProcess(argv, 0, "1 passed in 0.01s\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    result = run_pytest_shard(
        tmp_path,
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
    )

    assert result["schemaVersion"] == "full-pytest-shard-v1"
    assert result["status"] == "PASS"
    assert result["authority"] == "prototype"
    assert result["formalReleaseEnabled"] is False
    assert result["executedNodeids"] == manifest["nodeids"]


def test_run_pytest_shard_blocks_collection_mismatch(monkeypatch, tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "tests/test_other.py::test_two\n", ""),
    )
    result = run_pytest_shard(
        tmp_path,
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
    )

    assert result["status"] == "FAIL"
    assert result["metadata"]["failureCode"] == "collection_mismatch"


def test_run_pytest_shard_rejects_existing_fixture_root(tmp_path):
    manifest = _manifest(["tests/test_a.py::test_one"])
    fixture_root = tmp_path / "already-used"
    fixture_root.mkdir()

    with pytest.raises(ValueError, match="unique"):
        run_pytest_shard(
            tmp_path,
            manifest,
            shard_index=0,
            shard_count=1,
            fixture_root=fixture_root,
        )


def test_run_pytest_shard_records_distinct_finish_timestamp(monkeypatch, tmp_path):
    manifest = _manifest([])
    timestamps = iter(["2026-09-11T08:00:00Z", "2026-09-11T08:00:01Z"])
    monkeypatch.setattr("scripts.full_pytest_shard._timestamp", lambda: next(timestamps))

    result = run_pytest_shard(
        tmp_path,
        manifest,
        shard_index=0,
        shard_count=1,
        fixture_root=tmp_path / "shard-0",
    )

    assert result["startedAt"] == "2026-09-11T08:00:00Z"
    assert result["finishedAt"] == "2026-09-11T08:00:01Z"
