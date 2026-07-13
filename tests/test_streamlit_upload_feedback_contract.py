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

    assert "execute_upload_operation(" in source
    assert '"stability_gate": response.get("stabilityGate")' in source


def test_upload_flow_runs_preflight_before_writing_sqlite():
    source = _pages_function_source("_render_upload_area")
    helper_source = _pages_function_source("_render_upload_audit_notice")

    assert "execute_upload_operation(" in source
    assert "preflight_report" in source
    assert "查看上傳預演結果" in helper_source
    assert "preflight_report" in helper_source
    assert "driftDiagnosis" in helper_source
    assert "Drift Diagnosis" in helper_source
    assert "drift_diagnosis.json" in helper_source
    assert "drift_diagnosis.xlsx" in helper_source
    assert "_build_drift_diagnosis_workbook(drift_diagnosis)" in helper_source
    assert '"drift_diagnosis": preflight.get("driftDiagnosis") or {}' in source


def test_upsert_feedback_surfaces_filtered_excluded_rows():
    source = _workflows_function_source("_upsert_summary_rows")

    assert '"口徑排除行數": item.get("filtered_excluded_rows", 0)' in source
    assert '"實際寫入行數": item.get("write_rows", item.get("inserted_rows", 0))' in source


def test_upload_success_persists_phase2g_stability_history():
    source = _pages_function_source("_render_upload_area")

    assert "execute_upload_operation(" in source
    assert '"source_files"' in source


def test_upload_flow_persists_monthly_baseline_monitoring_payload():
    source = _pages_function_source("_render_upload_area")
    workflows = WORKFLOWS_PATH.read_text(encoding="utf-8")

    assert "build_governed_stability_gate as build_phase2c_stability_gate" in workflows
    assert '"monthly_baseline": response.get("monthlyBaseline") or {}' in source
    assert '"history_record_id"' in source
    assert '"history_error"' in source


def test_monthly_baseline_governance_panel_requires_ready_and_confirmation():
    source = _pages_function_source("_render_monthly_baseline_governance")

    assert "Monthly Baseline Governance" in source
    assert '"monitoring": "Monitoring"' in source
    assert '"promotion_ready": "Ready"' in source
    assert '"blocking": "Blocking"' in source
    assert 'governance.get("promotionReady")' in source
    assert "升級為阻擋式基準" in source
    assert "我理解升級後的上傳阻擋與 rollback 影響" in source
    assert "promote_monthly_baselines(" in source
    assert 'expected_record_id=int(governance["eligibleRecordId"])' in source
    assert "st.rerun()" in source


def test_phase2h_upload_uses_lock_and_persists_history_after_rollback():
    app_source = APP_PATH.read_text(encoding="utf-8")
    source = _pages_function_source("_render_upload_area")
    rebuild_source = _workflows_function_source("_rebuild_cache_after_database_restore")

    assert "UPLOAD_OPERATION_LOCK" not in app_source
    assert "acquire_upload_lease(" in source
    assert "execute_upload_operation(" in source
    assert 'st.session_state["PROCESSED_DATA_CACHE"] = None' in rebuild_source
    assert '"rollback_status"' in source
    assert '"quarantine_path"' in source
    assert source.index("acquire_upload_lease(") < source.index("_uploaded_excel_frame(_streamlit_named_bytes(main_up))")


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
    assert "accepted_cache_rebuilder=lambda: _load_and_compute_cache(include_ai=False)" in source
    assert "include_ai: bool = True" in cache_source
    assert 'ai_cache_status = "deferred"' in cache_source


def test_initial_dashboard_load_defers_ai_cache_rebuild():
    source = _pages_function_source("_render_dashboard_tab")

    assert "_load_and_compute_cache(include_ai=False)" in source
    assert "_load_and_compute_cache()" not in source


def test_initial_dashboard_cache_load_does_not_trigger_second_full_rerun():
    source = _pages_function_source("_render_dashboard_tab")
    cache_load_start = source.index("_load_and_compute_cache(include_ai=False)")
    cache_assignment = source.index('cache = st.session_state["PROCESSED_DATA_CACHE"]')

    assert "st.rerun()" not in source[cache_load_start:cache_assignment]


