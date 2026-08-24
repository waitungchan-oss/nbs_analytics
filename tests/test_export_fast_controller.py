from pathlib import Path

import pandas as pd


def _candidate_builder(scope_id, intermediate):
    from io import BytesIO

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "營收"
    sheet.append(["來源單據號", "收款原幣金額"])
    frame = intermediate.classified_tour if scope_id == "all" else intermediate.classified_tour.head(1)
    for _, row in frame.iterrows():
        sheet.append([row.get("來源單據號", ""), float(row.get("收款原幣金額", 0))])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _reference_builder(tour, others):
    from io import BytesIO

    from openpyxl import Workbook

    result = {}
    for key in ("ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer"):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "營收"
        sheet.append(["來源單據號", "收款原幣金額"])
        for _, row in tour.iterrows():
            sheet.append([row.get("來源單據號", ""), float(row.get("收款原幣金額", 0))])
        output = BytesIO()
        workbook.save(output)
        result[key] = output.getvalue()
    return result


def _frames():
    return (
        pd.DataFrame([{"來源單據號": "T-001", "統一日期": "2026-05-01", "收款原幣金額": 100}]),
        pd.DataFrame([{"來源單據號": "O-001", "統一日期": "2026-05-01", "收款原幣金額": 25}]),
    )


def test_fast_export_job_publishes_ready_manifest_after_equivalence(tmp_path):
    from backend.services.export_fast_path_service import build_fast_export_job

    tour, others = _frames()
    result = build_fast_export_job(
        tour,
        others,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        export_schema_version="schema-1",
        cache_root=tmp_path,
        reference_builder=_reference_builder,
        candidate_builder=_candidate_builder,
        worker_count=1,
    )

    assert result.status == "READY"
    assert result.manifest_path is not None
    assert result.fallback_reason is None
    assert result.timings["total_ms"] >= 0


def test_fast_export_job_uses_bounded_parallel_workers(tmp_path):
    from backend.services.export_fast_path_service import build_fast_export_job

    tour, others = _frames()
    result = build_fast_export_job(
        tour,
        others,
        generation_token="generation-parallel",
        rules_fingerprint="rules-1",
        export_schema_version="schema-1",
        cache_root=tmp_path,
        reference_builder=_reference_builder,
        candidate_builder=_candidate_builder,
        worker_count=3,
    )

    assert result.status == "READY"


def test_fast_export_job_falls_back_when_candidate_is_not_equivalent(tmp_path):
    from backend.services.export_fast_path_service import build_fast_export_job

    def mismatching_builder(scope_id, intermediate):
        return b"not-an-xlsx"

    tour, others = _frames()
    result = build_fast_export_job(
        tour,
        others,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        export_schema_version="schema-1",
        cache_root=tmp_path,
        reference_builder=_reference_builder,
        candidate_builder=mismatching_builder,
        worker_count=1,
    )

    assert result.status == "FALLBACK"
    assert result.manifest_path is None
    assert result.fallback_reason


def test_fast_export_job_blocks_publication_when_baseline_is_not_pass(tmp_path):
    from backend.services.export_fast_path_service import build_fast_export_job

    tour, others = _frames()
    result = build_fast_export_job(
        tour,
        others,
        generation_token="generation-baseline-blocked",
        rules_fingerprint="rules-1",
        export_schema_version="schema-1",
        cache_root=tmp_path,
        reference_builder=_reference_builder,
        candidate_builder=_candidate_builder,
        worker_count=1,
        baseline_status="DRIFT",
    )

    assert result.status == "FALLBACK"
    assert "baseline status" in (result.fallback_reason or "")


def test_rollout_mode_controls_whether_ready_fast_result_is_selected():
    from backend.services.export_fast_path_service import ExportRolloutMode, select_export_path

    assert select_export_path(ExportRolloutMode.DISABLED, fast_ready=True) == "legacy"
    assert select_export_path(ExportRolloutMode.SHADOW, fast_ready=True) == "legacy"
    assert select_export_path(ExportRolloutMode.OPT_IN, fast_ready=True) == "fast"
    assert select_export_path(ExportRolloutMode.DEFAULT, fast_ready=True) == "fast"
    assert select_export_path(ExportRolloutMode.DEFAULT, fast_ready=False) == "legacy"
