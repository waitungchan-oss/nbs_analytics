import warnings

import pandas as pd


def test_pipeline_report_facts_are_futurewarning_free_and_numeric():
    import pipeline

    tour = pd.DataFrame(
        [
            {
                "來源單據號": "T1",
                "統一日期": "2026-05-01",
                "交易時間": "2026-05-01 09:00:00",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Alice",
                "收款原幣金額": "100",
                "收款類型": "正常收款",
                "收款方式": "現金",
                "數量": "2",
                "行程天數": "3",
                "團負責人部門": "",
            },
            {
                "來源單據號": "T2",
                "統一日期": "2026-05-02",
                "交易時間": "not-a-date",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Bob",
                "收款原幣金額": None,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "數量": None,
                "行程天數": None,
                "團負責人部門": "郵輪部",
            },
        ]
    )
    others = pd.DataFrame(
        [
            {
                "來源單據號": "O1",
                "統一日期": "2026-05-01",
                "交易時間": "2026-05-01 10:00:00",
                "銷售點": "銅鑼灣分社",
                "銷售員": "Alice",
                "收款原幣金額": "25",
                "收款類型": "正常收款",
                "收款方式": "現金",
                "數量": "1",
                "行程天數": "0",
                "來源報表標籤": "門券all",
                "團名稱": "景點門票",
            }
        ]
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        _, _, facts = pipeline.build_dashboard_data(
            tour,
            others,
            {"1A": "銅鑼灣分社", "2B": "專職銷售組"},
            ["銅鑼灣分社"],
            ["郵輪部"],
            ["Alice", "Bob"],
            make_workbook=False,
            include_branch_salesperson_sheet=True,
            return_facts=True,
        )

    assert facts["分社每天旅行團交易人數"]["交易人數"].dtype.kind in {"i", "u", "f"}
    assert facts["分社每天票務交易數量"]["交易數量"].dtype.kind in {"i", "u", "f"}
    assert set(facts["分社經營統計"]["月份"].dropna()) == {"2026-05"}


def test_date_fallback_and_numeric_fill_helpers_have_explicit_contract():
    import pipeline

    source = pd.Series(["2026-05-01", "invalid"], dtype="object")
    fallback = pd.Series(["2026-05-01", "2026-05-02"], dtype="object")
    result = pipeline._format_date_with_fallback(source, fallback)
    assert result.tolist() == ["2026-05-01", "2026-05-02"]
    assert str(result.dtype) == "string"

    frame = pd.DataFrame({"交易人數": [None, "2"], "文字欄": [None, "保留"]})
    result = pipeline._fill_numeric_columns(frame, ["交易人數"])
    assert result["交易人數"].tolist() == [0, 2]
    assert pd.isna(result.loc[0, "文字欄"])
