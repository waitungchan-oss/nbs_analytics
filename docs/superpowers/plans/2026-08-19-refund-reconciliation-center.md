# Refund Reconciliation Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 GMV 退款扣減看板中加入退款 Preflight、三段式來源單據匹配分類與只讀 Exception Center，讓總退款／已退款結果可在生成報表前被檢查與追查。

**Architecture:** 沿用 `app_workflows.py` 的純 pandas workflow helpers 與 `app_pages.py` 的既有 Streamlit GMV tab，不新增資料庫、API 或外部服務。先把退款列標準化，再建立 raw SQLite 與正式 Revenue Scope 的來源單據索引，最後由同一個 read model 同時供 Preflight、Exception Center 與既有扣減／匯出流程使用。所有結果留在 session state，正式 SQLite 與正式 export cache 保持不變。

**Tech Stack:** Python 3、pandas、pytest、Streamlit、既有 `clean_invoice_number()`、`_build_revenue_scope_frames()`、`_apply_gmv_refund_adjustments()` 與 `st.session_state`。

**Spec:** `docs/superpowers/specs/2026-08-19-refund-reconciliation-center-design.md`

## Global Constraints

- 正式收入口徑固定為 `不含掛賬核銷與TT退款轉團款`。
- GMV 退款扣減是 session-only derived view，不回寫正式 SQLite。
- 不新增 SQLite table、migration、API router、外部服務、背景 job、approval、dispatch 或 orchestration control。
- 不修改正式 upload transaction、upsert、rollback、baseline、rules config、AI cache、WAPE 或正式 export schema。
- `總退款` 使用全部有效退款狀態；`已退款` 只使用 `退款狀態 == 已退款`。
- Implementation runner 不 commit、push、merge 或修改正式 runtime；完成每個 Task 後交付 diff 與測試證據給 Review checkpoint。
- 任何可疑資料只產生 `blocking`／`warning`／`info` issue，不以未驗證的固定匹配率門檻阻塞跨期間退款檔。

---

## File Map

- Modify: `app_workflows.py`
  - 保留 `_parse_gmv_refund_data()` 的檔案 adapter。
  - 新增退款列 normalization、file metrics、match index、exception row 與 Preflight read model。
  - 讓既有 `_apply_gmv_refund_adjustments()` 的結果可被 Preflight 共用；其既有回傳欄位與扣減結果保持不變。
- Modify: `app_pages.py`
  - 在 `_render_gmv_exclusion_tab()` 中插入 Preflight gate、dimension comparison、Exception Center 與 CSV download。
  - 管理退款 signature 變更時的 stale session state。
- Modify: `tests/test_gmv_refund_adjustment.py`
  - 保留既有總退款／已退款聚合、比例分配與超額 cap regression tests；補上與分類結果的契約。
- Create: `tests/test_gmv_refund_preflight.py`
  - 測試檔案 schema、metrics、三段式匹配、status dimensions、issues 與 exception row。
- Create: `tests/test_streamlit_gmv_refund_contract.py`
  - 以 AST/source contract 測試 UI 先 Preflight、blocked gate、stale state、exception CSV 與報表保留。

## Task 1: 建立退款列 normalization 與檔案 Preflight 基礎

**Files:**
- Modify: `app_workflows.py` around `_parse_gmv_refund_data()` (目前約 1049-1061 行)
- Create: `tests/test_gmv_refund_preflight.py`

**Interfaces:**
- Consumes: raw `pd.DataFrame` from `_read_gmv_exclusion_file()` and the existing refund columns.
- Produces:
  - `_normalize_gmv_refund_rows(refund_rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]`
  - `fileMetrics` fields and schema issues consumed by Task 2's full Preflight builder.

- [ ] **Step 1: Write failing tests for required columns and status aliases**

Add these tests to `tests/test_gmv_refund_preflight.py`:

