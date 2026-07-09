import ast
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
RENDERING_PATH = Path(__file__).resolve().parents[1] / "streamlit_rendering.py"
PAGES_PATH = Path(__file__).resolve().parents[1] / "app_pages.py"
WORKFLOWS_PATH = Path(__file__).resolve().parents[1] / "app_workflows.py"
STYLES_PATH = Path(__file__).resolve().parents[1] / "app_styles.py"
STREAMLIT_CONFIG_PATH = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"


def _function_source(function_name: str) -> str:
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{function_name} not found in app.py")


def _rendering_function_source(function_name: str) -> str:
    source = RENDERING_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{function_name} not found in streamlit_rendering.py")


def _pages_function_source(function_name: str) -> str:
    source = PAGES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{function_name} not found in app_pages.py")


def _workflows_function_source(function_name: str) -> str:
    source = WORKFLOWS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{function_name} not found in app_workflows.py")


def _styles_source() -> str:
    return STYLES_PATH.read_text(encoding="utf-8")


def test_upload_audit_details_are_collapsed_by_default():
    source = _pages_function_source("_render_upload_audit_notice")

    assert 'st.expander("查看本次上傳詳細反饋", expanded=False)' in source


def test_upload_audit_tables_use_safe_renderer():
    source = _pages_function_source("_render_upload_audit_notice")

    assert "_render_entity_audit_dataframe(" in source
    assert "st.dataframe(_style_entity_audit_table" not in source


def test_upload_audit_notice_renders_phase2c_stability_gate():
    source = _pages_function_source("_render_upload_audit_notice")
    helper_source = _pages_function_source("_render_upload_stability_gate")

    assert 'audit.get("stability_gate")' in source
    assert "_render_upload_stability_gate(stability_gate)" in source
    assert "Phase 2C Upload Rebuild Stability Gate" in helper_source
    assert "st.success(gate.get(\"message\"" in helper_source
    assert "st.warning(gate.get(\"message\"" in helper_source


def test_phase2f_upload_gate_uses_card_drift_table_and_download_report():
    helper_source = _pages_function_source("_render_upload_stability_gate")
    workbook_source = _workflows_function_source("_build_upload_stability_gate_workbook")

    assert "nbs-upload-gate-card" in helper_source
    assert "口徑驗收結果" in helper_source
    assert "driftChecks" in helper_source
    assert "st.download_button(" in helper_source
    assert "_build_upload_stability_gate_workbook(gate)" in helper_source
    assert "本次上傳_Phase2F_Stability_Gate.xlsx" in helper_source
    assert "Gate Summary" in workbook_source
    assert "Gate Checks" in workbook_source
    assert "Core Validation" in workbook_source
    assert "Freshness Update" in workbook_source
    assert "資料更新狀態" in helper_source
    assert "freshnessUpdate" in helper_source


def test_upload_success_audit_stores_phase2c_stability_gate():
    source = _pages_function_source("_render_upload_area")

    assert "build_phase2c_stability_gate()" in source
    assert '"stability_gate": stability_gate' in source


def test_upload_flow_runs_preflight_before_writing_sqlite():
    source = _pages_function_source("_render_upload_area")
    helper_source = _pages_function_source("_render_upload_audit_notice")

    assert "run_upload_preflight(" in source
    assert "preflight_report" in source
    assert "if preflight_status != \"matched\":" in source
    assert "查看上傳預演結果" in helper_source
    assert "preflight_report" in helper_source
    assert "driftDiagnosis" in helper_source
    assert "Drift Diagnosis" in helper_source
    assert "drift_diagnosis.json" in helper_source
    assert "drift_diagnosis.xlsx" in helper_source
    assert "_build_drift_diagnosis_workbook(drift_diagnosis)" in helper_source
    assert '"drift_diagnosis": preflight_result.get("driftDiagnosis") or {}' in source


def test_upsert_feedback_surfaces_filtered_excluded_rows():
    source = _workflows_function_source("_upsert_summary_rows")

    assert '"口徑排除行數": item.get("filtered_excluded_rows", 0)' in source
    assert '"實際寫入行數": item.get("write_rows", item.get("inserted_rows", 0))' in source


