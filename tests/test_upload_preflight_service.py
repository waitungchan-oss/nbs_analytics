from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

import database


def test_upload_preflight_never_changes_module_global_db_target(tmp_path, monkeypatch):
    from backend.services import upload_preflight_service

    live_path = tmp_path / "live.db"
    default_path = tmp_path / "default.db"
    live_path.touch()
    monkeypatch.setattr(database, "DB_FILE", str(default_path))
    observed = {}

    def fake_process_raw_files(*args, **kwargs):
        observed["db_file_during_processing"] = database.DB_FILE
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"summary": pd.DataFrame()}

    def fake_gate(*, db_path=None):
        observed["gate_db_path"] = Path(db_path)
        return {
            "status": "matched",
            "formattedActualTotal": "HKD 12,057,968",
            "deltaAmount": 0.0,
            "driftChecks": [],
            "monthlyBaseline": {"allMatched": True},
        }

    monkeypatch.setattr(upload_preflight_service, "process_raw_files", fake_process_raw_files)
    monkeypatch.setattr(
        upload_preflight_service,
        "build_beta_only_exception_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy Beta exception frames must not be generated")
        ),
        raising=False,
    )
    monkeypatch.setattr(upload_preflight_service, "build_phase2c_stability_gate", fake_gate)
    monkeypatch.setattr(
        upload_preflight_service,
        "build_upload_drift_diagnosis",
        lambda *args, **kwargs: {"status": "no_drift"},
    )
    monkeypatch.setattr(
        database,
        "snapshot_sqlite_database",
        lambda source, destination: observed.update(
            snapshot_source=Path(source),
            snapshot_target=Path(destination),
        ),
    )
    monkeypatch.setattr(
        database,
        "load_all_data_from_db",
        lambda *, db_path=None: observed.setdefault("load_paths", []).append(Path(db_path))
        or (pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr(
        database,
        "upsert_to_db",
        lambda *args, db_path=None, **kwargs: observed.update(upsert_path=Path(db_path))
        or {
            "tour_data": {"filtered_excluded_rows": 0, "write_rows": 0},
            "others_data": {"filtered_excluded_rows": 0, "write_rows": 0},
        },
    )
    monkeypatch.setattr(upload_preflight_service, "_table_row_count", lambda *args, **kwargs: 0)

    upload_preflight_service.run_upload_preflight(
        BytesIO(b"dummy"),
        None,
        [],
        {},
        [],
        [],
        source_files=["main.xlsx"],
        live_db_path=live_path,
    )

    assert observed["db_file_during_processing"] == str(default_path)
    assert observed["snapshot_source"] == live_path
    assert observed["upsert_path"] == observed["gate_db_path"]
    assert observed["gate_db_path"] != live_path
    assert observed["load_paths"] == [live_path, observed["gate_db_path"]]
    assert database.DB_FILE == str(default_path)


def test_upload_preflight_rejects_missing_explicit_live_db_without_creating_it(tmp_path, monkeypatch):
    from backend.services import upload_preflight_service

    missing_live_path = tmp_path / "missing-live.db"
    monkeypatch.setattr(
        upload_preflight_service,
        "process_raw_files",
        lambda *args, **kwargs: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"summary": pd.DataFrame()}),
    )
    monkeypatch.setattr(
        upload_preflight_service,
        "build_phase2c_stability_gate",
        lambda *, db_path=None: {"status": "matched", "driftChecks": []},
    )
    monkeypatch.setattr(
        upload_preflight_service,
        "build_upload_drift_diagnosis",
        lambda *args, **kwargs: {"status": "no_drift"},
    )

    with pytest.raises(FileNotFoundError, match="explicit live database not found"):
        upload_preflight_service.run_upload_preflight(
            BytesIO(b"dummy"),
            None,
            [],
            {},
            [],
            [],
            source_files=["main.xlsx"],
            live_db_path=missing_live_path,
        )

    assert not missing_live_path.exists()


