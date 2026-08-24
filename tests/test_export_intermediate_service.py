import hashlib

import pandas as pd
import pytest


def _frames():
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
            },
            {
                "來源單據號": "T-002",
                "統一日期": "2026-05-01",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Bob",
                "收款原幣金額": 50,
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "團負責人部門": "",
                "交易時間": "2026-05-01",
                "行程天數": 3,
                "數量": 1,
            },
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
            },
            {
                "來源單據號": "O-002",
                "統一日期": "2026-05-01",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Bob",
                "收款原幣金額": 75,
                "收款類型": "正常收款",
                "收款方式": "TT 退款轉團款",
                "交易時間": "2026-05-01",
                "團名稱": "景點門票",
                "來源報表標籤": "門券all",
                "行程天數": 0,
                "數量": 4,
            },
        ]
    )
    return tour, others


def test_build_export_intermediate_is_deterministic_and_does_not_mutate_sources():
    from backend.services.export_intermediate_service import build_export_intermediate

    tour, others = _frames()
    before_tour = tour.copy(deep=True)
    before_others = others.copy(deep=True)

    first = build_export_intermediate(
        tour,
        others,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        schema_version="schema-1",
    )
    second = build_export_intermediate(
        tour,
        others,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        schema_version="schema-1",
    )

    assert first.source_fingerprints == second.source_fingerprints
    assert first.normalized_tour.equals(second.normalized_tour)
    assert first.normalized_others.equals(second.normalized_others)
    assert tour.equals(before_tour)
    assert others.equals(before_others)


def test_scope_inputs_apply_official_exclusions_without_database_access(monkeypatch):
    import database

    from backend.services.export_intermediate_service import (
        ExportScope,
        build_export_intermediate,
        build_scope_report_inputs,
    )

    monkeypatch.setattr(database, "get_connection", lambda: pytest.fail("SQLite must not be opened"), raising=False)
    tour, others = _frames()
    intermediate = build_export_intermediate(
        tour,
        others,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        schema_version="schema-1",
    )

    all_inputs = build_scope_report_inputs(intermediate, ExportScope.ALL)
    official_inputs = build_scope_report_inputs(intermediate, ExportScope.OFFICIAL)

    assert len(all_inputs.tour) == 2
    assert len(all_inputs.others) == 2
    assert len(official_inputs.tour) == 1
    assert len(official_inputs.others) == 1
    assert official_inputs.scope_id == "official"
    assert official_inputs.tour["收款原幣金額"].sum() == 100
    assert official_inputs.others["收款原幣金額"].sum() == 25


def test_source_fingerprint_changes_when_business_frame_changes():
    from backend.services.export_intermediate_service import build_export_intermediate

    tour, others = _frames()
    baseline = build_export_intermediate(
        tour,
        others,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        schema_version="schema-1",
    )
    changed = others.copy()
    changed.loc[0, "收款原幣金額"] = 26
    candidate = build_export_intermediate(
        tour,
        changed,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        schema_version="schema-1",
    )

    assert baseline.source_fingerprints["others"] != candidate.source_fingerprints["others"]
