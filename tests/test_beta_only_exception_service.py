import pickle
from pathlib import Path

import pandas as pd


def _frame(order_id, sales_point):
    return pd.DataFrame(
        [
            {
                "來源單據號": order_id,
                "收款單號": f"R-{order_id}",
                "銷售點": sales_point,
                "收款原幣金額": 100,
            }
        ]
    )


def test_build_beta_only_exception_frames_reopens_only_allowed_prefix(monkeypatch):
    from backend.services import beta_only_exception_service

    calls = []

    def fake_process(main, tour, others, branch_mapping, exclude_prefixes, sales_reps, **kwargs):
        calls.append(tuple(exclude_prefixes))
        frame = _frame("1950506001", "市場及商務部-電子商務組")
        return pd.DataFrame(), frame, pd.DataFrame(), {}

    monkeypatch.setattr(beta_only_exception_service, "process_raw_files", fake_process)

    tour, others = beta_only_exception_service.build_beta_only_exception_frames(
        "main.xlsx",
        "tour.xlsx",
        ["other.xlsx"],
        {"19": "沙田分社"},
        ["1950506", "1950404"],
        [],
    )

    assert calls == [("1950404",)]
    assert tour.empty
    assert list(others["來源單據號"]) == ["1950506001"]


def test_build_beta_only_exception_frames_keeps_all_beta_sales_points(monkeypatch):
    from backend.services import beta_only_exception_service

    sales_points = (
        "市場及電商部-電子商務組",
        "營銷運營中心-同業銷售部",
        "市場及商務部-電子商務組",
        "網銷組 i6",
    )

    def fake_process(main, tour, others, branch_mapping, exclude_prefixes, sales_reps, **kwargs):
        frame = pd.concat(
            [_frame(f"195050600{i}", sales_point) for i, sales_point in enumerate(sales_points)],
            ignore_index=True,
        )
        return pd.DataFrame(), frame, pd.DataFrame(), {}

    monkeypatch.setattr(beta_only_exception_service, "process_raw_files", fake_process)

    _, others = beta_only_exception_service.build_beta_only_exception_frames(
        "main.xlsx",
        "tour.xlsx",
        ["other.xlsx"],
        {"19": "沙田分社"},
        ["1950506", "1950404"],
        [],
    )

    assert set(others["銷售點"]) == set(sales_points)


def test_beta_only_exception_cache_round_trips_and_deduplicates_by_receipt(tmp_path):
    from backend.services import beta_only_exception_service

    cache_path = Path(tmp_path) / "beta-only-exception.pkl"
    first = _frame("1950506001", "市場及商務部-電子商務組")
    second = first.assign(**{"收款原幣金額": 200})

    beta_only_exception_service.persist_beta_only_exception_frames(
        first, pd.DataFrame(), cache_path=cache_path
    )
    beta_only_exception_service.persist_beta_only_exception_frames(
        second, pd.DataFrame(), cache_path=cache_path
    )

    tour, others = beta_only_exception_service.load_beta_only_exception_frames(cache_path=cache_path)
    assert len(tour) == 1
    assert float(tour.iloc[0]["收款原幣金額"]) == 200
    assert others.empty


def test_build_beta_only_exception_frames_requires_allowed_prefix_and_sales_point(monkeypatch):
    from backend.services import beta_only_exception_service

    valid = _frame("1950506001", "市場及商務部-電子商務組")
    wrong_prefix = _frame("1950404001", "網銷組 i6")
    wrong_sales_point = _frame("1950506002", "銅鑼灣分社")
    rows = pd.concat([valid, wrong_prefix, wrong_sales_point], ignore_index=True)

    monkeypatch.setattr(
        beta_only_exception_service,
        "process_raw_files",
        lambda *args, **kwargs: (rows, rows, pd.DataFrame(), {}),
    )

    tour, others = beta_only_exception_service.build_beta_only_exception_frames(
        "main.xlsx", "tour.xlsx", [], {}, ["1950506"], []
    )

    assert list(tour["來源單據號"]) == ["1950506001"]
    assert list(others["來源單據號"]) == ["1950506001"]


def test_load_beta_only_exception_frames_filters_legacy_cache_rows(tmp_path):
    from backend.services import beta_only_exception_service
    from rules import BETA_ONLY_EXCEPTION_VERSION

    cache_path = Path(tmp_path) / "legacy-beta-only-exception.pkl"
    cached_rows = pd.concat(
        [
            _frame("1950506001", "市場及商務部-電子商務組"),
            _frame("1950404001", "網銷組 i6"),
            _frame("1950506002", "銅鑼灣分社"),
        ],
        ignore_index=True,
    )
    with cache_path.open("wb") as handle:
        pickle.dump(
            {"version": BETA_ONLY_EXCEPTION_VERSION, "tour": cached_rows, "others": cached_rows},
            handle,
        )

    tour, others = beta_only_exception_service.load_beta_only_exception_frames(
        cache_path=cache_path
    )

    assert list(tour["來源單據號"]) == ["1950506001"]
    assert list(others["來源單據號"]) == ["1950506001"]


def test_persist_beta_only_exception_frames_does_not_cache_non_exception_rows(tmp_path):
    from backend.services import beta_only_exception_service

    cache_path = Path(tmp_path) / "beta-only-exception.pkl"
    tour_rows = pd.concat(
        [
            _frame("1950506001", "市場及商務部-電子商務組"),
            _frame("1950404001", "網銷組 i6"),
        ],
        ignore_index=True,
    )
    others_rows = pd.concat(
        [
            _frame("1950506002", "營銷運營中心-同業銷售部"),
            _frame("1950506003", "銅鑼灣分社"),
        ],
        ignore_index=True,
    )

    result = beta_only_exception_service.persist_beta_only_exception_frames(
        tour_rows, others_rows, cache_path=cache_path
    )

    assert result["tourRows"] == 1
    assert result["othersRows"] == 1
    with cache_path.open("rb") as handle:
        payload = pickle.load(handle)
    assert list(payload["tour"]["來源單據號"]) == ["1950506001"]
    assert list(payload["others"]["來源單據號"]) == ["1950506002"]


def test_beta_export_can_include_beta_only_exception_without_changing_formal_inputs():
    import app_workflows

    formal_tour = _frame("T001", "銅鑼灣分社")
    formal_others = _frame("O001", "營銷運營中心-同業銷售部")
    exception_others = _frame("1950506001", "市場及商務部-電子商務組")

    payload = app_workflows._compute_beta_export_workbooks(
        formal_tour,
        formal_others,
        beta_exception_others=exception_others,
    )

    assert payload["export_variant"] == "beta_market_ecommerce_peer_sales_points_v3"
    assert payload["beta_exception_rows"] == {"tour": 0, "others": 1}
    assert payload["ex_no_writeoff_refund_transfer_beta"]