def test_upload_preflight_uses_temp_database_and_preserves_live_db(tmp_path, monkeypatch):
    from backend.services import upload_preflight_service

    live_path = tmp_path / "live.db"
    monkeypatch.setattr(database, "DB_FILE", str(live_path))

    live_conn = database.sqlite3.connect(live_path)
    try:
        pd.DataFrame(
            [
                {
                    "來源單據號": "OLD001",
                    "收款單號": "SK2605000001",
                    "收款類型": "旅費",
                    "收款方式": "現金",
                    "收款原幣金額": 1000.0,
                    "統一日期": "2026-05-01",
                }
            ]
        ).to_sql("tour_data", live_conn, if_exists="replace", index=False)
        live_conn.commit()
    finally:
        live_conn.close()

    main_file = BytesIO(b"dummy")
    main_file.name = "財務收款總數-0101-0625.xlsx"  # type: ignore[attr-defined]

    tour_df = pd.DataFrame(
        [
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2605000001",
                "收款類型": "旅費",
                "收款方式": "現金",
                "收款原幣金額": 1000.0,
                "統一日期": "2026-05-01",
            },
            {
                "來源單據號": "OLD001",
                "收款單號": "SK2606000002",
                "收款類型": "掛賬核銷",
                "收款方式": "現金",
                "收款原幣金額": 500.0,
                "統一日期": "2026-06-25",
            },
        ]
    )
    others_df = pd.DataFrame(
        [
            {
                "來源單據號": "O001",
                "收款單號": "O001",
                "收款類型": "正常收款",
                "收款方式": "現金",
                "收款原幣金額": 200.0,
                "收款時間": "2026-06-15 09:19:16",
            }
        ]
    )

    def fake_process_raw_files(*args, **kwargs):
        return tour_df.copy(), others_df.copy(), pd.DataFrame(), {"summary": pd.DataFrame()}

    monkeypatch.setattr(upload_preflight_service, "process_raw_files", fake_process_raw_files)
    monkeypatch.setattr(
        upload_preflight_service,
        "build_upload_drift_diagnosis",
        lambda *args, **kwargs: {
            "status": "no_drift",
            "baselineMonth": "2026-05",
            "expectedTotal": 12057968.0,
            "actualTotal": 12057968.0,
            "deltaAmount": 0.0,
            "summaryMessage": "核心口徑未漂移。",
            "rowLimit": 50,
            "sourceOrderDiffs": [],
            "receiptDiffs": [],
            "excludedReceiptDiffs": [],
            "topDrivers": [],
        },
    )
    monkeypatch.setattr(
        upload_preflight_service,
        "build_phase2c_stability_gate",
        lambda *, db_path=None: {
            "status": "matched",
            "formattedExpectedTotal": "HKD 12,057,968",
            "formattedActualTotal": "HKD 12,057,968",
            "deltaAmount": -0.08,
            "deltaPct": 0.0,
            "totalChecks": 2,
            "matchedChecks": 2,
            "driftCheckCount": 0,
            "driftChecks": [],
            "coreValidation": {"status": "matched", "summary": {"totalChecks": 2, "matchedChecks": 2, "driftChecks": 0}, "checks": []},
            "freshnessStatus": "stable",
            "freshnessUpdateCount": 0,
            "freshnessUpdates": [],
            "freshnessUpdate": {"status": "stable", "summary": {"totalChecks": 3, "stableChecks": 3, "updatedChecks": 0}, "checks": []},
            "stabilityBaseline": {"formattedActualTotal": "HKD 12,057,968"},
        },
    )

    report = upload_preflight_service.run_upload_preflight(
        main_file,
        None,
        [],
        branch_mapping={"OLD": "Legacy Branch"},
        exclude_prefixes=[],
        sales_reps=[],
        source_files=["財務收款總數-0101-0625.xlsx"],
        live_db_path=live_path,
    )

    assert report["status"] == "matched"
    assert report["filteredExcludedRows"] == 1
    assert report["writeRows"] == 2
    assert report["sourceFiles"] == ["財務收款總數-0101-0625.xlsx"]
    assert report["liveDbUnchanged"] is True
    assert report["driftDiagnosis"]["status"] == "no_drift"
    assert report["prepared"]["tour"].shape[0] == 2
    assert report["prepared"]["others"].shape[0] == 1
    assert report["batchSummary"][0]["包含 2026-06-15"] is False
    assert report["batchSummary"][1]["包含 2026-06-15"] is True
    assert report["batchSummary"][1]["最早收款時間"] == "2026-06-15 09:19:16"
    timing_labels = [item["階段"] for item in report["stageTimings"]]
    assert "建立 Preflight 臨時 DB" in timing_labels
    assert "清洗與 Entity Resolution" in timing_labels
    assert "臨時 SQLite upsert" in timing_labels
    assert "Preflight stability gate" in timing_labels
    assert "Drift diagnosis" in timing_labels
    assert "Preflight total" in timing_labels


