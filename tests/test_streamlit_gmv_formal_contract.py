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


def test_gmv_tab_has_explicit_formal_load_gate_before_database_read():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "載入正式淨 GMV" in source
    assert source.index("st.file_uploader(") < source.index("load_all_data_from_db()")
    assert "GMV_FORMAL_SCOPE_LOADED" in source


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
    assert "_render_active_gmv_scope" in tab_source
    assert "生成 active version 總退款及已退款完整報表" in active_source
    assert "GMV_ACTIVE_VERSION_WORKBOOKS" in active_source
    assert '"version_id": model.version_id' in active_source


def test_formal_preview_uses_revenue_only_generation_token():
    source = _function_source("_render_gmv_exclusion_tab")

    assert "revenue_state_token" in source
    assert "load_cache_generation" not in source
