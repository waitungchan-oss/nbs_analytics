import pandas as pd

from tests.fixtures.gmv_export_semantic_fixture import semantic_fixture


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
