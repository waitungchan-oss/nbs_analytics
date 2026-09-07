import pandas as pd
import pytest


E_COMMERCE = "市場及電商部-電子商務組"


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
