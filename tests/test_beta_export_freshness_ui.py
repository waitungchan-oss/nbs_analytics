from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

BETA_CATEGORIES = {
    "002785 朱淑宜": "客戶服務部",
    "004499 胡闹香": "客戶服務部",
    "006105 刘冠寰": "客戶服務部",
    "007939 陈秀珠": "客戶服務部",
    "009282 石颖": "客戶服務部",
    "NL9 麦浩贤": "客戶服務部",
    "011185 洪淑芬": "商務同業組",
    "debby 卢淑贞": "商務同業組",
    "ctsit 杨慧如": "商務同業組",
    "004014 杨慧如": "商務同業組",
}


def _complete_scope():
    return {
        "tour": pd.DataFrame([{"收款單號": "R-1", "銷售員": "002785 朱淑宜"}]),
        "others": pd.DataFrame([{"收款單號": "R-2", "銷售員": "011185 洪淑芬"}]),
        "coverage": {
            "version": "beta-seller-scope-v3",
            "complete": True,
            "betaExportReady": True,
            "status": "complete",
            "requiredDateIntervals": [{"start": "2025-01-01", "end": "2026-10-12"}],
            "missingDateIntervals": [],
            "sourceRoles": ["main", "tour", "transport", "ticket", "package", "hotel"],
            "requiredSourceRoles": ["main", "tour", "transport", "ticket", "package", "hotel"],
            "missingSourceRoles": [],
            "sourceRoleMappingVerified": True,
            "dataGeneration": 8,
            "sourceFingerprint": "a" * 64,
            "sourceFingerprints": ["a" * 64],
            "rowsFingerprintMatched": True,
            "verification": {
                "preflightStatus": "matched",
                "driftStatus": "no_drift",
                "entityMatchStatus": "matched",
                "sourceRolesStatus": "complete",
                "sourceRoleMappingStatus": "verified",
                "liveDbUnchanged": True,
            },
        },
    }


def _fake_builder(calls, *, fail_seller_scope=False):
    def build(tour, others, *args, **kwargs):
        calls.append((tour, others, kwargs))
        if fail_seller_scope and kwargs.get("beta_salesperson_scope"):
            raise RuntimeError("seller-scope workbook rebuild failed")
        label = "seller" if kwargs.get("beta_salesperson_scope") else "legacy"
        return io.BytesIO(f"{label}-{len(calls)}".encode()), None, None

    return build


def test_partial_coverage_blocks_beta_download_without_legacy_fallback(monkeypatch):
    import app_workflows

    calls = []
    monkeypatch.setattr(app_workflows, "build_dashboard_data_excluding_receipt_types", _fake_builder(calls))
    scope = _complete_scope()
    scope["coverage"].update({"complete": False, "status": "partial"})

    payload = app_workflows._compute_beta_export_workbooks(
        pd.DataFrame(), pd.DataFrame(), beta_seller_scope=scope, current_data_generation=8
    )

    assert "ex_no_writeoff_refund_transfer_beta" not in payload
    assert payload["export_variant"] == "beta_market_ecommerce_peer_salesperson_v1"
    assert payload["status"] == "blocked"
    assert payload["seller_scope_ready"] is False
    assert payload["warning"]
    assert calls == []


