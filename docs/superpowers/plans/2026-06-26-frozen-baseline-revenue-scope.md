# Frozen Baseline Revenue Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent later `掛賬核銷` / `TT 退款轉團款` rows from retroactively excluding already validated non-excluded receipts under the same source order id.

**Architecture:** Keep historical analysis scope unchanged inside `backend/services/revenue_scope_service.py`. Add write-time protection in `database.upsert_to_db`: preserve excluded receipt rows that already exist in SQLite, but skip newly uploaded excluded receipt numbers so they cannot retroactively alter locked-month source-order scope.

**Tech Stack:** Python, pandas, SQLite-backed read models, pytest.

---

### Task 1: Add Regression Test

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_dashboard_service.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_dashboard_service.py`

- [ ] **Step 1: Write the failing test**

Add a test that builds two rows with the same `來源單據號`: one historical normal travel receipt and one later `掛賬核銷` receipt. Assert that only the `掛賬核銷` row is excluded.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_dashboard_service.py::test_revenue_scope_does_not_retroactively_exclude_non_writeoff_receipts -q
```

Expected: FAIL because current implementation excludes by `來源單據號`.

### Task 2: Implement New-Receipt Upload Scope Guard

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/database.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_database_rollback.py`

- [ ] **Step 1: Query existing receipt numbers**

Before deleting or appending rows, query existing `收款單號` values from the target table.

- [ ] **Step 2: Filter only new excluded receipt rows**

Skip rows where `收款類型 = 掛賬核銷` or `收款方式 = TT 退款轉團款` only when their `收款單號` does not already exist in SQLite. Preserve existing excluded receipt rows from full snapshot uploads.

- [ ] **Step 3: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_database_rollback.py::test_upsert_filters_new_excluded_receipt_rows_without_deleting_existing_non_excluded_source_order tests/test_database_rollback.py::test_upsert_preserves_existing_excluded_receipts_from_full_snapshot_upload -q
```

Expected: PASS.

### Task 3: Verify Real 0625 Incident and Full Suite

**Files:**
- No production file changes beyond Task 2.

- [ ] **Step 1: Run incident check against quarantine DB**

Use the same dashboard summary path to verify the previously drifted quarantine DB no longer drops the 5 月 `JIA 江嘉韵` non-writeoff receipt under the new analysis rule.

- [ ] **Step 2: Run all tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run operational acceptance**

Run:

```bash
.venv/bin/python scripts/system_manager.py acceptance
```

Expected: `status` is `passed`.
