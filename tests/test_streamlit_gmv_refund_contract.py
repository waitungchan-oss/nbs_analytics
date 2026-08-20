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
    assert "_apply_gmv_refund_adjustments(" in source
    assert source.index("_build_gmv_refund_preflight(") < source.index("_apply_gmv_refund_adjustments(")


def test_gmv_tab_blocks_report_generation_for_blocked_preflight():
    source = _function_source("_render_gmv_exclusion_tab")

    assert 'preflight_report.get("status") == "blocked"' in source
    assert "生成總退款及已退款兩套完整報表" in source


def test_gmv_tab_exposes_exception_download_and_clears_stale_state():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "exceptionRows" in source
    assert "st.download_button(" in source
    assert "GMV_REFUND_PREFLIGHT_SIGNATURE" in source
    assert 'st.session_state.pop("GMV_EXCLUSION_WORKBOOKS"' in source
    assert 'st.session_state.pop("GMV_REFUND_PREFLIGHT"' in source
    assert 'st.session_state.pop("GMV_REFUND_EXCEPTION_ROWS"' in source


def test_gmv_tab_distinguishes_read_only_preview_from_confirmed_ledger_write():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "Preview 不寫入 SQLite" in source
    assert "人工確認後才建立新的正式 active version" in source
    assert "不覆蓋正式營收看板" in source
