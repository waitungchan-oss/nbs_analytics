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