def test_upload_success_persists_phase2g_stability_history():
    source = _pages_function_source("_render_upload_area")

    assert "record_stability_history(" in source
    assert '"source_files"' in source
    assert '"latest_data_date"' in source
    assert '"history_record_id"' in source
    assert '"history_error"' in source


def test_phase2h_upload_uses_lock_and_persists_history_after_rollback():
    app_source = APP_PATH.read_text(encoding="utf-8")
    source = _pages_function_source("_render_upload_area")
    rebuild_source = _workflows_function_source("_rebuild_cache_after_database_restore")

    assert "UPLOAD_OPERATION_LOCK" in app_source
    assert "handle_core_drift_rollback(" in source
    assert "_rebuild_cache_after_database_restore" in source
    assert 'st.session_state["PROCESSED_DATA_CACHE"] = None' in rebuild_source
    assert '"rollback_status"' in source
    assert '"quarantine_path"' in source
    assert source.index("handle_core_drift_rollback(") < source.index("record_stability_history(")


def test_upload_status_alert_does_not_render_streamlit_deltagenerator_magic():
    source = _pages_function_source("_render_upload_area")

    assert "st.success(\"✅ AI 預測環境就緒\") if HAS_AI_LIBS else st.warning" not in source
    assert "if HAS_AI_LIBS:" in source
    assert "_ = st.success(\"✅ AI 預測環境就緒\")" in source
    assert "_ = st.warning(\"⚠️ 缺 matplotlib/statsmodels，AI 預測會降級或跳過\")" in source


def test_streamlit_magic_is_disabled_for_dashboard_app():
    config_source = STREAMLIT_CONFIG_PATH.read_text(encoding="utf-8")

    assert "[runner]" in config_source
    assert "magicEnabled = false" in config_source


def test_upload_flow_uses_fast_dashboard_cache_rebuild_without_ai_blocking():
    source = _pages_function_source("_render_upload_area")
    cache_source = _workflows_function_source("_load_and_compute_cache")

    assert "_load_and_compute_cache(include_ai=False)" in source
    assert "重建 dashboard cache（不重跑 AI）" in source
    assert "include_ai: bool = True" in cache_source
    assert 'ai_cache_status = "deferred"' in cache_source


def test_initial_dashboard_load_defers_ai_cache_rebuild():
    source = _pages_function_source("_render_dashboard_tab")

    assert "_load_and_compute_cache(include_ai=False)" in source
    assert "_load_and_compute_cache()" not in source


def test_upload_flow_profiles_all_post_write_guard_stages():
    source = _pages_function_source("_render_upload_area")

    for label in [
        "讀取 Excel 與日期診斷",
        "Preflight 臨時 DB 與口徑驗收",
        "正式 SQLite upsert",
        "Dashboard cache 快速重建",
        "寫入後 SQLite reload",
        "Stability gate 驗證",
        "Rollback guard",
        "Stability history 記錄",
        "Upload total",
    ]:
        assert label in source


def test_upload_preflight_internal_timings_are_rendered():
    source = _pages_function_source("_render_upload_audit_notice")

    assert 'preflight_report.get("stageTimings")' in source
    assert "Preflight 內部耗時" in source


def test_ai_section_exposes_manual_recompute_button():
    source = _pages_function_source("_render_ai_and_exports")

    assert 'st.button("補算 AI"' in source
    assert "_load_and_compute_cache(include_ai=True)" in source
    assert "st.rerun()" in source


def test_export_status_card_surfaces_cache_version_and_schema_contract():
    source = _rendering_function_source("_render_export_status_card")
    cache_source = _workflows_function_source("_load_and_compute_cache")
    compute_source = _workflows_function_source("_compute_export_workbooks")
    ensure_source = _workflows_function_source("_ensure_export_workbooks")

    assert "export_cache_version" in source
    assert "official_export_schema" in source
    assert "EXPORT_CACHE_VERSION" in cache_source
    assert "official_export_schema" in cache_source
    assert "official_export_schema" in compute_source
    assert "official_export_schema" in ensure_source


