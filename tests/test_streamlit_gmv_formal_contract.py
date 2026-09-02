import ast
from pathlib import Path

import pytest

from app_pages import _active_gmv_summary_rows


PAGES_PATH = Path(__file__).resolve().parents[1] / "app_pages.py"
SMOKE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "streamlit_ui_smoke.py"


def _function_source(name: str) -> str:
    source = PAGES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found")


def test_gmv_tab_reads_active_scope_without_a_manual_load_gate():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "載入正式淨 GMV" not in source
    assert source.index("st.file_uploader(") < source.index("load_all_data_from_db()")
    assert "load_gmv_export_cache" in source


def test_gmv_tab_never_runs_schema_migration_during_render():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "migrate_gmv_schema" not in source
    assert "GmvRefundRepository" in source


def test_formal_labels_keep_total_refund_operational_and_quantity_original():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "總退款" in source
    assert "已退款" in source
    assert "原交易人數／數量（未按退款調整）" in source


def test_active_scope_reopens_without_refund_upload_and_supports_versioned_exports():
    tab_source = _function_source("_render_gmv_exclusion_tab")
    active_source = _function_source("_render_active_gmv_scope")

    assert "build_active_gmv_read_model" in tab_source
    assert "cache_manifest=cache_manifest" in tab_source
    assert "read_gmv_export_artifact" in tab_source
    assert "生成 active version 總退款及已退款完整報表" not in active_source
    assert "_compute_gmv_exclusion_workbooks" not in tab_source


def test_formal_preview_uses_revenue_only_generation_token():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "revenue_state_token" in source
    assert "load_cache_generation" not in source


def test_active_summary_contract_rejects_missing_before_gmv():
    adjusted = {
        "refund_status": "總退款",
        "refund_total": 20.0,
        "applied_refund_total": 20.0,
        "over_refund_total": 0.0,
    }

    with pytest.raises(ValueError, match="before_gmv"):
        _active_gmv_summary_rows(adjusted)


def test_served_ui_smoke_targets_the_gmv_refund_uploader_semantically():
    source = SMOKE_PATH.read_text(encoding="utf-8")

    assert 'section[data-testid="stFileUploaderDropzone"][aria-label^="上傳退款明細數據"] input[type="file"]' in source
    assert 'locator("input[type=file]").last' not in source
