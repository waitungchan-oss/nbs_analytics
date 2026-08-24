import ast
from pathlib import Path


PAGES_PATH = Path(__file__).resolve().parents[1] / "app_pages.py"


def _function_source(name: str) -> str:
    source = PAGES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


def test_gmv_tab_runs_refund_preflight_before_adjustment():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "_build_gmv_refund_preflight(" in source
    assert "preflight_report" in source
    assert '上傳並合併退款資料庫' in source
    assert source.index("_build_gmv_refund_preflight(") < source.index('st.button("上傳並合併退款資料庫"')


def test_gmv_tab_blocks_report_generation_for_blocked_preflight():
    source = _function_source("_render_gmv_exclusion_tab")

    assert 'preflight_report.get("status") == "blocked"' in source
    assert "build_gmv_formal_artifacts" in source
    assert "st.rerun()" in source


def test_gmv_tab_clears_upload_before_rerun_to_show_active_reports():
    source = _function_source("_render_gmv_exclusion_tab")

    assert 'GMV_CLEAR_UPLOAD_AFTER_MERGE' in source
    assert 'st.session_state.pop("GMV_EXCLUSION_UPLOAD", None)' in source
    assert 'st.session_state["GMV_CLEAR_UPLOAD_AFTER_MERGE"] = True' in source


def test_gmv_tab_exposes_exception_download_and_clears_stale_state():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "exceptionRows" in source
    assert "st.download_button(" in source
    assert "warning_summaries" in source


def test_gmv_tab_distinguishes_read_only_preview_from_confirmed_ledger_write():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "Preview" in source
    assert "streamlit-auto-merge" in source
    assert "warning" in source
    assert "blocking" in source


def test_gmv_tab_has_one_primary_merge_action_and_no_manual_confirmation_controls():
    source = _function_source("_render_gmv_exclusion_tab")

    assert 'st.button("上傳並合併退款資料庫"' in source
    assert "載入正式淨 GMV" not in source
    assert "確認人員" not in source
    assert "GMV_FORMAL_WARNING_ACK" not in source
