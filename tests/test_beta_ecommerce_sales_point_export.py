import pandas as pd
import pytest
from pathlib import Path


E_COMMERCE = "市場及電商部-電子商務組"
BETA_SALES_POINTS = (
    E_COMMERCE,
    "營銷運營中心-同業銷售部",
    "市場及商務部-電子商務組",
    "網銷組 i6",
)
BETA_LABEL = "市場電商及同業"


def _row(order_id, sales_point, salesperson, department=""):
    return {
        "來源單據號": order_id,
        "統一日期": "2026-05-01",
        "銷售點": sales_point,
        "銷售員": salesperson,
        "收款原幣金額": 100,
        "收款類型": "正常收款",
        "收款方式": "現金",
        "團負責人部門": department,
        "交易時間": "2026-05-01",
        "行程天數": 3,
        "數量": 2,
        "團名稱": "測試行程",
        "來源報表標籤": "旅行團",
    }


def tour_frame():
    return pd.DataFrame(
        [
            _row("T001", E_COMMERCE, "Alice"),
            _row("T002", E_COMMERCE, ""),
            _row("T003", "銅鑼灣分社", "Wrong", E_COMMERCE),
        ]
    )


def others_frame():
    return pd.DataFrame([_row("O001", E_COMMERCE, "Bob"), _row("O002", "銅鑼灣分社", "Other")])


def empty_frame():
    return tour_frame().iloc[0:0].copy()


def branch_mapping():
    return {"I6": E_COMMERCE, "225": "營銷運營中心-專職銷售組"}


def beta_category_frames():
    salespeople = [
        "011185 洪淑芬",
        "CTSIT 杨慧如",
        "debby 卢淑贞",
        "004014 杨慧如",
        "New Ecommerce Rep",
        " NaN ",
    ]
    tours = []
    tickets = []
    for index, salesperson in enumerate(salespeople, start=1):
        tours.append(_row(f"BT{index:03}", E_COMMERCE, salesperson))
        ticket = _row(f"BO{index:03}", E_COMMERCE, salesperson)
        ticket["來源報表標籤"] = "門券"
        tickets.append(ticket)
    return pd.DataFrame(tours), pd.DataFrame(tickets)


def test_beta_selects_sales_point_and_keeps_all_salespeople():
    import pipeline

    beta_tour, beta_others = pipeline._select_sales_point_frames(
        tour_frame(), others_frame(), sales_point=E_COMMERCE
    )

    assert set(beta_tour["銷售員"]) == {"Alice", "未指定"}
    assert set(beta_others["銷售員"]) == {"Bob"}
    assert set(beta_tour["銷售點"]) == {E_COMMERCE}


def test_beta_does_not_use_department_as_fallback():
    import pipeline

    beta_tour, _ = pipeline._select_sales_point_frames(
        tour_frame(), others_frame(), sales_point=E_COMMERCE
    )

    assert "Wrong" not in set(beta_tour["銷售員"])
    assert len(beta_tour) == 2


def test_beta_missing_salesperson_column_becomes_unspecified():
    import pipeline

    tours = tour_frame().drop(columns=["銷售員"])
    beta_tour, _ = pipeline._select_sales_point_frames(
        tours, empty_frame(), sales_point=E_COMMERCE
    )

    assert set(beta_tour["銷售員"]) == {"未指定"}


def test_beta_does_not_filter_by_legacy_sales_rep_list():
    import pipeline

    frames = tour_frame().assign(銷售員="New Ecommerce Rep")
    beta_tour, _ = pipeline._select_sales_point_frames(frames, others_frame(), sales_point=E_COMMERCE)

    assert "New Ecommerce Rep" in set(beta_tour["銷售員"])


def test_beta_selection_does_not_mutate_sources():
    import pipeline

    tour = tour_frame()
    others = others_frame()
    before_tour = tour.copy(deep=True)
    before_others = others.copy(deep=True)

    pipeline._select_sales_point_frames(tour, others, sales_point=E_COMMERCE)

    pd.testing.assert_frame_equal(tour, before_tour)
    pd.testing.assert_frame_equal(others, before_others)