def test_dashboard_cache_load_never_reads_legacy_beta_exception_cache(monkeypatch, tmp_path):
    import app_workflows

    raw_tour = pd.DataFrame([{"來源單據號": "R-1", "統一日期": "2026-10-01"}])
    empty = pd.DataFrame()
    facts = {
        "rawTour": raw_tour,
        "rawOthers": empty,
        "analysisTour": raw_tour,
        "analysisOthers": empty,
        "scopeAudit": {},
        "branchFacts": empty,
        "specialistFacts": empty,
        "serviceVersion": "test-v1",
        "cacheKey": "facts-key",
        "factsCacheStatus": "hit",
    }
    session_state = {}
    monkeypatch.setattr(app_workflows, "st", type("StreamlitStub", (), {"session_state": session_state})())
    monkeypatch.setattr(
        app_workflows,
        "load_beta_only_exception_frames",
        lambda: (_ for _ in ()).throw(AssertionError("legacy Beta cache must not be loaded")),
        raising=False,
    )
    monkeypatch.setattr(
        app_workflows,
        "load_cache_generation",
        lambda **kwargs: {"cacheToken": "1:test", "generation": 1, "signatureMatched": True},
    )
    monkeypatch.setattr(app_workflows, "_current_rules", lambda: ({}, [], [], [], []))
    monkeypatch.setattr(app_workflows, "build_dashboard_facts", lambda **kwargs: facts)
    monkeypatch.setattr(
        app_workflows,
        "load_beta_seller_scope_frames",
        lambda **kwargs: (empty, empty, {"complete": False, "status": "unverified"}),
    )
    monkeypatch.setattr(app_workflows, "_ai_cache_key", lambda *args: "ai-key")
    monkeypatch.setattr(app_workflows, "_legacy_ai_cache_key", lambda *args: "legacy-ai-key")
    monkeypatch.setattr(
        app_workflows,
        "_load_ai_runtime_cache",
        lambda *args, **kwargs: ({"ptrk": None, "err": None, "bt": {}, "bt_err": None, "bt_macro": {}, "bt_macro_err": None}, "hit", "ai-key"),
    )
    monkeypatch.setattr(app_workflows, "_export_cache_key", lambda *args: "export-key")
    monkeypatch.setattr(app_workflows, "_export_cache_path", lambda key: tmp_path / "export.pkl")

    app_workflows._load_and_compute_cache(include_ai=False)

    assert "beta_exception_tour" not in session_state["PROCESSED_DATA_CACHE"]
    assert "beta_exception_others" not in session_state["PROCESSED_DATA_CACHE"]


def test_stale_source_generation_blocks_beta_download(monkeypatch):
    import app_workflows

    calls = []
    monkeypatch.setattr(app_workflows, "build_dashboard_data_excluding_receipt_types", _fake_builder(calls))

    payload = app_workflows._compute_beta_export_workbooks(
        pd.DataFrame(), pd.DataFrame(), beta_seller_scope=_complete_scope(), current_data_generation=9
    )

    assert payload["export_variant"] == "beta_market_ecommerce_peer_salesperson_v1"
    assert payload["status"] == "blocked"
    assert payload["seller_scope_ready"] is False
    assert "generation" in payload["warning"].lower()
    assert calls == []


def test_stale_source_fingerprint_blocks_beta_download(monkeypatch):
    import app_workflows

    calls = []
    monkeypatch.setattr(app_workflows, "build_dashboard_data_excluding_receipt_types", _fake_builder(calls))
    scope = _complete_scope()
    scope["coverage"]["sourceFingerprints"] = ["b" * 64]

    payload = app_workflows._compute_beta_export_workbooks(
        pd.DataFrame(), pd.DataFrame(), beta_seller_scope=scope, current_data_generation=8
    )

    assert payload["export_variant"] == "beta_market_ecommerce_peer_salesperson_v1"
    assert payload["status"] == "blocked"
    assert "fingerprint" in payload["warning"].lower()
    assert calls == []


def test_complete_current_scope_uses_seller_only_variant_and_fingerprint_identity(monkeypatch):
    import app_workflows

    calls = []
    monkeypatch.setattr(app_workflows.pipeline_module, "build_dashboard_data", _fake_builder(calls))

    payload = app_workflows._compute_beta_export_workbooks(
        pd.DataFrame(),
        pd.DataFrame(),
        beta_seller_scope=_complete_scope(),
        current_data_generation=8,
    )

    assert payload["export_variant"] == "beta_market_ecommerce_peer_salesperson_v1"
    assert payload["seller_scope_ready"] is True
    assert payload["warning"] is None
    assert payload["source_fingerprint"] == "a" * 64
    assert "beta-seller-scope-v3" in payload["export_variant_key_material"]
    assert len(calls) == 3
    assert all(call[2].get("beta_salesperson_scope") is True for call in calls)
    assert all("beta_sales_point" not in call[2] for call in calls)


def test_seller_scope_rebuild_error_blocks_beta_download(monkeypatch):
    import app_workflows

    calls = []
    monkeypatch.setattr(
        app_workflows.pipeline_module,
        "build_dashboard_data",
        _fake_builder(calls, fail_seller_scope=True),
    )

    payload = app_workflows._compute_beta_export_workbooks(
        pd.DataFrame(),
        pd.DataFrame(),
        beta_seller_scope=_complete_scope(),
        current_data_generation=8,
    )

    assert payload["export_variant"] == "beta_market_ecommerce_peer_salesperson_v1"
    assert payload["status"] == "blocked"
    assert "ex_no_writeoff_refund_transfer_beta" not in payload
    assert payload["seller_scope_ready"] is False
    assert "rebuild" in payload["warning"].lower()


