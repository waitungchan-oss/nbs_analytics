import json
import sqlite3

import pytest


def test_generation_advances_atomically_with_database_signature(tmp_path):
    from backend.services.cache_generation_service import (
        advance_cache_generation,
        load_cache_generation,
    )

    db_path = tmp_path / "live.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('accepted')")
        connection.commit()
    finally:
        connection.close()

    generation_path = tmp_path / "data_generation.json"
    first = advance_cache_generation(
        db_path=db_path,
        operation_id="op-1",
        status="accepted",
        path=generation_path,
    )
    second = advance_cache_generation(
        db_path=db_path,
        operation_id="op-2",
        status="rejected_rolled_back",
        path=generation_path,
    )

    assert first["generation"] == 1
    assert second["generation"] == 2
    assert len(second["dbSignature"]["sha256"]) == 64

    loaded = load_cache_generation(generation_path, db_path=db_path)
    assert loaded["signatureMatched"] is True
    assert loaded["cacheToken"] == f"2:{second['dbSignature']['sha256']}"

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("INSERT INTO sample VALUES ('newer than generation file')")
        connection.commit()
    finally:
        connection.close()

    stale = load_cache_generation(generation_path, db_path=db_path)
    assert stale["signatureMatched"] is False
    assert stale["cacheToken"] != loaded["cacheToken"]


def test_generation_signature_refresh_preserves_generation_and_operation(tmp_path):
    from backend.services.cache_generation_service import (
        advance_cache_generation,
        load_cache_generation,
        refresh_cache_generation_signature,
    )

    db_path = tmp_path / "live.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    generation_path = tmp_path / "data_generation.json"
    before = advance_cache_generation(
        db_path=db_path,
        operation_id="upload-op",
        status="accepted",
        path=generation_path,
    )
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("INSERT INTO sample VALUES ('governance metadata')")
        connection.commit()
    finally:
        connection.close()
    assert load_cache_generation(generation_path, db_path=db_path)["signatureMatched"] is False

    refreshed = refresh_cache_generation_signature(db_path=db_path, path=generation_path)

    assert refreshed["generation"] == before["generation"]
    assert refreshed["operationId"] == before["operationId"]
    assert refreshed["status"] == before["status"]
    assert refreshed["signatureMatched"] is True


def test_refresh_missing_database_does_not_clobber_existing_signature(tmp_path):
    from backend.services.cache_generation_service import (
        advance_cache_generation,
        refresh_cache_generation_signature,
    )

    db_path = tmp_path / "live.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    generation_path = tmp_path / "data_generation.json"
    before = advance_cache_generation(
        db_path=db_path, operation_id="op-1", status="accepted", path=generation_path,
    )
    with pytest.raises(FileNotFoundError, match="database signature"):
        refresh_cache_generation_signature(
            db_path=tmp_path / "missing.db", path=generation_path,
        )

    after = json.loads(generation_path.read_text(encoding="utf-8"))
    assert after["dbSignature"] == before["dbSignature"]
