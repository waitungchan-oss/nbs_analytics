import pytest


def test_affected_rebuild_benchmark_is_read_only_contract():
    from scripts.benchmark_gmv_affected_rebuild import run_affected_rebuild_benchmark

    report = run_affected_rebuild_benchmark(receipt_count=100, affected_count=3, samples=3)

    assert report["schemaVersion"] == "gmv-affected-rebuild-benchmark-v1"
    assert report["unaffectedCount"] == 97
    assert report["unaffectedAggregationCalls"] == 0
    assert report["sampleCount"] == 3
    assert report["decision"] == "INCREMENTAL_ELIGIBLE"
    assert report["p95PlanMs"] >= report["medianPlanMs"]


def test_affected_rebuild_benchmark_rejects_invalid_bounds():
    from scripts.benchmark_gmv_affected_rebuild import run_affected_rebuild_benchmark

    with pytest.raises(ValueError):
        run_affected_rebuild_benchmark(receipt_count=10, affected_count=11)