def test_error_traceback_is_hidden_behind_technical_details_expander():
    source = _rendering_function_source("_render_error")

    assert 'st.expander("查看技術細節", expanded=False)' in source
    assert "st.code(detail, language=\"python\")" in source


def test_sidebar_styles_are_driven_by_theme_tokens():
    theme_source = _rendering_function_source("_theme_tokens")
    dynamic_css_source = _rendering_function_source("_render_dynamic_theme_css")
    styles_source = _styles_source()

    for token in [
        "sidebar_bg",
        "sidebar_border",
        "sidebar_panel",
        "sidebar_panel_hover",
        "sidebar_text",
        "sidebar_text_strong",
        "sidebar_muted",
        "sidebar_input_bg",
        "sidebar_input_text",
    ]:
        assert f'"{token}"' in theme_source

    for css_var in [
        "--nbs-sidebar-bg",
        "--nbs-sidebar-border",
        "--nbs-sidebar-panel",
        "--nbs-sidebar-panel-hover",
        "--nbs-sidebar-text",
        "--nbs-sidebar-text-strong",
        "--nbs-sidebar-muted",
        "--nbs-sidebar-input-bg",
        "--nbs-sidebar-input-text",
    ]:
        assert css_var in dynamic_css_source

    assert "background: var(--nbs-sidebar-bg)" in styles_source
    assert "border-right: 1px solid var(--nbs-sidebar-border)" in styles_source
    assert "color: var(--nbs-sidebar-text)" in styles_source


def test_app_py_no_longer_defines_moved_rendering_helpers():
    app_source = APP_PATH.read_text(encoding="utf-8")

    for function_name in [
        "_theme_tokens",
        "_chart_theme",
        "_render_dynamic_theme_css",
        "_render_error",
        "_health_badge_class",
        "_render_section",
        "_render_anchor",
        "_render_sidebar_navigation",
        "_render_sidebar_control_header",
        "_render_info_panel",
        "_render_database_status_card",
        "_render_applied_filter_chips",
        "_render_executive_summary_band",
        "_render_panel_header",
        "_render_forecast_panel_header",
        "_render_role_badges",
        "_render_export_status_card",
        "_render_forecast_card",
        "_render_kpi_strip",
    ]:
        assert f"def {function_name}" not in app_source


def test_scope_pill_uses_theme_aware_colors():
    styles_source = _styles_source()

    assert ".nbs-scope-pill" in styles_source
    assert "color: var(--nbs-primary-navy);" not in styles_source
    assert "border: 1px solid #CFE0F4;" not in styles_source


def test_sidebar_control_layout_contract():
    styles_source = _styles_source()

    assert "top: 1.18rem !important;" not in styles_source
    assert "top: 5.1rem !important;" in styles_source
    assert "flex-wrap: wrap !important;" in styles_source
    assert "max-height: 7.5rem !important;" in styles_source
    assert "overflow-y: auto !important;" in styles_source


def test_dark_theme_controls_and_tables_have_overrides():
    styles_source = _styles_source()

    assert "section[data-testid=\"stSidebar\"] div.stButton > button" in styles_source
    assert "section[data-testid=\"stSidebar\"] div.stFormSubmitButton > button" in styles_source
    assert "div.stButton > button,\n        div.stDownloadButton > button,\n        div.stFormSubmitButton > button {\n            background: var(--nbs-chip-bg) !important;" in styles_source
    assert "background: var(--nbs-sidebar-active-bg)" in styles_source
    assert "[data-testid=\"stDataFrame\"] canvas" in styles_source
    assert "filter: var(--nbs-dataframe-filter)" in styles_source
    assert "--nbs-dataframe-filter" in styles_source


def test_light_sidebar_badges_use_readable_semantic_tokens():
    styles_source = _styles_source()

    assert "--nbs-badge-official-text" in styles_source
    assert "--nbs-badge-official-bg" in styles_source
    assert ".nbs-sidebar-nav-badge.official" in styles_source
    assert "color: var(--nbs-badge-official-text)" in styles_source