```python
import pandas as pd
import pytest

from app_workflows import _normalize_gmv_refund_rows


def test_refund_rows_accept_simplified_status_and_return_canonical_columns():
    rows, metrics = _normalize_gmv_refund_rows(
        pd.DataFrame(
            [
                {"來源單據號": " A-1 ", "退款原幣金額": "30.5", "退款状态": "已退款"},
                {"來源單據號": "A-1", "退款原幣金額": "20", "退款状态": "待退款"},
            ]
        )
    )

    assert list(rows.columns) == ["來源單據號", "退款原幣金額", "退款狀態"]
    assert rows["來源單據號"].tolist() == ["A-1", "A-1"]
    assert rows["退款原幣金額"].tolist() == [pytest.approx(30.5), pytest.approx(20.0)]
    assert metrics["sourceOrders"] == 1
    assert metrics["statusCounts"] == {"已退款": 1, "待退款": 1}


def test_refund_rows_report_blocking_schema_issue_without_required_columns():
    rows, metrics = _normalize_gmv_refund_rows(
        pd.DataFrame([{"來源單據號": "A-1", "退款原幣金額": "30"}])
    )

    assert rows.empty
    assert metrics["status"] == "blocked"
    assert "退款狀態" in metrics["missing"]


def test_refund_rows_count_duplicate_invalid_and_negative_values():
    rows, metrics = _normalize_gmv_refund_rows(
        pd.DataFrame(
            [
                {"來源單據號": "A-1", "退款原幣金額": "30", "退款狀態": "已退款"},
                {"來源單據號": "A-1", "退款原幣金額": "30", "退款狀態": "已退款"},
                {"來源單據號": "A-2", "退款原幣金額": "-5", "退款狀態": "待退款"},
                {"來源單據號": "A-3", "退款原幣金額": "bad", "退款狀態": "待退款"},
            ]
        )
    )

    assert len(rows) == 4
    assert metrics["duplicateRows"] == 1
    assert metrics["negativeAmountRows"] == 1
    assert metrics["invalidAmountRows"] == 1
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_preflight.py
```

Expected: FAIL because `_normalize_gmv_refund_rows` does not yet exist.

- [ ] **Step 3: Implement normalization without changing the file adapter contract**

In `app_workflows.py`, implement `_normalize_gmv_refund_rows()` with this behavior:

```python
def _normalize_gmv_refund_rows(refund_rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    status_column = "退款状态" if "退款状态" in refund_rows.columns else "退款狀態"
    required = {COL_ORDER_ID, "退款原幣金額", status_column}
    missing = sorted(required - set(refund_rows.columns))
    if missing:
        return pd.DataFrame(columns=[COL_ORDER_ID, "退款原幣金額", "退款狀態"]), {
            "status": "blocked",
            "missing": ["退款狀態" if item == "退款狀態" else item for item in missing],
        }

    work = refund_rows[[COL_ORDER_ID, "退款原幣金額", status_column]].copy()
    work.rename(columns={status_column: "退款狀態"}, inplace=True)
    raw_amount = work["退款原幣金額"].copy()
    parsed_amount = pd.to_numeric(raw_amount, errors="coerce")
    work[COL_ORDER_ID] = clean_invoice_number(work[COL_ORDER_ID])
    work["退款原幣金額"] = parsed_amount.fillna(0.0)
    work["退款狀態"] = work["退款狀態"].fillna("").astype(str).str.strip()
    valid_source = ~work[COL_ORDER_ID].isin(["", "NAN"])
    work = work.loc[valid_source].reset_index(drop=True)
    duplicate_rows = int(work.duplicated(keep="first").sum())
    status_counts = {
        str(key): int(value)
        for key, value in work["退款狀態"].value_counts(dropna=False).to_dict().items()
    }
    metrics = {
        "status": "ready" if not work.empty else "blocked",
        "missing": [],
        "sourceOrders": int(work[COL_ORDER_ID].nunique()),
        "duplicateRows": duplicate_rows,
        "emptySourceRows": int((~valid_source).sum()),
        "invalidAmountRows": int(parsed_amount.isna().sum()),
        "negativeAmountRows": int((parsed_amount < 0).fillna(False).sum()),
        "zeroAmountRows": int((parsed_amount == 0).fillna(False).sum()),
        "statusCounts": status_counts,
        "refundTotal": float(work["退款原幣金額"].clip(lower=0).sum()),
    }
    return work, metrics
```

The implementation must preserve all valid rows for the total dimension. Do not silently drop duplicate rows; count them as an issue and let Task 2 surface the warning.

