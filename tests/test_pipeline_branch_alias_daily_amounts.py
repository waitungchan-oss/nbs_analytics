import pandas as pd


def test_branch_aliases_merge_only_in_report_copy_without_mutating_source():
    import pipeline
    from rules import BRANCH_NAME_ALIASES

    source = pd.DataFrame(
        {
            "銷售點": list(BRANCH_NAME_ALIASES),
            "副表_銷售點": list(BRANCH_NAME_ALIASES),
        }
    )
    original = source.copy(deep=True)

    normalized = pipeline.apply_branch_name_aliases(source)

    assert normalized["銷售點"].tolist() == list(BRANCH_NAME_ALIASES.values())
    assert normalized["副表_銷售點"].tolist() == list(BRANCH_NAME_ALIASES)
    pd.testing.assert_frame_equal(source, original)


def test_daily_amount_sheets_use_receipt_date_and_match_official_branch_summary():
    import pipeline

    canonical_branch = "大埔分社"
    receipt_date = "2026-10-12"
    tour = pd.DataFrame(
        [
            {
                "來源單據號": "T-ALIAS",
                "收款時間": f"{receipt_date} 09:00:00",
                "統一日期": receipt_date,
                "交易時間": "2026-10-10 23:50:00",
                "銷售點": "大埔門市",
                "副表_銷售點": "大埔門市",
                "收款原幣金額": 100,
                "團負責人部門": "",
                "目的地大類": "中國長線",
                "行程天數": 3,
                "數量": 1,
            },
            {
                "來源單據號": "T-CANONICAL",
                "收款時間": f"{receipt_date} 10:00:00",
                "統一日期": receipt_date,
                "交易時間": "2026-10-10 23:40:00",
                "銷售點": canonical_branch,
                "副表_銷售點": canonical_branch,
                "收款原幣金額": 50,
                "團負責人部門": "",
                "目的地大類": "中國長線",
                "行程天數": 3,
                "數量": 1,
            },
        ]
    )
    others = pd.DataFrame(
        [
            {
                "來源單據號": "O-ALIAS",
                "收款時間": f"{receipt_date} 11:00:00",
                "統一日期": receipt_date,
                "交易時間": "2026-10-10 23:30:00",
                "銷售點": "大埔門市",
                "副表_銷售點": "大埔門市",
                "收款原幣金額": 30,
                "團名稱": "景點門票",
                "來源報表標籤": "門券all",
                "數量": 1,
            }
        ]
    )

    _, _, facts = pipeline.build_dashboard_data(
        tour,
        others,
        {"20": canonical_branch},
        [canonical_branch],
        [],
        [],
        make_workbook=False,
        return_facts=True,
    )

    official = facts["分社經營統計"]
    official_day = official.loc[
        (official["文本"] == f"20{canonical_branch}")
        & (official["日期"] == receipt_date)
    ].iloc[0]
    assert official_day["旅行團"] == 150
    assert official_day["票務"] == 30

    tour_daily = facts["NBS分社_旅行團金額統計"]
    assert set(tour_daily["日期"]) == {receipt_date}
    assert set(tour_daily["分社種類"]) == {canonical_branch}
    assert tour_daily["交易金額"].sum() == 150

    route_daily = facts["分社線路種類每天統計"]
    route_day = route_daily.loc[
        (route_daily["分社"] == canonical_branch)
        & (route_daily["日子"] == receipt_date)
    ]
    assert route_day["交易金額"].sum() == 150

    ticket_daily = facts["NBS分社_票務金額統計"]
    assert set(ticket_daily["日期"]) == {receipt_date}
    assert set(ticket_daily["分社名稱"]) == {canonical_branch}
    assert ticket_daily["交易金額"].sum() == 30

    detail = facts["總表_多表匹配完成"]
    alias_detail = detail.loc[detail["來源單據號"] == "T-ALIAS"].iloc[0]
    assert alias_detail["銷售點"] == "大埔門市"
    assert alias_detail["副表_銷售點"] == "大埔門市"
