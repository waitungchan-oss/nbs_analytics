from __future__ import annotations

import hashlib
import io
import json
import time
import traceback
from html import escape

import numpy as np
import pandas as pd
import streamlit as st

import database as database_module

from app_workflows import (
    COL_BRANCH,
    COL_DATE,
    COL_MONEY,
    COL_ORDER_ID,
    COL_QTY,
    COL_SALESPERSON,
    CONFIG_FILE,
    HAS_AI_LIBS,
    HAS_MATPLOTLIB,
    REVENUE_SCOPE_CAPTION,
    REVENUE_SCOPE_LABEL,
    SESSION_RULE_KEYS,
    TARGET_DEPT_FOR_REP,
    _add_health_column,
    _apply_ai_cleaning_suggestions,
    _apply_filters,
    _best_macro_metric,
    _best_metric_row,
    _build_ai_cleaning_suggestions_workbook,
    _build_causal_analytics_workbook,
    _build_dashboard_kpis,
    _build_data_quality_workbook,
    _build_drift_diagnosis_workbook,
    _build_entity_resolution_workbook,
    _build_feature_store_workbook,
    _build_forecast_governance_workbook,
    _build_gmv_audit_workbook,
    _build_horizon_weighted_consensus,
    _build_upload_stability_gate_workbook,
    _change_summary_value,
    _clean_editor_list,
    _combined_max_date,
    _compute_ai_cleaning_suggestions,
    _compute_causal_driver_analytics,
    _compute_data_quality_scorecard,
    _compute_feature_store_lead_signals,
    _compute_forecast_governance,
    _compute_gmv_exclusion_workbooks,
    _current_entity_resolution_audit,
    _current_rules,
    _ensure_export_workbooks,
    _evaluate_monthly_baselines_for_runtime,
    _filter_gmv_exclusion_frames,
    _fmt_date,
    _format_quality_display_value,
    _gmv_summary_rows,
    _governance_summary_value,
    _load_and_compute_cache,
    _invalidate_session_cache_if_generation_changed,
    _model_health_label,
    _parse_gmv_exclusion_ids,
    _rebuild_cache_after_database_restore,
    _refresh_cache_and_rerun,
    _repair_operator_assignments_before_load,
    _repair_subtable_branch_assignments_before_load,
    _render_kpi_filter_center,
    _render_rank_filter_center,
    _render_sidebar_shell,
    _upload_date_source_diagnostics_from_frames,
    _uploaded_excel_frame,
    _upsert_summary_rows,
    _weight_bucket_for_horizon,
    _weight_schedule_from_backtest,
    build_dashboard_data,
    build_macro_forecast_summary,
    build_monthly_baseline_governance,
    build_phase2c_stability_gate,
    clear_database,
    draw_forecast_chart,
    draw_month_end_macro_chart,
    draw_seven_day_macro_chart,
    draw_top10_barh,
    handle_core_drift_rollback,
    load_all_data_from_db,
    map_dest_category,
    map_ticket_category,
    list_monthly_baseline_promotions,
    promote_monthly_baselines,
    record_stability_history,
    restore_database_from_backup,
    run_upload_preflight,
    safe_draw_pie,
    save_business_rules,
    upsert_to_db,
)
from backend.services.upload_lock_service import UploadBusyError, acquire_upload_lease
from backend.services.upload_orchestrator_service import execute_upload_operation
from streamlit_rendering import *


def _coerce_entity_audit_dataframe(value: object) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        df = value.copy()
    else:
        try:
            df = pd.DataFrame(value)
        except Exception:
            return pd.DataFrame({"內容": [str(value)]})
    if df.empty:
        return df

    df = df.loc[:, ~pd.Index(df.columns).duplicated()].copy()
    df.columns = [str(column) for column in df.columns]

    seen: dict[str, int] = {}
    unique_columns: list[str] = []
    for column in df.columns:
        count = seen.get(column, 0)
        unique_columns.append(column if count == 0 else f"{column}_{count + 1}")
        seen[column] = count + 1
    df.columns = unique_columns

    complex_types = (dict, list, tuple, set)
    for column in df.select_dtypes(include=["object"]).columns:
        df[column] = df[column].map(
            lambda item: json.dumps(item, ensure_ascii=False, default=str)
            if isinstance(item, complex_types)
                else item
        )
    return df

def _coerce_arrow_safe_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    display = df.copy()
    for column in display.select_dtypes(include=["object"]).columns:
        non_null = display[column].dropna()
        if non_null.map(type).nunique() > 1:
            display[column] = display[column].map(lambda item: "不適用" if pd.isna(item) else str(item))
    return display

def _render_entity_audit_dataframe(value: object) -> None:
    frame = _coerce_entity_audit_dataframe(value)
    try:
        st.dataframe(_style_entity_audit_table(frame), hide_index=True, width="stretch")
    except Exception:
        st.warning("本段稽核明細暫時無法套用表格樣式，已改用原始表格顯示。")
        st.dataframe(frame.astype(str), hide_index=True, width="stretch")

