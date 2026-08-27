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
