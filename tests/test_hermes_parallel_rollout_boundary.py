from backend.agents.acceptance_parallel_rollout import build_parallel_rollout_evidence
from backend.agents.evidence_models import canonical_fingerprint


LINEAGE = {
    "contract_fingerprint": "c" * 64,
    "baseline_family_id": "acceptance-full-serial-ci-v1",
    "manifest_fingerprint": "d" * 64,
    "test_population_fingerprint": canonical_fingerprint({
        "nodeids": sorted(f"tests/test_parallel.py::test_case_{index:02d}" for index in range(16))
    }),
    "runner_fingerprint": "f" * 64,
    "environment_fingerprint": "0" * 64,
    "dataset_snapshot_fingerprint": "1" * 64,
    "selection_mode": "full",
}


def _run(index: int) -> dict:
    from backend.agents.evidence_models import canonical_fingerprint

    serial_result = {"passed": 16, "failed": 0, "skipped": 0}
    aggregate_unsigned = {
        "schemaVersion": "full-pytest-shard-aggregate-v1",
        "status": "PASS",
        "authority": "prototype",
        "formalReleaseEnabled": False,
        "manifestFingerprint": LINEAGE["manifest_fingerprint"],
        "shardCount": 4,
        "coveredNodeids": 16,
        "shards": {str(shard): (str(shard) + "9") * 32 for shard in range(4)},
        "result": serial_result,
    }
    aggregate = {
        **aggregate_unsigned,
        "evidenceFingerprint": canonical_fingerprint(aggregate_unsigned),
    }
    parity_unsigned = {
        "schemaVersion": "serial-shard-parity-v1",
        "status": "PASS",
        "failureCode": None,
        "serial": serial_result,
        "shard": serial_result,
    }
    parity = {
        **parity_unsigned,
        "evidenceFingerprint": canonical_fingerprint(parity_unsigned),
    }
    lineage = {
        "contractFingerprint": LINEAGE["contract_fingerprint"],
        "baselineFamilyId": LINEAGE["baseline_family_id"],
        "manifestFingerprint": LINEAGE["manifest_fingerprint"],
        "testPopulationFingerprint": LINEAGE["test_population_fingerprint"],
        "runnerFingerprint": LINEAGE["runner_fingerprint"],
        "environmentFingerprint": LINEAGE["environment_fingerprint"],
        "datasetSnapshotFingerprint": LINEAGE["dataset_snapshot_fingerprint"],
        "selectionMode": LINEAGE["selection_mode"],
    }
    return {
        **lineage,
        "populationKind": "full-pytest-nodeid",
        "runIndex": index,
        "serialStatus": "PASS",
        "parallelStatus": "PASS",
        "testPopulationCount": 16,
        "populationNodeids": sorted(f"tests/test_parallel.py::test_case_{index:02d}" for index in range(16)),
        "shardCoverage": [
            {
                "shardIndex": shard,
                "assignedNodeids": [
                    nodeid for position, nodeid in enumerate(sorted(f"tests/test_parallel.py::test_case_{index:02d}" for index in range(16)))
                    if position % 4 == shard
                ],
                "executedNodeids": [
                    nodeid for position, nodeid in enumerate(sorted(f"tests/test_parallel.py::test_case_{index:02d}" for index in range(16)))
                    if position % 4 == shard
                ],
            }
            for shard in range(4)
        ],
        "serialResult": serial_result,
        "serialLineage": lineage,
        "parallelLineage": lineage,
        "serialWallSeconds": 100.0,
        "parallelWallSeconds": 75.0,
        "serialArtifactFingerprint": "2" * 64,
        "parallelArtifactFingerprint": "3" * 64,
        "shardAggregateFingerprint": "4" * 64,
        "parity": parity,
        "shardAggregate": aggregate,
        "blockers": [],
    }


def _artifact() -> dict:
    return build_parallel_rollout_evidence(
        commit_sha="a" * 40,
        source_fingerprint="b" * 64,
        shard_count=4,
        measured_runs=[_run(index) for index in range(3)],
        **LINEAGE,
    )


def test_hermes_accepts_parallel_artifact_as_diagnostic_only():
    from backend.agents.hermes_parallel_rollout_boundary import validate_parallel_rollout_boundary

    result = validate_parallel_rollout_boundary(_artifact())

    assert result["status"] == "PASS"
    assert result["authority"] == "diagnostic"
    assert result["formalReleaseEnabled"] is False
    assert result["populationKind"] == "full-pytest-nodeid"


def test_hermes_blocks_72_slot_population_from_parallel_comparison():
    from backend.agents.hermes_parallel_rollout_boundary import validate_parallel_rollout_boundary

    artifact = _artifact()
    artifact["populationKind"] = "agent-eval-72-slot"
    artifact["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in artifact.items() if key != "evidenceFingerprint"}
    )

    result = validate_parallel_rollout_boundary(artifact)

    assert result["status"] == "BLOCKED"
    assert result["failureCode"] == "population_kind_mismatch"
    assert result["formalReleaseEnabled"] is False


def test_hermes_boundary_does_not_promote_or_mutate_input():
    from backend.agents.hermes_parallel_rollout_boundary import validate_parallel_rollout_boundary

    artifact = _artifact()
    before = dict(artifact)

    result = validate_parallel_rollout_boundary(artifact)

    assert result["formalReleaseEnabled"] is False
    assert artifact == before