- [ ] **Step 4: Update `_parse_gmv_refund_data()` to delegate normalization**

Keep `_parse_gmv_refund_data(file_obj) -> tuple[pd.DataFrame, pd.DataFrame]` for the current UI. It should read the file, call `_normalize_gmv_refund_rows()`, raise a user-facing `ValueError` for a blocking schema, and return the canonical rows as both parsed data and audit input so existing callers remain compatible.

- [ ] **Step 5: Run focused tests and compile check**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_preflight.py
.venv/bin/python -m pytest -q tests/test_gmv_refund_adjustment.py
.venv/bin/python -m py_compile app_workflows.py
```

Expected: all focused tests pass; existing refund adjustment tests remain green.

## Task 2: 建立三段式匹配、Preflight 維度摘要與 Exception rows

**Files:**
- Modify: `app_workflows.py` around `_apply_gmv_refund_adjustments()` and `_gmv_summary_rows()` (目前約 1064-1200 行)
- Modify: `tests/test_gmv_refund_adjustment.py`
- Modify: `tests/test_gmv_refund_preflight.py`

**Interfaces:**
- Consumes: canonical refund rows and raw/formal revenue frames from Task 1.
- Produces:
  - `_build_gmv_refund_match_index(raw_tour: pd.DataFrame, raw_others: pd.DataFrame, formal_tour: pd.DataFrame, formal_others: pd.DataFrame) -> pd.DataFrame`
  - `_build_gmv_refund_exception_rows(refund_rows: pd.DataFrame, raw_tour: pd.DataFrame, raw_others: pd.DataFrame, formal_tour: pd.DataFrame, formal_others: pd.DataFrame, refund_status: str | None = None) -> pd.DataFrame`
  - `_build_gmv_refund_preflight(raw_tour: pd.DataFrame, raw_others: pd.DataFrame, refund_rows: pd.DataFrame, formal_tour: pd.DataFrame | None = None, formal_others: pd.DataFrame | None = None) -> dict`
  - Existing `_apply_gmv_refund_adjustments()` result remains backward compatible; classification is produced by `_build_gmv_refund_match_index()` and `_build_gmv_refund_exception_rows()`, so the existing engine does not require a new result field.

- [ ] **Step 1: Write failing tests for the three match states and both dimensions**

Append tests to `tests/test_gmv_refund_preflight.py`:

```python
from app_workflows import (
    _build_gmv_refund_exception_rows,
    _build_gmv_refund_preflight,
)


def _raw_rows():
    return pd.DataFrame(
        [
            {"來源單據號": "FORMAL-1", "收款原幣金額": 100.0, "收款類型": "正常", "收款方式": "現金"},
            {"來源單據號": "EXCLUDED-1", "收款原幣金額": 80.0, "收款類型": "掛賬核銷", "收款方式": "現金"},
        ]
    )


def test_preflight_classifies_formal_excluded_and_missing_sources():
    raw = _raw_rows()
    refunds = pd.DataFrame(
        [
            {"來源單據號": "FORMAL-1", "退款原幣金額": 20.0, "退款狀態": "已退款"},
            {"來源單據號": "EXCLUDED-1", "退款原幣金額": 30.0, "退款狀態": "已退款"},
            {"來源單據號": "MISSING-1", "退款原幣金額": 40.0, "退款狀態": "已退款"},
        ]
    )

    formal = raw.loc[raw["收款類型"] != "掛賬核銷"].copy()
    report = _build_gmv_refund_preflight(raw, pd.DataFrame(), refunds, formal, pd.DataFrame())

    total = report["dimensions"]["總退款"]
    assert total["matchedFormalOrders"] == 1
    assert total["matchedExcludedOrders"] == 1
    assert total["unmatchedOrders"] == 1
    assert set(report["exceptionRows"]["匹配狀態"]) == {
        "正式口徑匹配", "被收入規則排除", "SQLite 找不到"
    }


