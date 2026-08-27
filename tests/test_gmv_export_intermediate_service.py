import pandas as pd

from tests.fixtures.gmv_export_semantic_fixture import semantic_fixture
from tests.fixtures.gmv_export_semantic_fixture import read_gmv_workbook_semantics


def test_base_preparation_has_deterministic_key_fingerprints_and_scope_masks():
    from backend.services.gmv_export_intermediate_service import build_gmv_export_base_preparation

    tour, others, _, _, _ = semantic_fixture()
    first = build_gmv_export_base_preparation(
        version_id="v1",
        revenue_generation_token="revenue-1",
        rules_fingerprint="rules-1",
        export_schema_version="official-branch-salesperson-v1",
        pipeline_fingerprint="pipeline-1",
        tour=tour,
        others=others,
    )
    reordered = build_gmv_export_base_preparation(
        version_id="v1",
        revenue_generation_token="revenue-1",
        rules_fingerprint="rules-1",
        export_schema_version="official-branch-salesperson-v1",
        pipeline_fingerprint="pipeline-1",
        tour=tour.iloc[::-1].reset_index(drop=True),
        others=others,
    )

    assert first.key == reordered.key
    assert first.source_fingerprints == reordered.source_fingerprints
    assert first.scope_masks["all"][0].all()
    assert first.scope_masks["no_writeoff"][0].sum() == len(tour) - 1
    assert first.scope_masks["official"][0].sum() == len(tour) - 2
    assert first.scope_masks["official"][1].all()


def test_base_preparation_key_changes_when_any_contract_input_changes():
    from backend.services.gmv_export_intermediate_service import build_gmv_export_base_preparation

    tour, others, _, _, _ = semantic_fixture()
    kwargs = {
        "version_id": "v1",
        "revenue_generation_token": "revenue-1",
        "rules_fingerprint": "rules-1",
        "export_schema_version": "official-branch-salesperson-v1",
        "pipeline_fingerprint": "pipeline-1",
        "tour": tour,
        "others": others,
    }
    baseline = build_gmv_export_base_preparation(**kwargs)
    for field, changed in (
        ("version_id", "v2"),
        ("revenue_generation_token", "revenue-2"),
        ("rules_fingerprint", "rules-2"),
        ("export_schema_version", "schema-2"),
        ("pipeline_fingerprint", "pipeline-2"),
    ):
        candidate_kwargs = dict(kwargs)
        candidate_kwargs[field] = changed
        assert build_gmv_export_base_preparation(**candidate_kwargs).key != baseline.key


def test_base_preparation_does_not_mutate_inputs_and_normalizes_copies():
    from backend.services.gmv_export_intermediate_service import build_gmv_export_base_preparation

    tour, others, _, _, _ = semantic_fixture()
    tour_before = tour.copy(deep=True)
    others_before = others.copy(deep=True)
    result = build_gmv_export_base_preparation(
        version_id="v1", revenue_generation_token="revenue-1", rules_fingerprint="rules-1",
        export_schema_version="schema-1", pipeline_fingerprint="pipeline-1", tour=tour, others=others,
    )

    pd.testing.assert_frame_equal(tour, tour_before)
    pd.testing.assert_frame_equal(others, others_before)
    assert result.tour is not tour
    assert result.others is not others
    assert "__gmv_source_id" in result.tour.columns


def test_report_facts_builds_all_scopes_without_excel_serialization(monkeypatch):
    import pipeline
    from backend.services.gmv_export_intermediate_service import build_gmv_report_facts

    tour, others, _, _, _ = semantic_fixture()
    tour.attrs["gmv_refund_dimension"] = "總退款"
    others.attrs["gmv_refund_dimension"] = "總退款"
    rules = (
        {"1A": "銅鑼灣分社", "2B": "上環服務點", "3C": "元朗服務點"},
        ["銅鑼灣分社", "上環服務點", "元朗服務點"],
        ["郵輪部"],
        ["Alice", "Bob", "Ben", "Carol", "Specialist"],
        [],
    )

    def fail_excel_writer(*args, **kwargs):
        raise AssertionError("facts build must not create Excel bytes")

    monkeypatch.setattr(pipeline.pd, "ExcelWriter", fail_excel_writer)
    for scope_id in ("all", "no_writeoff", "official"):
        facts = build_gmv_report_facts(
            adjusted_tour=tour,
            adjusted_others=others,
            scope_id=scope_id,
            rules=rules,
            include_branch_salesperson_sheet=scope_id == "official",
        )
        assert facts.scope_id == scope_id
        assert facts.sheets
        assert facts.row_counts == {name: len(frame) for name, frame in facts.sheets.items()}
        assert facts.schema_fingerprint
        assert facts.data_fingerprint


