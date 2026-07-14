import pandas as pd

from backend.services import dashboard_facts_service as service


def _tour_frame():
    return pd.DataFrame(
        [
            {
                "來源單據號": "A001",
                "收款時間": "2026-06-01",
                "統一日期": "2026-06-01",
                "收款原幣金額": 1000,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            },
            {
                "來源單據號": "A002",
                "收款時間": "2026-06-02",
                "統一日期": "2026-06-02",
                "收款原幣金額": 500,
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "銷售點": "銅鑼灣分社",
                "銷售員": "YTLAU 刘元太",
                "目的地大類": "旅行團",
                "團負責人部門": "",
                "行程天數": 3,
                "數量": 1,
            },
        ]
    )


def _others_frame():
    return pd.DataFrame(
        [
            {
                "來源單據號": "B001",
                "收款時間": "2026-06-03",
                "統一日期": "2026-06-03",
                "收款原幣金額": 300,
                "收款類型": "正常收款",
                "收款方式": "信用卡",
                "銷售點": "太古分社",
                "銷售員": "ELSA 谢玲玲",
                "目的地大類": "票務",
                "團負責人部門": "",
                "行程天數": 0,
                "數量": 1,
            }
        ]
    )


def _kwargs(tmp_path):
    return {
        "db_path": tmp_path / "facts.db",
        "branch_mapping": {"01": "銅鑼灣分社", "47": "太古分社"},
        "target_branches_s3": ["銅鑼灣分社", "太古分社"],
        "cruise_depts": [],
        "sales_rep_list": ["YTLAU 刘元太", "ELSA 谢玲玲"],
        "cache_dir": tmp_path / "cache",
    }


def test_build_dashboard_facts_returns_scope_and_summary_frames(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service,
        "load_all_data_from_db",
        lambda *, db_path: (_tour_frame(), _others_frame()),
    )

    payload = service.build_dashboard_facts(generation_token="1:test-sha", **_kwargs(tmp_path))

    assert payload["generationToken"] == "1:test-sha"
    assert payload["scopeAudit"]["scope_label"] == "不含掛賬核銷與TT退款轉團款"
    assert payload["scopeAudit"]["analysis_amount"] == 1300
    assert {"analysisTour", "analysisOthers", "branchFacts", "specialistFacts"} <= payload.keys()
    assert payload["factsCacheStatus"] == "rebuilt"


def test_generation_token_changes_cache_key_and_forces_rebuild(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        service,
        "load_all_data_from_db",
        lambda *, db_path: (calls.append(db_path) or (_tour_frame(), _others_frame())),
    )
    kwargs = _kwargs(tmp_path)

    service.build_dashboard_facts(generation_token="1:a", **kwargs)
    cached = service.build_dashboard_facts(generation_token="1:a", **kwargs)
    service.build_dashboard_facts(generation_token="2:b", **kwargs)

    assert cached["factsCacheStatus"] == "hit"
    assert len(calls) == 2


def test_cache_key_changes_when_business_rules_change(tmp_path):
    first = service.build_facts_cache_key("1:a", {"01": "銅鑼灣分社"}, [], [], [])
    second = service.build_facts_cache_key("1:a", {"01": "太古分社"}, [], [], [])

    assert first != second


def test_dashboard_read_model_reuses_cached_analytics(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service,
        "load_all_data_from_db",
        lambda *, db_path: (_tour_frame(), _others_frame()),
    )
    calls = []

    def build_analytics(branch_facts, specialist_facts, filters):
        calls.append(filters)
        return {
            "branchRanking": [{"totalRevenue": 1000.0}],
            "specialistRanking": [{"totalRevenue": 300.0}],
            "productDrilldown": {
                "all": [
                    {"product": "旅行團", "revenue": 1000.0},
                    {"product": "郵輪", "revenue": 0.0},
                    {"product": "票務", "revenue": 300.0},
                ]
            },
            "monthlyTrend": [],
            "reconciliation": {},
        }

    monkeypatch.setattr(service, "build_analytics_from_facts", build_analytics)
    kwargs = {**_kwargs(tmp_path), "generation_token": "1:abc"}

    first = service.build_dashboard_facts_read_model(**kwargs)
    second = service.build_dashboard_facts_read_model(**kwargs)

    assert first["readModelCacheStatus"] == "rebuilt"
    assert second["readModelCacheStatus"] == "hit"
    assert len(calls) == 1


def test_dashboard_read_model_rebuilds_for_new_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service,
        "load_all_data_from_db",
        lambda *, db_path: (_tour_frame(), _others_frame()),
    )
    calls = []

    def build_analytics(branch_facts, specialist_facts, filters):
        calls.append(filters)
        return {
            "branchRanking": [],
            "specialistRanking": [],
            "productDrilldown": {},
            "monthlyTrend": [],
            "reconciliation": {},
        }

    monkeypatch.setattr(service, "build_analytics_from_facts", build_analytics)
    kwargs = _kwargs(tmp_path)

    first = service.build_dashboard_facts_read_model(generation_token="1:abc", **kwargs)
    second = service.build_dashboard_facts_read_model(generation_token="2:def", **kwargs)

    assert first["readModelCacheStatus"] == second["readModelCacheStatus"] == "rebuilt"
    assert second["generationToken"] == "2:def"
    assert len(calls) == 2


def test_dashboard_read_model_repairs_corrupted_json(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service,
        "load_all_data_from_db",
        lambda *, db_path: (_tour_frame(), _others_frame()),
    )
    calls = []

    def build_analytics(branch_facts, specialist_facts, filters):
        calls.append(filters)
        return {
            "branchRanking": [],
            "specialistRanking": [],
            "productDrilldown": {},
            "monthlyTrend": [],
            "reconciliation": {},
        }

    monkeypatch.setattr(service, "build_analytics_from_facts", build_analytics)
    cache_dir = tmp_path / "cache"
    kwargs = {**_kwargs(tmp_path), "cache_dir": cache_dir, "generation_token": "1:abc"}
    service.build_dashboard_facts_read_model(**kwargs)
    cache_file = next(cache_dir.glob("dashboard_read_model_*.json"))
    cache_file.write_text("{broken", encoding="utf-8")

    rebuilt = service.build_dashboard_facts_read_model(**kwargs)

    assert rebuilt["readModelCacheStatus"] == "rebuilt"
    assert len(calls) == 2
