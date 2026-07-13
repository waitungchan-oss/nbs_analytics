import pandas as pd

from backend.services import dashboard_facts_service as service


def _facts_payload():
    branch = pd.DataFrame(
        [
            {"文本": "銅鑼灣分社", "日期": "2026-05-01", "旅行團": 100.0, "郵輪": 20.0, "票務": 30.0},
            {"文本": "太古分社", "日期": "2026-05-02", "旅行團": 50.0, "郵輪": 0.0, "票務": 5.0},
        ]
    )
    specialist = pd.DataFrame(
        [{"文本": "專職銷售組", "日期": "2026-05-03", "旅行團": 40.0, "郵輪": 0.0, "票務": 10.0}]
    )
    return {
        "serviceVersion": "dashboard-facts-v1",
        "cacheKey": "facts-key",
        "generationToken": "1:test",
        "dbPath": "/tmp/facts.db",
        "scopeAudit": {
            "scope_label": "不含掛賬核銷與TT退款轉團款",
            "raw_rows": 4,
            "analysis_rows": 3,
            "excluded_rows": 1,
        },
        "rawTour": pd.DataFrame([{"來源單據號": "A001"}]),
        "rawOthers": pd.DataFrame([{"來源單據號": "B001"}]),
        "analysisTour": pd.DataFrame([{"來源單據號": "A001"}]),
        "analysisOthers": pd.DataFrame([{"來源單據號": "B001"}]),
        "branchFacts": branch,
        "specialistFacts": specialist,
        "factsCacheStatus": "hit",
        "factsCachePath": "/tmp/facts.pkl",
    }


def test_read_model_contains_summary_without_raw_frames(monkeypatch, tmp_path):
    seen = {}

    def fake_build(**kwargs):
        seen.update(kwargs)
        return _facts_payload()

    monkeypatch.setattr(service, "build_dashboard_facts", fake_build)

    result = service.build_dashboard_facts_read_model(
        db_path=tmp_path / "facts.db",
        generation_token="1:test",
        branch_mapping={},
        target_branches_s3=[],
        cruise_depts=[],
        sales_rep_list=[],
        cache_dir=tmp_path / "cache",
    )

    assert result["status"] == "ready"
    assert result["generationToken"] == "1:test"
    assert result["factsCacheStatus"] == "hit"
    assert result["kpiTotals"]["combinedRevenue"] == 255.0
    assert result["monthlyTotals"][0]["combinedRevenue"] == 255.0
    assert result["productTotals"] == [
        {"product": "旅行團", "revenue": 190.0, "sharePct": 74.51},
        {"product": "郵輪", "revenue": 20.0, "sharePct": 7.84},
        {"product": "票務", "revenue": 45.0, "sharePct": 17.65},
    ]
    assert result["reconciliation"]["status"] == "matched"
    assert "rawTour" not in result
    assert seen["generation_token"] == "1:test"


def test_read_model_keeps_formal_scope_label(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "build_dashboard_facts", lambda **kwargs: _facts_payload())

    result = service.build_dashboard_facts_read_model(
        db_path=tmp_path / "facts.db",
        generation_token="1:test",
        branch_mapping={},
        target_branches_s3=[],
        cruise_depts=[],
        sales_rep_list=[],
        cache_dir=tmp_path / "cache",
    )

    assert result["revenueScope"] == "不含掛賬核銷與TT退款轉團款"
