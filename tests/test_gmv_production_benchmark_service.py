import pytest


def test_benchmark_case_normalizes_ratio_and_has_bounded_manifest():
    from backend.services.gmv_production_benchmark_service import build_benchmark_case

    case = build_benchmark_case(
        affected_ratio=0.001,
        receipt_count=10_000,
        scenario_flags=("tt_method_transition",),
    )

    assert case.case_id == "ratio-0.001"
    assert case.affected_count == 10
    assert case.formal_scope == "不含掛賬核銷與TT退款轉團款"
    assert case.database_mutated is False
    assert case.scenario_flags == ("tt_method_transition",)
    assert "raw" not in case.to_dict()


def test_benchmark_case_supports_over_guardrail_case():
    from backend.services.gmv_production_benchmark_service import build_benchmark_case

    case = build_benchmark_case(
        affected_ratio=0.25,
        receipt_count=100,
        max_affected_ratio=0.2,
    )

    assert case.case_id == "over-guardrail"
    assert case.affected_count == 25
    assert case.expected_decision == "FULL_REBUILD_REQUIRED"


def test_benchmark_case_rejects_invalid_ratio_and_count():
    from backend.services.gmv_production_benchmark_service import build_benchmark_case

    with pytest.raises(ValueError, match="affected_ratio"):
        build_benchmark_case(affected_ratio=1.1, receipt_count=100)
    with pytest.raises(ValueError, match="receipt_count"):
        build_benchmark_case(affected_ratio=0.1, receipt_count=0)


def test_isolated_fixture_contains_required_scenarios_and_is_hashable(tmp_path):
    from backend.services.gmv_production_benchmark_service import (
        build_benchmark_case,
        create_isolated_benchmark_fixture,
    )

    case = build_benchmark_case(
        affected_ratio=0.01,
        receipt_count=100,
        scenario_flags=("status_transition", "tt_method_transition", "over_refund", "multi_member"),
    )
    fixture = create_isolated_benchmark_fixture(case, root=tmp_path / "fixture")

    assert fixture.db_path.is_file()
    assert fixture.cache_dir.is_dir()
    assert fixture.database_sha256
    assert fixture.schema_table_count >= 2
    assert fixture.version_id
    assert fixture.revenue_generation_token
    assert set(fixture.scenario_manifest) >= {
        "status_transition", "amount_change", "tt_method_transition", "over_refund", "multi_member", "unmatched"
    }
    assert fixture.database_mutated is False


def test_fixture_rejects_project_runtime_paths(tmp_path):
    from backend.services.gmv_production_benchmark_service import (
        build_benchmark_case,
        create_isolated_benchmark_fixture,
    )

    case = build_benchmark_case(affected_ratio=0.01, receipt_count=100)
    with pytest.raises(ValueError, match="isolated"):
        create_isolated_benchmark_fixture(case, root=".nbs_runtime_cache")


def test_production_like_runner_reports_cold_candidate_and_warm_evidence(tmp_path):
    from backend.services.gmv_production_benchmark_service import (
        BenchmarkRunEvidence,
        BenchmarkSummary,
        build_benchmark_case,
        run_production_rebuild_benchmark,
    )

    case = build_benchmark_case(affected_ratio=0.01, receipt_count=100)
    calls = []

    def full_runner(case, run_root):
        calls.append(("full", case.case_id, run_root))
        return {"status": "ready", "artifactCount": 11, "equivalenceStatus": "PASS"}

    def candidate_runner(case, run_root):
        calls.append(("candidate", case.case_id, run_root))
        return {"status": "ready", "artifactCount": 11, "equivalenceStatus": "PASS"}

    def warm_reader(case, run_root):
        calls.append(("warm", case.case_id, run_root))
        return {"status": "CURRENT", "canExport": True}

    summary = run_production_rebuild_benchmark(
        case,
        root=tmp_path / "bench",
        runs=3,
        warm_reads=3,
        full_runner=full_runner,
        candidate_runner=candidate_runner,
        warm_reader=warm_reader,
    )

    assert isinstance(summary, BenchmarkSummary)
    assert len(summary.runs) == 3
    assert len(summary.warm_reads) == 3
    assert all(isinstance(item, BenchmarkRunEvidence) for item in summary.runs)
    assert all(item.equivalence_status == "PASS" for item in summary.runs)
    assert summary.status == "INCONCLUSIVE"
    assert len(calls) == 9