def test_report_facts_match_legacy_sheet_schema_and_row_counts():
    import pipeline
    from backend.services.gmv_export_intermediate_service import build_gmv_report_facts

    tour, others, _, _, _ = semantic_fixture()
    tour.attrs["gmv_refund_dimension"] = "總退款"
    others.attrs["gmv_refund_dimension"] = "總退款"
    rules = (
        {"1A": "銅鑼灣分社", "2B": "上環服務點", "3C": "元朗服務點"},
        ["銅鑼灣分社", "上環服務點", "元朗服務點"],
        ["郵輪部"],
        ["Alice", "Bob", "Ben", "Carol", "Specialist"],
        [],
    )
    builders = {
        "all": lambda: pipeline.build_dashboard_data(
            tour, others, *rules[:4], include_branch_salesperson_sheet=False,
        ),
        "no_writeoff": lambda: pipeline.build_dashboard_data_excluding_receipt_types(
            tour, others, *rules[:4], ["掛賬核銷"], include_branch_salesperson_sheet=False,
        ),
        "official": lambda: pipeline.build_dashboard_data_excluding_receipt_types(
            tour, others, *rules[:4], ["掛賬核銷"], excluded_payment_methods=["TT 退款轉團款"],
            include_branch_salesperson_sheet=True,
        ),
    }
    for scope_id, builder in builders.items():
        workbook, _, _ = builder()
        legacy = read_gmv_workbook_semantics(workbook.getvalue())
        facts = build_gmv_report_facts(
            adjusted_tour=tour, adjusted_others=others, scope_id=scope_id,
            rules=rules, include_branch_salesperson_sheet=scope_id == "official",
        )
        assert set(facts.sheets) == set(legacy["sheetNames"])
        for sheet_name, frame in facts.sheets.items():
            legacy_sheet = legacy["sheets"][sheet_name]
            headers = [str(column) for column in frame.columns]
            assert facts.row_counts[sheet_name] == legacy_sheet["rowCount"]
            assert headers == legacy_sheet["headers"]

            def normalize(value):
                if pd.isna(value):
                    return ""
                if isinstance(value, float):
                    return format(value, ".12g")
                return str(value).strip()

            normalized_rows = [
                [normalize(value) for value in row]
                for row in frame.itertuples(index=False, name=None)
            ]
            assert normalized_rows == legacy_sheet["rows"]
            key_columns = {column: headers.index(column) for column in ("來源單據號", "退款維度", "指標", "欄位") if column in headers}
            stable_keys = [
                {column: row[index] for column, index in key_columns.items()}
                for row in normalized_rows
            ]
            assert stable_keys == legacy_sheet["stableKeys"]


def test_report_facts_rejects_unknown_refund_dimension():
    from backend.services.gmv_export_intermediate_service import build_gmv_report_facts

    tour, others, _, _, _ = semantic_fixture()
    try:
        build_gmv_report_facts(
            adjusted_tour=tour, adjusted_others=others, scope_id="official",
            rules=({}, [], [], [], []), include_branch_salesperson_sheet=False,
            dimension="未知退款維度",
        )
    except ValueError as exc:
        assert "dimension" in str(exc)
    else:
        raise AssertionError("unknown refund dimension must fail closed")


def test_report_fact_set_reuses_preparation_for_three_scopes():
    from backend.services.gmv_export_intermediate_service import (
        build_gmv_export_base_preparation,
        build_gmv_report_fact_set,
    )

    tour, others, _, _, _ = semantic_fixture()
    preparation = build_gmv_export_base_preparation(
        version_id="v1", revenue_generation_token="r1", rules_fingerprint="rules-1",
        export_schema_version="schema-1", pipeline_fingerprint="pipeline-1",
        tour=tour, others=others,
    )
    rules = (
        {"1A": "銅鑼灣分社", "2B": "上環服務點", "3C": "元朗服務點"},
        ["銅鑼灣分社", "上環服務點", "元朗服務點"],
        ["郵輪部"], ["Alice", "Bob", "Ben", "Carol", "Specialist"], [],
    )
    result = build_gmv_report_fact_set(
        preparation=preparation, adjusted_tour=tour, adjusted_others=others,
        dimension="總退款", rules=rules, include_branch_salesperson_sheet=True,
    )

    assert set(result.facts_by_scope) == {"all", "no_writeoff", "official"}
    assert result.aggregation_count == 1
    assert result.preparation_fingerprint
    assert "分社經營統計_含銷售員" not in result.facts_by_scope["all"].sheets
    assert "分社經營統計_含銷售員" in result.facts_by_scope["official"].sheets


def test_dashboard_intermediate_preserves_scoped_report_contract():
    import pipeline

    tour, others, _, _, _ = semantic_fixture()
    intermediate = pipeline.build_dashboard_intermediate(
        tour, others,
        branch_mapping={"1A": "銅鑼灣分社", "2B": "上環服務點", "3C": "元朗服務點"},
        target_branches_s3=["銅鑼灣分社", "上環服務點", "元朗服務點"],
        cruise_depts=["郵輪部"],
        sales_rep_list=["Alice", "Bob", "Ben", "Carol", "Specialist"],
    )
    _, branch, facts = pipeline.build_dashboard_data_from_intermediate(
        intermediate, scope_id="official", include_branch_salesperson_sheet=True,
    )
    assert intermediate.source_fingerprint
    assert "分社經營統計_含銷售員" in facts
    assert branch is not None