def test_preflight_paid_dimension_uses_only_exact_paid_status():
    raw = pd.DataFrame([{"來源單據號": "A-1", "收款原幣金額": 100.0}])
    refunds = pd.DataFrame(
        [
            {"來源單據號": "A-1", "退款原幣金額": 20.0, "退款狀態": "待退款"},
            {"來源單據號": "A-1", "退款原幣金額": 30.0, "退款狀態": "已退款"},
        ]
    )

    report = _build_gmv_refund_preflight(raw, pd.DataFrame(), refunds, raw, pd.DataFrame())

    assert report["dimensions"]["總退款"]["refundTotal"] == pytest.approx(50.0)
    assert report["dimensions"]["已退款"]["refundTotal"] == pytest.approx(30.0)


def test_exception_rows_include_required_audit_columns():
    raw = pd.DataFrame([{"來源單據號": "A-1", "收款原幣金額": 100.0}])
    refunds = pd.DataFrame(
        [{"來源單據號": "A-1", "退款原幣金額": 120.0, "退款狀態": "已退款"}]
    )

    rows = _build_gmv_refund_exception_rows(refunds, raw, pd.DataFrame(), raw, pd.DataFrame())

    assert {"退款維度", "來源單據號", "匹配狀態", "原因代碼", "超額退款金額"}.issubset(rows.columns)
    assert rows.iloc[0]["原因代碼"] == "OVER_REFUND"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_preflight.py
```

Expected: FAIL because the match index, exception builder and Preflight read model do not yet exist.

- [ ] **Step 3: Implement a normalized raw/formal match index**

Implement `_build_gmv_refund_match_index()` by concatenating raw tour/others and formal tour/others with `資料表` labels, normalizing `來源單據號` through `_order_id_series()`, and returning one row per source ID with:

```text
來源單據號
raw_present
formal_present
raw_amount
formal_amount
資料表
分社
銷售代表
```

Use these deterministic rules:

```python
if formal_present:
    status = "正式口徑匹配"
elif raw_present:
    status = "被收入規則排除"
else:
    status = "SQLite 找不到"
```

Do not infer a match from product name, branch, amount, or date. The only match key is normalized `來源單據號`.

- [ ] **Step 4: Implement dimension-specific exception rows**

Implement `_build_gmv_refund_exception_rows()` as an additive read model over the existing engine:

1. Filter `refund_rows` to all valid rows for `refund_status=None` or exact `退款狀態 == refund_status`.
2. Group by `來源單據號` to calculate `退款明細金額`.
3. Join the match index and current adjustment detail by source ID.
4. Set `退款維度` to `總退款` or `已退款`.
5. Set `是否可扣減` to `True` only for `正式口徑匹配` rows in the formal analysis frame.
6. Set `原因代碼` using the first applicable condition in this order: invalid amount, empty source, over-refund, duplicate row, scope exclusion, source not found, formal matched.
7. Preserve representative `資料表`、`分社`、`銷售代表` values from the raw match index.

The output must include all required audit columns from the spec and remain a pandas DataFrame even when empty.

- [ ] **Step 5: Implement `_build_gmv_refund_preflight()`**

The function must:

1. Normalize/validate rows using Task 1.
2. Default `formal_tour` and `formal_others` by calling `_build_revenue_scope_frames(raw_tour, raw_others)` when the caller does not provide them.
3. Build one exception DataFrame for `總退款` and one for `已退款`, concatenate them into `exceptionRows`.
4. Call `_apply_gmv_refund_adjustments()` once for each dimension to preserve the existing proportional allocation and cap behavior.
5. Return `dimensions` with `sourceOrders`, `matchedFormalOrders`, `matchedExcludedOrders`, `unmatchedOrders`, `formalMatchRate`, `unmatchedAmount`, `refundTotal`, `appliedRefundTotal`, and `overRefundTotal`.
6. Build issue rows with only deterministic counts, amounts, examples, and user-facing Traditional Chinese messages.
7. Set status to `blocked` for Task 1 blocking conditions, `warning` when any warning issue exists, otherwise `ready`.

The `appliedRefundTotal` in each dimension must be labeled according to the frame used by the existing GMV output. The report must additionally expose formal-match counts so a raw/all-source GMV amount is not mistaken for formal Revenue Scope impact.

- [ ] **Step 6: Keep the existing engine regression-safe**

Update `tests/test_gmv_refund_adjustment.py` with additive assertions:

```python
assert result["refund_status"] == "總退款"
assert paid_result["refund_status"] == "已退款"
assert result["tour"]["收款原幣金額"].sum() == pytest.approx(150.0)
```

Do not change the existing expected proportional allocation or cap values.

- [ ] **Step 7: Run Task 2 focused tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_preflight.py tests/test_gmv_refund_adjustment.py
.venv/bin/python -m py_compile app_workflows.py
```