def test_isolated_runner_uses_existing_cache_service_without_mutating_fixture_db(tmp_path):
    import hashlib

    from backend.services.gmv_production_benchmark_service import (
        build_benchmark_case,
        create_isolated_benchmark_fixture,
        run_isolated_production_rebuild_benchmark,
    )

    case = build_benchmark_case(affected_ratio=0.01, receipt_count=100)
    fixture = create_isolated_benchmark_fixture(case, root=tmp_path / "fixture")
    before = hashlib.sha256(fixture.db_path.read_bytes()).hexdigest()

    summary = run_isolated_production_rebuild_benchmark(
        case, fixture=fixture, root=tmp_path / "runs", runs=3, warm_reads=3
    )

    assert summary.case.case_id == case.case_id
    assert len(summary.runs) == 3
    assert len(summary.warm_reads) == 3
    assert all(item.equivalence_status in {"PASS", "FAIL", "NOT_RUN"} for item in summary.runs)
    assert summary.status == "INCONCLUSIVE"
    assert hashlib.sha256(fixture.db_path.read_bytes()).hexdigest() == before


def _evidence(**kwargs):
    from backend.services.gmv_production_benchmark_service import BenchmarkRunEvidence

    values = {
        "mode": "incremental-shadow",
        "decision": "INCREMENTAL_ELIGIBLE",
        "stage_ms": {"fullCold": 100.0, "incrementalShadow": 25.0},
        "affected_rows": 1,
        "copied_rows": 99,
        "recomputed_rows": 1,
        "unaffected_aggregation_calls": 0,
        "peak_rss_bytes": 100,
        "reference_peak_rss_bytes": 100,
        "equivalence_status": "PASS",
    }
    values.update(kwargs)
    return BenchmarkRunEvidence(**values)


def test_benchmark_gate_requires_equivalence_and_zero_unaffected_calls():
    from backend.services.gmv_production_benchmark_service import (
        BenchmarkSummary,
        build_benchmark_case,
        evaluate_benchmark_gates,
    )

    case = build_benchmark_case(affected_ratio=0.01, receipt_count=100)
    summary = BenchmarkSummary(case, (_evidence(),) * 3, (), "PASS")
    gate = evaluate_benchmark_gates(summary)

    assert gate.status == "PASS"
    assert gate.median_ms == 25.0
    assert gate.p95_ms == 25.0

    failed = BenchmarkSummary(
        case,
        (_evidence(equivalence_status="FAIL"), _evidence(), _evidence()),
        (),
        "FAIL",
    )
    assert evaluate_benchmark_gates(failed).status == "FAIL"

    inconclusive = BenchmarkSummary(
        case,
        (_evidence(unaffected_aggregation_calls=None),) * 3,
        (),
        "INCONCLUSIVE",
    )
    assert evaluate_benchmark_gates(inconclusive).status == "INCONCLUSIVE"


def test_benchmark_gate_rejects_rss_regression_and_requires_full_fallback():
    from backend.services.gmv_production_benchmark_service import (
        BenchmarkSummary,
        build_benchmark_case,
        evaluate_benchmark_gates,
    )

    case = build_benchmark_case(affected_ratio=0.01, receipt_count=100)
    rss_summary = BenchmarkSummary(
        case,
        (_evidence(peak_rss_bytes=151, reference_peak_rss_bytes=100),) * 3,
        (),
        "PASS",
    )
    gate = evaluate_benchmark_gates(rss_summary)
    assert gate.status == "FAIL"
    assert "RSS_GUARD_EXCEEDED" in gate.failure_reasons

    over = build_benchmark_case(affected_ratio=0.25, receipt_count=100)
    over_summary = BenchmarkSummary(
        over,
        (_evidence(decision="FULL_REBUILD_REQUIRED"),) * 3,
        (),
        "INCONCLUSIVE",
    )
    assert evaluate_benchmark_gates(over_summary).status == "PASS"
