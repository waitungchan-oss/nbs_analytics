import hashlib
import sqlite3
import threading

import pandas as pd
import streamlit as st

from backend.services.gmv_refund_repository import GmvRefundRepository, migrate_gmv_schema
from backend.services.gmv_refund_service import (
    RevenueFrames,
    build_active_gmv_read_model,
    build_gmv_formal_artifacts,
    confirm_refund_batch,
    load_active_gmv_read_model,
    preview_refund_batch,
    revenue_state_token,
)


def _frames():
    tour = pd.DataFrame([{"來源單據號": "S-1", "收款原幣金額": 100.0, "收款類型": "旅費"}])
    return RevenueFrames(tour, pd.DataFrame(), tour.copy(), pd.DataFrame())


def _active(tmp_path):
    db_path = tmp_path / "nbs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tour_data (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE others_data (id INTEGER PRIMARY KEY)")
    migrate_gmv_schema(db_path)
    frames = _frames()
    token = revenue_state_token(frames, "rules-1")
    preview = preview_refund_batch(
        pd.DataFrame([{"退款單號": "R-1", "來源單據號": "S-1", "退款原幣金額": 20, "退款狀態": "已退款"}]),
        repository=GmvRefundRepository(db_path), revenue_frames=frames,
        revenue_generation_token=token, rule_version="rules-1", file_sha256="integration-file",
    )
    receipt = confirm_refund_batch(
        preview, actor="tester", acknowledgements=frozenset(), db_path=db_path,
        coordination_db_path=tmp_path / "coordination.db", revenue_loader=lambda: frames,
        revenue_generation_loader=lambda: token,
    )
    return db_path, frames, token, receipt


def test_active_read_uses_ready_cache_without_recomputing_revenue(tmp_path, monkeypatch):
    db_path, frames, token, receipt = _active(tmp_path)
    artifacts = build_gmv_formal_artifacts(
        repository=GmvRefundRepository(db_path), version_id=receipt.version_id,
        revenue_frames=frames, rule_version="rules-1", cache_dir=tmp_path / "cache",
    )

    def fail_if_recomputed(*args, **kwargs):
        raise AssertionError("active read must not rebuild full revenue adjustments")

    monkeypatch.setattr("app_workflows._apply_gmv_refund_adjustments", fail_if_recomputed)
    model = build_active_gmv_read_model(
        GmvRefundRepository(db_path), frames, rule_version="rules-1",
        cache_manifest=artifacts.cache_manifest, cache_dir=tmp_path / "cache",
    )
    assert model.status == "CURRENT"
    assert model.can_export is True
    assert model.total_adjusted["refund_total"] == 20
    assert model.paid_adjusted["applied_refund_total"] == 20


def test_formal_cache_builds_total_and_paid_exports_concurrently(tmp_path, monkeypatch):
    db_path, frames, token, receipt = _active(tmp_path)
    barrier = threading.Barrier(2, timeout=2)

    def synchronized_export(*args, **kwargs):
        barrier.wait()
        return {"ex": b"ex", "ex_no_writeoff": b"no-writeoff", "ex_no_writeoff_refund_transfer": b"official"}

    monkeypatch.setattr("app_workflows._compute_gmv_exclusion_workbooks", synchronized_export)
    artifacts = build_gmv_formal_artifacts(
        repository=GmvRefundRepository(db_path), version_id=receipt.version_id,
        revenue_frames=frames, rule_version="rules-1", cache_dir=tmp_path / "cache",
    )
    assert artifacts.cache_manifest.status == "ready"


def test_formal_cache_build_does_not_require_branch_mapping_session_key(tmp_path, monkeypatch):
    db_path, frames, token, receipt = _active(tmp_path)
    monkeypatch.delitem(st.session_state, "BRANCH_MAPPING", raising=False)

    artifacts = build_gmv_formal_artifacts(
        repository=GmvRefundRepository(db_path), version_id=receipt.version_id,
        revenue_frames=frames, rule_version="rules-1", cache_dir=tmp_path / "cache",
    )

    assert artifacts.cache_manifest.status == "ready"


def test_active_read_rejects_cache_for_stale_revenue_token(tmp_path):
    db_path, frames, token, receipt = _active(tmp_path)
    artifacts = build_gmv_formal_artifacts(
        repository=GmvRefundRepository(db_path), version_id=receipt.version_id,
        revenue_frames=frames, rule_version="rules-1", cache_dir=tmp_path / "cache",
    )
    model = load_active_gmv_read_model(
        repository=GmvRefundRepository(db_path), cache_manifest=artifacts.cache_manifest,
        current_revenue_token="different-token", cache_dir=tmp_path / "cache",
    )
    assert model.status == "STALE_REVENUE_GENERATION"
    assert model.can_export is False


def test_active_read_rejects_cache_with_incomplete_summary_contract(tmp_path):
    db_path, frames, token, receipt = _active(tmp_path)
    artifacts = build_gmv_formal_artifacts(
        repository=GmvRefundRepository(db_path), version_id=receipt.version_id,
        revenue_frames=frames, rule_version="rules-1", cache_dir=tmp_path / "cache",
    )
    manifest = artifacts.cache_manifest
    summaries_record = manifest.artifacts["summaries"]
    summaries_path = (
        tmp_path / "cache" / receipt.version_id / manifest.cache_key.replace(":", "-")
        / str(summaries_record["path"])
    )
    summaries_path.write_text(
        '[{"退款維度":"總退款","指標":"退款明細金額","數值":20}]',
        encoding="utf-8",
    )
    summary_bytes = summaries_path.read_bytes()
    summaries_record.update(
        bytes=len(summary_bytes), sha256=hashlib.sha256(summary_bytes).hexdigest()
    )
    model = load_active_gmv_read_model(
        repository=GmvRefundRepository(db_path), cache_manifest=manifest,
        current_revenue_token=token, cache_dir=tmp_path / "cache",
    )
    assert model.status == "CACHE_INVALID"
    assert model.can_export is False