def test_beta_export_ui_fails_closed_and_preserves_reference_schema():
    source = Path("app_pages.py").read_text(encoding="utf-8")

    assert "下載 Beta 市場電商及同業比較匯出（正式口徑）" in source
    assert "19 張工作表" in source
    assert "財務主表、旅行團、交通、門券、套票、酒店六類來源未完成覆蓋驗證前" in source
    assert "Beta 下載會暫停" in source


def test_streamlit_secondary_upload_role_reaches_beta_source_classification():
    import app_pages
    from backend.services import beta_seller_scope_service

    class UploadedFile:
        name = "交通副表.xlsx"

        @staticmethod
        def getvalue():
            return b"transport-workbook"

    upload = app_pages._streamlit_named_bytes(
        UploadedFile(), beta_source_role="transport"
    )
    _, records = beta_seller_scope_service._source_fingerprint(
        ("main.xlsx", b"main"),
        ("旅行團副表.xlsx", b"tour"),
        [upload],
    )

    transport_record = next(record for record in records if record["role"] == "other-0")
    assert transport_record["sourceCategory"] == "transport"


def test_seller_scope_export_matches_v5_headers_and_official_receipt_scope():
    import app_workflows

    rows = []
    for index, salesperson in enumerate(BETA_CATEGORIES, start=1):
        rows.append(
            {
                "來源單據號": f"UNMAPPED-{index}",
                "收款單號": f"R-{index}",
                "收款時間": "2026-05-01 10:00:00",
                "統一日期": "2026-05-01",
                "交易時間": "2026-05-01 10:00:00",
                "銷售點": f"任意門市-{index}",
                "銷售員": salesperson,
                "收款原幣金額": 100,
                "收款本幣金額": 100,
                "收款類型": "正常收款",
                "收款方式": "現金",
                "行程天數": 3,
                "數量": 1,
                "團名稱": "測試行程",
                "來源報表標籤": "旅行團",
            }
        )
    rows[0]["收款方式"] = "TT 退款轉團款"
    rows[1]["收款類型"] = "掛賬核銷"
    rows.append(
        {
            "來源單據號": "UNMAPPED-OUTSIDER",
            "收款單號": "R-OUT",
            "收款時間": "2026-05-01 10:00:00",
            "統一日期": "2026-05-01",
            "交易時間": "2026-05-01 10:00:00",
            "銷售點": "市場及電商部-電子商務組",
            "銷售員": "不在名單",
            "收款原幣金額": 999,
            "收款本幣金額": 999,
            "收款類型": "正常收款",
            "收款方式": "現金",
            "行程天數": 3,
            "數量": 1,
            "團名稱": "名單外測試",
            "來源報表標籤": "旅行團",
        }
    )
    frame = pd.DataFrame(rows)
    coverage = _complete_scope()["coverage"]
    payload = app_workflows._compute_beta_export_workbooks(
        frame,
        pd.DataFrame(),
        beta_seller_scope={"tour": frame, "others": pd.DataFrame(), "coverage": coverage},
        current_data_generation=8,
    )
    workbook = pd.ExcelFile(io.BytesIO(payload["ex_no_writeoff_refund_transfer_beta"]))
    expected_headers = {'分社經營統計': ['文本', '單選', '日期', '月份', '旅行團', '郵輪', '票務'],
     '分社經營統計_含銷售員': ['文本', '單選', '銷售員', '日期', '月份', '旅行團', '郵輪', '票務', '旅行團交易人數', '票務交易數量'],
     '市場電商及同業_經營統計': ['文本', '種類', '日期', '月份', '旅行團', '郵輪', '票務'],
     '分社旅行團統計': ['文本', '天數', '日期', '月份', '交易人數'],
     '市場電商及同業_旅行團統計': ['銷售員', '所屬種類', '文本', '天數', '日期', '月份', '交易人數'],
     '分社票務總計': ['文本', '日期', '月份', '交易數量'],
     '市場電商及同業_票務總計': ['銷售員', '所屬種類', '文本', '日期', '月份', '交易數量'],
     '票務總計': ['文本', '日期', '月份', '交易數量'],
     '分社每天旅行團交易人數': ['文本', '日期', '月份', '交易人數', '郵輪交易人數'],
     '市場電商及同業_每天旅行團交易人數': ['銷售員', '所屬種類', '日期', '月份', '旅行團交易人數', '郵輪交易人數'],
     '分社每天票務交易數量': ['文本', '種類', '日期', '月份', '交易數量'],
     '市場電商及同業_每天票務交易數量': ['銷售員', '所屬種類', '種類', '日期', '月份', '交易數量'],
     'NBS分社_旅行團金額統計': ['分社種類', '線路種類', '天數', '日期', '月份', '交易金額'],
     'NBS分社_票務金額統計': ['分社名稱', '票務種類', '天數', '日期', '月份', '交易金額'],
     '總表_多表匹配完成': ['收款單號',
                   '收款單狀態',
                   '來源單據號',
                   '銷售點',
                   '匯總單號',
                   '收款状态',
                   '原幣幣種',
                   '匯率',
                   '收款原幣金額',
                   '收款本幣金額',
                   '收款類型',
                   '收款方式',
                   '收款流水號',
                   '收款操作員',
                   '客戶名稱',
                   '銷售公司',
                   '銷售員',
                   '收款時間',
                   '收款月份分組',
                   '團代號',
                   '團名稱',
                   '行程天數',
                   '數量',
                   '幣種',
                   '應收',
                   '已收',
                   '交易時間',
                   '交易月份分組',
                   '團負責人',
                   '團負責人部門',
                   '目的地大類',
                   '一級目的地',
                   '二級目的地',
                   '目的地名稱',
                   '副表_銷售點',
                   '來源報表標籤'],
     '旅行團_匹配成功': ['收款單號',
                  '收款單狀態',
                  '來源單據號',
                  '銷售點',
                  '匯總單號',
                  '收款状态',
                  '原幣幣種',
                  '匯率',
                  '收款原幣金額',
                  '收款本幣金額',
                  '收款類型',
                  '收款方式',
                  '收款流水號',
                  '收款操作員',
                  '客戶名稱',
                  '銷售公司',
                  '銷售員',
                  '收款時間',
                  '收款月份分組',
                  '團代號',
                  '團名稱',
                  '行程天數',
                  '數量',
                  '幣種',
                  '應收',
                  '已收',
                  '交易時間',
                  '交易月份分組',
                  '團負責人',
                  '團負責人部門',
                  '目的地大類',
                  '一級目的地',
                  '二級目的地',
                  '目的地名稱',
                  '副表_銷售點',
                  '來源報表標籤'],
     '其它_未匹配_包含其它業務': ['收款單號',
                       '收款單狀態',
                       '來源單據號',
                       '銷售點',
                       '匯總單號',
                       '收款状态',
                       '原幣幣種',
                       '匯率',
                       '收款原幣金額',
                       '收款本幣金額',
                       '收款類型',
                       '收款方式',
                       '收款流水號',
                       '收款操作員',
                       '客戶名稱',
                       '銷售公司',
                       '銷售員',
                       '收款時間',
                       '收款月份分組',
                       '團代號',
                       '團名稱',
                       '行程天數',
                       '數量',
                       '幣種',
                       '應收',
                       '已收',
                       '交易時間',
                       '交易月份分組',
                       '團負責人',
                       '團負責人部門',
                       '目的地大類',
                       '一級目的地',
                       '二級目的地',
                       '目的地名稱',
                       '副表_銷售點',
                       '來源報表標籤'],
     '分社線路種類每天統計': ['分社', '線路種類', '日子', '月份', '交易人數', '交易金額'],
     '市場電商及同業_線路種類每天統計': ['銷售員', '所屬種類', '線路種類', '日子', '月份', '交易人數', '交易金額']}

    assert payload["seller_scope_ready"] is True
    assert workbook.sheet_names == list(expected_headers)
    for sheet_name, columns in expected_headers.items():
        exported = pd.read_excel(workbook, sheet_name=sheet_name)
        assert list(exported.columns) == columns, sheet_name
        if sheet_name.startswith("市場電商及同業_") and "銷售員" in exported.columns:
            assert set(exported["銷售員"].dropna()) <= set(BETA_CATEGORIES), sheet_name
        if sheet_name.startswith("市場電商及同業_") and "所屬種類" in exported.columns:
            assert set(exported["所屬種類"].dropna()) <= set(BETA_CATEGORIES.values()), sheet_name
        if sheet_name == "市場電商及同業_經營統計":
            assert set(exported["文本"].dropna()) <= set(BETA_CATEGORIES), sheet_name
            amount_columns = [column for column in ("旅行團", "郵輪", "票務") if column in exported]
            assert round(exported[amount_columns].apply(pd.to_numeric, errors="coerce").fillna(0).to_numpy().sum(), 2) == 800