def _render_upload_stability_gate(gate: dict) -> None:
    if not gate:
        return
    status = gate.get("status")
    status_label = "Matched" if status == "matched" else "Drift"
    tone = "#14532D" if status == "matched" else "#7C2D12"
    border = "#86EFAC" if status == "matched" else "#FDBA74"
    bg = "#ECFDF5" if status == "matched" else "#FFF7ED"
    st.markdown(
        f"""
        <div class="nbs-upload-gate-card" style="border:1px solid {border}; background:{bg}; border-radius:10px; padding:14px 16px; margin:12px 0;">
          <div style="font-size:0.78rem; font-weight:800; color:{tone}; letter-spacing:.08em; text-transform:uppercase;">Phase 2F Upload Acceptance</div>
          <div style="font-size:1.05rem; font-weight:800; color:{tone}; margin-top:4px;">口徑驗收結果：{escape(status_label)}</div>
          <div style="color:{tone}; margin-top:6px;">{escape(str(gate.get("message", "")))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if gate.get("status") == "matched":
        st.success(gate.get("message", "Phase 2C Upload Rebuild Stability Gate：口徑穩定。"))
    else:
        st.warning(gate.get("message", "Phase 2C Upload Rebuild Stability Gate：請檢查口徑漂移。"))
    freshness_update = gate.get("freshnessUpdate") or {}
    freshness_summary = freshness_update.get("summary") or {}
    freshness_update_count = int(freshness_summary.get("updatedChecks", 0))
    if freshness_update_count:
        st.info(f"資料更新狀態：已偵測到 {freshness_update_count} 項正常推進，這些變化不影響核心口徑驗收。")
    else:
        st.caption("資料更新狀態：最新日期與資料筆數未變動。")

    with st.expander("查看 Phase 2C Upload Rebuild Stability Gate", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Baseline", gate.get("formattedExpectedTotal", "—"))
        c2.metric("Actual", gate.get("formattedActualTotal", "—"))
        c3.metric("Delta", _money_text(float(gate.get("deltaAmount") or 0)))
        c4.metric("Core Checks", f"{gate.get('matchedChecks', 0)} / {gate.get('totalChecks', 0)} matched")
        drift_checks = gate.get("driftChecks") or []
        if drift_checks:
            st.caption("核心口徑漂移：請優先檢查以下 failed checks")
            st.dataframe(pd.DataFrame(drift_checks), hide_index=True, width="stretch")
        freshness_checks = freshness_update.get("checks") or []
        if freshness_checks:
            st.caption("資料更新狀態：日期與筆數變化只作 freshness 稽核，不阻擋驗收")
            st.dataframe(pd.DataFrame(freshness_checks), hide_index=True, width="stretch")
        baseline = gate.get("stabilityBaseline") or {}
        checks = baseline.get("checks") or []
        if checks:
            st.caption("完整 Gate Checks")
            st.dataframe(pd.DataFrame(checks), hide_index=True, width="stretch")
        st.download_button(
            "📥 下載本次上傳 Phase 2F Stability Gate 驗收報告",
            _build_upload_stability_gate_workbook(gate),
            "本次上傳_Phase2F_Stability_Gate.xlsx",
            width="stretch",
        )

def _render_upload_audit_notice() -> None:
    audit = st.session_state.pop("LAST_UPLOAD_AUDIT", None)
    if not audit:
        return
    if audit.get("status") == "success":
        st.success(audit.get("message", "上傳批次已完成。"))
    elif audit.get("status") == "warning":
        st.warning(audit.get("message", "上傳批次已完成，但需要檢查日期範圍。"))
    else:
        st.error(audit.get("message", "上傳批次沒有寫入。"))

    preflight_report = audit.get("preflight_report") or {}
    if preflight_report:
        if preflight_report.get("status") == "matched":
            st.success(preflight_report.get("message", "上傳預演通過，正式寫入前已完成臨時資料庫驗證。"))
        else:
            st.error(preflight_report.get("message", "上傳預演發現核心口徑漂移，正式 SQLite 不會寫入。"))
        with st.expander("查看上傳預演結果", expanded=False):
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Preflight Actual", preflight_report.get("formattedActualTotal", "—"))
            p2.metric("Preflight Delta", _money_text(float(preflight_report.get("deltaAmount") or 0)))
            p3.metric("口徑排除行數", f"{preflight_report.get('filteredExcludedRows', 0):,}")
            p4.metric("實際寫入行數", f"{preflight_report.get('writeRows', 0):,}")
            st.caption(f"來源檔案：{', '.join(preflight_report.get('sourceFiles') or []) or '—'}")
            st.caption(f"最新資料日期：{preflight_report.get('latestDataDate') or '—'}")
            drift_checks = preflight_report.get("driftChecks") or []
            if drift_checks:
                st.caption("預演核心口徑漂移：")
                st.dataframe(pd.DataFrame(drift_checks), hide_index=True, width="stretch")
            preflight_timings = preflight_report.get("stageTimings") or []
            if preflight_timings:
                st.caption("Preflight 內部耗時")
                st.dataframe(pd.DataFrame(preflight_timings), hide_index=True, width="stretch")
            drift_diagnosis = preflight_report.get("driftDiagnosis") or {}
            if drift_diagnosis:
                st.caption("Drift Diagnosis")
                d1, d2, d3 = st.columns(3)
                d1.metric("Diagnosis Status", drift_diagnosis.get("status", "—"))
                d2.metric("Expected", _money_text(float(drift_diagnosis.get("expectedTotal") or 0)))
                d3.metric("Actual", _money_text(float(drift_diagnosis.get("actualTotal") or 0)))
                st.metric("Delta", _money_text(float(drift_diagnosis.get("deltaAmount") or 0)))
                st.caption(drift_diagnosis.get("summaryMessage", "—"))
                diagnosis_rows = drift_diagnosis.get("topDrivers") or []
                if diagnosis_rows:
                    st.dataframe(pd.DataFrame(diagnosis_rows), hide_index=True, width="stretch")
                st.download_button(
                    "📥 下載 Drift Diagnosis JSON",
                    json.dumps(drift_diagnosis, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                    "drift_diagnosis.json",
                    mime="application/json",
                    width="stretch",
                )
                st.download_button(
                    "📥 下載 Drift Diagnosis Excel",
                    _build_drift_diagnosis_workbook(drift_diagnosis),
                    "drift_diagnosis.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )
            batch_rows = preflight_report.get("batchSummary") or []
            if batch_rows:
                st.caption("預演批次摘要")
                st.dataframe(pd.DataFrame(batch_rows), hide_index=True, width="stretch")
            upsert_rows = _upsert_summary_rows(preflight_report.get("upsertSummary") or {})
            if upsert_rows:
                st.caption("預演 SQLite Upsert 摘要")
                st.dataframe(pd.DataFrame(upsert_rows), hide_index=True, width="stretch")

    stability_gate = audit.get("stability_gate")
    if stability_gate:
        _render_upload_stability_gate(stability_gate)
    if audit.get("history_record_id"):
        st.caption(f"Phase 2G 驗收歷史已保存，Record ID：{audit['history_record_id']}。")
    if audit.get("history_error"):
        st.warning(f"上傳與重建已完成，但驗收歷史保存失敗：{audit['history_error']}")
    if audit.get("rollback_status") == "verified":
        st.success(f"Phase 2H 自動回滾已完成並通過二次驗證。異常副本：{audit.get('quarantine_path') or '未提供'}")
    elif audit.get("rollback_status") in {"restore_failed", "verification_failed", "backup_missing"}:
        st.error(f"Phase 2H Rollback Failed：{audit.get('rollback_error') or '未知錯誤'}")
    post_rollback_gate = audit.get("post_rollback_gate")
    if post_rollback_gate:
        with st.expander("查看回滾後二次驗證 Gate", expanded=False):
            st.json(post_rollback_gate)

    batch_rows = audit.get("batch_summary") or []
    diagnostic_rows = audit.get("date_diagnostics") or []
    upsert_rows = audit.get("upsert_summary") or []
    stage_timings = audit.get("stage_timings") or []
    entity_audit = audit.get("entity_audit")
    has_entity_audit = isinstance(entity_audit, dict) and not entity_audit.get("summary", pd.DataFrame()).empty
    if not any([batch_rows, diagnostic_rows, upsert_rows, stage_timings, has_entity_audit]):
        return

    with st.expander("查看本次上傳詳細反饋", expanded=False):
        if stage_timings:
            st.caption("本次上傳階段耗時")
            st.dataframe(pd.DataFrame(stage_timings), hide_index=True, width="stretch")

        if batch_rows:
            st.caption("本次清洗後批次摘要")
            st.dataframe(pd.DataFrame(batch_rows), hide_index=True, width="stretch")

        if diagnostic_rows:
            st.caption("上傳檔案日期來源診斷")
            st.dataframe(pd.DataFrame(diagnostic_rows), hide_index=True, width="stretch")

        if upsert_rows:
            st.caption("SQLite Upsert 寫入摘要")
            st.dataframe(pd.DataFrame(upsert_rows), hide_index=True, width="stretch")

        if has_entity_audit:
            st.caption("本次 Entity Resolution 單號匹配稽核")
            entity_summary = entity_audit["summary"]
            secondary_only_match = entity_summary[entity_summary["指標"] == "副表未落主表單號數"] if "指標" in entity_summary.columns else pd.DataFrame()
            if not secondary_only_match.empty:
                secondary_only_count = int(float(secondary_only_match["數值"].iloc[0] or 0))
                if secondary_only_count > 0:
                    st.warning(f"本次副表有 {secondary_only_count:,} 個交易號碼未在財務主表找到對應來源單據號；這些副表-only 記錄不會進正式營收看板。")
            _render_entity_audit_dataframe(entity_audit["summary"])
            st.markdown("###### Entity Resolution 詳細表")
            st.markdown("###### 來源拆解")
            _render_entity_audit_dataframe(entity_audit.get("source_breakdown", pd.DataFrame()))
            st.markdown("###### 重複單號明細")
            _render_entity_audit_dataframe(entity_audit.get("duplicate_detail", pd.DataFrame()))
            st.markdown("###### 未匹配明細")
            _render_entity_audit_dataframe(entity_audit.get("unmatched_detail", pd.DataFrame()))
            st.markdown("###### ID 清洗樣本")
            _render_entity_audit_dataframe(entity_audit.get("id_cleaning_samples", pd.DataFrame()))
            st.download_button(
                "📥 下載本次 Entity Resolution Audit",
                _build_entity_resolution_workbook(entity_audit),
                "本次上傳_Entity_Resolution_Audit.xlsx",
                width="stretch",
            )

def _money_text(value: float) -> str:
    return f"HKD {value:,.0f}"

def _style_rank_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    return (
        df.style.format(
            {
                "總額": "{:,.0f}",
                "旅行團": "{:,.0f}",
                "郵輪": "{:,.0f}",
                "票務": "{:,.0f}",
                "貢獻佔比": "{:.2f}%",
            }
        )
        .background_gradient(subset=["總額"], cmap="Blues", text_color_threshold=0.0)
        .background_gradient(subset=["貢獻佔比"], cmap="Greens")
        .highlight_max(subset=["總額"], color="#FFF2CC")
        .set_properties(subset=["總額"], **{"color": "#1F1F1F", "font-weight": "700"})
        .hide(axis="index")
    )

def _style_mix_table(df: pd.DataFrame, value_col: str) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    return (
        df.style.format({value_col: "{:,.0f}", "佔比": "{:.2f}%"})
        .background_gradient(subset=[value_col], cmap="Blues")
        .background_gradient(subset=["佔比"], cmap="Greens")
        .hide(axis="index")
    )

def _style_forecast_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    formatters = {
        "ARIMA": "{:,.0f}",
        "Prophet": "{:,.0f}",
        "LightGBM": "{:,.0f}",
        "Consensus (共識)": "{:,.0f}",
        "MonthEnd Consensus": "{:,.0f}",
        "Lower": "{:,.0f}",
        "Upper": "{:,.0f}",
        "MTDActual": "{:,.0f}",
        "RemainingPrediction": "{:,.0f}",
        "RemainingDays": "{:,.0f}",
    }
    available_formatters = {k: v for k, v in formatters.items() if k in df.columns}
    styled = df.style.format(available_formatters)
    if "Consensus (共識)" in df.columns:
        styled = styled.background_gradient(subset=["Consensus (共識)"], cmap="Blues")
    if "MonthEnd Consensus" in df.columns:
        styled = styled.background_gradient(subset=["MonthEnd Consensus"], cmap="Blues")
    if "Lower" in df.columns:
        styled = styled.background_gradient(subset=["Lower"], cmap="Greens")
    if "Upper" in df.columns:
        styled = styled.background_gradient(subset=["Upper"], cmap="Oranges")
    return styled.hide(axis="index")

def _style_backtest_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    formatters = {
        "MAE": "{:,.0f}",
        "WAPE": "{:.2f}%",
        "MAPE": "{:.2f}%",
        "SMAPE": "{:.2f}%",
        "MedianAPE": "{:.2f}%",
        "Bias": "{:,.0f}",
        "樣本數": "{:,.0f}",
        "ARIMA": "{:.1f}%",
        "Prophet": "{:.1f}%",
        "LightGBM": "{:.1f}%",
        "依據樣本數": "{:,.0f}",
        "最佳WAPE": "{:.2f}%",
        "最佳MAPE": "{:.2f}%",
        "策略最佳WAPE": "{:.2f}%",
        "平均預測天數": "{:.1f}",
        "Actual": "{:,.0f}",
        "Prediction": "{:,.0f}",
        "MTDActual": "{:,.0f}",
        "RemainingPrediction": "{:,.0f}",
    }
    available_formatters = {k: v for k, v in formatters.items() if k in df.columns}
    styled = df.style.format(available_formatters)
    if "WAPE" in df.columns:
        styled = styled.background_gradient(subset=["WAPE"], cmap="Reds_r")
    elif "MAPE" in df.columns:
        styled = styled.background_gradient(subset=["MAPE"], cmap="Reds_r")
    return styled.hide(axis="index")

def _style_quality_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    display = df.copy()
    if "數值" in display.columns:
        display["數值"] = display["數值"].apply(_format_quality_display_value)
    formatters = {
        "分數": "{:.2f}",
        "完整率": "{:.2%}",
        "比例": "{:.2%}",
    }
    available_formatters = {k: v for k, v in formatters.items() if k in df.columns}
    styled = display.style.format(available_formatters, na_rep="不適用")
    if "分數" in display.columns:
        styled = styled.background_gradient(subset=["分數"], cmap="RdYlGn", vmin=0, vmax=100)
    if "比例" in display.columns:
        styled = styled.background_gradient(subset=["比例"], cmap="Blues")
    return styled.hide(axis="index")

def _style_entity_audit_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    display = df.copy()
    if "數值" in display.columns:
        display["數值"] = display["數值"].apply(_format_quality_display_value)
    formatters = {
        "比例": "{:.2%}",
        "金額合計": "{:,.0f}",
        "匹配率": "{:.2%}",
    }
    available_formatters = {k: v for k, v in formatters.items() if k in display.columns}
    styled = display.style.format(available_formatters, na_rep="不適用")
    if "金額合計" in display.columns:
        styled = styled.background_gradient(subset=["金額合計"], cmap="Blues")
    if "行數" in display.columns:
        styled = styled.background_gradient(subset=["行數"], cmap="Greens")
    return styled.hide(axis="index")

def _style_ai_cleaning_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    display = df.copy()
    formatters = {"信心分": "{:.1f}", "影響行數": "{:,.0f}"}
    available_formatters = {k: v for k, v in formatters.items() if k in display.columns}
    styled = display.style.format(available_formatters, na_rep="不適用")
    if "信心分" in display.columns:
        styled = styled.background_gradient(subset=["信心分"], cmap="RdYlGn", vmin=0, vmax=100)
    if "影響行數" in display.columns:
        styled = styled.background_gradient(subset=["影響行數"], cmap="Blues")
    return styled.hide(axis="index")

def _style_governance_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    formatters = {
        "治理分數": "{:.2f}",
        "GovernanceScore": "{:.2f}",
        "AccuracyScore": "{:.2f}",
        "BiasScore": "{:.2f}",
        "StabilityScore": "{:.2f}",
        "SampleScore": "{:.2f}",
        "RoleScore": "{:.2f}",
        "WAPE": "{:.2f}%",
        "MAPE": "{:.2f}%",
        "MedianAPE": "{:.2f}%",
        "SMAPE": "{:.2f}%",
        "MAE": "{:,.0f}",
        "Bias": "{:,.0f}",
        "Bias/MAE": "{:.2f}",
        "APE波動": "{:.2f}",
        "樣本數": "{:,.0f}",
    }
    available_formatters = {k: v for k, v in formatters.items() if k in df.columns}
    styled = df.style.format(available_formatters, na_rep="未評估")
    if "GovernanceScore" in df.columns:
        styled = styled.background_gradient(subset=["GovernanceScore"], cmap="RdYlGn", vmin=0, vmax=100)
    elif "治理分數" in df.columns:
        styled = styled.background_gradient(subset=["治理分數"], cmap="RdYlGn", vmin=0, vmax=100)
    if "WAPE" in df.columns:
        styled = styled.background_gradient(subset=["WAPE"], cmap="Reds_r")
    return styled.hide(axis="index")

def _style_feature_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    formatters = {
        "缺失率": "{:.2f}%",
        "NoFutureLeakPassRate": "{:.2f}%",
        "最佳WAPE": "{:.2f}%",
        "樣本數": "{:,.0f}",
        "非空樣本": "{:,.0f}",
        "RecentTotalRevenue7D": "{:,.0f}",
        "RecentTotalRevenue14D": "{:,.0f}",
        "RecentSpecialistShare7D": "{:.2%}",
        "SpecialistMomentumRatio": "{:.2f}",
        "RecentLonghaulShare7D": "{:.2%}",
        "LonghaulMomentumRatio": "{:.2f}",
        "RecentLargeOrderCount7D": "{:,.0f}",
        "RecentLargeOrderAmount7D": "{:,.0f}",
        "RecentTopSalespersonShare7D": "{:.2%}",
        "RecentRevenueVolatility7D": "{:.2f}",
        "RecentRevenueVs14DAvg": "{:.2f}",
    }
    available = {k: v for k, v in formatters.items() if k in df.columns}
    return df.style.format(available, na_rep="未評估").hide(axis="index")

def _style_causal_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    if df.empty:
        return df.style
    formatters = {
        "CurrentAmount": "{:,.0f}",
        "BaselineAmount": "{:,.0f}",
        "Delta": "{:,.0f}",
        "AbsDelta": "{:,.0f}",
        "ContributionRate": "{:.2f}%",
        "DriverDeltaSum": "{:,.0f}",
        "TotalDelta": "{:,.0f}",
        "ReconciliationDiff": "{:,.2f}",
        "Top5訂單佔比": "{:.2f}%",
        "訂單數": "{:,.0f}",
    }
    available = {k: v for k, v in formatters.items() if k in df.columns}
    styled = df.style.format(available, na_rep="未評估")
    if "Delta" in df.columns:
        styled = styled.background_gradient(subset=["Delta"], cmap="RdYlGn")
    return styled.hide(axis="index")


def _render_monthly_baseline_governance() -> None:
    st.markdown("### Monthly Baseline Governance")
    try:
        evaluation = _evaluate_monthly_baselines_for_runtime()
        governance = build_monthly_baseline_governance(evaluation=evaluation)
        promotions = list_monthly_baseline_promotions(limit=1)
    except Exception as exc:
        st.error(f"月度基準治理狀態載入失敗：{type(exc).__name__}: {exc}")
        return

    status_labels = {
        "monitoring": "Monitoring",
        "promotion_ready": "Ready",
        "blocking": "Blocking",
        "drift": "Drift",
    }
    status = str(governance.get("status") or "monitoring")
    status_label = status_labels.get(status, status.title())
    stable_cycles = int(governance.get("stableUploadCycles") or 0)
    required_cycles = int(governance.get("requiredStableUploadCycles") or 1)
    checks = governance.get("checks") or []

    with st.container(border=True):
        st.markdown(f"**狀態：{status_label}**")
        st.caption(
            f"正式口徑：{governance.get('scope')}｜"
            f"母體：{governance.get('population')}｜"
            f"穩定上傳週期：{stable_cycles} / {required_cycles}"
        )
        rows = pd.DataFrame(
            [
                {
                    "月份": row.get("month"),
                    "治理模式": str(row.get("mode") or "").title(),
                    "顯示基準": row.get("formattedExpectedTotal"),
                    "目前金額": row.get("formattedActualTotal"),
                    "精確差額": float(row.get("deltaAmount") or 0),
                    "狀態": str(row.get("status") or "").title(),
                }
                for row in checks
            ]
        )
        st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            column_config={"精確差額": st.column_config.NumberColumn("精確差額", format="HKD %.2f")},
        )

        if status == "drift":
            st.warning("監測月份出現 drift；穩定週期已重設為 0 / 1。目前只警告，不阻擋 upload、不執行 rollback。")
        elif status == "promotion_ready":
            st.success("6 / 6 月份匹配，已完成 1 個穩定上傳週期，可進行人工升級。")
        elif status == "blocking":
            latest = promotions[0] if promotions else {}
            st.success(
                "2026-01 至 2026-06 已是 Blocking baseline。"
                + (f" Promotion Record #{latest.get('id')}。" if latest else "")
            )
        else:
            st.info("月度基準正在監測；需完成一次功能部署後的正式 accepted upload 才可升級。")

        promotion_ready = bool(governance.get("promotionReady"))
        if st.button(
            "升級為阻擋式基準",
            disabled=not promotion_ready,
            key="MONTHLY_BASELINE_PROMOTION_OPEN",
            width="stretch",
        ):
            st.session_state["MONTHLY_BASELINE_PROMOTION_CONFIRM_OPEN"] = True
            st.rerun()

        if st.session_state.get("MONTHLY_BASELINE_PROMOTION_CONFIRM_OPEN") and promotion_ready:
            st.warning(
                "本次會新增 2026-01、02、03、04、06 為 Blocking；2026-05 已是 Blocking。"
                "生效後 drift 會拒絕 upload 並觸發 rollback。"
            )
            confirmed = st.checkbox(
                "我理解升級後的上傳阻擋與 rollback 影響",
                key="MONTHLY_BASELINE_PROMOTION_CONFIRMED",
            )
            cancel_col, confirm_col = st.columns(2)
            with cancel_col:
                if st.button("取消", key="MONTHLY_BASELINE_PROMOTION_CANCEL", width="stretch"):
                    st.session_state["MONTHLY_BASELINE_PROMOTION_CONFIRM_OPEN"] = False
                    st.session_state["MONTHLY_BASELINE_PROMOTION_CONFIRMED"] = False
                    st.rerun()
            with confirm_col:
                if st.button(
                    "確認升級",
                    type="primary",
                    disabled=not confirmed,
                    key="MONTHLY_BASELINE_PROMOTION_CONFIRM",
                    width="stretch",
                ):
                    try:
                        result = promote_monthly_baselines(
                            confirmed=True,
                            expected_record_id=int(governance["eligibleRecordId"]),
                        )
                    except Exception as exc:
                        st.error(f"月度基準升級失敗：{type(exc).__name__}: {exc}")
                    else:
                        st.session_state["MONTHLY_BASELINE_PROMOTION_CONFIRM_OPEN"] = False
                        st.session_state["MONTHLY_BASELINE_PROMOTION_CONFIRMED"] = False
                        st.success(
                            f"月度基準已升級；Promotion Event #{result.get('promotionEventId')}。"
                        )
                        st.rerun()


def _render_config_tab() -> None:
    _render_monthly_baseline_governance()
    c1, c2 = st.columns(2)
    with c1:
        st.write("**1. 銷售點代碼與分社名稱對應表**")
        ed_b = st.data_editor(pd.DataFrame(list(st.session_state["BRANCH_MAPPING"].items()), columns=["代碼", "名稱"]), num_rows="dynamic", width="stretch")
        st.write("**2. 專職銷售代表名單**")
        ed_s = st.data_editor(pd.DataFrame(st.session_state["SALES_REP_LIST"], columns=["姓名"]), num_rows="dynamic", width="stretch")
    with c2:
        st.write("**3. 單號排除前綴**")
        ed_e = st.data_editor(pd.DataFrame(st.session_state["EXCLUDE_PREFIXES"], columns=["前綴"]), num_rows="dynamic", width="stretch")
        st.write("**4. 郵輪排除部門**")
        ed_c = st.data_editor(pd.DataFrame(st.session_state["CRUISE_DEPTS"], columns=["部門"]), num_rows="dynamic", width="stretch")

    if st.button("💾 儲存並套用設定", type="primary"):
        new_rules = {
            "BRANCH_MAPPING": {
                str(code).strip().upper(): str(name).strip()
                for code, name in zip(ed_b["代碼"], ed_b["名稱"])
                if str(code).strip() and str(name).strip()
            },
            "SALES_REP_LIST": _clean_editor_list(ed_s, "姓名"),
            "EXCLUDE_PREFIXES": _clean_editor_list(ed_e, "前綴"),
            "CRUISE_DEPTS": _clean_editor_list(ed_c, "部門"),
            "TARGET_BRANCHES_S3": list(st.session_state["TARGET_BRANCHES_S3"]),
        }
        for key in SESSION_RULE_KEYS:
            st.session_state[key] = new_rules[key]
        if save_business_rules(new_rules):
            st.success(f"設定已保存至 {CONFIG_FILE}，並將立刻生效。")
            _refresh_cache_and_rerun()
        else:
            st.error("設定儲存失敗，請檢查資料夾權限。")

    st.write("---")
    st.markdown("### 🗄️ 資料庫管理 (Database Management)")
    confirm = st.checkbox("我確認要清空所有歷史資料庫")
    if st.button("🗑️ 清空所有歷史資料庫 (危險操作)", disabled=not confirm):
        clear_database()
        st.session_state["PROCESSED_DATA_CACHE"] = None
        st.session_state["DB_LOADED_FLAG"] = False
        st.success("資料庫已完全清空，請重新上傳基礎數據。")
        st.rerun()

def _streamlit_named_bytes(upload) -> io.BytesIO:
    payload = io.BytesIO(upload.getvalue())
    payload.name = getattr(upload, "name", "upload.xlsx")
    return payload


def _render_upload_area(has_db_data: bool) -> None:
    _render_upload_audit_notice()
    with st.expander("📥 追加上傳新月份數據 (Upsert New Data)", expanded=not has_db_data):
        left, right = st.columns(2)
        with left:
            main_up = st.file_uploader("營收主表 (必填)", type=["xlsx", "xls"])
            tour_up = st.file_uploader("旅行團副表 (選填)", type=["xlsx", "xls"])
            oth_up = st.file_uploader("其他業務副表 (多選)", type=["xlsx", "xls"], accept_multiple_files=True)
        with right:
            if HAS_AI_LIBS:
                _ = st.success("✅ AI 預測環境就緒")
            else:
                _ = st.warning("⚠️ 缺 matplotlib/statsmodels，AI 預測會降級或跳過")
            if not st.button("🔥 上傳並合併至資料庫", type="primary", width="stretch"):
                return
        if not main_up:
            st.error("需上傳主表。")
            return

        source_files = [item.name for item in [main_up, tour_up, *(oth_up or [])] if item is not None]
        try:
            with acquire_upload_lease(entry_point="streamlit", source_files=source_files) as lease:
                with st.status("讀取與清洗檔案...", expanded=True) as upload_status:
                    main_frame = _uploaded_excel_frame(_streamlit_named_bytes(main_up))
                    tour_frame = _uploaded_excel_frame(_streamlit_named_bytes(tour_up)) if tour_up else None
                    other_frames = [
                        (item.name, _uploaded_excel_frame(_streamlit_named_bytes(item)))
                        for item in (oth_up or [])
                    ]
                    secondary = []
                    if tour_frame is not None:
                        secondary.append(("旅行團副表", tour_up.name, tour_frame))
                    secondary.extend(("其他業務副表", name, frame) for name, frame in other_frames)
                    date_diag = _upload_date_source_diagnostics_from_frames(main_up.name, main_frame, secondary)
                    if date_diag.get("date_mismatch_warning"):
                        st.session_state["LAST_UPLOAD_AUDIT"] = {
                            "status": "warning", "message": date_diag["date_mismatch_warning"],
                            "date_diagnostics": date_diag.get("rows", []), "batch_summary": [],
                        }
                        upload_status.update(label="主表未包含目標收款日期，已停止寫入 SQLite", state="error")
                        st.rerun()

                    upload_status.update(label="上傳預演、寫入與口徑驗收...", state="running")
                    execution = execute_upload_operation(
                        lease.operation,
                        main_file=_streamlit_named_bytes(main_up),
                        tour_file=_streamlit_named_bytes(tour_up) if tour_up else None,
                        other_files=[_streamlit_named_bytes(item) for item in (oth_up or [])],
                        live_db_path=database_module.DB_FILE,
                        accepted_cache_rebuilder=lambda: _load_and_compute_cache(include_ai=False),
                    )
                    if st.session_state.get("PROCESSED_DATA_CACHE") is not None:
                        st.session_state["PROCESSED_DATA_CACHE"]["anm"] = execution.anomaly_frame
                    response = execution.response
                    preflight = response.get("preflightReport") or {}
                    st.session_state["LAST_UPLOAD_AUDIT"] = {
                        "status": response.get("status"), "message": response.get("message"),
                        "batch_summary": preflight.get("batchSummary") or [],
                        "date_diagnostics": date_diag.get("rows", []),
                        "upsert_summary": _upsert_summary_rows(response.get("upsertSummary") or {}),
                        "entity_audit": execution.entity_audit,
                        "stability_gate": response.get("stabilityGate"),
                        "monthly_baseline": response.get("monthlyBaseline") or {},
                        "source_files": response.get("sourceFiles") or source_files,
                        "history_record_id": response.get("historyRecordId"),
                        "history_error": response.get("historyError"),
                        "rollback_status": (response.get("rollbackResult") or {}).get("rollbackStatus"),
                        "quarantine_path": (response.get("rollbackResult") or {}).get("quarantinePath"),
                        "post_rollback_gate": (response.get("rollbackResult") or {}).get("postRollbackGate"),
                        "rollback_error": (response.get("rollbackResult") or {}).get("rollbackError"),
                        "drift_diagnosis": preflight.get("driftDiagnosis") or {},
                        "preflight_report": preflight,
                        "stage_timings": response.get("stageTimings") or [],
                    }
                    upload_status.update(label=response.get("message") or "上傳完成", state="complete")
                st.rerun()
        except UploadBusyError as exc:
            owner = exc.owner or {}
            st.warning(f"另一個上傳正在執行：{owner.get('entry_point', 'unknown')}（{owner.get('started_at', 'unknown')}）。")
        except Exception:
            _render_error("程式在清洗關聯或寫入資料庫階段出錯。", traceback.format_exc())


def _render_upload_area_legacy(has_db_data: bool) -> None:
    _render_upload_audit_notice()
    with st.expander("📥 追加上傳新月份數據 (Upsert New Data)", expanded=not has_db_data):
        cu1, cu2 = st.columns(2)
        with cu1:
            main_up = st.file_uploader("營收主表 (必填)", type=["xlsx", "xls"])
            tour_up = st.file_uploader("旅行團副表 (選填)", type=["xlsx", "xls"])
            oth_up = st.file_uploader("其他業務副表 (多選)", type=["xlsx", "xls"], accept_multiple_files=True)
        with cu2:
            if HAS_AI_LIBS:
                _ = st.success("✅ AI 預測環境就緒")
            else:
                _ = st.warning("⚠️ 缺 matplotlib/statsmodels，AI 預測會降級或跳過")
            if st.button("🔥 上傳並合併至資料庫", type="primary", width="stretch"):
                if not main_up:
                    st.error("需上傳主表。")
                    return
                if not UPLOAD_OPERATION_LOCK.acquire(blocking=False):
                    st.warning("另一個上傳或回滾正在執行，請等待完成後再試。")
                    return
                try:
                    branch_mapping, _, _, sales_reps, exclude_prefixes = _current_rules()
                    db_before_tour, db_before_others = load_all_data_from_db()
                    db_before_max = _combined_max_date(db_before_tour, db_before_others)
                    with st.status("讀取與清洗檔案...", expanded=True) as upload_status:
                        stage_timings: list[dict] = []
                        upload_started = time.perf_counter()

                        def _record_stage(label: str, started_at: float) -> None:
                            stage_timings.append({"階段": label, "秒數": round(time.perf_counter() - started_at, 2)})

                        stage_started = time.perf_counter()
                        source_files = [
                            item.name
                            for item in [main_up, tour_up, *(oth_up or [])]
                            if item is not None and getattr(item, "name", None)
                        ]
                        main_raw_df = _uploaded_excel_frame(main_up)
                        tour_raw_df = _uploaded_excel_frame(tour_up) if tour_up is not None else None
                        other_raw_frames = [
                            (getattr(item, "name", str(item)), _uploaded_excel_frame(item))
                            for item in (oth_up or [])
                        ]
                        secondary_diag_frames: list[tuple[str, str, pd.DataFrame]] = []
                        if tour_raw_df is not None:
                            secondary_diag_frames.append(("旅行團副表", getattr(tour_up, "name", "旅行團副表"), tour_raw_df))
                        secondary_diag_frames.extend(("其他業務副表", name, frame) for name, frame in other_raw_frames)
                        date_diag = _upload_date_source_diagnostics_from_frames(
                            getattr(main_up, "name", "營收主表"),
                            main_raw_df,
                            secondary_diag_frames,
                        )
                        _record_stage("讀取 Excel 與日期診斷", stage_started)
                        if date_diag.get("date_mismatch_warning"):
                            st.session_state["LAST_UPLOAD_AUDIT"] = {
                                "status": "warning",
                                "message": date_diag["date_mismatch_warning"],
                                "batch_summary": [],
                                "date_diagnostics": date_diag.get("rows", []),
                                "upsert_summary": [],
                                "stage_timings": stage_timings,
                            }
                            upload_status.update(label="主表未包含目標收款日期，已停止寫入 SQLite", state="error")
                            st.rerun()

                        upload_status.update(label="上傳預演與口徑驗收...", state="running")
                        stage_started = time.perf_counter()
                        preflight_result = run_upload_preflight(
                            main_raw_df,
                            tour_raw_df,
                            other_raw_frames,
                            branch_mapping,
                            exclude_prefixes,
                            sales_reps,
                            source_files=source_files,
                        )
                        _record_stage("Preflight 臨時 DB 與口徑驗收", stage_started)
                        batch_summary = preflight_result.get("batchSummary") or []
                        batch_max = preflight_result.get("latestDataDate")
                        contains_target_date = any(row.get("包含 2026-06-15") for row in batch_summary)
                        preflight_status = str(preflight_result.get("status") or "drift")
                        if preflight_status != "matched":
                            st.session_state["LAST_UPLOAD_AUDIT"] = {
                                "status": "error",
                                "message": preflight_result.get("message", "上傳預演發現核心口徑漂移，正式 SQLite 不會寫入。"),
                                "batch_summary": batch_summary,
                                "date_diagnostics": date_diag.get("rows", []),
                                "upsert_summary": preflight_result.get("upsertSummary") or [],
                                "entity_audit": preflight_result.get("prepared", {}).get("entity_audit"),
                                "preflight_report": preflight_result,
                                "stage_timings": stage_timings,
                            }
                            upload_status.update(label="上傳預演發現核心口徑漂移，已停止寫入 SQLite", state="error")
                            st.rerun()

                        new_t_df = preflight_result.get("prepared", {}).get("tour", pd.DataFrame())
                        new_o_df = preflight_result.get("prepared", {}).get("others", pd.DataFrame())
                        anm_df = preflight_result.get("prepared", {}).get("anm", pd.DataFrame())
                        entity_audit = preflight_result.get("prepared", {}).get("entity_audit", {})
                        if new_t_df.empty and new_o_df.empty:
                            st.session_state["LAST_UPLOAD_AUDIT"] = {
                                "status": "error",
                                "message": "清洗後沒有任何可寫入資料；請檢查主表來源單據號、分社 prefix、排除 prefix 與副表匹配欄位。",
                                "batch_summary": batch_summary,
                                "date_diagnostics": date_diag.get("rows", []),
                                "upsert_summary": [],
                                "entity_audit": entity_audit,
                                "preflight_report": preflight_result,
                                "stage_timings": stage_timings,
                            }
                            upload_status.update(label="清洗後沒有可寫入資料", state="error")
                            st.rerun()

                        upload_status.update(label="寫入 SQLite...", state="running")
                        stage_started = time.perf_counter()
                        upsert_summary = upsert_to_db(new_t_df, new_o_df)
                        _record_stage("正式 SQLite upsert", stage_started)
                        upload_status.update(label="重建 dashboard cache（不重跑 AI）...", state="running")
                        stage_started = time.perf_counter()
                        _load_and_compute_cache(include_ai=False)
                        _record_stage("Dashboard cache 快速重建", stage_started)
                        st.session_state["PROCESSED_DATA_CACHE"]["anm"] = anm_df
                        upsert_rows = _upsert_summary_rows(upsert_summary)

                        stage_started = time.perf_counter()
                        db_after_tour, db_after_others = load_all_data_from_db()
                        db_after_max = _combined_max_date(db_after_tour, db_after_others)
                        _record_stage("寫入後 SQLite reload", stage_started)
                        stage_started = time.perf_counter()
                        stability_gate = build_phase2c_stability_gate()
                        _record_stage("Stability gate 驗證", stage_started)
                        status = "success"
                        message = (
                            f"上傳批次已寫入並重建 dashboard cache；SQLite 最新收款時間：{_fmt_date(db_after_max)}。"
                        )
                        if not contains_target_date:
                            status = "warning"
                            message = (
                                "本次清洗後批次未解析到 2026-06-15；"
                                f"批次最大收款時間：{_fmt_date(batch_max)}，SQLite 最新收款時間：{_fmt_date(db_after_max)}。"
                            )
                        elif db_before_max is not None and db_after_max is not None and db_after_max <= db_before_max:
                            status = "warning"
                            message = (
                                "本次批次包含 2026-06-15，但 SQLite 最大日期沒有前進；"
                                f"寫入前：{_fmt_date(db_before_max)}，寫入後：{_fmt_date(db_after_max)}。請檢查是否全部為既有來源單據號覆蓋。"
                            )

                        stage_started = time.perf_counter()
                        rollback_result = handle_core_drift_rollback(
                            stability_gate,
                            upsert_summary.get("backup_path") if isinstance(upsert_summary, dict) else None,
                            restore_database=restore_database_from_backup,
                            rebuild_cache=_rebuild_cache_after_database_restore,
                            build_gate=build_phase2c_stability_gate,
                        )
                        _record_stage("Rollback guard", stage_started)
                        if rollback_result["status"] == "rejected_rolled_back":
                            status = "error"
                            message = (
                                "本次上傳因核心口徑 Drift 已被拒絕；"
                                "異常資料庫已隔離，正式 SQLite 已回滾，回滾後核心口徑 2/2 matched。"
                            )
                        elif rollback_result["status"] == "rollback_failed":
                            status = "error"
                            message = (
                                "偵測到核心口徑 Drift，但自動回滾未能完成二次驗證。"
                                "請停止使用本次更新後數據並查看 Rollback Error。"
                            )

                        source_files = [
                            item.name
                            for item in [main_up, tour_up, *(oth_up or [])]
                            if item is not None and getattr(item, "name", None)
                        ]
                        history_record_id = None
                        history_error = None
                        stage_started = time.perf_counter()
                        try:
                            history_record_id = record_stability_history(
                                stability_gate,
                                {
                                    "upload_status": rollback_result.get("status", status),
                                    "upload_message": message,
                                "source_files": source_files,
                                "latest_data_date": _fmt_date(db_after_max),
                                "batch_summary": batch_summary,
                                "upsert_summary": upsert_rows,
                                    "drift_diagnosis": preflight_result.get("driftDiagnosis") or {},
                                    "monthly_baseline": stability_gate.get("monthlyBaseline") or {},
                                    "rollback_status": rollback_result.get("rollbackStatus"),
                                "backup_path": rollback_result.get("backupPath"),
                                "quarantine_path": rollback_result.get("quarantinePath"),
                                    "post_rollback_gate": rollback_result.get("postRollbackGate"),
                                    "rollback_error": rollback_result.get("rollbackError"),
                                },
                            )
                        except Exception as history_exc:
                            history_error = f"{type(history_exc).__name__}: {history_exc}"
                        _record_stage("Stability history 記錄", stage_started)
                        _record_stage("Upload total", upload_started)

                        st.session_state["LAST_UPLOAD_AUDIT"] = {
                            "status": status,
                            "message": message,
                            "batch_summary": batch_summary,
                            "date_diagnostics": date_diag.get("rows", []),
                            "upsert_summary": upsert_rows,
                            "entity_audit": entity_audit,
                            "stability_gate": stability_gate,
                            "monthly_baseline": stability_gate.get("monthlyBaseline") or {},
                            "backup_path": upsert_summary.get("backup_path") if isinstance(upsert_summary, dict) else None,
                            "source_files": source_files,
                            "history_record_id": history_record_id,
                            "history_error": history_error,
                            "rollback_status": rollback_result.get("rollbackStatus"),
                            "quarantine_path": rollback_result.get("quarantinePath"),
                            "post_rollback_gate": rollback_result.get("postRollbackGate"),
                            "rollback_error": rollback_result.get("rollbackError"),
                            "drift_diagnosis": preflight_result.get("driftDiagnosis") or {},
                            "preflight_report": preflight_result,
                            "stage_timings": stage_timings,
                        }
                        upload_status.update(label="上傳合併完成，正在刷新畫面...", state="complete")
                    st.rerun()
                except Exception:
                    _render_error("程式在清洗關聯或寫入資料庫階段出錯。", traceback.format_exc())
                finally:
                    UPLOAD_OPERATION_LOCK.release()

def _render_year_summary(s1: pd.DataFrame, s2: pd.DataFrame, year_sel: list[int], month_sel: list[str], date_rng) -> None:
    s1 = s1.copy()
    s2 = s2.copy()
    s1 = _apply_filters(s1, "日期", year_sel, month_sel, date_rng)
    s2 = _apply_filters(s2, "日期", year_sel, month_sel, date_rng)
    s1["Y"] = pd.to_datetime(s1["日期"], errors="coerce").dt.year
    s2["Y"] = pd.to_datetime(s2["日期"], errors="coerce").dt.year
    years = sorted(list(set(s1["Y"].dropna().astype(int).unique().tolist()) | set(s2["Y"].dropna().astype(int).unique().tolist())))
    if not years:
        st.info("目前沒有可供年度總覽的資料。")
        return

    _render_anchor("section-year-summary")
    _render_section("年度總覽", f"先看整體規模，再往門店與專職通路下鑽。{REVENUE_SCOPE_CAPTION}", "🌐")
    year_tabs = st.tabs([f"📅 {y} 年" for y in years])

    for tab, y_val in zip(year_tabs, years):
        with tab:
            d1, d2 = s1[s1["Y"] == y_val], s2[s2["Y"] == y_val]
            b_tot = d1[["旅行團", "郵輪", "票務"]].sum().sum()
            d_tot = d2[["旅行團", "郵輪", "票務"]].sum().sum()
            total = b_tot + d_tot
            if total <= 0:
                st.info(f"當前資料庫中無 {y_val} 年記錄。")
                continue
            m1, m2, m3 = st.columns(3)
            m1.metric("淨營收 (HKD)", f"${total:,.2f}")
            m2.metric("分社淨營收", f"${b_tot:,.2f}", f"{b_tot / total * 100:.1f}%")
            m3.metric("專職淨營收", f"${d_tot:,.2f}", f"{d_tot / total * 100:.1f}%")
            st.markdown('<div class="channel-header">🏬 分社產品矩陣</div>', unsafe_allow_html=True)
            cb1, cb2, cb3 = st.columns(3)
            cb1.metric("旅行團", f"${d1['旅行團'].sum():,.2f}", f"{d1['旅行團'].sum() / b_tot * 100:.1f}%" if b_tot else "0%")
            cb2.metric("郵輪", f"${d1['郵輪'].sum():,.2f}", f"{d1['郵輪'].sum() / b_tot * 100:.1f}%" if b_tot else "0%")
            cb3.metric("票務", f"${d1['票務'].sum():,.2f}", f"{d1['票務'].sum() / b_tot * 100:.1f}%" if b_tot else "0%")

def _render_rank_and_drilldown(
    s1: pd.DataFrame,
    t_df: pd.DataFrame,
    o_df: pd.DataFrame,
    branch_sel: str,
    sales_sel: str,
    year_sel: list[int],
    month_sel: list[str],
    date_rng,
):
    st.write("---")
    _render_anchor("section-branch-ranking")
    _render_section("門店業績排行榜", "淨口徑 / 動態篩選器；用於快速辨識門店貢獻與集中度。", "🏆")
    f_s1 = _apply_filters(s1, "日期", year_sel, month_sel, date_rng)
    if branch_sel != "全部分社":
        f_s1 = f_s1[f_s1["文本"].astype(str).str.strip() == str(branch_sel).strip()].copy()
    f_s1["總額"] = f_s1["旅行團"] + f_s1["郵輪"] + f_s1["票務"]
    rk = f_s1.groupby("文本")[["旅行團", "郵輪", "票務", "總額"]].sum().reset_index().sort_values("總額", ascending=False)
    if rk.empty:
        st.info("目前篩選條件下沒有門店排行榜資料，請放寬時間或改選其他分社。")
    else:
        total_rk = rk["總額"].sum()
        rk["貢獻佔比"] = rk["總額"] / total_rk * 100 if total_rk > 0 else 0
        rk = rk.rename(columns={"文本": "分社名稱"})
        rk.insert(0, "排名", range(1, len(rk) + 1))
        top_branch = rk.iloc[0]["分社名稱"] if not rk.empty else "—"
        top_value = float(rk.iloc[0]["總額"]) if not rk.empty else 0.0
        if isinstance(date_rng, (tuple, list)) and len(date_rng) >= 2:
            date_start, date_end = date_rng[0], date_rng[1]
        else:
            date_start = date_end = date_rng

        with st.container(border=True):
            _render_panel_header(
                "Branch Performance",
                "門店業績排行榜",
                "按目前篩選條件比較分社淨營收、產品構成與貢獻佔比。",
            )
            s1c, s2c, s3c = st.columns(3)
            s1c.metric("可見分社數", f"{len(rk):,}")
            s2c.metric("淨營收 (HKD)", f"HKD {total_rk:,.0f}")
            s3c.metric("Top 1 分社", f"{top_branch}", f"HKD {top_value:,.0f}")

            st.caption(
                f"目前篩選：年份 {', '.join(map(str, year_sel)) if year_sel else '全部'}；"
                f"月份 {', '.join(month_sel) if month_sel else '全部'}；"
                f"日期 {date_start} ~ {date_end}"
            )
            cr1, cr2 = st.columns([5, 4])
            with cr1:
                st.dataframe(
                    _style_rank_table(rk[["排名", "分社名稱", "旅行團", "郵輪", "票務", "總額", "貢獻佔比"]]),
                    hide_index=True,
                    width="stretch",
                )
            with cr2:
                st.pyplot(draw_top10_barh(rk[["分社名稱", "總額"]], "總額", "全部銷售點淨營收排行榜圖 (HKD)", theme=_chart_theme()), width="stretch")

    st.write("---")
    _render_anchor("section-product-drilldown")
    _render_section("產品佔比下鑽分析", "按營收金額或交易數量切換，分開觀察分社與專職通路。", "🧩")
    m_type = st.radio("選擇分析度量 (Measure)", ["💰 營收金額 (HKD)", "📦 交易數量 (人/套)"], horizontal=True)
    selected_col = COL_MONEY if "金額" in m_type else COL_QTY
    label = "金額" if "金額" in m_type else "數量"
    measure_label = "交易金額" if selected_col == COL_MONEY else "交易數量"

    t_df_f = _apply_filters(t_df, "統一日期", year_sel, month_sel, date_rng)
    o_df_f = _apply_filters(o_df, "統一日期", year_sel, month_sel, date_rng)
    if branch_sel != "全部分社":
        t_df_f = t_df_f[t_df_f[COL_BRANCH].astype(str).str.strip() == str(branch_sel).strip()].copy()
        o_df_f = o_df_f[o_df_f[COL_BRANCH].astype(str).str.strip() == str(branch_sel).strip()].copy()
    t_df_f["類"] = t_df_f.apply(lambda r: map_dest_category(r, st.session_state["CRUISE_DEPTS"]), axis=1)
    o_df_f["類"] = o_df_f.apply(map_ticket_category, axis=1)
    o_df_f = o_df_f[o_df_f["類"].notna()]

    b_t = t_df_f[t_df_f[COL_BRANCH] != TARGET_DEPT_FOR_REP].copy()
    b_o = o_df_f[o_df_f[COL_BRANCH] != TARGET_DEPT_FOR_REP].copy()
    s_t = t_df_f[t_df_f[COL_BRANCH] == TARGET_DEPT_FOR_REP].copy()
    s_o = o_df_f[o_df_f[COL_BRANCH] == TARGET_DEPT_FOR_REP].copy()

    cp1, cp2 = st.columns(2)
    with cp1:
        with st.container(border=True):
            _render_panel_header("Branch Channel", "分社通路視角", "比較分社端旅行團線路與票務構成。")
            branch_drill = st.radio("分社下鑽切換", ["線路種類", "票務構成"], horizontal=True, key="branch_drill_mode")
            title = branch_sel if branch_sel != "全部分社" else "全部分社"
            if b_t.empty and b_o.empty:
                st.info("目前篩選條件下，分社通路沒有可顯示資料。")
            else:
                if branch_drill == "線路種類":
                    branch_t = b_t.groupby("類")[selected_col].sum().sort_values(ascending=False)
                    branch_df = pd.DataFrame(
                        {
                            "分類": branch_t.index.astype(str),
                            measure_label: branch_t.values,
                        }
                    )
                    branch_df["佔比"] = branch_df[measure_label] / branch_df[measure_label].sum() * 100 if branch_df[measure_label].sum() else 0
                    st.pyplot(safe_draw_pie(branch_t, f"{title} | 旅行團線路種類深鑽 ({label})", theme=_chart_theme()), width="stretch")
                    st.dataframe(
                        _style_mix_table(branch_df, measure_label),
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    branch_o = b_o.groupby("類")[selected_col].sum().sort_values(ascending=False)
                    branch_df = pd.DataFrame(
                        {
                            "分類": branch_o.index.astype(str),
                            measure_label: branch_o.values,
                        }
                    )
                    branch_df["佔比"] = branch_df[measure_label] / branch_df[measure_label].sum() * 100 if branch_df[measure_label].sum() else 0
                    st.pyplot(safe_draw_pie(branch_o, f"{title} | 票務構成深鑽 ({label})", theme=_chart_theme()), width="stretch")
                    st.dataframe(
                        _style_mix_table(branch_df, measure_label),
                        hide_index=True,
                        width="stretch",
                    )

    with cp2:
        with st.container(border=True):
            _render_panel_header("Specialist Channel", "專職通路視角", "比較專職銷售組的旅行團線路與票務構成。")
            sales_drill = st.radio("專職下鑽切換", ["線路種類", "票務構成"], horizontal=True, key="sales_drill_mode")
            if sales_sel != "全部銷售組":
                s_t = s_t[s_t[COL_SALESPERSON].astype(str).str.strip() == str(sales_sel).strip()].copy()
                s_o = s_o[s_o[COL_SALESPERSON].astype(str).str.strip() == str(sales_sel).strip()].copy()
            title = sales_sel if sales_sel != "全部銷售組" else "全部銷售組"
            if s_t.empty and s_o.empty:
                st.info("目前篩選條件下，專職通路沒有可顯示資料。")
            else:
                if sales_drill == "線路種類":
                    sales_t = s_t.groupby("類")[selected_col].sum().sort_values(ascending=False)
                    sales_df = pd.DataFrame(
                        {
                            "分類": sales_t.index.astype(str),
                            measure_label: sales_t.values,
                        }
                    )
                    sales_df["佔比"] = sales_df[measure_label] / sales_df[measure_label].sum() * 100 if sales_df[measure_label].sum() else 0
                    st.pyplot(safe_draw_pie(sales_t, f"{title} | 旅行團線路種類深鑽 ({label})", theme=_chart_theme()), width="stretch")
                    st.dataframe(
                        _style_mix_table(sales_df, measure_label),
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    sales_o = s_o.groupby("類")[selected_col].sum().sort_values(ascending=False)
                    sales_df = pd.DataFrame(
                        {
                            "分類": sales_o.index.astype(str),
                            measure_label: sales_o.values,
                        }
                    )
                    sales_df["佔比"] = sales_df[measure_label] / sales_df[measure_label].sum() * 100 if sales_df[measure_label].sum() else 0
                    st.pyplot(safe_draw_pie(sales_o, f"{title} | 票務構成深鑽 ({label})", theme=_chart_theme()), width="stretch")
                    st.dataframe(
                        _style_mix_table(sales_df, measure_label),
                        hide_index=True,
                        width="stretch",
                    )

def _render_data_quality_scorecard(cache: dict) -> None:
    scorecard = _compute_data_quality_scorecard(cache)
    overall_score = scorecard.get("overall_score")
    overall_health = str(scorecard.get("overall_health", "不適用"))
    health_class = _health_badge_class(
        {
            "優秀": "優秀",
            "可接受": "可接受",
            "需關注": "可參考",
            "需處理": "需謹慎",
        }.get(overall_health, "未評估")
    )

    _render_anchor("section-data-quality")
    _render_section(
        "Data Quality Scorecard：資料品質健康檢查",
        f"只讀診斷層；基於 SQLite 原始明細與正式口徑 {REVENUE_SCOPE_LABEL} 計算，不影響 AI Forecast、WAPE 或正式 Export。",
        "🧭",
    )
    with st.container(border=True):
        _render_panel_header(
            "Data Quality",
            "可信數據供應鏈 Scorecard",
            "分數用來定位資料風險與清洗優先級，不代表模型準確率，也不替代財務對帳。",
        )
        _render_role_badges(
            [
                ("診斷", "只讀派生視角", "nbs-badge-info"),
                ("口徑", REVENUE_SCOPE_LABEL, "nbs-badge-success"),
                ("不影響", "SQLite / AI / WAPE / Export", "nbs-badge-muted"),
            ]
        )
        q1, q2, q3, q4 = st.columns(4)
        q1.metric(
            "Overall Score",
            f"{float(overall_score):.1f}" if overall_score is not None and pd.notna(overall_score) else "—",
            overall_health,
        )
        q2.metric("最新收款日期", str(scorecard.get("latest_date", "不適用")), "以主表收款時間為準")
        missing_days = scorecard.get("missing_days")
        q3.metric("缺失日期", f"{int(missing_days):,} 天" if missing_days is not None and pd.notna(missing_days) else "—", "最早至最新日期區間")
        q4.metric(
            "排除金額占比",
            f"{float(scorecard.get('excluded_amount_rate', 0) or 0):.2%}",
            REVENUE_SCOPE_LABEL,
        )
        st.markdown(
            f'<div style="margin:0.2rem 0 0.8rem 0"><span class="nbs-badge {health_class}">資料品質燈號：{escape(overall_health)}</span></div>',
            unsafe_allow_html=True,
        )
        st.dataframe(_style_quality_table(scorecard["dimension_summary"]), hide_index=True, width="stretch")

        with st.expander("查看 Data Quality 詳細診斷表", expanded=False):
            st.markdown("###### Scorecard Overview")
            st.dataframe(_style_quality_table(scorecard["overview"]), hide_index=True, width="stretch")
            st.markdown("###### Field Completeness")
            st.dataframe(_style_quality_table(scorecard["field_completeness"]), hide_index=True, width="stretch")
            st.markdown("###### Date Coverage")
            st.dataframe(_style_quality_table(scorecard["date_coverage"]), hide_index=True, width="stretch")
            st.markdown("###### Entity Resolution")
            st.dataframe(_style_quality_table(scorecard["entity_resolution"]), hide_index=True, width="stretch")
            st.markdown("###### Official Scope Health")
            st.dataframe(_style_quality_table(scorecard["official_scope"]), hide_index=True, width="stretch")
            st.markdown("###### Amount Health")
            st.dataframe(_style_quality_table(scorecard["amount_health"]), hide_index=True, width="stretch")

        st.download_button(
            "📥 下載 Data Quality Scorecard",
            _build_data_quality_workbook(scorecard),
            "Data_Quality_Scorecard_不含掛賬核銷與TT退款轉團款.xlsx",
            width="stretch",
        )

def _render_entity_resolution_audit(cache: dict) -> None:
    entity_audit = _current_entity_resolution_audit(cache)
    match_rate = entity_audit.get("match_rate")
    unmatched_rows = int(entity_audit.get("unmatched_rows", 0) or 0)
    duplicate_rows = int(entity_audit.get("duplicate_rows", 0) or 0)

    _render_anchor("section-entity-audit")
    _render_section(
        "Entity Resolution Audit：單號匹配稽核",
        "把財務主表來源單據號與副表交易號碼的匹配健康變成可追查視角；副表-only 不會被寫入正式營收。",
        "🔎",
    )
    with st.container(border=True):
        _render_panel_header(
            "Entity Resolution",
            "來源單據號 / 交易號碼匹配健康",
            "此區只做稽核與下載，不改 SQLite、不改正式口徑、不改 AI Forecast。",
        )
        _render_role_badges(
            [
                ("稽核", "單號匹配可追查", "nbs-badge-info"),
                ("正式口徑", "仍以主表收款時間與來源單據號為準", "nbs-badge-success"),
                ("副表-only", "不進正式營收", "nbs-badge-warning"),
            ]
        )
        e1, e2, e3, e4 = st.columns(4)
        e1.metric("匹配健康率", f"{float(match_rate or 0):.2%}", "非未匹配行數 / 已落地行數")
        e2.metric("主表未匹配行數", f"{unmatched_rows:,}", "保留正式營收，但副表資訊不足")
        e3.metric("副表-only 單號", "批次上傳時顯示", "不回推正式營收")
        e4.metric("重複單號筆數", f"{duplicate_rows:,}", "需按業務語境判斷")

        st.markdown("###### 匹配總覽")
        st.dataframe(_style_entity_audit_table(entity_audit["summary"]), hide_index=True, width="stretch")
        with st.expander("查看 Entity Resolution 詳細稽核表", expanded=False):
            st.markdown("###### 來源拆解")
            st.dataframe(_style_entity_audit_table(entity_audit["source_breakdown"]), hide_index=True, width="stretch")
            st.markdown("###### 重複單號明細")
            st.dataframe(_style_entity_audit_table(entity_audit["duplicate_detail"]), hide_index=True, width="stretch")
            st.markdown("###### 未匹配明細")
            st.dataframe(_style_entity_audit_table(entity_audit["unmatched_detail"]), hide_index=True, width="stretch")
            st.markdown("###### ID 清洗樣本")
            st.dataframe(_style_entity_audit_table(entity_audit["id_cleaning_samples"]), hide_index=True, width="stretch")

        st.download_button(
            "📥 下載 Entity Resolution Audit",
            _build_entity_resolution_workbook(entity_audit),
            "Entity_Resolution_Audit_不含掛賬核銷與TT退款轉團款.xlsx",
            width="stretch",
        )

def _render_ai_cleaning_suggestions(cache: dict) -> None:
    ai_cleaning = _compute_ai_cleaning_suggestions(cache)
    suggestions = ai_cleaning.get("suggestions", pd.DataFrame()).copy()
    actionable_count = int(suggestions["可落規則"].sum()) if not suggestions.empty and "可落規則" in suggestions.columns else 0
    observation_count = int(len(suggestions) - actionable_count)
    avg_confidence = pd.to_numeric(suggestions.get("信心分", pd.Series(dtype=float)), errors="coerce").mean() if not suggestions.empty else np.nan

    _render_anchor("section-ai-cleaning")
    _render_section(
        "AI-assisted Data Cleaning：智能清洗建議",
        "本地智能規則只生成建議，不接外部 LLM、不外傳資料；必須人工勾選確認後才會寫入現有業務規則。",
        "🧠",
    )
    with st.container(border=True):
        _render_panel_header(
            "Human-in-the-loop Cleaning",
            "建議先進 inbox，確認後才落 rules_config.json",
            "此區不改 SQLite、不改已入庫資料、不改正式營收口徑；低信心建議只作觀察與下載。",
        )
        _render_role_badges(
            [
                ("本地", "不接外部 LLM / API", "nbs-badge-info"),
                ("人工確認", "勾選後才套用", "nbs-badge-success"),
                ("安全邊界", "只更新既有規則類型", "nbs-badge-warning"),
            ]
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("建議總數", f"{len(suggestions):,}", "本地規則派生")
        c2.metric("可一鍵套用", f"{actionable_count:,}", "仍需人工勾選")
        c3.metric("觀察級", f"{observation_count:,}", "不允許直接落規則")
        c4.metric("平均信心分", f"{float(avg_confidence):.1f}" if not pd.isna(avg_confidence) else "—", "非準確率")

        st.dataframe(_style_quality_table(ai_cleaning["metrics"]), hide_index=True, width="stretch")

        if suggestions.empty:
            st.info("目前無足夠證據生成高信心智能清洗建議。你仍可下載 metrics 留作稽核紀錄。")
        else:
            st.markdown("###### Suggestion Inbox")
            display_cols = [
                "套用",
                "建議類型",
                "候選值",
                "建議名稱",
                "證據來源",
                "影響行數",
                "信心分",
                "信心等級",
                "風險",
                "建議動作",
                "可落規則",
                "為什麼建議",
            ]
            editor_df = suggestions[[c for c in display_cols if c in suggestions.columns]].copy()
            edited = st.data_editor(
                editor_df,
                key="AI_CLEANING_SUGGESTION_EDITOR",
                width="stretch",
                hide_index=True,
                disabled=[c for c in editor_df.columns if c not in {"套用", "建議名稱"}],
                column_config={
                    "套用": st.column_config.CheckboxColumn("套用", help="勾選後才會進入套用預覽。觀察級建議即使勾選也不會寫入。"),
                    "建議名稱": st.column_config.TextColumn("建議名稱", help="BRANCH_MAPPING 需要在這裡填入分社名稱。"),
                    "可落規則": st.column_config.CheckboxColumn("可落規則"),
                    "信心分": st.column_config.NumberColumn("信心分", format="%.1f"),
                },
            )
            selected = edited[edited.get("套用", False).astype(bool)].copy() if "套用" in edited.columns else pd.DataFrame()
            if not selected.empty:
                st.markdown("###### 套用前 Preview")
                st.dataframe(
                    _style_ai_cleaning_table(selected[["建議類型", "候選值", "建議名稱", "影響行數", "信心分", "風險", "可落規則"]]),
                    hide_index=True,
                    width="stretch",
                )
                st.warning("按下套用後，只會更新 rules_config.json 的既有規則欄位；不會改 SQLite 或既有已入庫資料。")
            apply_disabled = selected.empty
            if st.button("套用已選建議到業務規則", type="primary", disabled=apply_disabled, width="stretch"):
                applied, skipped = _apply_ai_cleaning_suggestions(selected)
                if applied:
                    st.session_state["AI_CLEANING_APPLY_NOTICE"] = {
                        "applied": applied,
                        "skipped": skipped,
                    }
                    st.rerun()
                else:
                    st.warning("沒有任何建議被套用。")
                    if skipped:
                        st.write("未套用原因：")
                        st.write(skipped)

        notice = st.session_state.pop("AI_CLEANING_APPLY_NOTICE", None)
        if notice:
            st.success(f"已套用 {len(notice.get('applied', []))} 條建議到 {CONFIG_FILE}。Dashboard cache 已清空，請重新載入資料。")
            if notice.get("applied"):
                st.write("已套用：")
                st.write(notice["applied"])
            if notice.get("skipped"):
                st.write("未套用：")
                st.write(notice["skipped"])

        with st.expander("查看建議規則定義與安全邊界", expanded=False):
            st.dataframe(ai_cleaning["definitions"], hide_index=True, width="stretch")
            if not suggestions.empty:
                readonly_cols = [c for c in suggestions.columns if c != "套用"]
                st.markdown("###### 全部建議明細")
                st.dataframe(_style_ai_cleaning_table(suggestions[readonly_cols]), hide_index=True, width="stretch")

        st.download_button(
            "📥 下載 AI-assisted Data Cleaning 建議",
            _build_ai_cleaning_suggestions_workbook(ai_cleaning),
            "AI_Assisted_Data_Cleaning_智能清洗建議_不含掛賬核銷與TT退款轉團款.xlsx",
            width="stretch",
        )

def _render_forecast_governance(cache: dict) -> None:
    governance = _compute_forecast_governance(cache)
    summary_df = governance.get("summary", pd.DataFrame())
    matrix_df = governance.get("matrix", pd.DataFrame())
    recommendations_df = governance.get("recommendations", pd.DataFrame())
    definitions_df = governance.get("definitions", pd.DataFrame())

    _render_anchor("section-forecast-governance")
    _render_section(
        "Forecast Governance：模型健康治理",
        "只讀治理層；綜合 Accuracy、Bias、Stability、Sample 與模型角色，不取代 WAPE，也不改正式 Forecast 或權重。",
        "🧭",
    )
    with st.container(border=True):
        _render_panel_header(
            "Forecast Governance",
            "Daily / 7-Day / Month-End 模型健康總覽",
            f"治理分數由現有回測與宏觀回測派生；正式口徑：{REVENUE_SCOPE_LABEL}。",
        )
        _render_role_badges(
            [
                ("只讀", "不影響正式 Forecast", "nbs-badge-info"),
                ("治理", "不只看 WAPE", "nbs-badge-success"),
                ("分層", "正式 / 診斷 / 實驗", "nbs-badge-muted"),
            ]
        )
        overall_score = governance.get("overall_score")
        overall_health = governance.get("overall_health", "未評估")
        daily_value, daily_detail = _governance_summary_value(summary_df, "Daily Forecast")
        seven_value, seven_detail = _governance_summary_value(summary_df, "7-Day Macro")
        month_value, month_detail = _governance_summary_value(summary_df, "Month-End Macro")
        g1, g2, g3, g4 = st.columns(4)
        g1.metric(
            "Overall Forecast Governance",
            f"{overall_health} {float(overall_score):.1f}" if overall_score is not None and pd.notna(overall_score) else "未評估",
            "治理分數，不是準確率",
        )
        g2.metric("Daily Official Health", daily_value, daily_detail)
        g3.metric("7-Day Macro Health", seven_value, seven_detail)
        g4.metric("Month-End Macro Health", month_value, month_detail)

        if summary_df.empty:
            st.info("目前沒有足夠 backtest / macro backtest 結果可產生 Forecast Governance。")
            return

        st.markdown("###### Governance Summary")
        st.dataframe(_style_governance_table(summary_df), hide_index=True, width="stretch")
        with st.expander("查看 Model Health Matrix / Action Recommendations", expanded=False):
            st.markdown("###### Model Health Matrix")
            st.dataframe(_style_governance_table(matrix_df), hide_index=True, width="stretch")
            st.markdown("###### Action Recommendations")
            st.dataframe(recommendations_df, hide_index=True, width="stretch")
            st.markdown("###### Metric Definitions")
            st.dataframe(definitions_df, hide_index=True, width="stretch")

        st.download_button(
            "📥 下載 Forecast Governance 模型健康治理",
            _build_forecast_governance_workbook(governance),
            "Forecast_Governance_模型健康治理_不含掛賬核銷與TT退款轉團款.xlsx",
            width="stretch",
        )

def _render_feature_store_lead_signals(cache: dict) -> None:
    feature_store = _compute_feature_store_lead_signals(cache)
    catalog_df = feature_store.get("catalog", pd.DataFrame())
    snapshot_df = feature_store.get("snapshot", pd.DataFrame())
    health_df = feature_store.get("health", pd.DataFrame())
    readiness_df = feature_store.get("readiness", pd.DataFrame())
    overview_df = feature_store.get("overview", pd.DataFrame())
    leak_ok = feature_store.get("no_future_leak_ok", True)

    _render_anchor("section-feature-store")
    _render_section(
        "Feature Store / Lead Signal：預測特徵與先行信號庫",
        "只讀派生視角；整理 Daily WAPE、spike/event signal 與日曆特徵，為後續降低 Daily WAPE 做可稽核準備。",
        "🧬",
    )
    with st.container(border=True):
        _render_panel_header(
            "Feature Store v1",
            "Daily Forecast 特徵治理與先行信號稽核",
            f"所有特徵都必須遵守 NoFutureLeak；正式口徑：{REVENUE_SCOPE_LABEL}。",
        )
        _render_role_badges(
            [
                ("只讀", "不改模型權重", "nbs-badge-info"),
                ("NoFutureLeak", "FeatureMaxDate <= Cutoff", "nbs-badge-success" if leak_ok else "nbs-badge-danger"),
                ("用途", "Daily WAPE 長期優化", "nbs-badge-muted"),
            ]
        )
        latest_feature_date = "未評估"
        if not overview_df.empty:
            hit = overview_df[overview_df["指標"].eq("最新特徵日期")]
            if not hit.empty:
                latest_feature_date = str(hit.iloc[0].get("數值", "未評估"))
        leak_rate = np.nan
        if not health_df.empty and "NoFutureLeakPassRate" in health_df.columns:
            leak_rate = pd.to_numeric(health_df["NoFutureLeakPassRate"], errors="coerce").dropna().min()
        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Feature Catalog", f"{len(catalog_df):,}", "特徵 / lead signal 條目")
        f2.metric("Lead Snapshot", f"{len(snapshot_df):,}", "最近 cutoff 樣本")
        f3.metric("NoFutureLeak", f"{leak_rate:.1f}%" if pd.notna(leak_rate) else "未評估", "低於 100% 需稽核")
        f4.metric("最新特徵日期", latest_feature_date, "FeatureMaxDate")

        if not leak_ok:
            st.warning("存在 NoFutureLeak = False 的特徵 snapshot。這些結果只顯示警告，不會阻斷 dashboard，但不應接入正式模型。")
        if catalog_df.empty:
            st.info("目前沒有可展示的 Feature Catalog。")
            return
        st.markdown("###### Feature Catalog")
        st.dataframe(catalog_df, hide_index=True, width="stretch")
        with st.expander("查看 Lead Signal Snapshot / Health / Readiness", expanded=False):
            st.markdown("###### Daily Lead Signal Snapshot")
            if snapshot_df.empty:
                st.info("目前沒有 Daily lead signal snapshot；可能尚未產生 AI backtest cache。")
            else:
                st.dataframe(_style_feature_table(snapshot_df), hide_index=True, width="stretch")
            st.markdown("###### Lead Signal Health")
            st.dataframe(_style_feature_table(health_df), hide_index=True, width="stretch")
            st.markdown("###### Model Readiness Matrix")
            st.dataframe(_style_feature_table(readiness_df), hide_index=True, width="stretch")

        st.download_button(
            "📥 下載 Feature Store / Lead Signal",
            _build_feature_store_workbook(feature_store),
            "Feature_Store_Lead_Signal_不含掛賬核銷與TT退款轉團款.xlsx",
            width="stretch",
        )

def _render_causal_driver_analytics(cache: dict) -> None:
    causal = _compute_causal_driver_analytics(cache)
    change_summary_df = causal.get("change_summary", pd.DataFrame())
    top_drivers_df = causal.get("top_drivers", pd.DataFrame())
    event_window_df = causal.get("event_window", pd.DataFrame())
    order_concentration_df = causal.get("order_concentration", pd.DataFrame())
    reconciliation_df = causal.get("reconciliation", pd.DataFrame())
    drivers = causal.get("drivers", {})

    _render_anchor("section-causal-analytics")
    _render_section(
        "Causal Analytics：營收變動解釋",
        "v1 是解釋型 driver analytics，用資料拆解收入變動來源；不是嚴格因果結論。",
        "🔎",
    )
    with st.container(border=True):
        _render_panel_header(
            "Driver Analytics v1",
            "從「預測」走向「解釋」",
            f"比較目前期間與前一期間，按產品線、通路、分社、目的地與事件窗口拆解；正式口徑：{REVENUE_SCOPE_LABEL}。",
        )
        _render_role_badges(
            [
                ("解釋型", "不是嚴格因果", "nbs-badge-warning"),
                ("可對帳", "Driver delta reconciles", "nbs-badge-success"),
                ("只讀", "不改 Forecast / WAPE", "nbs-badge-info"),
            ]
        )
        if change_summary_df.empty:
            st.info("目前沒有足夠正式口徑資料可產生 Causal Analytics。")
            return

        current_amount = _change_summary_value(change_summary_df, "目前期間收入")
        baseline_amount = _change_summary_value(change_summary_df, "比較期間收入")
        delta_amount = _change_summary_value(change_summary_df, "收入差額")
        period_text = _change_summary_value(change_summary_df, "目前期間")
        top_driver_text = "未評估"
        if not top_drivers_df.empty:
            top = top_drivers_df.sort_values("Delta", key=lambda s: s.abs(), ascending=False).iloc[0]
            top_driver_text = f"{top.get('Dimension', '')} / {top.get('Driver', '')}"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("目前期間收入", f"HKD {float(current_amount):,.0f}" if pd.notna(current_amount) else "未評估", str(period_text))
        c2.metric("比較期間收入", f"HKD {float(baseline_amount):,.0f}" if pd.notna(baseline_amount) else "未評估", "前一期間")
        c3.metric("收入差額", f"HKD {float(delta_amount):,.0f}" if pd.notna(delta_amount) else "未評估", "Current - Baseline")
        c4.metric("最大變動 Driver", top_driver_text, "按絕對差額排序")

        st.caption("固定說明：這是解釋型分析，不是嚴格因果結論；同一維度內 Delta 可對帳到總差額，不同維度不可互相相加。")
        st.markdown("###### Change Summary")
        st.dataframe(_coerce_arrow_safe_display_frame(change_summary_df), hide_index=True, width="stretch")
        st.markdown("###### Top Driver Contribution")
        st.dataframe(_style_causal_table(top_drivers_df), hide_index=True, width="stretch")

        with st.expander("查看 Driver Contribution / Event Window / Reconciliation", expanded=False):
            for dim, title in [
                ("ProductLine", "產品線 Driver"),
                ("Channel", "通路 Driver"),
                (COL_BRANCH, "分社 Driver"),
                (COL_SALESPERSON, "銷售員 Driver"),
                ("目的地大類", "目的地大類 Driver"),
                ("來源報表標籤", "來源標籤 Driver"),
            ]:
                table = drivers.get(dim, pd.DataFrame())
                if not table.empty:
                    st.markdown(f"###### {title}")
                    st.dataframe(_style_causal_table(table.head(20)), hide_index=True, width="stretch")
            st.markdown("###### Event Window Explanation")
            st.dataframe(_style_causal_table(event_window_df), hide_index=True, width="stretch")
            st.markdown("###### 大單集中度")
            st.dataframe(_style_causal_table(order_concentration_df), hide_index=True, width="stretch")
            st.markdown("###### Reconciliation")
            st.dataframe(_style_causal_table(reconciliation_df), hide_index=True, width="stretch")

        st.download_button(
            "📥 下載 Causal Analytics Driver Explanation",
            _build_causal_analytics_workbook(causal),
            "Causal_Analytics_Driver_Explanation_不含掛賬核銷與TT退款轉團款.xlsx",
            width="stretch",
        )

def _render_backtest_report(cache: dict) -> None:
    _render_anchor("section-model-diagnostics")
    _render_section("Model Diagnostics：模型回測評分", None, "🧪")
    st.caption(
        f"回測基礎：{REVENUE_SCOPE_LABEL}；每個預測天期各取最近 30 個可驗證 cutoff，按 1 / 7 / 30 日預測天期計算 WAPE、MAPE、SMAPE、MedianAPE、MAE 與 Bias。"
        "WAPE 作為主要營運判斷，MAPE 作為單日百分比誤差輔助。"
        "為保持看板互動速度，回測中的 Prophet 欄使用同一趨勢邏輯的快速軌；正式 30 天預測仍會在可用時使用 Prophet。"
    )
    st.caption("模型健康燈號：WAPE < 10% = 優秀；10%-20% = 可接受；20%-30% = 可參考；> 30% = 需謹慎。")

    report = cache.get("bt")
    err = cache.get("bt_err")
    macro_report = cache.get("bt_macro")
    macro_err = cache.get("bt_macro_err")
    if not report:
        st.info(f"暫未產生回測評分：{err or '歷史資料不足或模型無可比較實際值'}")
        return

    summary_df = report.get("summary", pd.DataFrame())
    weights_df = report.get("weights", pd.DataFrame())
    detail_df = report.get("detail", pd.DataFrame())
    strategy_df = report.get("strategy_comparison", pd.DataFrame())
    daily_wape_baseline_df = report.get("daily_wape_baseline", pd.DataFrame())
    daily_normal_summary_df = report.get("daily_normal_day_experiment_summary", pd.DataFrame())
    daily_normal_detail_df = report.get("daily_normal_day_experiment_detail", pd.DataFrame())
    daily_two_lane_summary_df = report.get("daily_two_lane_selector_summary", pd.DataFrame())
    daily_two_lane_detail_df = report.get("daily_two_lane_selector_detail", pd.DataFrame())
    daily_event_summary_df = report.get("daily_event_lead_signal_summary", pd.DataFrame())
    if summary_df.empty:
        st.info("暫未產生回測評分：目前沒有足夠模型結果可比較。")
        return
    _render_forecast_governance(cache)
    _render_feature_store_lead_signals(cache)
    _render_causal_driver_analytics(cache)
    summary_display = _add_health_column(summary_df, "WAPE")
    weights_display = _add_health_column(weights_df, "策略最佳WAPE", "策略健康燈號")
    strategy_display = _add_health_column(strategy_df, "最佳WAPE", "策略健康燈號")
    daily_normal_display = _add_health_column(daily_normal_summary_df, "WAPE")
    daily_two_lane_display = _add_health_column(daily_two_lane_summary_df, "WAPE")

    one_day = summary_df[(summary_df["預測天期"] == 1) & (summary_df["模型"] != "Fusion")].copy()
    primary_metric = "WAPE" if "WAPE" in summary_df.columns else "MAPE"
    best_short = one_day.sort_values(primary_metric).iloc[0] if not one_day.empty else None
    fusion_1 = summary_df[(summary_df["預測天期"] == 1) & (summary_df["模型"] == "Fusion")].copy()
    fusion_1 = fusion_1.sort_values(primary_metric) if not fusion_1.empty else fusion_1
    fusion_primary_error = (
        float(fusion_1[primary_metric].iloc[0])
        if not fusion_1.empty and pd.notna(fusion_1[primary_metric].iloc[0])
        else None
    )
    first_weights = weights_df.iloc[0] if not weights_df.empty else None
    short_health = _model_health_label(best_short[primary_metric]) if best_short is not None and pd.notna(best_short[primary_metric]) else "未評估"

    with st.container(border=True):
        _render_panel_header(
            "Official Model Backtest",
            "Daily Current Models",
            "正式 Daily 模型回測與權重建議；此區是目前 dashboard 判斷 Daily 模型健康的主口徑。",
        )
        _render_role_badges(
            [
                ("正式", "Daily Backtest / Suggested Weights", "nbs-badge-info"),
                ("口徑", REVENUE_SCOPE_LABEL, "nbs-badge-muted"),
            ]
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "最佳短期模型",
            f"{best_short['策略']} / {best_short['模型']}" if best_short is not None and "策略" in best_short.index else (str(best_short["模型"]) if best_short is not None else "—"),
            f"{primary_metric} {float(best_short[primary_metric]):.2f}%" if best_short is not None and pd.notna(best_short[primary_metric]) else None,
        )
        m2.metric("Daily 模型健康", short_health, "以 1 日最佳 WAPE 判斷")
        m3.metric(f"Fusion 1 日 {primary_metric}", f"{fusion_primary_error:.2f}%" if fusion_primary_error is not None else "—")
        if first_weights is not None:
            m4.metric(
                "短期建議權重",
                f"A {first_weights['ARIMA']:.0f}% / P {first_weights['Prophet']:.0f}% / L {first_weights['LightGBM']:.0f}%",
                f"{first_weights.get('推薦策略', '—')} / {first_weights['權重版本']}",
            )
        else:
            m4.metric("短期建議權重", "—")

        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("###### 回測評分表")
            st.dataframe(_style_backtest_table(summary_display), hide_index=True, width="stretch")
        with c2:
            st.markdown("###### 自動權重建議")
            st.dataframe(_style_backtest_table(weights_display), hide_index=True, width="stretch")
            if not strategy_display.empty:
                st.markdown("###### 策略比較")
                st.dataframe(_style_backtest_table(strategy_display), hide_index=True, width="stretch")

        with st.expander("查看 rolling backtest 明細", expanded=False):
            st.dataframe(detail_df, hide_index=True, width="stretch")

    if not daily_wape_baseline_df.empty or not daily_normal_display.empty or not daily_two_lane_display.empty:
        _render_anchor("section-daily-wape")
        _render_section("Daily WAPE 診斷與改良模型", None, "🩺")
        with st.container(border=True):
            _render_panel_header(
                "Daily Diagnostics",
                "正式 / 診斷 / 實驗模型分層",
                "Normal-Day Tight Guardrail 是常規日診斷最佳模型；Two-Lane Selector 仍未打贏正式 Daily 最佳 WAPE，因此不覆蓋正式 Daily Forecast。",
            )
            _render_role_badges(
                [
                    ("正式", "Daily Official WAPE", "nbs-badge-info"),
                    ("診斷", "Normal-Day Tight Guardrail", "nbs-badge-warning"),
                    ("實驗", "Two-Lane Selector", "nbs-badge-muted"),
                ]
            )

            official_row = None
            normal_baseline_row = None
            if not daily_wape_baseline_df.empty and "基準指標" in daily_wape_baseline_df.columns:
                official_match = daily_wape_baseline_df[daily_wape_baseline_df["基準指標"] == "Official All Days WAPE"]
                normal_match = daily_wape_baseline_df[daily_wape_baseline_df["基準指標"] == "Normal Days WAPE"]
                official_row = official_match.iloc[0] if not official_match.empty else None
                normal_baseline_row = normal_match.iloc[0] if not normal_match.empty else None
            normal_best_row = _best_metric_row(daily_normal_summary_df)
            two_lane_best_row = _best_metric_row(daily_two_lane_summary_df)

            d1, d2, d3, d4 = st.columns(4)
            official_wape = float(official_row["WAPE"]) if official_row is not None and pd.notna(official_row.get("WAPE")) else None
            normal_base_wape = float(normal_baseline_row["WAPE"]) if normal_baseline_row is not None and pd.notna(normal_baseline_row.get("WAPE")) else None
            normal_best_wape = float(normal_best_row["WAPE"]) if normal_best_row is not None and pd.notna(normal_best_row.get("WAPE")) else None
            two_lane_wape = float(two_lane_best_row["WAPE"]) if two_lane_best_row is not None and pd.notna(two_lane_best_row.get("WAPE")) else None
            d1.metric(
                "Daily Official WAPE",
                f"{official_wape:.2f}%" if official_wape is not None else "—",
                "正式全量口徑",
            )
            d2.metric(
                "Normal Days 最佳",
                f"{normal_best_wape:.2f}%" if normal_best_wape is not None else "—",
                (
                    f"較基準降 {normal_base_wape - normal_best_wape:.2f}pt"
                    if normal_base_wape is not None and normal_best_wape is not None
                    else "常規日診斷"
                ),
            )
            d3.metric(
                "Two-Lane 實驗",
                f"{two_lane_wape:.2f}%" if two_lane_wape is not None else "—",
                (
                    f"較正式 {'降' if official_wape - two_lane_wape >= 0 else '升'} {abs(official_wape - two_lane_wape):.2f}pt"
                    if official_wape is not None and two_lane_wape is not None
                    else "獨立 backtest"
                ),
            )
            d4.metric(
                "接入狀態",
                "診斷展示",
                "不覆蓋正式 Daily Forecast",
            )

            if not daily_wape_baseline_df.empty:
                st.markdown("###### Daily Robust WAPE 基準")
                st.dataframe(_style_backtest_table(daily_wape_baseline_df), hide_index=True, width="stretch")
            if not daily_normal_display.empty:
                st.markdown("###### Daily Normal-Day 改良模型")
                st.dataframe(_style_backtest_table(daily_normal_display), hide_index=True, width="stretch")
            if not daily_two_lane_display.empty:
                st.markdown("###### Daily Two-Lane Selector 實驗")
                st.dataframe(_style_backtest_table(daily_two_lane_display), hide_index=True, width="stretch")
            with st.expander("查看 Daily 診斷 / 改良模型明細", expanded=False):
                if not daily_normal_detail_df.empty:
                    st.markdown("**Normal-Day detail**")
                    st.dataframe(daily_normal_detail_df, hide_index=True, width="stretch")
                if not daily_two_lane_detail_df.empty:
                    st.markdown("**Two-Lane detail**")
                    st.dataframe(daily_two_lane_detail_df, hide_index=True, width="stretch")
                if not daily_event_summary_df.empty:
                    st.markdown("**Event lead signal summary**")
                    st.dataframe(_style_backtest_table(daily_event_summary_df), hide_index=True, width="stretch")

    macro_summary_display = pd.DataFrame()
    macro_detail_df = pd.DataFrame()
    if macro_report:
        macro_summary_df = macro_report.get("summary", pd.DataFrame())
        macro_detail_df = macro_report.get("detail", pd.DataFrame())
        macro_summary_display = _add_health_column(macro_summary_df, "WAPE")
        seven_wape, seven_health, seven_label = _best_macro_metric(macro_report, "7-Day Macro")
        month_wape, month_health, month_label = _best_macro_metric(macro_report, "Month-End Macro")

        _render_anchor("section-macro-backtest")
        _render_section("週 / 月宏觀回測", None, "📈")
        with st.container(border=True):
            _render_panel_header(
                "Macro Backtest",
                "7-Day / Month-End Macro 獨立評估",
                "7-Day Macro 是未來 7 日總額，不是自然週；Month-End Macro 是 MTD + 本月剩餘天數預測，不是簡單未來 30 日加總。",
            )
            _render_role_badges(
                [
                    ("宏觀", "獨立展示", "nbs-badge-info"),
                    ("規則", "不覆蓋 Daily 權重邏輯", "nbs-badge-muted"),
                ]
            )
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric(
                "7日宏觀健康",
                seven_health,
                f"WAPE {seven_wape:.2f}% | {seven_label}" if seven_wape is not None else "未評估",
            )
            mc2.metric(
                "月末宏觀健康",
                month_health,
                f"WAPE {month_wape:.2f}% | {month_label}" if month_wape is not None else "未評估",
            )
            mc3.metric("宏觀評估口徑", "獨立展示", "不覆蓋 Daily 權重邏輯")
            st.dataframe(_style_backtest_table(macro_summary_display), hide_index=True, width="stretch")
            with st.expander("查看週 / 月宏觀 rolling backtest 明細", expanded=False):
                st.dataframe(_style_backtest_table(macro_detail_df), hide_index=True, width="stretch")
    else:
        st.info(f"暫未產生週 / 月宏觀回測：{macro_err or '歷史資料不足或未完成窗口不足'}")

    backtest_buf = io.BytesIO()
    with pd.ExcelWriter(backtest_buf, engine="openpyxl") as writer:
        summary_display.to_excel(writer, sheet_name="Daily Backtest", index=False)
        weights_display.to_excel(writer, sheet_name="Suggested Weights", index=False)
        if not strategy_display.empty:
            strategy_display.to_excel(writer, sheet_name="Strategy Comparison", index=False)
        detail_df.to_excel(writer, sheet_name="Daily Detail", index=False)
        if not daily_wape_baseline_df.empty:
            daily_wape_baseline_df.to_excel(writer, sheet_name="Daily WAPE Baseline", index=False)
        if not daily_normal_display.empty:
            daily_normal_display.to_excel(writer, sheet_name="Daily Normal-Day Models", index=False)
        if not daily_two_lane_display.empty:
            daily_two_lane_display.to_excel(writer, sheet_name="Daily Two-Lane Selector", index=False)
        if not daily_event_summary_df.empty:
            daily_event_summary_df.to_excel(writer, sheet_name="Daily Event Lead Signal", index=False)
        if not daily_normal_detail_df.empty:
            daily_normal_detail_df.to_excel(writer, sheet_name="Normal-Day Detail", index=False)
        if not daily_two_lane_detail_df.empty:
            daily_two_lane_detail_df.to_excel(writer, sheet_name="Two-Lane Detail", index=False)
        if not macro_summary_display.empty:
            macro_summary_display[macro_summary_display["聚合層級"] == "7-Day Macro"].to_excel(
                writer,
                sheet_name="7-Day Macro Backtest",
                index=False,
            )
            macro_summary_display[macro_summary_display["聚合層級"] == "Month-End Macro"].to_excel(
                writer,
                sheet_name="Month-End Macro Backtest",
                index=False,
            )
        if not macro_detail_df.empty:
            macro_detail_df.to_excel(writer, sheet_name="Macro Detail", index=False)
    backtest_buf.seek(0)
    st.download_button(
        "📥 下載模型回測評分報表",
        backtest_buf,
        "模型回測評分報表_不含掛賬核銷與TT退款轉團款.xlsx",
        width="stretch",
    )

def _render_ai_and_exports(cache: dict) -> None:
    st.write("---")
    _render_anchor("section-ai-forecast")
    _render_section(
        "AI Forecast Decision Support",
        f"Daily / 7-Day / Month-End 三個管理視角分開展示；這一段與上方 KPI / 排行區使用同一收入口徑。{REVENUE_SCOPE_CAPTION}",
        "🤖",
    )
    cache_status = cache.get("ai_cache_status")
    if cache_status == "hit":
        st.caption("AI 回測 / 預測已使用本地快取，避免重新訓練 rolling backtest。")
    elif cache_status == "legacy_hit":
        st.caption("AI 回測 / 預測已命中舊版 key 快取，並已遷移到新的資料 fingerprint key；下次會直接命中新快取。")
    elif cache_status == "rebuilt":
        st.caption("AI 回測 / 預測已重新計算並寫入本地快取；下次資料不變時會加速載入。")
    elif cache_status == "deferred":
        st.info("本次上傳後已先刷新營運 dashboard；AI 回測 / 預測 cache 延後重建，避免卡住上傳完成頁。")

    recompute_cols = st.columns([1, 3])
    with recompute_cols[0]:
        if st.button("補算 AI", type="secondary", width="stretch", key="ai_recompute_button"):
            with st.spinner("正在重新計算 AI / backtest 快取..."):
                _load_and_compute_cache(include_ai=True)
            st.success("AI 回測 / 預測快取已補算完成。")
            st.rerun()
    with recompute_cols[1]:
        st.caption("只在你按下按鈕時才會重算 AI / backtest 快取；一般上傳仍維持快速重建。")

    if cache["ptrk"]:
        ts, ar, pr, lgb_trk = cache["ptrk"]
        with st.container():
            _render_info_panel(
                "AI 權重調整與融合預測",
                f"Daily 是單日波動預測；7-Day Macro 是未來 7 日總額，不是自然週；Month-End Macro 是本月月底落點，不是簡單未來 30 日加總。預測基礎：{REVENUE_SCOPE_LABEL}。",
            )
            wa, wp, wl = st.columns(3)
            auto_schedule = _weight_schedule_from_backtest(cache.get("bt"))
            use_auto_weights = st.checkbox("使用回測自動策略與 horizon-aware 權重", value=bool(auto_schedule), disabled=not bool(auto_schedule))
            default_weights = auto_schedule.get(1) or auto_schedule.get(7) or auto_schedule.get(30) or {"ARIMA": 0.35, "Prophet": 0.35, "LightGBM": 0.30}
            w_a = wa.slider("ARIMA 權重 %", 0, 100, int(round(float(default_weights["ARIMA"]) * 100)), disabled=use_auto_weights)
            w_p = wp.slider("Prophet 權重 %", 0, 100, int(round(float(default_weights["Prophet"]) * 100)), disabled=use_auto_weights)
            w_l = wl.slider("LightGBM 權重 %", 0, 100, int(round(float(default_weights["LightGBM"]) * 100)), disabled=use_auto_weights)
            total_w = w_a + w_p + w_l
            wa_p, wp_p, wl_p = (w_a / total_w, w_p / total_w, w_l / total_w) if total_w else (0.34, 0.33, 0.33)
            if use_auto_weights:
                ens = _build_horizon_weighted_consensus(ar, pr, lgb_trk, auto_schedule)
            else:
                ens = wa_p * ar + wp_p * pr + wl_p * lgb_trk
            lower, upper = ens * 0.85, ens * 1.15
            ar_daily, pr_daily, lgb_daily = ar.head(30), pr.head(30), lgb_trk.head(30)
            ens_daily, lower_daily, upper_daily = ens.head(30), lower.head(30), upper.head(30)

            weight_cols = st.columns(3)
            if use_auto_weights and auto_schedule:
                strategy_text = " / ".join(
                    f"{h}日:{weights.get('策略', '—')}"
                    for h, weights in sorted(auto_schedule.items())
                )
                weight_cols[0].metric("策略選擇", strategy_text)
                weight_cols[1].metric("短期權重", f"A {float(default_weights['ARIMA']) * 100:.1f}% / P {float(default_weights['Prophet']) * 100:.1f}% / L {float(default_weights['LightGBM']) * 100:.1f}%")
                weight_cols[2].metric("權重模式", "回測自動")
            else:
                weight_cols[0].metric("ARIMA 實際權重", f"{wa_p * 100:.1f}%")
                weight_cols[1].metric("Prophet 實際權重", f"{wp_p * 100:.1f}%")
                weight_cols[2].metric("LightGBM 實際權重", f"{wl_p * 100:.1f}%")

            with st.container(border=True):
                _render_anchor("section-daily-forecast")
                _render_forecast_panel_header(
                    "Official Daily Forecast",
                    "Daily Forecast：逐日波動預測",
                    "保留原本逐日 30 天視角，用於短期波動與每日風險，不與週/月宏觀 WAPE 混用。",
                )
                st.caption("此區仍使用正式 Daily Forecast，不使用 Normal-Day 診斷模型覆蓋正式預測。")
                if HAS_MATPLOTLIB:
                    st.pyplot(draw_forecast_chart(ts, ar_daily, pr_daily, lgb_daily, ens_daily, lower_daily, upper_daily, theme=_chart_theme()), width="stretch")

                fdf = pd.DataFrame(
                    {
                        "Date": ens_daily.index.strftime("%Y-%m-%d"),
                        "權重版本": [
                            f"{_weight_bucket_for_horizon(i + 1)}日"
                            if use_auto_weights and auto_schedule
                            else "手動"
                            for i in range(len(ens_daily))
                        ],
                        "推薦策略": [
                            str((auto_schedule.get(_weight_bucket_for_horizon(i + 1)) or {}).get("策略", "手動"))
                            if use_auto_weights and auto_schedule
                            else "手動"
                            for i in range(len(ens_daily))
                        ],
                        "ARIMA": ar_daily.round(2),
                        "Prophet": pr_daily.round(2),
                        "LightGBM": lgb_daily.round(2),
                        "Consensus (共識)": ens_daily.round(2),
                        "Lower": lower_daily.round(2),
                        "Upper": upper_daily.round(2),
                    }
                )

                st.markdown("###### Daily 未來 7 天預測摘要")
                st.dataframe(_style_forecast_table(fdf.head(7)), hide_index=True, width="stretch")

                with st.expander("查看 Daily 完整 30 天預測報表", expanded=False):
                    st.dataframe(_style_forecast_table(fdf), hide_index=True, width="stretch")

            macro_forecast = build_macro_forecast_summary(ts, ar, pr, lgb_trk, ens, lower, upper)
            seven_macro = macro_forecast.get("seven_day", pd.DataFrame())
            month_macro = macro_forecast.get("month_end", pd.DataFrame())
            seven_wape, seven_health, seven_label = _best_macro_metric(cache.get("bt_macro"), "7-Day Macro")
            month_wape, month_health, month_label = _best_macro_metric(cache.get("bt_macro"), "Month-End Macro")

            with st.container(border=True):
                _render_anchor("section-seven-day-macro")
                _render_forecast_panel_header(
                    "7-Day Macro Forecast",
                    "7-Day Macro：未來 7 日總收入預測",
                    "Rolling future 7-day total，用來觀察一週營運總額，不按自然週一至週日切割。",
                    seven_health,
                    f"WAPE {seven_wape:.2f}% | {seven_label}" if seven_wape is not None else "暫未評估宏觀 WAPE",
                )
                if not seven_macro.empty:
                    seven_row = seven_macro.iloc[0]
                    sc1, sc2, sc3, sc4 = st.columns(4)
                    sc1.metric("7日 Consensus", _money_text(float(seven_row["Consensus (共識)"])))
                    sc2.metric("7日 Lower / Upper", f"{_money_text(float(seven_row['Lower']))} / {_money_text(float(seven_row['Upper']))}")
                    sc3.metric("7日宏觀健康", seven_health, f"WAPE {seven_wape:.2f}% | {seven_label}" if seven_wape is not None else "未評估")
                    sc4.metric("窗口", f"{seven_row['WindowStart']} 至 {seven_row['WindowEnd']}")
                    if HAS_MATPLOTLIB:
                        st.pyplot(
                            draw_seven_day_macro_chart(
                                ar.head(7),
                                pr.head(7),
                                lgb_trk.head(7),
                                ens.head(7),
                                lower.head(7),
                                upper.head(7),
                                theme=_chart_theme(),
                            ),
                            width="stretch",
                        )
                    st.dataframe(_style_forecast_table(seven_macro), hide_index=True, width="stretch")
                else:
                    st.info("暫未產生 7-Day Macro 預測。")

            with st.container(border=True):
                _render_anchor("section-month-end-macro")
                _render_forecast_panel_header(
                    "Month-End Macro Forecast",
                    "Month-End Macro：本月月底落點預測",
                    "MTD 已實現收入 + 本月剩餘天數預測，用來看本月月底 pacing，不是簡單未來 30 日加總。",
                    month_health,
                    f"WAPE {month_wape:.2f}% | {month_label}" if month_wape is not None else "暫未評估月末宏觀 WAPE",
                )
                if not month_macro.empty:
                    month_row = month_macro.iloc[0]
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric("MTD 已實現", _money_text(float(month_row["MTDActual"])))
                    mc2.metric("本月剩餘預測", _money_text(float(month_row["RemainingPrediction"])), f"{int(month_row['RemainingDays'])} 天")
                    mc3.metric("月底 Consensus", _money_text(float(month_row["MonthEnd Consensus"])))
                    mc4.metric("月末宏觀健康", month_health, f"WAPE {month_wape:.2f}% | {month_label}" if month_wape is not None else "未評估")
                    if HAS_MATPLOTLIB:
                        st.pyplot(draw_month_end_macro_chart(ts, ens, lower, upper, theme=_chart_theme()), width="stretch")
                    st.dataframe(_style_forecast_table(month_macro), hide_index=True, width="stretch")
                else:
                    st.info("暫未產生 Month-End Macro 預測。")

            forecast_buf = io.BytesIO()
            with pd.ExcelWriter(forecast_buf, engine="openpyxl") as writer:
                fdf.to_excel(writer, sheet_name="Daily Forecast", index=False)
                if not seven_macro.empty:
                    seven_macro.to_excel(writer, sheet_name="7-Day Macro Forecast", index=False)
                if not month_macro.empty:
                    month_macro.to_excel(writer, sheet_name="Month-End Macro Forecast", index=False)
            forecast_buf.seek(0)
            st.download_button(
                "📥 下載 Daily / 7-Day / Month-End AI 三軌融合預測報表（淨口徑）",
                forecast_buf,
                "4_AI淨營收多軌預報_不含掛賬核銷與TT退款轉團款.xlsx",
                width="stretch",
            )
    else:
        st.error(f"預測引擎錯誤: {cache['err']}")

    _render_backtest_report(cache)

    st.write("---")
    _render_anchor("section-data-exports")
    _render_section(
        "Data Exports：報表與日誌匯出",
        "下載區保留既有 workbook、sheet 與檔名；大型 Excel 採 lazy export，避免拖慢首屏。",
        "📥",
    )
    with st.container(border=True):
        _render_panel_header(
            "Data Export Center",
            "下載中心與稽核輸出",
            f"匯出內容包含全維度事實表、AI 預測報表與清洗異常日誌。正式口徑：{REVENUE_SCOPE_LABEL}。",
        )
        _render_role_badges(
            [
                ("Lazy Export", "按需載入 workbook bytes", "nbs-badge-info"),
                ("Official Scope", REVENUE_SCOPE_LABEL, "nbs-badge-success"),
                ("Cache Policy", "資料不變時重用本地 export cache", "nbs-badge-muted"),
            ]
        )
        export_loaded = bool(cache.get("ex") and cache.get("ex_no_writeoff") and cache.get("ex_no_writeoff_refund_transfer"))
        export_cache_ready = cache.get("export_cache_status") == "ready"
        _render_export_status_card(cache, export_loaded)
        if not export_loaded:
            if export_cache_ready:
                st.success("大型 Excel workbook 已在本地快取中準備好。為保持首頁快速載入，下載 bytes 會在你需要時才載入。")
                if st.button("載入下載按鈕", type="primary", width="stretch"):
                    with st.spinner("正在從本地 export 快取載入三份 Excel 下載檔..."):
                        ok = _ensure_export_workbooks(cache)
                    if ok:
                        st.rerun()
                    else:
                        st.error("讀取 export 快取失敗，請重新準備下載報表。")
            else:
                st.info(
                    "大型 Excel workbook 尚未準備。為加快首頁載入，系統已延後生成三份下載報表；需要下載時請按下方按鈕，完成後會寫入本地快取。"
                )
                if st.button("準備下載報表", type="primary", width="stretch"):
                    with st.spinner("正在生成三份 Excel 下載報表並寫入本地快取，首次準備約需 1-2 分鐘..."):
                        ok = _ensure_export_workbooks(cache)
                    if ok:
                        st.success("下載報表已準備完成，下次資料不變時會直接使用 export 快取。")
                        st.rerun()
                    else:
                        st.error("下載報表準備失敗，請檢查資料或本地快取目錄權限。")

        d1, d2, d3, d4 = st.columns(4)
        with d1:
            st.markdown('<div class="nbs-download-card"><div class="nbs-card-title">Full Dimension</div><div class="nbs-card-note">完整分析用 sheet 與明細表。</div></div>', unsafe_allow_html=True)
            if export_loaded:
                st.download_button(
                    "下載全維度事實表",
                    cache["ex"] or b"",
                    "分社與專職_經營統計_V5.0.xlsx",
                    width="stretch",
                )
        with d2:
            st.markdown('<div class="nbs-download-card"><div class="nbs-card-title">Exclude Write-off</div><div class="nbs-card-note">排除收款類型「掛賬核銷」。</div></div>', unsafe_allow_html=True)
            if export_loaded:
                st.download_button(
                    "下載全維度事實表（不含掛賬核銷）",
                    cache["ex_no_writeoff"] or b"",
                    "分社與專職_經營統計_V5.0_不含掛賬核銷.xlsx",
                    width="stretch",
                )
        with d3:
            st.markdown('<div class="nbs-download-card"><div class="nbs-card-title">Official Scope</div><div class="nbs-card-note">排除掛賬核銷與 TT 退款轉團款。</div></div>', unsafe_allow_html=True)
            if export_loaded:
                st.download_button(
                    "下載全維度事實表（不含掛賬核銷與TT退款轉團款）",
                    cache["ex_no_writeoff_refund_transfer"] or b"",
                    "分社與專職_經營統計_V5.0_不含掛賬核銷與TT退款轉團款.xlsx",
                    width="stretch",
                )
        with d4:
            if not cache["anm"].empty:
                st.markdown('<div class="nbs-download-card"><div class="nbs-card-title">Cleaning Log</div><div class="nbs-card-note">清洗異常與糾錯稽核。</div></div>', unsafe_allow_html=True)
                if export_loaded:
                    anomaly_buf = io.BytesIO()
                    cache["anm"].to_excel(anomaly_buf, index=False)
                    anomaly_buf.seek(0)
                    st.download_button(
                        "下載清洗異常糾錯日誌",
                        anomaly_buf,
                        "異常排查日誌.xlsx",
                        width="stretch",
                    )
            else:
                st.info("目前沒有清洗異常日誌可下載。")

def _render_dashboard_tab() -> None:
    _repair_subtable_branch_assignments_before_load()
    _repair_operator_assignments_before_load()
    if _invalidate_session_cache_if_generation_changed():
        st.rerun()
    subtable_notice = st.session_state.pop("SUBTABLE_BRANCH_REPAIR_NOTICE", None)
    notice = st.session_state.pop("OPERATOR_REPAIR_NOTICE", None)
    if subtable_notice:
        st.info(subtable_notice)
    if notice:
        st.info(notice)

    db_tour, db_others = load_all_data_from_db()
    has_db_data = not db_tour.empty or not db_others.empty
    if has_db_data:
        _render_database_status_card(db_tour, db_others)

    _render_upload_area(has_db_data)

    if has_db_data and not st.session_state["DB_LOADED_FLAG"]:
        with st.spinner("載入階段：SQLite loaded → Dashboard facts ready → AI cache hit/rebuild check；Export workbooks 會按需載入。"):
            try:
                _load_and_compute_cache(include_ai=False)
                st.rerun()
            except Exception:
                _render_error("從資料庫恢復數據失敗。", traceback.format_exc())

    cache = st.session_state["PROCESSED_DATA_CACHE"]
    if not cache:
        st.info("目前尚無可分析資料。請展開上方區塊上傳主副表。")
        return

    s1, s2, t_df, o_df = cache["s1"].copy(), cache["s2"].copy(), cache["t"].copy(), cache["o"].copy()
    _render_sidebar_shell()
    _render_executive_summary_band(REVENUE_SCOPE_LABEL)
    _render_anchor("section-current-context")
    _render_section("目前分析脈絡", None, "📌")
    scope = cache.get("scope", {})
    scope_note = (
        f"{REVENUE_SCOPE_CAPTION} 已排除 {int(scope.get('excluded_order_count', 0)):,} 個來源單據號、"
        f"{int(scope.get('excluded_rows', 0)):,} 筆記錄，金額約 HKD {float(scope.get('excluded_amount', 0)):,.0f}。"
    )
    _render_info_panel(
        "當前營運視角",
        f"上方正式口徑保持完整；KPI 與門店/產品分析各自使用局部篩選，避免一個條件影響整張大盤。{scope_note}",
    )
    _render_anchor("section-kpi-overview")
    kpi_year_sel, kpi_month_sel, kpi_date_rng = _render_kpi_filter_center(cache)
    _render_applied_filter_chips(kpi_year_sel, kpi_month_sel, kpi_date_rng, "全部分社", "全部銷售組")
    _render_kpi_strip(_build_dashboard_kpis(s1, t_df, o_df, kpi_year_sel, kpi_month_sel, kpi_date_rng))
    _render_data_quality_scorecard(cache)
    _render_entity_resolution_audit(cache)
    _render_ai_cleaning_suggestions(cache)
    _render_year_summary(s1, s2, [], [], ())
    rank_year_sel, rank_month_sel, rank_date_rng, rank_branch_sel, rank_sales_sel = _render_rank_filter_center(cache)
    _render_rank_and_drilldown(s1, t_df, o_df, rank_branch_sel, rank_sales_sel, rank_year_sel, rank_month_sel, rank_date_rng)
    _render_ai_and_exports(cache)

def _render_gmv_exclusion_tab() -> None:
    _render_section(
        "GMV 排除訂單看板",
        "上傳交易號碼清單後，系統會在記憶體中扣除匹配來源單據號，生成獨立 GMV 視角與同規格報表；不回寫 SQLite。",
        "🧾",
    )
    db_tour, db_others = load_all_data_from_db()
    if db_tour.empty and db_others.empty:
        st.info("目前 SQLite 尚無資料，請先在經營分析大盤上傳主副表。")
        return

    upload = st.file_uploader(
        "上傳 GMV 排除訂單清單（Excel / CSV；欄名交易號碼或 A 欄）",
        type=["xlsx", "xls", "csv"],
        key="GMV_EXCLUSION_UPLOAD",
    )
    if not upload:
        _render_info_panel(
            "GMV 排除看板尚未套用",
            "請上傳一份交易號碼清單；這個分析只影響本頁，不會改動正式看板、SQLite 或原本報表。",
        )
        return

    try:
        exclusion_ids, exclusion_audit = _parse_gmv_exclusion_ids(upload)
    except Exception:
        _render_error("GMV 排除清單解析失敗。", traceback.format_exc())
        return

    if not exclusion_ids:
        st.warning("排除清單沒有可用的交易號碼。請確認 A 欄或欄名「交易號碼」有內容。")
        return

    current_signature = hashlib.sha256(
        json.dumps(sorted(exclusion_ids), ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    if st.session_state.get("GMV_EXCLUSION_SIGNATURE") != current_signature:
        st.session_state["GMV_EXCLUSION_SIGNATURE"] = current_signature
        st.session_state.pop("GMV_EXCLUSION_WORKBOOKS", None)

    filtered = _filter_gmv_exclusion_frames(db_tour, db_others, exclusion_ids)
    summary_rows = _gmv_summary_rows(db_tour, db_others, filtered, exclusion_ids)
    summary_map = {row["指標"]: row["數值"] for row in summary_rows}
    branch_mapping, target_branches, cruise_depts, sales_reps, _ = _current_rules()

    _render_kpi_strip(
        [
            {
                "label": "排除清單筆數",
                "value": f"{int(summary_map.get('排除清單筆數', 0)):,}",
                "delta": f"成功匹配 {int(summary_map.get('成功匹配訂單數', 0)):,}",
                "note": "以交易號碼對來源單據號匹配",
                "accent": "#118DFF",
            },
            {
                "label": "排除前 GMV",
                "value": _money_text(float(summary_map.get("排除前 GMV", 0))),
                "delta": "SQLite 原始財務資料",
                "note": "未改動正式資料",
                "accent": "#12239E",
            },
            {
                "label": "排除金額",
                "value": _money_text(float(summary_map.get("排除金額", 0))),
                "delta": f"未匹配 {int(summary_map.get('未匹配訂單數', 0)):,}",
                "note": "被扣除的匹配訂單金額",
                "accent": "#E66C37",
            },
            {
                "label": "排除後 GMV",
                "value": _money_text(float(summary_map.get("排除後 GMV", 0))),
                "delta": "本頁派生視角",
                "note": "不覆蓋正式營收看板",
                "accent": "#197278",
            },
        ]
    )

    st.caption("GMV 排除摘要")
    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, width="stretch")

    _, gmv_s1, _ = build_dashboard_data(
        filtered["tour"],
        filtered["others"],
        branch_mapping,
        target_branches,
        cruise_depts,
        sales_reps,
        make_workbook=False,
    )
    if not gmv_s1.empty:
        rank_df = gmv_s1.copy()
        for col in ["旅行團", "郵輪", "票務"]:
            rank_df[col] = pd.to_numeric(rank_df[col], errors="coerce").fillna(0)
        rank = (
            rank_df.assign(總額=rank_df[["旅行團", "郵輪", "票務"]].sum(axis=1))
            .groupby("文本", as_index=False)[["旅行團", "郵輪", "票務", "總額"]]
            .sum()
            .sort_values("總額", ascending=False)
        )
        rank.insert(0, "排名", range(1, len(rank) + 1))
        st.caption("GMV 排除後分社排行")
        st.dataframe(rank.head(20), hide_index=True, width="stretch")

    detail_cols = [c for c in ["資料表", COL_ORDER_ID, COL_DATE, COL_MONEY, COL_BRANCH, COL_SALESPERSON, "來源報表標籤", "收款類型", "收款方式"] if c in filtered["excluded_detail"].columns]
    st.caption("被排除訂單明細")
    st.dataframe(filtered["excluded_detail"][detail_cols] if detail_cols else filtered["excluded_detail"], hide_index=True, width="stretch")

    with st.expander("查看未匹配交易號碼", expanded=False):
        st.dataframe(pd.DataFrame({"交易號碼": filtered["unmatched_ids"]}), hide_index=True, width="stretch")

    if st.button("生成 GMV 排除版完整報表", type="primary", width="stretch"):
        with st.spinner("正在生成三份 GMV 排除版完整報表，報表結構沿用原本正式報表..."):
            workbooks = _compute_gmv_exclusion_workbooks(filtered["tour"], filtered["others"])
            workbooks["audit"] = _build_gmv_audit_workbook(summary_rows, filtered["excluded_detail"], filtered["unmatched_ids"])
            st.session_state["GMV_EXCLUSION_WORKBOOKS"] = workbooks
        st.success("GMV 排除版報表已生成，可在下方下載。")

    workbooks = st.session_state.get("GMV_EXCLUSION_WORKBOOKS")
    if workbooks:
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            st.download_button(
                "下載 GMV 排除版全維度報表",
                workbooks.get("ex") or b"",
                "GMV排除訂單_分社與專職_經營統計_V5.0.xlsx",
                width="stretch",
            )
        with d2:
            st.download_button(
                "下載 GMV 排除版（不含掛賬核銷）",
                workbooks.get("ex_no_writeoff") or b"",
                "GMV排除訂單_分社與專職_經營統計_V5.0_不含掛賬核銷.xlsx",
                width="stretch",
            )
        with d3:
            st.download_button(
                "下載 GMV 排除版（正式口徑）",
                workbooks.get("ex_no_writeoff_refund_transfer") or b"",
                "GMV排除訂單_分社與專職_經營統計_V5.0_不含掛賬核銷與TT退款轉團款.xlsx",
                width="stretch",
            )
        with d4:
            st.download_button(
                "下載 GMV 排除匹配稽核",
                workbooks.get("audit") or b"",
                "GMV排除訂單_匹配稽核.xlsx",
                width="stretch",
            )

def main() -> None:
    st.markdown(
        f"""
        <div class="nbs-topbar">
            <div>
                <div class="nbs-brand-kicker">NBS Analytics</div>
                <div class="main-title">中旅 NBS 企業營運駕駛艙</div>
                <div class="sub-title">Enterprise Operation Cockpit；營運分析、AI Forecast、Backtest 與報表匯出。</div>
            </div>
            <div class="nbs-topbar-status">
                <div class="nbs-scope-pill">正式口徑：{escape(REVENUE_SCOPE_LABEL)}</div>
                <div class="nbs-status-chip">SQLite Local</div>
                <div class="nbs-status-chip">View Only</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tab_dash, tab_conf, tab_gmv = st.tabs(["經營分析大盤", "業務規則配置", "GMV 排除訂單看板"])
    with tab_dash:
        _render_dashboard_tab()
    with tab_conf:
        _render_config_tab()
    with tab_gmv:
        _render_gmv_exclusion_tab()