Expected: all new and existing refund tests pass.

## Task 3: Integrate Preflight and Exception Center into the GMV Streamlit tab

**Files:**
- Modify: `app_pages.py` inside `_render_gmv_exclusion_tab()` (目前約 2415-2597 行)
- Create: `tests/test_streamlit_gmv_refund_contract.py`

**Interfaces:**
- Consumes: `_parse_gmv_refund_data()`, `_build_revenue_scope_frames()`, `_build_gmv_refund_preflight()`, `_apply_gmv_refund_adjustments()` and existing workbook builders.
- Produces: visible Preflight summary, blocked/warning gate, dimension comparison, filtered exception dataframe and CSV download while preserving current dual workbook downloads.

- [ ] **Step 1: Write failing source contract tests**

Create `tests/test_streamlit_gmv_refund_contract.py`:

```python
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


def test_gmv_tab_keeps_read_only_boundary_visible():
    source = _function_source("_render_gmv_exclusion_tab")
    assert "不回寫 SQLite" in source
    assert "不覆蓋正式營收看板" in source
```

- [ ] **Step 2: Run the contract tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_streamlit_gmv_refund_contract.py
```

Expected: FAIL because the page does not call the new Preflight model or expose the new state keys.

- [ ] **Step 3: Add formal frames and Preflight state to the page**

Immediately after loading `db_tour` and `db_others`, derive formal frames once:

```python
formal_tour, formal_others, scope_audit = _build_revenue_scope_frames(db_tour, db_others)
```

After parsing `refund_data`, compute a content signature from canonical rows, then clear all stale derived state when it changes:

```python
if st.session_state.get("GMV_REFUND_PREFLIGHT_SIGNATURE") != current_signature:
    st.session_state["GMV_REFUND_PREFLIGHT_SIGNATURE"] = current_signature
    st.session_state.pop("GMV_REFUND_PREFLIGHT", None)
    st.session_state.pop("GMV_REFUND_EXCEPTION_ROWS", None)
    st.session_state.pop("GMV_EXCLUSION_WORKBOOKS", None)
```

Build the report for the current upload and keep it in session state for the current rerun:

```python
preflight_report = _build_gmv_refund_preflight(
    db_tour,
    db_others,
    refund_data,
    formal_tour,
    formal_others,
)
st.session_state["GMV_REFUND_PREFLIGHT"] = preflight_report
st.session_state["GMV_REFUND_EXCEPTION_ROWS"] = preflight_report.get("exceptionRows", pd.DataFrame())
```

- [ ] **Step 4: Render the Preflight gate before any GMV adjustment**

Render a compact panel containing file metrics, status counts, and the two dimension summaries. Use `st.error()` for `blocked`, `st.warning()` for `warning`, and `st.success()` for `ready`.

If blocked, render the issue table and return before calling `_apply_gmv_refund_adjustments()` or showing the report generation button:

```python
if preflight_report.get("status") == "blocked":
    st.error("退款 Preflight 未通過，請先修正退款檔案。")
    _render_refund_issue_table(preflight_report.get("issues") or [])
    return
```

Warning must allow continuation, but the warning status and issue summary must remain visible above the GMV results.

- [ ] **Step 5: Render the Exception Center and CSV download**

Add a bounded read-only section with two selectors:

```python
dimension = st.selectbox("退款維度", ["總退款", "已退款"], key="GMV_REFUND_EXCEPTION_DIMENSION")
match_state = st.selectbox(
    "匹配狀態",
    ["全部", "正式口徑匹配", "被收入規則排除", "SQLite 找不到"],
    key="GMV_REFUND_EXCEPTION_MATCH_STATE",
)
```

Filter only the session DataFrame, display the required audit columns, and expose a CSV download with UTF-8 BOM for Excel compatibility:

```python
exception_csv = filtered_exceptions.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "下載退款對帳異常 CSV",
    exception_csv,
    "退款對帳異常中心.csv",
    mime="text/csv",
    width="stretch",
)
```

The UI must label `是否可扣減` as formal Revenue Scope impact and keep the existing total/paid workbook download loop unchanged except for using the already validated state.

- [ ] **Step 6: Run Task 3 tests and compile check**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_streamlit_gmv_refund_contract.py tests/test_gmv_refund_preflight.py tests/test_gmv_refund_adjustment.py
.venv/bin/python -m py_compile app_pages.py app_workflows.py
git diff --check
```

