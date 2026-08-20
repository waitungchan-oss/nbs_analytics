from scripts.benchmark_gmv_page_load import compare_benchmarks, summarize_samples


def test_summary_uses_warm_median_and_p95():
    summary = summarize_samples([0.100, 0.110, 0.120, 0.130, 0.140])

    assert summary["medianMs"] == 120.0
    assert summary["p95Ms"] == 140.0


def test_home_regression_must_satisfy_both_relative_and_absolute_gates():
    result = compare_benchmarks(
        baseline={"medianMs": 1000.0},
        candidate={"medianMs": 1060.0},
        absolute_limit_ms=300.0,
        relative_limit=0.05,
    )

    assert result["passed"] is False
    assert result["regressionMs"] == 60.0
    assert result["regressionRatio"] == 0.06