def test_beta_workbook_replaces_specialist_sheets_with_all_ecommerce_salespeople():
    import pipeline

    _, _, facts = pipeline.build_dashboard_data(
        tour_frame(),
        others_frame(),
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    assert f"{BETA_LABEL}_經營統計" in facts
    assert set(facts[f"{BETA_LABEL}_經營統計"]["種類"]) == {"客戶服務部"}
    assert set(facts[f"{BETA_LABEL}_經營統計"]["文本"]) >= {"Alice", "未指定"}
    assert set(facts[f"{BETA_LABEL}_每天旅行團交易人數"]["銷售員"]) >= {"Alice", "未指定"}
    assert "Legacy Rep" not in set(facts[f"{BETA_LABEL}_每天旅行團交易人數"]["銷售員"])


def test_beta_combines_all_four_sales_points_into_six_named_sheets():
    import pipeline

    combined_tour = pd.concat(
        [
            tour_frame(),
            pd.DataFrame([_row("T004", "市場及商務部-電子商務組", "Business Rep")]),
        ],
        ignore_index=True,
    )
    combined_others = pd.concat(
        [
            others_frame(),
            pd.DataFrame(
                [
                    _row("O003", "營銷運營中心-同業銷售部", "Industry Rep"),
                    _row("O004", "網銷組 i6", "Network Rep"),
                ]
            ),
        ],
        ignore_index=True,
    )

    _, _, facts = pipeline.build_dashboard_data(
        combined_tour,
        combined_others,
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    expected_sheets = {
        f"{BETA_LABEL}_經營統計",
        f"{BETA_LABEL}_旅行團統計",
        f"{BETA_LABEL}_票務總計",
        f"{BETA_LABEL}_每天旅行團交易人數",
        f"{BETA_LABEL}_每天票務交易數量",
        f"{BETA_LABEL}_線路種類每天統計",
    }
    assert expected_sheets <= set(facts)
    assert set(facts[f"{BETA_LABEL}_經營統計"]["文本"]) >= {
        "Alice",
        "Bob",
        "Business Rep",
        "Industry Rep",
        "Network Rep",
        "未指定",
    }


def test_beta_export_normalizes_mixed_transaction_time_types_before_sorting():
    import pipeline

    tour = tour_frame()
    tour.loc[0, "交易時間"] = pd.Timestamp("2026-05-01 09:00:00")
    others = others_frame()
    others.loc[0, "交易時間"] = pd.Timestamp("2026-05-01 10:00:00")

    _, _, facts = pipeline.build_dashboard_data(
        tour,
        others,
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    assert not facts[f"{BETA_LABEL}_旅行團統計"].empty


def test_beta_empty_sales_point_keeps_schema_without_legacy_fallback():
    import pipeline

    _, _, facts = pipeline.build_dashboard_data(
        empty_frame(),
        empty_frame(),
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    expected_schemas = {
        f"{BETA_LABEL}_旅行團統計": [
            "銷售員", "所屬種類", "文本", "天數", "日期", "月份", "交易人數"
        ],
        f"{BETA_LABEL}_票務總計": [
            "銷售員", "所屬種類", "文本", "日期", "月份", "交易數量"
        ],
        f"{BETA_LABEL}_每天旅行團交易人數": [
            "銷售員", "所屬種類", "日期", "月份", "旅行團交易人數", "郵輪交易人數"
        ],
        f"{BETA_LABEL}_每天票務交易數量": [
            "銷售員", "所屬種類", "種類", "日期", "月份", "交易數量"
        ],
        f"{BETA_LABEL}_線路種類每天統計": [
            "銷售員", "所屬種類", "線路種類", "日子", "月份", "交易人數", "交易金額"
        ],
    }
    for sheet_name, columns in expected_schemas.items():
        assert facts[sheet_name].empty
        assert list(facts[sheet_name].columns) == columns


def test_beta_seller_scope_empty_sources_keep_ticket_sheet_schema():
    import pipeline

    _, _, facts = pipeline.build_dashboard_data(
        empty_frame(), empty_frame(), branch_mapping(), [], [], [],
        make_workbook=False, return_facts=True,
        beta_salesperson_scope=True, beta_seller_scope_verified=True,
    )

    sheet = facts[f"{BETA_LABEL}_票務總計"]
    assert sheet.empty
    assert list(sheet.columns) == ["銷售員", "所屬種類", "文本", "日期", "月份", "交易數量"]


def test_beta_market_summary_classifies_named_salespeople_and_defaults_others():
    import pipeline

    tours, tickets = beta_category_frames()
    _, _, facts = pipeline.build_dashboard_data(
        tours,
        tickets,
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    summary = facts[f"{BETA_LABEL}_經營統計"]
    categories = dict(zip(summary["文本"], summary["種類"]))
    assert categories == {
        "011185 洪淑芬": "商務同業組",
        "CTSIT 杨慧如": "商務同業組",
        "debby 卢淑贞": "商務同業組",
        "004014 杨慧如": "商務同業組",
        "New Ecommerce Rep": "客戶服務部",
        "未指定": "客戶服務部",
    }


def test_beta_seller_normalization_does_not_change_matched_total_detail():
    import pipeline

    _, _, facts = pipeline.build_dashboard_data(
        tour_frame(),
        others_frame(),
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    total = facts["總表_多表匹配完成"]
    blank_seller = total.loc[total["來源單據號"].eq("T002"), "銷售員"]
    assert len(blank_seller) == 1
    assert blank_seller.iloc[0] == ""


def test_beta_detail_sheets_split_by_salesperson_and_reconcile_totals():
    import pipeline

    tours, tickets = beta_category_frames()
    _, _, facts = pipeline.build_dashboard_data(
        tours,
        tickets,
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    expected_salespeople = {
        "011185 洪淑芬",
        "CTSIT 杨慧如",
        "debby 卢淑贞",
        "004014 杨慧如",
        "New Ecommerce Rep",
        "未指定",
    }
    sheet_specs = {
        f"{BETA_LABEL}_旅行團統計": ("交易人數", 12),
        f"{BETA_LABEL}_票務總計": ("交易數量", 12),
        f"{BETA_LABEL}_每天旅行團交易人數": ("旅行團交易人數", 12),
        f"{BETA_LABEL}_每天票務交易數量": ("交易數量", 12),
        f"{BETA_LABEL}_線路種類每天統計": ("交易人數", 12),
    }
    for sheet_name, (measure, expected_total) in sheet_specs.items():
        sheet = facts[sheet_name]
        assert {"銷售員", "所屬種類"} <= set(sheet.columns), sheet_name
        assert set(sheet["銷售員"]) == expected_salespeople, sheet_name
        assert int(sheet[measure].sum()) == expected_total, sheet_name
        assert set(sheet["所屬種類"]) == {"商務同業組", "客戶服務部"}, sheet_name
        assert set(
            sheet.loc[sheet["銷售員"].eq("004014 杨慧如"), "所屬種類"]
        ) == {"商務同業組"}, sheet_name

    assert "種類" in facts[f"{BETA_LABEL}_每天票務交易數量"].columns
    assert set(facts[f"{BETA_LABEL}_每天票務交易數量"]["種類"]) == {"其它門券"}
    assert int(facts[f"{BETA_LABEL}_線路種類每天統計"]["交易金額"].sum()) == 600


def test_beta_tour_detail_sheets_exclude_rows_without_a_reportable_date():
    import pipeline

    tours = pd.DataFrame([_row("BT-NO-DATE", E_COMMERCE, "Alice")])
    tickets = pd.DataFrame([_row("BO-NO-DATE", E_COMMERCE, "Alice")])
    tickets["來源報表標籤"] = "門券"
    for frame in (tours, tickets):
        frame["統一日期"] = None
        frame["交易時間"] = None
    _, _, facts = pipeline.build_dashboard_data(
        tours,
        tickets,
        branch_mapping(),
        [],
        [],
        [],
        make_workbook=False,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    assert facts[f"{BETA_LABEL}_旅行團統計"].empty
    assert facts[f"{BETA_LABEL}_每天旅行團交易人數"].empty
    assert facts[f"{BETA_LABEL}_票務總計"].empty
    assert facts[f"{BETA_LABEL}_每天票務交易數量"].empty
    assert facts[f"{BETA_LABEL}_線路種類每天統計"].empty


def test_beta_workbook_writes_salesperson_and_category_headers():
    import pipeline

    tours, tickets = beta_category_frames()
    workbook_bytes, _, _ = pipeline.build_dashboard_data(
        tours,
        tickets,
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        beta_sales_point=BETA_SALES_POINTS,
    )

    workbook = pd.ExcelFile(workbook_bytes)
    expected_headers = {
        f"{BETA_LABEL}_經營統計": {"文本", "種類"},
        f"{BETA_LABEL}_旅行團統計": {"銷售員", "所屬種類"},
        f"{BETA_LABEL}_票務總計": {"銷售員", "所屬種類"},
        f"{BETA_LABEL}_每天旅行團交易人數": {"銷售員", "所屬種類"},
        f"{BETA_LABEL}_每天票務交易數量": {"銷售員", "所屬種類", "種類"},
        f"{BETA_LABEL}_線路種類每天統計": {"銷售員", "所屬種類"},
    }
    assert set(expected_headers) <= set(workbook.sheet_names)
    for sheet_name, columns in expected_headers.items():
        assert columns <= set(pd.read_excel(workbook, sheet_name=sheet_name, nrows=0).columns)


def test_formal_dashboard_excludes_beta_sales_point_from_branch_aggregation():
    import pipeline

    _, _, facts = pipeline.build_dashboard_data(
        tour_frame(),
        others_frame(),
        branch_mapping(),
        [],
        [],
        ["Legacy Rep"],
        make_workbook=False,
        return_facts=True,
    )

    formal_branch_rows = facts["分社經營統計"]
    assert not formal_branch_rows["文本"].astype(str).str.contains(E_COMMERCE, regex=False).any()


def test_beta_export_has_distinct_variant_identity_and_artifacts():
    import app_workflows

    payload = app_workflows._compute_beta_export_workbooks(tour_frame(), others_frame())

    assert payload["export_variant"] == app_workflows.BETA_EXPORT_VARIANT
    assert payload["status"] == "blocked"
    assert payload["seller_scope_ready"] is False
    assert payload["warning"]
    assert not {"ex_beta", "ex_no_writeoff_beta", "ex_no_writeoff_refund_transfer_beta"} & set(payload)


def test_beta_export_does_not_fall_back_to_branch_or_salespoint_scope():
    import app_workflows

    tours = pd.DataFrame([_row("BT001", "銅鑼灣分社", "Alice")])
    others = pd.DataFrame([_row("BO001", "市場及商務部-電子商務組", "Ben")])

    payload = app_workflows._compute_beta_export_workbooks(tours, others)

    assert payload["status"] == "blocked"
    assert payload["seller_scope_ready"] is False
    assert "舊銷售點／單號前綴" in payload["warning"]
    assert not {"ex_beta", "ex_no_writeoff_beta", "ex_no_writeoff_refund_transfer_beta"} & set(payload)


def test_beta_export_blocks_unmatched_or_unverified_entity_scope():
    import app_workflows

    fingerprint = "a" * 64
    coverage = {
        "version": app_workflows.BETA_SELLER_SCOPE_CACHE_VERSION,
        "sourceFingerprint": fingerprint,
        "sourceFingerprints": [fingerprint],
        "dataGeneration": 8,
        "complete": True,
        "betaExportReady": True,
        "sourceRoleMappingVerified": True,
        "requiredDateIntervals": [{"start": "2026-05-01", "end": "2026-05-01"}],
        "missingDateIntervals": [],
        "missingSourceRoles": [],
        "rowsFingerprintMatched": True,
        "verification": {
            "preflightStatus": "matched",
            "driftStatus": "no_drift",
            "entityMatchStatus": "unmatched_or_unverified",
            "sourceRolesStatus": "complete",
                "sourceRoleMappingStatus": "verified",
            "liveDbUnchanged": True,
        },
    }

    payload = app_workflows._compute_beta_export_workbooks(
        pd.DataFrame([_row("BT001", "任意銷售點", "002785 朱淑宜")]),
        pd.DataFrame(),
        beta_seller_scope={
            "tour": pd.DataFrame([_row("BT001", "任意銷售點", "002785 朱淑宜")]),
            "others": pd.DataFrame(),
            "coverage": coverage,
        },
        current_data_generation=8,
    )

    assert payload["status"] == "blocked"
    assert payload["seller_scope_ready"] is False
    assert not set(app_workflows.BETA_EXPORT_WORKBOOK_KEYS) & set(payload)


def test_beta_export_builds_all_three_verified_v5_workbooks_with_reconciled_values():
    from io import BytesIO

    from openpyxl import load_workbook

    import app_workflows

    fingerprint = "b" * 64
    coverage = {
        "version": app_workflows.BETA_SELLER_SCOPE_CACHE_VERSION,
        "sourceFingerprint": fingerprint,
        "sourceFingerprints": [fingerprint],
        "dataGeneration": 8,
        "complete": True,
        "betaExportReady": True,
        "sourceRoleMappingVerified": True,
        "requiredDateIntervals": [{"start": "2026-05-01", "end": "2026-05-01"}],
        "missingDateIntervals": [],
        "missingSourceRoles": [],
        "rowsFingerprintMatched": True,
        "verification": {
            "preflightStatus": "matched",
            "driftStatus": "no_drift",
            "entityMatchStatus": "matched",
            "sourceRolesStatus": "complete",
                "sourceRoleMappingStatus": "verified",
            "liveDbUnchanged": True,
        },
    }
    tour = pd.DataFrame([
        _row("BT001", "銅鑼灣分社", "002785 朱淑宜"),
        _row("BT002", "銅鑼灣分社", "011185 洪淑芬", department="郵輪部"),
        _row("BT999", "銅鑼灣分社", "004499 胡闹香"),
    ])
    tour.loc[tour["來源單據號"].eq("BT999"), "收款類型"] = "掛賬核銷"
    others = pd.DataFrame([
        {**_row("TK001", "旺角分社", "002785 朱淑宜"), "來源報表標籤": "門券all"},
        {**_row("TK002", "旺角分社", "NL9 麦浩贤"), "來源報表標籤": "門券all"},
        {**_row("TK999", "旺角分社", "004014 杨慧如"), "來源報表標籤": "門券all"},
    ])
    others.loc[others["來源單據號"].eq("TK999"), "收款方式"] = "TT 退款轉團款"

    payload = app_workflows._compute_beta_export_workbooks(
        tour,
        others,
        beta_seller_scope={"tour": tour, "others": others, "coverage": coverage},
        current_data_generation=8,
        rules=(
            {"1A": "銅鑼灣分社", "2B": "旺角分社"},
            ["銅鑼灣分社", "旺角分社"],
            ["郵輪部"],
            [],
            [],
        ),
    )

    expected_metrics = {
        "ex_beta": ((200, 100, 300), 4, 2, 6),
        "ex_no_writeoff_beta": ((100, 100, 300), 2, 2, 6),
        "ex_no_writeoff_refund_transfer_beta": ((100, 100, 200), 2, 2, 4),
    }
    expected_headers = (
        "文本", "單選", "銷售員", "日期", "月份", "旅行團", "郵輪", "票務",
        "旅行團交易人數", "票務交易數量",
    )
    expected_salespeople = {
        "ex_beta": {
            "002785 朱淑宜", "011185 洪淑芬", "004499 胡闹香", "NL9 麦浩贤", "004014 杨慧如",
        },
        "ex_no_writeoff_beta": {
            "002785 朱淑宜", "011185 洪淑芬", "NL9 麦浩贤", "004014 杨慧如",
        },
        "ex_no_writeoff_refund_transfer_beta": {
            "002785 朱淑宜", "011185 洪淑芬", "NL9 麦浩贤",
        },
    }
    assert payload["seller_scope_ready"] is True
    assert set(app_workflows.BETA_EXPORT_WORKBOOK_KEYS) <= set(payload)
    assert all(payload[key] for key in app_workflows.BETA_EXPORT_WORKBOOK_KEYS)
    expected_sheet_order = None
    beta_sheet_headers = {
        f"{BETA_LABEL}_經營統計": ["文本", "種類", "日期", "月份", "旅行團", "郵輪", "票務"],
        f"{BETA_LABEL}_旅行團統計": ["銷售員", "所屬種類", "文本", "天數", "日期", "月份", "交易人數"],
        f"{BETA_LABEL}_票務總計": ["銷售員", "所屬種類", "文本", "日期", "月份", "交易數量"],
        f"{BETA_LABEL}_每天旅行團交易人數": ["銷售員", "所屬種類", "日期", "月份", "旅行團交易人數", "郵輪交易人數"],
        f"{BETA_LABEL}_每天票務交易數量": ["銷售員", "所屬種類", "種類", "日期", "月份", "交易數量"],
        f"{BETA_LABEL}_線路種類每天統計": ["銷售員", "所屬種類", "線路種類", "日子", "月份", "交易人數", "交易金額"],
    }
    for key in app_workflows.BETA_EXPORT_WORKBOOK_KEYS:
        workbook = load_workbook(BytesIO(payload[key]), read_only=True, data_only=True)
        assert len(workbook.sheetnames) == 19
        if expected_sheet_order is None:
            expected_sheet_order = workbook.sheetnames
        assert workbook.sheetnames == expected_sheet_order
        assert "分社經營統計_含銷售員" in workbook.sheetnames
        assert f"{BETA_LABEL}_經營統計" in workbook.sheetnames
        assert f"{BETA_LABEL}_旅行團統計" in workbook.sheetnames
        assert f"{BETA_LABEL}_票務總計" in workbook.sheetnames
        for sheet_name, headers in beta_sheet_headers.items():
            actual_headers = [
                cell.value for cell in next(workbook[sheet_name].iter_rows(min_row=1, max_row=1))
            ]
            assert actual_headers == headers, sheet_name
        branch_detail = list(workbook["分社經營統計_含銷售員"].iter_rows(values_only=True))
        branch_summary = list(workbook["分社經營統計"].iter_rows(values_only=True))
        tour_daily = list(workbook["分社每天旅行團交易人數"].iter_rows(values_only=True))
        ticket_daily = list(workbook["分社每天票務交易數量"].iter_rows(values_only=True))
        assert branch_detail[0] == expected_headers

        def total(rows, index):
            return sum(float(row[index] or 0) for row in rows[1:])

        expected_amounts, tour_people, cruise_people, ticket_quantity = expected_metrics[key]
        detail_amounts = tuple(total(branch_detail, index) for index in (5, 6, 7))
        summary_amounts = tuple(total(branch_summary, index) for index in (4, 5, 6))
        assert detail_amounts == expected_amounts
        assert detail_amounts == summary_amounts
        assert total(branch_detail, 8) == tour_people
        assert total(tour_daily, 3) == tour_people
        assert total(tour_daily, 4) == cruise_people
        assert total(branch_detail, 9) == ticket_quantity
        assert total(ticket_daily, 4) == ticket_quantity
        assert {row[2] for row in branch_detail[1:]} == expected_salespeople[key]
        workbook.close()


def test_beta_seller_scope_pipeline_rejects_unverified_direct_build():
    import pipeline

    with pytest.raises(ValueError, match="requires verified seller-scope coverage"):
        pipeline.build_dashboard_data(
            tour_frame(),
            empty_frame(),
            {},
            [],
            [],
            [],
            make_workbook=False,
            beta_salesperson_scope=True,
        )


def test_beta_branch_salesperson_tour_people_uses_global_order_deduplication():
    import pipeline

    first = _row("DUP001", E_COMMERCE, "Alice")
    first["交易時間"] = "2026-05-01 09:00:00"
    first["數量"] = 2
    second = _row("DUP001", "銅鑼灣分社", "Ben")
    second["交易時間"] = "2026-05-01 10:00:00"
    second["數量"] = 5
    retained_branch_row = _row("DUP002", "銅鑼灣分社", "Alice")
    retained_branch_row["交易時間"] = "2026-05-01 09:00:00"
    retained_branch_row["數量"] = 2
    later_beta_row = _row("DUP002", E_COMMERCE, "Ben")
    later_beta_row["交易時間"] = "2026-05-01 10:00:00"
    later_beta_row["數量"] = 5

    _, _, facts = pipeline.build_dashboard_data_excluding_receipt_types(
        pd.DataFrame([first, second, retained_branch_row, later_beta_row]),
        empty_frame(),
        {"1A": "銅鑼灣分社", "I6": E_COMMERCE},
        ["銅鑼灣分社"],
        [],
        [],
        [],
        make_workbook=False,
        include_branch_salesperson_sheet=True,
        return_facts=True,
        beta_sales_point=BETA_SALES_POINTS,
    )

    detail = facts["分社經營統計_含銷售員"]
    official_daily = facts["分社每天旅行團交易人數"]
    assert detail["旅行團交易人數"].sum() == 2
    assert official_daily["交易人數"].sum() + official_daily["郵輪交易人數"].sum() == 2
    assert detail.loc[detail["銷售員"].eq("Ben"), "旅行團交易人數"].sum() == 0
    assert detail.loc[
        detail["銷售員"].eq("Alice") & detail["文本"].str.endswith("銅鑼灣分社"),
        "旅行團交易人數",
    ].sum() == 2


def test_beta_export_cache_requires_current_variant_and_all_three_workbooks():
    import app_workflows

    keys = ("ex_beta", "ex_no_writeoff_beta", "ex_no_writeoff_refund_transfer_beta")
    current_payload = {key: b"workbook" for key in keys}
    current_payload["beta_export_variant"] = app_workflows.BETA_EXPORT_VARIANT
    current_payload["beta_export_seller_scope_ready"] = True
    stale_payload = dict(current_payload, beta_export_variant="beta_market_ecommerce_peer_sales_points_v4")
    incomplete_payload = dict(current_payload)
    incomplete_payload.pop("ex_no_writeoff_beta")

    assert app_workflows._beta_export_cache_is_current(current_payload)
    assert not app_workflows._beta_export_cache_is_current(stale_payload)
    assert not app_workflows._beta_export_cache_is_current(incomplete_payload)


def test_beta_export_persistent_cache_survives_refresh_and_invalidates_on_inputs(tmp_path, monkeypatch):
    import app_workflows

    monkeypatch.setattr(app_workflows, "AI_CACHE_DIR", tmp_path)
    calls = []

    def fake_compute(*args, **kwargs):
        calls.append(1)
        return {
            **{key: f"workbook-{len(calls)}".encode() for key in app_workflows.BETA_EXPORT_WORKBOOK_KEYS},
            "export_variant": app_workflows.BETA_EXPORT_VARIANT,
            "seller_scope_ready": True,
        }

    monkeypatch.setattr(app_workflows, "_compute_beta_export_workbooks", fake_compute)

    def fresh_cache(formal_key="formal-1", seller_amount=10, generation=3):
        return {
            "export_cache_key": formal_key,
            "current_data_generation": generation,
            "raw_t": pd.DataFrame({"單號": ["1"], "金額": [100]}),
            "raw_o": pd.DataFrame(),
            "beta_seller_scope_tour": pd.DataFrame({"單號": ["2"], "金額": [seller_amount]}),
            "beta_seller_scope_others": pd.DataFrame(),
            "beta_seller_scope_coverage": {"dataGeneration": generation},
        }

    first = fresh_cache()
    assert app_workflows._ensure_beta_export_workbooks(first)
    assert len(calls) == 1
    refreshed = fresh_cache()
    assert app_workflows._load_beta_export_workbooks(refreshed)
    assert refreshed["ex_no_writeoff_refund_transfer_beta"] == b"workbook-1"
    assert len(calls) == 1

    assert not app_workflows._load_beta_export_workbooks(fresh_cache(seller_amount=20))
    assert not app_workflows._load_beta_export_workbooks(fresh_cache(formal_key="formal-2"))
    changed_source = fresh_cache()
    changed_source["raw_t"].loc[0, "金額"] = 101
    assert not app_workflows._load_beta_export_workbooks(changed_source)

    monkeypatch.setattr(app_workflows, "BETA_EXPORT_VARIANT", "beta-next-version")
    assert not app_workflows._load_beta_export_workbooks(fresh_cache())


def test_beta_export_persistent_cache_reports_disk_write_failure(tmp_path, monkeypatch):
    import app_workflows

    monkeypatch.setattr(app_workflows, "AI_CACHE_DIR", tmp_path)
    monkeypatch.setattr(app_workflows, "_save_pickle_cache", lambda *args: False)
    monkeypatch.setattr(
        app_workflows,
        "_compute_beta_export_workbooks",
        lambda *args, **kwargs: {
            **{key: b"workbook" for key in app_workflows.BETA_EXPORT_WORKBOOK_KEYS},
            "export_variant": app_workflows.BETA_EXPORT_VARIANT,
            "seller_scope_ready": True,
        },
    )
    cache = {"export_cache_key": "formal", "raw_t": pd.DataFrame(), "raw_o": pd.DataFrame()}
    assert not app_workflows._ensure_beta_export_workbooks(cache)
    assert not app_workflows._beta_export_cache_is_current(cache)


def test_beta_cache_identity_differs_from_formal_identity():
    import app_workflows

    assert app_workflows._build_export_variant_key(
        "source", "rules", "beta_ecommerce_sales_point_v1"
    ) != app_workflows._build_export_variant_key("source", "rules", "official")


def test_export_ui_exposes_beta_ecommerce_comparison_without_replacing_official_action():
    source = Path("app_pages.py").read_text(encoding="utf-8")

    assert "Beta comparison export" in source
    assert BETA_LABEL in source
    assert "ex_no_writeoff_refund_transfer" in source


def test_beta_seller_scope_uses_beta_specific_approved_list_and_restores_v5_dimensions():
    import pipeline

    tour = pd.DataFrame(
        [
            _row("SELLER-PEER", "任意銷售點", "011185 洪淑芬"),
            _row("SELLER-CS", "另一任意銷售點", "002785 朱淑宜"),
            _row("SELLER-EXTRA", "未知銷售點", "新合資格銷售員"),
            _row("SELLER-OUTSIDER", "電子商務舊銷售點", "未核准銷售員"),
        ]
    )
    others = pd.DataFrame(
        [
            {**_row("TICKET-EXTRA", "另一新銷售點", "新合資格銷售員"), "來源報表標籤": "門券"},
            {**_row("TICKET-OUTSIDER", E_COMMERCE, "未核准銷售員"), "來源報表標籤": "門券"},
        ]
    )

    _, _, facts = pipeline.build_dashboard_data(
        tour,
        others,
        branch_mapping(),
        [],
        [],
        ["002785 朱淑宜", "新合資格銷售員"],
        make_workbook=False,
        return_facts=True,
        beta_salesperson_scope=True,
        beta_seller_scope_verified=True,
    )

    summary = facts[f"{BETA_LABEL}_經營統計"]
    assert set(summary["文本"]) == {"011185 洪淑芬", "002785 朱淑宜"}
    assert dict(zip(summary["文本"], summary["種類"])) == {
        "011185 洪淑芬": "商務同業組",
        "002785 朱淑宜": "客戶服務部",
    }
    assert "新合資格銷售員" not in set(facts[f"{BETA_LABEL}_旅行團統計"]["銷售員"])
    assert "未核准銷售員" not in set(facts[f"{BETA_LABEL}_旅行團統計"]["銷售員"])
    assert {"銷售員", "所屬種類"} <= set(facts[f"{BETA_LABEL}_旅行團統計"].columns)


def test_beta_seller_scope_deduplicates_in_input_order_without_transaction_time():
    import pipeline

    first = _row("DUPLICATE-ORDER", "任意銷售點", "002785 朱淑宜")
    second = _row("DUPLICATE-ORDER", "任意銷售點", "011185 洪淑芬")
    tour = pd.DataFrame([first, second]).drop(columns=["交易時間"])

    _, _, facts = pipeline.build_dashboard_data(
        tour,
        empty_frame().drop(columns=["交易時間"]),
        branch_mapping(),
        [],
        [],
        [],
        make_workbook=False,
        return_facts=True,
        _already_normalized=True,
        beta_salesperson_scope=True,
        beta_seller_scope_verified=True,
    )

    daily = facts[f"{BETA_LABEL}_每天旅行團交易人數"]
    assert set(daily["銷售員"]) == {"002785 朱淑宜"}


def test_beta_seller_scope_retains_v5_branch_salesperson_sheet_without_branch_drift():
    import pipeline

    tour = pd.DataFrame(
        [
            _row("BRANCH-TOUR", "銅鑼灣分社", "普通分社銷售員"),
            _row("BETA-TOUR", "任意銷售點", "002785 朱淑宜"),
        ]
    )
    others = pd.DataFrame(
        [{**_row("BRANCH-TICKET", "銅鑼灣分社", "普通分社銷售員"), "來源報表標籤": "門券"}]
    )

    _, _, legacy = pipeline.build_dashboard_data(
        tour, others, {"33": "銅鑼灣分社"}, [], [], [], make_workbook=False, return_facts=True
    )
    _, _, seller_scope = pipeline.build_dashboard_data(
        tour,
        others,
        {"33": "銅鑼灣分社"},
        [],
        [],
        ["002785 朱淑宜"],
        make_workbook=False,
        return_facts=True,
        include_branch_salesperson_sheet=True,
        beta_salesperson_scope=True,
        beta_seller_scope_verified=True,
    )

    assert "分社經營統計_含銷售員" in seller_scope
    pd.testing.assert_frame_equal(legacy["分社經營統計"], seller_scope["分社經營統計"])


def test_beta_seller_scope_workbook_headers_match_v5():
    from io import BytesIO

    from openpyxl import load_workbook

    import pipeline

    tour = pd.DataFrame(
        [
            _row("BRANCH-TOUR", "銅鑼灣分社", "普通分社銷售員"),
            _row("BETA-TOUR", "任意銷售點", "002785 朱淑宜"),
        ]
    )
    others = pd.DataFrame(
        [
            {**_row("BRANCH-TICKET", "銅鑼灣分社", "普通分社銷售員"), "來源報表標籤": "門券"},
            {**_row("BETA-TICKET", "另一任意銷售點", "新合資格銷售員"), "來源報表標籤": "門券"},
        ]
    )
    buffer, _, _ = pipeline.build_dashboard_data(
        tour,
        others,
        {"33": "銅鑼灣分社"},
        [],
        [],
        ["002785 朱淑宜", "新合資格銷售員", "普通分社銷售員"],
        make_workbook=True,
        include_branch_salesperson_sheet=True,
        beta_salesperson_scope=True,
        beta_seller_scope_verified=True,
    )
    workbook = load_workbook(BytesIO(buffer.getvalue()), read_only=True, data_only=True)
    expected = {
        "分社經營統計_含銷售員": ["文本", "單選", "銷售員", "日期", "月份", "旅行團", "郵輪", "票務", "旅行團交易人數", "票務交易數量"],
        "市場電商及同業_經營統計": ["文本", "種類", "日期", "月份", "旅行團", "郵輪", "票務"],
        "市場電商及同業_旅行團統計": ["銷售員", "所屬種類", "文本", "天數", "日期", "月份", "交易人數"],
        "市場電商及同業_票務總計": ["銷售員", "所屬種類", "文本", "日期", "月份", "交易數量"],
        "市場電商及同業_每天旅行團交易人數": ["銷售員", "所屬種類", "日期", "月份", "旅行團交易人數", "郵輪交易人數"],
        "市場電商及同業_每天票務交易數量": ["銷售員", "所屬種類", "種類", "日期", "月份", "交易數量"],
        "市場電商及同業_線路種類每天統計": ["銷售員", "所屬種類", "線路種類", "日子", "月份", "交易人數", "交易金額"],
    }

    assert set(expected) <= set(workbook.sheetnames)
    for sheet_name, headers in expected.items():
        actual = [cell.value for cell in next(workbook[sheet_name].iter_rows(min_row=1, max_row=1))]
        assert actual == headers, sheet_name


def test_beta_capture_keeps_nonbranch_prefix_rows_and_uses_main_seller_identity():
    import pipeline

    main = pd.DataFrame(
        [
            {"來源單據號": "ZZ-001", "收款時間": "2026-05-01", "收款原幣金額": 100, "銷售員": "002785 朱淑宜"},
            {"來源單據號": "XX-002", "收款時間": "2026-05-02", "收款原幣金額": 200, "銷售員": "新合資格銷售員"},
            {"來源單據號": "YY-003", "收款時間": "2026-05-03", "收款原幣金額": 300, "銷售員": "不在名冊"},
        ]
    )
    tours = pd.DataFrame(
        [
            {"交易號碼": "ZZ-001", "銷售員": "錯誤副表銷售員", "交易時間": "2026-05-01", "團負責人部門": "旅行團部", "行程天數": 3, "數量": 1, "團名稱": "測試"},
            {"交易號碼": "XX-002", "銷售員": "錯誤副表銷售員", "交易時間": "2026-05-02", "團負責人部門": "旅行團部", "行程天數": 3, "數量": 1, "團名稱": "測試"},
            {"交易號碼": "YY-003", "銷售員": "錯誤副表銷售員", "交易時間": "2026-05-03", "團負責人部門": "旅行團部", "行程天數": 3, "數量": 1, "團名稱": "測試"},
        ]
    )

    tour, _, _ = pipeline.process_raw_files(
        main,
        ("tour.xlsx", tours),
        [],
        {"195": E_COMMERCE},
        ["ZZ"],
        ["新合資格銷售員"],
        beta_capture_mode=True,
    )
    selected, _ = pipeline._select_beta_salesperson_frames(
        tour, empty_frame(), ["新合資格銷售員"]
    )

    assert set(tour["來源單據號"]) == {"ZZ-001", "XX-002", "YY-003"}
    assert dict(zip(tour["來源單據號"], tour["銷售員"])) == {
        "ZZ-001": "002785 朱淑宜",
        "XX-002": "新合資格銷售員",
        "YY-003": "不在名冊",
    }
    assert set(selected["來源單據號"]) == {"ZZ-001"}
