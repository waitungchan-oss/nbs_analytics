# Single Order 0A Reassignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `E9MF16613172500` 在 2026年6月精確歸入 0A 展覽會場專用，並以正式計算鏈路驗證上環、0A、六月總額及五月 frozen baseline。

**Architecture:** 擴充既有 `BRANCH_REASSIGNMENT_OVERRIDES`，新增完整來源單據號 `source_order_id` 等值條件。Pipeline 與 SQLite repair 各自讀取同一欄位，持久設定與預設規則保持一致；正式修復沿用現有 hot backup 與 cache rebuild 流程。

**Tech Stack:** Python 3、pandas、SQLite、pytest、Streamlit runtime cache、既有 Hermes post-change check。

## Global Constraints

- 只處理來源單據號 `E9MF16613172500`，不得擴大至其他 E9 訂單。
- 只處理 `2026-06` 且目前銷售點或副表銷售點為「上環服務點」的資料。
- 現有 `2026-06 + E6 + 上環服務點 -> 0A` 規則保持不變。
- 正式口徑維持「不含掛賬核銷與TT退款轉團款」。
- 不改交易金額、收入排除規則、銷售員歸屬或人數計算。
- 驗收值：上環 `HKD 0`、0A `HKD 703,425`、六月總額 `HKD 9,083,241`、五月 baseline `HKD 12,057,968`。

---

### Task 1: Pipeline 精確訂單匹配

**Files:**
- Modify: `tests/test_pipeline_preloaded_frames.py`
- Modify: `pipeline.py:291-307`

**Interfaces:**
- Consumes: override dictionary optional field `source_order_id: str`。
- Produces: `apply_branch_reassignment_overrides(df, overrides, anomaly_log)` 支援完整來源單據號等值匹配。

- [ ] **Step 1: 寫入失敗測試**

在 `tests/test_pipeline_preloaded_frames.py` 新增：

```python
def test_branch_reassignment_override_matches_one_exact_source_order_only():
    import pipeline

    source = pd.DataFrame(
        [
            {"來源單據號": "E9MF16613172500", "統一日期": "2026-06-13", "銷售點": "上環服務點", "副表_銷售點": "上環服務點"},
            {"來源單據號": "E9OTHER202606", "統一日期": "2026-06-13", "銷售點": "上環服務點", "副表_銷售點": "上環服務點"},
            {"來源單據號": "E9MF16613172500", "統一日期": "2026-07-13", "銷售點": "上環服務點", "副表_銷售點": "上環服務點"},
            {"來源單據號": "E9MF16613172500", "統一日期": "2026-06-13", "銷售點": "元朗服務點", "副表_銷售點": "元朗服務點"},
        ]
    )
    override = {
        "month": "2026-06",
        "source_order_id": "E9MF16613172500",
        "from_branch": "上環服務點",
        "to_branch": "展覽會場專用",
        "to_prefix": "0A",
    }

    result = pipeline.apply_branch_reassignment_overrides(source, [override])

    assert result.loc[0, "銷售點"] == "展覽會場專用"
    assert result.loc[0, "副表_銷售點"] == "展覽會場專用"
    assert result.loc[1:, "銷售點"].tolist() == ["上環服務點", "上環服務點", "元朗服務點"]
```

- [ ] **Step 2: 執行 RED 測試**

Run: `.venv/bin/python -m pytest tests/test_pipeline_preloaded_frames.py::test_branch_reassignment_override_matches_one_exact_source_order_only -q`

Expected: FAIL，因現有 pipeline 尚未處理 `source_order_id`，其他 E9 上環訂單也會被錯誤轉移。

- [ ] **Step 3: 實作最小 pipeline 條件**

在 `apply_branch_reassignment_overrides` 讀取並套用完整訂單號：

```python
source_order_id = _normalize_branch_value(override.get("source_order_id")).upper()

if (from_prefix or source_order_id) and COL_ORDER_ID in result.columns:
    order_ids = result[COL_ORDER_ID].map(_normalize_branch_value).str.upper()
    if from_prefix:
        mask &= order_ids.str.startswith(from_prefix)
    if source_order_id:
        mask &= order_ids.eq(source_order_id)
```

- [ ] **Step 4: 執行 GREEN 與既有 E6 回歸測試**

Run: `.venv/bin/python -m pytest tests/test_pipeline_preloaded_frames.py::test_branch_reassignment_override_matches_one_exact_source_order_only tests/test_pipeline_preloaded_frames.py::test_branch_reassignment_override_moves_2026_06_e6_to_0a_only -q`

Expected: `2 passed`。

---

### Task 2: SQLite repair 與持久規則一致性

**Files:**
- Modify: `tests/test_database_rollback.py`
- Modify: `database.py:360-381`
- Modify: `rules.py:40-50`
- Modify: `rules_config.json:26-39`

**Interfaces:**
- Consumes: `BRANCH_REASSIGNMENT_OVERRIDES[*].source_order_id`。
- Produces: `_branch_reassignment_target(row, table_cols) -> str | None` 與 pipeline 使用相同精確匹配語義。

- [ ] **Step 1: 寫入 SQLite repair 失敗測試**

在 `tests/test_database_rollback.py` 新增一個 temporary SQLite 測試，建立目標訂單、其他 E9 訂單、同訂單七月資料及同訂單非上環資料；monkeypatch override 為：

