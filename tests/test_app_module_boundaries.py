from pathlib import Path

import pandas as pd
import pyarrow as pa


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
PAGES_PATH = Path(__file__).resolve().parents[1] / "app_pages.py"
WORKFLOWS_PATH = Path(__file__).resolve().parents[1] / "app_workflows.py"
STYLES_PATH = Path(__file__).resolve().parents[1] / "app_styles.py"


def test_app_py_only_keeps_entrypoint_wiring():
    app_source = APP_PATH.read_text(encoding="utf-8")

    assert "def _render_dashboard_tab" not in app_source
    assert "def _render_upload_area" not in app_source
    assert "def _load_and_compute_cache" not in app_source


def test_pages_module_hosts_page_orchestration():
    pages_source = PAGES_PATH.read_text(encoding="utf-8")

    assert "def _render_dashboard_tab" in pages_source
    assert "def _render_upload_area" in pages_source


def test_pages_module_uses_explicit_workflow_imports():
    pages_source = PAGES_PATH.read_text(encoding="utf-8")

    assert "from app_workflows import *" not in pages_source
    assert "from app_workflows import (" in pages_source
    assert "import hashlib" in pages_source
    assert "import io" in pages_source
    assert "import time" in pages_source
    assert "import numpy as np" in pages_source


def test_workflows_module_hosts_non_ui_helpers():
    workflows_source = WORKFLOWS_PATH.read_text(encoding="utf-8")

    assert "def _load_and_compute_cache" in workflows_source
    assert "def _build_dashboard_kpis" in workflows_source


def test_app_py_no_longer_holds_top_level_css():
    app_source = APP_PATH.read_text(encoding="utf-8")
    styles_source = STYLES_PATH.read_text(encoding="utf-8")

    assert "@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC" not in app_source
    assert "div.stButton > button[kind=\"primary\"]" not in app_source
    assert "@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC" in styles_source
    assert "div.stButton > button[kind=\"primary\"]" in styles_source


def test_pages_module_runtime_namespace_has_private_helpers():
    import app_pages

    for helper_name in [
        "_render_anchor",
        "_render_error",
        "_load_and_compute_cache",
        "_render_sidebar_shell",
        "_render_kpi_filter_center",
        "_render_rank_filter_center",
        "_build_dashboard_kpis",
    ]:
        assert hasattr(app_pages, helper_name), f"{helper_name} should be available to app_pages at runtime"


def test_dashboard_page_passes_scope_label_to_executive_band():
    pages_source = PAGES_PATH.read_text(encoding="utf-8")

    assert "_render_executive_summary_band(REVENUE_SCOPE_LABEL)" in pages_source
    assert "_render_executive_summary_band()" not in pages_source


def test_arrow_safe_display_frame_normalizes_mixed_object_columns():
    from app_pages import _coerce_arrow_safe_display_frame

    mixed = pd.DataFrame(
        [
            {"指標": "分析類型", "數值": "解釋型 Driver Analytics", "說明": "文字"},
            {"指標": "目前期間收入", "數值": 9485484.29, "說明": "數字"},
            {"指標": "缺值", "數值": None, "說明": "空值"},
        ]
    )

    safe = _coerce_arrow_safe_display_frame(mixed)

    assert safe["數值"].tolist() == ["解釋型 Driver Analytics", "9485484.29", "不適用"]
    pa.Table.from_pandas(safe)
