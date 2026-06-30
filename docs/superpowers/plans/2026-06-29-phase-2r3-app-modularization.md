# Phase 2R-3 App Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the remaining `app.py` orchestration into focused modules while keeping Streamlit behavior, data results, and acceptance contracts unchanged.

**Architecture:** `app.py` becomes a thin defensive entrypoint. The current page renderers move into `app_pages.py`, and the remaining upload / quality / forecast orchestration helpers move into `app_workflows.py`. Shared constants and stable service imports stay in the existing lower-level modules so the behavior stays identical and the runtime boundary stays easy to verify.

**Tech Stack:** Python 3.10+, Streamlit, pandas, pytest.

---

## Implementation Addendum - 2026-06-30

Current status:

- `app.py` is now a thin defensive Streamlit entrypoint.
- `app_pages.py` hosts page/tab orchestration, upload UI, dashboard sections and GMV tab.
- `app_workflows.py` hosts upload/cache/quality/forecast/export workflow helpers.
- `app_styles.py` owns Streamlit CSS and theme tokens.
- `streamlit_rendering.py` owns shared sidebar, section and rendering helpers.
- `tests/test_app_module_boundaries.py` locks the major modularization boundary.
- `app_pages.py` no longer uses `from app_workflows import *`; dependencies are now explicit imports.

Remaining hardening after this plan:

- Replace broad `streamlit_rendering` star imports with explicit imports.
- Narrow `app_workflows.py` and `streamlit_rendering.py` export surfaces.
- Keep Streamlit smoke tests and `/api/dashboard/summary` baseline checks as guardrails after each split.

---

### Task 1: Lock the modularization boundary with tests

**Files:**
- Modify: `tests/test_streamlit_upload_feedback_contract.py`
- Create: `tests/test_app_module_boundaries.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
PAGES_PATH = Path(__file__).resolve().parents[1] / "app_pages.py"
WORKFLOWS_PATH = Path(__file__).resolve().parents[1] / "app_workflows.py"

def test_app_py_only_keeps_entrypoint_wiring():
    app_source = APP_PATH.read_text(encoding="utf-8")
    assert "def _render_dashboard_tab" not in app_source
    assert "def _render_upload_area" not in app_source
    assert "def _load_and_compute_cache" not in app_source

def test_pages_module_hosts_page_orchestration():
    pages_source = PAGES_PATH.read_text(encoding="utf-8")
    assert "def _render_dashboard_tab" in pages_source
    assert "def _render_upload_area" in pages_source

def test_workflows_module_hosts_non_ui_helpers():
    workflows_source = WORKFLOWS_PATH.read_text(encoding="utf-8")
    assert "def _load_and_compute_cache" in workflows_source
    assert "def _build_dashboard_kpis" in workflows_source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_app_module_boundaries.py`
Expected: FAIL because `app_pages.py` and `app_workflows.py` do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create the new modules with the moved functions and keep the existing behavior identical.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/test_app_module_boundaries.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py app_pages.py app_workflows.py tests/test_app_module_boundaries.py tests/test_streamlit_upload_feedback_contract.py
git commit -m "refactor: modularize app orchestration"
```

### Task 2: Move page orchestration into `app_pages.py`

**Files:**
- Create: `app_pages.py`
- Modify: `app.py`

- [ ] **Step 1: Write the failing test**

Use the boundary test above to confirm page renderers are no longer defined in `app.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_app_module_boundaries.py`
Expected: FAIL until the functions are moved.

- [ ] **Step 3: Write minimal implementation**

Move these functions into `app_pages.py` and keep their signatures unchanged:
`_render_config_tab`, `_render_upload_area`, `_render_year_summary`, `_render_rank_and_drilldown`, `_render_data_quality_scorecard`, `_render_entity_resolution_audit`, `_render_ai_cleaning_suggestions`, `_render_forecast_governance`, `_render_feature_store_lead_signals`, `_render_causal_driver_analytics`, `_render_backtest_report`, `_render_ai_and_exports`, `_render_dashboard_tab`, `_render_gmv_exclusion_tab`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q tests/test_app_module_boundaries.py tests/test_streamlit_upload_feedback_contract.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py app_pages.py tests/test_app_module_boundaries.py
git commit -m "refactor: move streamlit page orchestration"
```

### Task 3: Move reusable workflow helpers into `app_workflows.py`

**Files:**
- Create: `app_workflows.py`
- Modify: `app.py`, `app_pages.py`

- [ ] **Step 1: Write the failing test**

Extend the boundary test so `app.py` no longer defines the following helpers:
`_current_rules`, `_sum_money`, `_date_bounds`, `_fmt_date`, `_upload_batch_summary`, `_reset_uploaded_file`, `_parse_upload_dates`, `_uploaded_excel_frame`, `_upload_date_source_diagnostics_from_frames`, `_upload_date_source_diagnostics`, `_combined_max_date`, `_coerce_entity_audit_dataframe`, `_render_entity_audit_dataframe`, `_build_upload_stability_gate_workbook`, `_build_drift_diagnosis_workbook`, `_render_upload_stability_gate`, `_render_upload_audit_notice`, `_upsert_summary_rows`, `_strategy_by_horizon_from_backtest`, `_model_health_label`, `_quality_health_label`, `_safe_rate`, `_quality_score`, `_combine_quality_frames`, `_add_health_column`, `_build_dashboard_kpis`, `_load_pickle_cache`, `_load_ai_runtime_cache`, `_load_export_runtime_cache`, `_build_gmv_audit_workbook`, `_load_and_compute_cache`.

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_app_module_boundaries.py`
Expected: FAIL until the helpers are moved.

- [ ] **Step 3: Write minimal implementation**

Move the listed helpers into `app_workflows.py`, keep their current behavior intact, and update `app_pages.py` imports to use the new module.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py app_pages.py app_workflows.py tests/test_app_module_boundaries.py
git commit -m "refactor: extract app workflows"
```

### Task 4: Collapse `app.py` into a thin entrypoint

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Write the failing test**

Use the boundary test to confirm `app.py` only keeps startup wiring and `main()` invocation.

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest -q tests/test_app_module_boundaries.py`
Expected: FAIL until `app.py` is reduced.

- [ ] **Step 3: Write minimal implementation**

Keep `st.set_page_config`, defensive imports, session init, and the final `main()` call in `app.py`. Import the page/workflow functions from the new modules and remove the moved definitions from `app.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py app_pages.py app_workflows.py tests/test_app_module_boundaries.py
git commit -m "refactor: thin app entrypoint"
```