```python
{
    "month": "2026-06",
    "source_order_id": "E9MF16613172500",
    "from_branch": "上環服務點",
    "to_branch": "展覽會場專用",
    "to_prefix": "0A",
}
```

呼叫 `database.repair_subtable_branch_assignments([])` 後，斷言只有 2026年6月目標訂單歸入展覽會場專用，其他三筆保持原值，且 `result["updated"] == 1`。

- [ ] **Step 2: 執行 RED 測試**

Run: `.venv/bin/python -m pytest tests/test_database_rollback.py::test_repair_subtable_branch_assignments_matches_one_exact_source_order_only -q`

Expected: FAIL，因 `_branch_reassignment_target` 尚未處理 `source_order_id`。

- [ ] **Step 3: 實作 SQLite repair 精確條件**

在 `_branch_reassignment_target` 的 prefix 判斷後加入：

```python
override_source_order_id = _clean_branch_value(override.get("source_order_id")).upper()
if override_source_order_id and source_id != override_source_order_id:
    continue
```

- [ ] **Step 4: 新增正式規則**

在 `rules.py` 與 `rules_config.json` 的既有 E6 override 後新增：

```python
{
    "month": "2026-06",
    "source_order_id": "E9MF16613172500",
    "from_branch": "上環服務點",
    "to_prefix": "0A",
    "to_branch": "展覽會場專用",
    "scope": ["票務"],
    "reason": "2026年6月指定上環票務訂單歸入0A展覽會場專用",
}
```

`rules_config.json` 使用等價 JSON 物件，確保 Streamlit session 與 upload processing 讀到相同規則。

- [ ] **Step 5: 執行 GREEN 與 database 回歸測試**

Run: `.venv/bin/python -m pytest tests/test_database_rollback.py::test_repair_subtable_branch_assignments_matches_one_exact_source_order_only tests/test_database_rollback.py::test_repair_subtable_branch_assignments_keeps_2026_06_e6_reassigned_to_0a_only tests/test_pipeline_preloaded_frames.py -q`

Expected: 全部 PASS。

---

### Task 3: 正式 SQLite 修復與四項數字驗收

**Files:**
- Modify at runtime: `nbs_marketing_data.db`
- Create at runtime: existing hot backup path produced by `hot_backup_database()`
- Refresh at runtime: `.nbs_runtime_cache/`

**Interfaces:**
- Consumes: `database.repair_subtable_branch_assignments(rules.SALES_REP_LIST)`。
- Produces: 正式 SQLite 的目標訂單分社歸屬與可重建 dashboard cache。

- [ ] **Step 1: 執行修改前 targeted verification**

Run:

```bash
.venv/bin/python -m py_compile pipeline.py database.py rules.py
.venv/bin/python -m pytest tests/test_pipeline_preloaded_frames.py tests/test_database_rollback.py -q
```

Expected: compile exit `0`，所有 targeted tests PASS。

- [ ] **Step 2: 執行正式 SQLite repair**

Run:

```bash
.venv/bin/python - <<'PY'
from database import repair_subtable_branch_assignments
from rules import SALES_REP_LIST

print(repair_subtable_branch_assignments(SALES_REP_LIST))
PY
```

Expected: `updated` 至少為 `1`，並回傳非空 `backup` 路徑。立即再次執行時 Expected: `updated` 為 `0`，證明 idempotent。

- [ ] **Step 3: 用正式計算鏈路核對四項數字**

Run:

```bash
.venv/bin/python - <<'PY'
from backend.services.dashboard_service import build_dashboard_summary

def summary(month):
    return build_dashboard_summary({
        "years": [2026],
        "months": [month],
        "dateRange": [f"{month}-01", f"{month}-31" if month == "2026-05" else f"{month}-30"],
        "branch": "全部分社",
        "salesGroup": "全部銷售組",
    })

june = summary("2026-06")
may = summary("2026-05")
rank = {row["branch"]: round(row["totalRevenue"]) for row in june["branchRanking"]}
print({
    "june_sheung_wan": rank.get("E6上環服務點", 0),
    "june_0a": rank.get("0A展覽會場專用", 0),
    "june_total": round(june["revenueTotals"]["combinedRevenue"]),
    "may_total": round(may["revenueTotals"]["combinedRevenue"]),
    "may_status": may["stabilityBaseline"]["status"],
})
PY
```

Expected:

```python
{
    "june_sheung_wan": 0,
    "june_0a": 703425,
    "june_total": 9083241,
    "may_total": 12057968,
    "may_status": "matched",
}
```

- [ ] **Step 4: 執行跨層回歸與 acceptance**

Run:

```bash
.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py tests/test_dashboard_service.py tests/test_dashboard_api.py -q
.venv/bin/python -m pytest tests/test_upload_rollback_service.py tests/test_stability_history_service.py -q
.venv/bin/python scripts/system_manager.py stop
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --json
```

Expected: pytest suites PASS；三項服務 ready；acceptance 與 Hermes overall status 均為 PASS。

- [ ] **Step 5: Commit 程式、規則、測試與正式 SQLite checkpoint**

Run:

```bash
git add pipeline.py database.py rules.py rules_config.json tests/test_pipeline_preloaded_frames.py tests/test_database_rollback.py nbs_marketing_data.db
git commit -m "fix: reassign June ticket order to 0A"
```

Expected: commit 成功，`git status --short` 為空。