Expected: all focused tests pass and no whitespace errors are reported.

## Task 4: Integrated verification and acceptance evidence

**Files:**
- Read-only verification of `app_pages.py`, `app_workflows.py`, and tests.
- No source file changes unless a preceding test identifies a concrete contract defect.

**Interfaces:**
- Consumes: the completed Preflight read model, exception UI, existing SQLite read-only loader, and the attached refund workbook.
- Produces: targeted test evidence, full test evidence, Hermes report, and manual UI acceptance notes.

- [ ] **Step 1: Run the complete refund-focused test pack**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_gmv_refund_preflight.py \
  tests/test_gmv_refund_adjustment.py \
  tests/test_streamlit_gmv_refund_contract.py
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Run full project verification**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py forecasting.py pipeline.py database.py business_calendar.py visuals.py backend/services/upload_preflight_service.py scripts/system_manager.py scripts/validate_business_calendar.py scripts/inspect_sqlite_latest.py scripts/prewarm_ai_cache.py
git diff --check
```

Expected: the complete suite passes, all listed modules compile, and `git diff --check` is clean.

- [ ] **Step 3: Run Hermes post-change verification**

Run:

```bash
.venv/bin/python scripts/hermes_post_change_check.py --json
```

Expected: JSON contains `"overallStatus": "pass"`; system readiness, SQLite integrity, frozen baseline, revenue scope, and targeted acceptance remain matched.

- [ ] **Step 4: Perform read-only manual UI acceptance with the supplied refund file**

Use `退款明細數據.xlsx` at `/Users/chanwaitung2025/Downloads/退款明細數據.xlsx` in the local Streamlit GMV tab and verify:

1. Preflight appears before the GMV KPI and report-generation controls.
2. `總退款` and `已退款` show separate status distributions and matching metrics.
3. The exception selector can show `正式口徑匹配`、`被收入規則排除` and `SQLite 找不到` rows.
4. The CSV download contains `退款維度`、`匹配狀態`、`原因代碼` and source order columns.
5. A blocked fixture does not show report generation controls.
6. The existing total and paid workbook buttons still generate both report sets.
7. SQLite file signature, generation token and row counts are unchanged before and after the interaction.

- [ ] **Step 5: Produce the implementation handoff**

The implementation runner reports, without committing or merging:

```text
Task status: complete / blocked
Files changed: exact paths
Focused tests: command + result
Full tests: command + result
Hermes: overallStatus
SQLite/baseline/runtime mutation: none observed / concrete finding
Open findings: exact issue or none
```

After the Review Agent returns findings-first PASS, Codex performs any findings fix, reruns full verification and Hermes, and only then decides whether a separate push/PR/merge action is requested.

## Plan Self-Review

- Spec coverage: Tasks 1-2 cover the Preflight schema, metrics, issue severity, three match states, refund dimensions, aggregation, proportional allocation, cap, and exception rows; Task 3 covers the Streamlit gate, stale state, filters, CSV, and existing workbook preservation; Task 4 covers full tests, Hermes, and manual acceptance.
- Placeholder scan: no `TBD`, `TODO`, `FIXME`, or unspecified implementation step is required.
- Type consistency: Task 1 produces canonical refund rows and file metrics; Task 2 consumes those rows and produces `_build_gmv_refund_preflight()`; Task 3 consumes that exact function and stores `exceptionRows`; Task 4 verifies the resulting UI and runtime boundaries.
- Boundary check: no task writes SQLite, changes formal Revenue Scope rules, adds a database/API surface, or modifies Forecast/WAPE behavior.
