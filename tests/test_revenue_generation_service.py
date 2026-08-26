import json
import sqlite3

import pytest


def _make_scope_fixture(tmp_path):
    db_path = tmp_path / "scope.db"
    with sqlite3.connect(db_path) as connection:
        for table in ("tour_data", "others_data"):
            connection.execute(
                f"CREATE TABLE {table} ("
                "來源單據號 TEXT, 收款原幣金額 REAL, 收款類型 TEXT, "
                "收款方式 TEXT, 收款時間 TEXT)"
            )
        connection.execute(
            "INSERT INTO tour_data VALUES (?, ?, ?, ?, ?)",
            ("S-1", 100.0, "旅費", "現金", "2026-08-20"),
        )
        connection.execute(
            "INSERT INTO others_data VALUES (?, ?, ?, ?, ?)",
            ("S-2", 50.0, "其它", "現金", "2026-08-21"),
        )
        connection.commit()
    return db_path


def _insert_gmv_only_row(db_path):
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS gmv_refund_current (id TEXT)")
        connection.execute("INSERT INTO gmv_refund_current VALUES ('GMV-ONLY')")
        connection.commit()


def test_core_signature_ignores_gmv_only_write(tmp_path):
    from backend.services.revenue_generation_service import build_core_revenue_signature

    db_path = _make_scope_fixture(tmp_path)
    before = build_core_revenue_signature(db_path)
    _insert_gmv_only_row(db_path)
    after = build_core_revenue_signature(db_path)

    assert after.token == before.token
    assert after.raw_tour_sha256 == before.raw_tour_sha256
    assert after.raw_others_sha256 == before.raw_others_sha256


def test_core_signature_changes_when_revenue_row_changes(tmp_path):
    from backend.services.revenue_generation_service import build_core_revenue_signature

    db_path = _make_scope_fixture(tmp_path)
    before = build_core_revenue_signature(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE tour_data SET 收款原幣金額 = ? WHERE 來源單據號 = ?",
            (101.0, "S-1"),
        )
        connection.commit()
    after = build_core_revenue_signature(db_path)

    assert after.token != before.token
    assert after.formal_tour_sha256 != before.formal_tour_sha256


def test_core_signature_has_formal_scope_and_deterministic_contract(tmp_path):
    from backend.services.revenue_generation_service import build_core_revenue_signature

    db_path = _make_scope_fixture(tmp_path)
    signature = build_core_revenue_signature(db_path)

    assert signature.schema_version == "nbs-core-revenue-signature-v1"
    assert signature.scope_label == "不含掛賬核銷與TT退款轉團款"
    assert signature.scope_contract_version == "revenue-scope-v1"
    assert signature.source_tables == ("tour_data", "others_data")
    assert signature.row_counts == {"tour_data": 1, "others_data": 1}
    assert signature.token.startswith("nbs-core-revenue-v1:")
    assert len(signature.sha256) == 64


def test_v2_core_match_with_full_file_change_is_not_degraded(monkeypatch, tmp_path):
    from backend.services import system_health_service
    from backend.services.cache_generation_service import advance_cache_generation

    db_path = _make_scope_fixture(tmp_path)
    generation_path = tmp_path / "runtime" / "data_generation.json"
    generation = advance_cache_generation(
        db_path=db_path,
        operation_id="op-core-1",
        status="accepted",
        path=generation_path,
    )
    _insert_gmv_only_row(db_path)
    monkeypatch.setattr(system_health_service, "list_stability_history", lambda **kwargs: [])

    payload = system_health_service.build_system_health(
        db_path=db_path,
        cache_path=tmp_path / "cache",
        runtime_dir=tmp_path / "runtime",
        generation_path=generation_path,
        read_only=True,
    )

    assert payload["dataGeneration"]["signatureMatched"] is True
    assert payload["dataGeneration"]["fileSignatureMatched"] is False
    assert payload["status"] == "ok"
    assert payload["dataGeneration"]["cacheToken"] == generation["cacheToken"]


def test_v1_generation_is_read_compatible_but_requires_migration(tmp_path):
    from backend.services.cache_generation_service import load_cache_generation

    db_path = _make_scope_fixture(tmp_path)
    generation_path = tmp_path / "data_generation.json"
    generation_path.write_text(
        json.dumps(
            {
                "generation": 4,
                "operationId": "legacy-op",
                "status": "accepted",
                "dbSignature": {"sha256": "legacy-sha"},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_cache_generation(generation_path, db_path=db_path)

    assert loaded["legacyMode"] is True
    assert loaded["migrationRequired"] is True
    assert loaded["signatureMatched"] is False
    assert loaded["cacheToken"].startswith("4:")


def test_core_signature_missing_database_fails_closed(tmp_path):
    from backend.services.revenue_generation_service import build_core_revenue_signature

    with pytest.raises(FileNotFoundError):
        build_core_revenue_signature(tmp_path / "missing.db")
