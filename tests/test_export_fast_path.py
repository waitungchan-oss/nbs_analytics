import io
import time

import pandas as pd


def _fixture_frames():
    tour = pd.DataFrame(
        [
            {
                "來源單據號": "T-001",
                "統一日期": "2026-05-01",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Alice",
                "收款原幣金額": 100,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "團負責人部門": "",
                "交易時間": "2026-05-01",
                "行程天數": 3,
                "數量": 2,
            }
        ]
    )
    others = pd.DataFrame(
        [
            {
                "來源單據號": "O-001",
                "統一日期": "2026-05-01",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Alice",
                "收款原幣金額": 25,
                "收款類型": "正常收款",
                "收款方式": "信用卡",
                "交易時間": "2026-05-01",
                "團名稱": "景點門票",
                "來源報表標籤": "門券all",
                "行程天數": 0,
                "數量": 3,
            }
        ]
    )
    return tour, others


def test_legacy_export_produces_three_workbooks():
    import app_workflows

    tour, others = _fixture_frames()
    started = time.perf_counter()
    payload = app_workflows._compute_export_workbooks(tour, others)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    keys = ("ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer")
    assert all(isinstance(payload[key], bytes) and payload[key] for key in keys)
    assert payload["export_cache_version"] == app_workflows.EXPORT_CACHE_VERSION
    assert elapsed_ms >= 0
    assert all(len(payload[key]) > 0 for key in keys)


def test_facts_controller_builds_intermediate_once_and_publishes_equivalent_artifacts(tmp_path):
    from openpyxl import Workbook

    from backend.services.export_fast_path_service import build_fast_export_job_from_facts
    from backend.services.export_intermediate_service import ExportScope, build_scope_report_facts

    tour, others = _fixture_frames()
    calls = {"facts": 0}

    def facts_builder(intermediate):
        calls["facts"] += 1
        return {
            scope.value: build_scope_report_facts(intermediate, scope)
            for scope in ExportScope
        }

    def writer(facts, path):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "營收"
        sheet.append(["scope", "rows"])
        sheet.append([facts.scope_id, len(facts.tour) + len(facts.others)])
        workbook.save(path)

    def reference_builder(raw_tour, raw_others):
        return {
            key: _reference_workbook(scope, len(raw_tour) + len(raw_others))
            for key, scope in {
                "ex": "all", "ex_no_writeoff": "no_writeoff",
                "ex_no_writeoff_refund_transfer": "official",
            }.items()
        }

    result = build_fast_export_job_from_facts(
        tour, others, generation_token="generation-1", rules_fingerprint="rules-1",
        export_schema_version="schema-1", cache_root=tmp_path,
        reference_builder=reference_builder, facts_builder=facts_builder,
        writer=writer, worker_count=2,
    )

    assert result.status == "READY"
    assert result.manifest_path is not None
    assert calls["facts"] == 1
    assert not (tmp_path / ".staging").exists()


def _reference_workbook(scope, rows):
    from io import BytesIO
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "營收"
    sheet.append(["scope", "rows"])
    sheet.append([scope, rows])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
