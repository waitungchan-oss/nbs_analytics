import sqlite3

import pandas as pd

import database
from backend.services.receipt_exclusion_models import ReceiptExclusionIdentity, ReceiptExclusionRule
from backend.services.upload_preflight_service import run_upload_preflight
from pipeline import read_excel_source


def _rule():
    return ReceiptExclusionRule(
        id=7,
        identity=ReceiptExclusionIdentity(
            receipt_no="SK2606005393",
            source_order_no="31NZY6629115617",
            exclusion_kind="payment_method:TT 退款轉團款",
        ),
        status="active",
    )


def _main_frame(source_order="31NZY6629115617"):
    return pd.DataFrame([{
        "來源單據號": source_order,
        "收款單號": "SK2606005393",
        "收款方式": "TT 退款轉團款",
        "收款類型": "旅費",
        "收款原幣金額": 1630.0,
    }])


def test_read_excel_source_accepts_named_dataframe_tuple():
    frame, name = read_excel_source(("main.xlsx", pd.DataFrame([{"A": 1}])))

    assert name == "main.xlsx"
    assert frame.to_dict(orient="records") == [{"A": 1}]


def test_preflight_blocks_identity_collision_before_process_raw_files(tmp_path, monkeypatch):
    from backend.services import upload_preflight_service

    live_path = tmp_path / "live.db"
    sqlite3.connect(live_path).close()
    monkeypatch.setattr(
        upload_preflight_service,
        "process_raw_files",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not process collision")),
    )

    report = run_upload_preflight(
        ("main.xlsx", _main_frame(source_order="DIFFERENT")),
        None,
        [],
        {},
        [],
        [],
        source_files=["main.xlsx"],
        live_db_path=live_path,
        registry_loader=lambda **kwargs: {"revision": "r1", "rules": (_rule(),)},
    )

    assert report["status"] == "receipt_exclusion_collision"
    assert report["prepared"] == {}
    assert report["receiptExclusion"]["collisions"][0]["reason"] == "source_order_mismatch"


def test_preflight_passes_filtered_main_frame_to_existing_pipeline(tmp_path, monkeypatch):
    from backend.services import upload_preflight_service

    live_path = tmp_path / "live.db"
    sqlite3.connect(live_path).close()
    observed = {}
    def fake_process_raw_files(main, *args, **kwargs):
        observed["main"] = main
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}

    monkeypatch.setattr(upload_preflight_service, "process_raw_files", fake_process_raw_files)
    monkeypatch.setattr(database, "snapshot_sqlite_database", lambda source, destination: sqlite3.connect(destination).close())
    monkeypatch.setattr(database, "load_all_data_from_db", lambda **kwargs: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(database, "upsert_to_db", lambda *args, **kwargs: {"tour_data": {}, "others_data": {}})
    monkeypatch.setattr(upload_preflight_service, "_table_row_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(upload_preflight_service, "build_phase2c_stability_gate", lambda **kwargs: {"status": "matched", "driftChecks": []})
    monkeypatch.setattr(upload_preflight_service, "build_upload_drift_diagnosis", lambda *args, **kwargs: {"status": "no_drift", "topDrivers": []})

    report = run_upload_preflight(
        ("main.xlsx", _main_frame()), None, [], {}, [], [],
        source_files=["main.xlsx"], live_db_path=live_path,
        registry_loader=lambda **kwargs: {"revision": "r1", "rules": (_rule(),)},
    )

    assert observed["main"][0] == "main.xlsx"
    assert observed["main"][1].empty
    assert report["receiptExclusion"]["matchedRules"][0]["receiptNo"] == "SK2606005393"