def test_sidebar_keeps_theme_but_no_global_control_filters():
    sidebar_source = _workflows_function_source("_render_sidebar_shell")
    workflows_source = WORKFLOWS_PATH.read_text(encoding="utf-8")

    assert "_render_sidebar_navigation()" in sidebar_source
    assert "NBS_UI_THEME" in sidebar_source
    assert "_render_sidebar_control_header" not in sidebar_source
    assert "control_center_form" not in workflows_source
    assert "CTRL_YEAR_SEL" not in workflows_source
    assert "CTRL_MONTH_SEL" not in workflows_source
    assert "CTRL_DATE_RANGE" not in workflows_source
    assert "CTRL_BRANCH_SEL" not in workflows_source
    assert "CTRL_SALES_SEL" not in workflows_source


def test_kpi_and_rank_filters_have_independent_scope_keys():
    kpi_source = _workflows_function_source("_render_kpi_filter_center")
    rank_source = _workflows_function_source("_render_rank_filter_center")

    for key in ["KPI_YEAR_SEL", "KPI_MONTH_SEL", "KPI_DATE_RANGE"]:
        assert key in kpi_source
        assert key not in rank_source

    for key in ["RANK_YEAR_SEL", "RANK_MONTH_SEL", "RANK_DATE_RANGE", "RANK_BRANCH_SEL", "RANK_SALES_SEL"]:
        assert key in rank_source
        assert key not in kpi_source

    assert "營運總覽與管理層 KPI 篩選" in kpi_source
    assert "門店與產品分析篩選" in rank_source


def test_dashboard_uses_separate_filter_scopes_for_kpi_and_rank_sections():
    source = _pages_function_source("_render_dashboard_tab")

    assert "_render_sidebar_shell()" in source
    assert "_sidebar_control_center" not in source
    assert "kpi_year_sel, kpi_month_sel, kpi_date_rng = _render_kpi_filter_center(cache)" in source
    assert "rank_year_sel, rank_month_sel, rank_date_rng, rank_branch_sel, rank_sales_sel = _render_rank_filter_center(cache)" in source
    assert "_build_dashboard_kpis(s1, t_df, o_df, kpi_year_sel, kpi_month_sel, kpi_date_rng)" in source
    assert "_render_rank_and_drilldown(s1, t_df, o_df, rank_branch_sel, rank_sales_sel, rank_year_sel, rank_month_sel, rank_date_rng)" in source
    assert "左側控制中心" not in source


def test_dashboard_persistent_repairs_are_gated_by_generation_and_rule_versions():
    from app_workflows import _persistent_repair_token, _should_run_persistent_repairs

    token = _persistent_repair_token("1:db-sha", operator_rule_version=1, subtable_rule_version=1)
    assert _should_run_persistent_repairs(token, None) is True
    assert _should_run_persistent_repairs(token, token) is False
    next_token = _persistent_repair_token("2:new-db-sha", operator_rule_version=1, subtable_rule_version=1)
    assert _should_run_persistent_repairs(next_token, token) is True


def test_dashboard_repairs_refresh_generation_after_updates():
    source = _pages_function_source("_render_dashboard_tab")
    workflows = WORKFLOWS_PATH.read_text(encoding="utf-8")

    assert "_run_persistent_repairs_before_load()" in source
    assert "_persistent_repair_token(" in workflows
    assert "refresh_cache_generation_signature(" in workflows


def test_persistent_repair_gate_survives_streamlit_session_restart(tmp_path):
    from app_workflows import _load_persistent_repair_token, _save_persistent_repair_token

    state_path = tmp_path / "persistent_repair_state.json"
    assert _load_persistent_repair_token(state_path) is None

    _save_persistent_repair_token("1:db-sha|operator:1|subtable:1", state_path)

    assert _load_persistent_repair_token(state_path) == "1:db-sha|operator:1|subtable:1"


def test_upload_flow_profiles_all_post_write_guard_stages():
    source = _pages_function_source("_render_upload_area")

    assert '"stage_timings": response.get("stageTimings") or []' in source


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