def _coverage_row(order_id, receipt_date, salesperson="002785 朱淑宜"):
    return {
        "來源單據號": order_id,
        "收款單號": f"R-{order_id}",
        "收款時間": f"{receipt_date} 10:00:00",
        "統一日期": receipt_date,
        "銷售點": "任意銷售點",
        "銷售員": salesperson,
        "收款原幣金額": 100,
        "交易時間": f"{receipt_date} 10:00:00",
        "行程天數": 3,
        "數量": 1,
        "團名稱": "測試團",
        "來源報表標籤": "旅行團",
    }


@pytest.mark.parametrize(
    "source_dates,target_dates,unmatched_rows,gate_status,expected_complete,expected_missing,source_complete",
    [
        (["2026-01-01", "2026-01-03"], ["2026-01-01", "2026-01-03"], 0, "matched", True, [], True),
        (
            ["2026-05-01"],
            ["2026-01-01", "2026-01-03", "2026-05-01"],
            0,
            "matched",
            False,
            [{"start": "2026-01-01", "end": "2026-01-01"}, {"start": "2026-01-03", "end": "2026-01-03"}],
            True,
        ),
        (["2026-01-01", "2026-01-03"], ["2026-01-01", "2026-01-03"], 1, "matched", True, [], True),
        (["2026-01-01", "2026-01-03"], ["2026-01-01", "2026-01-03"], 0, "drift", False, [], True),
        (["2026-01-01", "2026-01-03"], ["2026-01-01", "2026-01-03"], 0, "matched", False,
         [{"start": "2026-01-01", "end": "2026-01-01"}, {"start": "2026-01-03", "end": "2026-01-03"}], False),
    ],
)
def test_preflight_prepares_beta_seller_scope_with_date_gaps_and_match_status(
    tmp_path,
    monkeypatch,
    source_dates,
    target_dates,
    unmatched_rows,
    gate_status,
    expected_complete,
    expected_missing,
    source_complete,
):
    from backend.services import upload_preflight_service

    live_path = tmp_path / "live.db"
    live_path.touch()
    target_rows = pd.DataFrame(
        [_coverage_row(f"TARGET-{index}", date) for index, date in enumerate(target_dates)]
    )
    seller_rows = pd.DataFrame(
        [_coverage_row(f"SOURCE-{index}", date) for index, date in enumerate(source_dates)]
    )
    entity_summary = pd.DataFrame(
        [
            {"指標": "主表有效來源單據號數", "數值": len(source_dates)},
            {"指標": "副表有效交易號碼數", "數值": len(source_dates) - unmatched_rows + 1},
            {"指標": "成功匹配唯一單號數", "數值": len(source_dates) - unmatched_rows},
            {"指標": "成功匹配主表行數", "數值": len(source_dates) - unmatched_rows},
            {"指標": "主表未匹配行數", "數值": unmatched_rows},
            {"指標": "副表未落主表單號數", "數值": 1},
        ]
    )
    calls = []
    database_reads = [
        (target_rows.iloc[:2].copy(), pd.DataFrame()),
        (target_rows.copy(), pd.DataFrame()),
    ]

    def fake_process_raw_files(*args, **kwargs):
        calls.append(kwargs)
        rows = seller_rows if kwargs.get("beta_capture_mode") else target_rows.iloc[:1]
        return rows.copy(), pd.DataFrame(), pd.DataFrame(), {"summary": entity_summary}

    monkeypatch.setattr(upload_preflight_service, "process_raw_files", fake_process_raw_files)
    monkeypatch.setattr(database, "snapshot_sqlite_database", lambda *args, **kwargs: None)
    monkeypatch.setattr(database, "load_all_data_from_db", lambda **kwargs: database_reads.pop(0))
    monkeypatch.setattr(upload_preflight_service, "_table_row_count", lambda *args, **kwargs: 7)
    upsert_targets = []

    def fake_upsert(*args, db_path=None, **kwargs):
        upsert_targets.append(Path(db_path))
        return {
            "tour_data": {"filtered_excluded_rows": 0, "write_rows": 1},
            "others_data": {"filtered_excluded_rows": 0, "write_rows": 0},
        }

    monkeypatch.setattr(database, "upsert_to_db", fake_upsert)
    monkeypatch.setattr(
        upload_preflight_service,
        "build_phase2c_stability_gate",
        lambda **kwargs: {"status": gate_status, "driftChecks": []},
    )
    monkeypatch.setattr(
        upload_preflight_service,
        "build_upload_drift_diagnosis",
        lambda *args, **kwargs: {"status": "no_drift"},
    )

    other_sources = []
    if source_complete:
        for role, name in (
            ("transport", "交通副表.xlsx"),
            ("ticket", "門券副表.xlsx"),
            ("package", "套票副表.xlsx"),
            ("hotel", "酒店副表.xlsx"),
        ):
            source = BytesIO(name.encode("utf-8"))
            source.name = name
            source.beta_source_role = role
            other_sources.append(source)
    source_names = ["main.xlsx", "旅行團副表.xlsx", *(source.name for source in other_sources)]
    report = upload_preflight_service.run_upload_preflight(
        BytesIO(b"main-source"),
        "旅行團副表.xlsx",
        other_sources,
        {},
        ["1950506"],
        [],
        source_files=source_names,
        live_db_path=live_path,
        registry_loader=lambda **kwargs: {"revision": "test", "rules": []},
    )

    prepared = report["prepared"]["beta_seller_scope"]
    coverage = prepared["coverage"]
    assert isinstance(prepared["tour"], pd.DataFrame)
    assert len(coverage["sourceFingerprint"]) == 64
    assert coverage["complete"] is expected_complete
    assert coverage["status"] == ("complete" if expected_complete else "partial")
    assert coverage["betaExportReady"] is expected_complete
    assert coverage["dateIntervals"] == (
        [{"start": date, "end": date} for date in source_dates] if source_complete else []
    )
    assert coverage["missingDateIntervals"] == expected_missing
    assert coverage["missingSourceRoles"] == ([] if source_complete else ["hotel", "package", "ticket", "transport"])
    assert coverage["verification"]["sourceRolesStatus"] == ("complete" if source_complete else "incomplete")
    assert coverage["verification"]["entityMatchStatus"] == (
        "matched" if source_complete else "unmatched_or_unverified"
    )
    assert any(call.get("beta_capture_mode") is True for call in calls) is source_complete
    assert report["liveDbUnchanged"] is True
    assert upsert_targets and all(path != live_path for path in upsert_targets)


@pytest.mark.parametrize("broken_metric,broken_value", [
    ("成功匹配主表行數", 3),
    ("副表未落主表單號數", 0),
    ("主表未匹配行數", -1),
    ("成功匹配唯一單號數", None),
])
def test_beta_entity_audit_rejects_inconsistent_or_missing_counts(broken_metric, broken_value):
    from backend.services.beta_seller_scope_service import _entity_audit_reconciled

    metrics = {
        "主表有效來源單據號數": 2,
        "副表有效交易號碼數": 2,
        "成功匹配唯一單號數": 1,
        "成功匹配主表行數": 1,
        "主表未匹配行數": 1,
        "副表未落主表單號數": 1,
    }
    metrics[broken_metric] = broken_value
    coverage = {
        "audit": {"inputRows": 2},
        "entityAuditSummary": [{"指標": name, "數值": count} for name, count in metrics.items()],
    }
    assert _entity_audit_reconciled(coverage) is False
