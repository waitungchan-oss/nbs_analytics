# 2026-07-09 Change Checkpoint

This checkpoint groups the current working-tree changes before the next
optimization phase. It is a coordination artifact only; it does not change
runtime behavior.

## Guardrails

- Official revenue scope remains `不含掛賬核銷與TT退款轉團款`.
- 2026-05 frozen baseline must remain `HKD 12,057,968`.
- Export schema changes must not be used to repair baseline drift.
- If drift appears, investigate upload write path, SQLite upsert, rollback,
  quarantine DB, and stability history before touching presentation layers.

## Change Groups

### 1. Official Export Schema Contract

Files:

- `app.py`
- `app_workflows.py`
- `pipeline.py`
- `tests/test_pipeline_preloaded_frames.py`
- `tests/test_official_export_workbook_contract.py`

Scope:

- Adds the official workbook sheet `分社經營統計_含銷售員`.
- Adds `旅行團交易人數` and `票務交易數量`.
- Bumps lazy export cache to `export-lazy-v3`.
- Verifies the new sheet totals against existing branch amount, tour-count,
  and ticket-count sheets.

### 2. 2026-06 E6 to 0A Reassignment Rule

Files:

- `rules.py`
- `rules_config.json`
- `config.py`
- `database.py`
- `pipeline.py`
- `tests/test_database_rollback.py`
- `tests/test_pipeline_preloaded_frames.py`

Scope:

- Limits the E6 上環服務點 to 0A 展覽會場專用 reassignment to `2026-06`.
- Preserves later months such as `2026-07` under their original assignment.

### 3. Upload Profiling and Drift Diagnosis

Files:

- `backend/services/upload_preflight_service.py`
- `backend/services/drift_diagnosis_service.py`
- `backend/services/upload_profiling_service.py`
- `scripts/upload_profiling_dry_run.py`
- `tests/test_upload_preflight_service.py`
- `tests/test_drift_diagnosis_service.py`
- `tests/test_upload_profiling_service.py`

Scope:

- Adds profiling hooks and dry-run support for upload/preflight timing.
- Narrows the area for future performance work to preflight, temporary DB
  validation, and drift diagnosis rather than SQLite upsert.

### 4. Streamlit Upload and Export Feedback

Files:

- `app_pages.py`
- `app_workflows.py`
- `tests/test_streamlit_upload_feedback_contract.py`

Scope:

- Surfaces upload stage timing and preflight internal timing.
- Keeps AI rebuild deferred and export generation lazy.

### 5. Context and Handoff Documentation

Files:

- `Summay/`
- Obsidian NBS Analytics knowledge base

Scope:

- Documents frozen baseline, acceptance baseline, and export contract decisions.
- Provides future Codex/Hermes context anchors.

## Recommended Validation Before Commit

```bash
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py forecasting.py pipeline.py database.py business_calendar.py visuals.py backend/services/upload_preflight_service.py scripts/system_manager.py
.venv/bin/python -m pytest tests/test_official_export_workbook_contract.py tests/test_pipeline_preloaded_frames.py tests/test_phase2_precheck_acceptance.py -q
.venv/bin/python -m pytest tests/test_dashboard_service.py tests/test_dashboard_api.py -q
.venv/bin/python -m pytest tests/test_database_rollback.py tests/test_upload_rollback_service.py tests/test_stability_history_service.py -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --json
```

## Suggested Commit Grouping

1. `feat: add official export branch salesperson contract`
2. `feat: add month-scoped branch reassignment override`
3. `feat: add upload profiling and drift diagnosis instrumentation`
4. `docs: add nbs analytics baseline and export context`

Do not merge these groups blindly if the working tree has changed since this
checkpoint. Re-run Hermes before commit.
