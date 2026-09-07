# Beta 電商組銷售點比較匯出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改正式 Dashboard、Forecast 與專職銷售語意的前提下，讓 Beta comparison export 將原專職表格改以銷售點「市場及電商部-電子商務組」及其全部實際銷售員生成。

**Architecture:** 在既有 `pipeline.build_dashboard_data()` 增加明確、可選的 Beta sales-point view 參數；未傳入時維持原專職邏輯。`app_workflows.py` 只在 Beta export composition path 傳入該參數，並為 Beta artifact/cache/manifest 建立獨立 identity。統計 builder 重用既有欄位與 aggregation，透過 helper 將銷售員空值標準化為「未指定」，不以 `TARGET_DEPT_FOR_REP` 或 `SALES_REP_LIST` 作為 Beta 條件。

**Tech Stack:** Python 3、pandas、openpyxl、Streamlit、pytest、既有 export intermediate/cache/manifest services、Hermes read-only checks。

**Spec:** `docs/superpowers/specs/2026-09-07-beta-ecommerce-sales-point-export-design.md`

## Global Constraints

- 正式收入口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- Beta 唯一資料條件是 `銷售點 == "市場及電商部-電子商務組"`。
- Beta 顯示該銷售點所有實際 `銷售員`；空白、`NaN`、純空白統一為「未指定」。
- Beta 不套用原 `SALES_REP_LIST`，不改名銷售員。
- 不修改 `TARGET_DEPT_FOR_REP`、正式 Dashboard、Forecast、SQLite、baseline、revenue ledger 或 upload flow。
- Governance Graph、Memory Hub、Memory Sidecar、local agent 僅 read-only／non-authoritative；不得改變資料條件或正式狀態。
- 每個 Task 必須先測試、實作、驗證、checkpoint commit，再交 fresh findings-first Review；Review 未 PASS 不進下一 Task。
- 高風險 export/business-rule Task 使用 `gpt-5.6-luna`、`medium`；local agent 僅執行單一 bounded allowlist Task。

## File Map

- Modify: `pipeline.py` — 新增可選 Beta sales-point view 與共用 specialist-like aggregation。
- Modify: `app_workflows.py` — 只在 Beta export composition 呼叫 variant，管理獨立 artifact/cache identity。
- Modify: `backend/services/export_intermediate_service.py` — 若需要，為 Beta variant 建立 source-bound facts identity；不得改正式 scope semantics。
- Create/Modify: `tests/test_beta_ecommerce_sales_point_export.py` — Beta filter、銷售員、空資料與 sheet contract。
- Modify: `tests/test_official_export_workbook_contract.py`、`tests/test_export_fast_path.py` 或相關 regression tests — 證明正式 path 不變。
- Modify: `docs/agents/NBS_ANALYTICS_HANDOFF.md` 或當輪 handoff snapshot — 只在所有 gates PASS 後回填 live evidence，不把 plan 當完成證據。

### Task 1: 建立可測試的 Beta sales-point view contract

**Files:**
- Modify: `pipeline.py:745-770, 895-912, 941-986, 1082-1087`
- Create: `tests/test_beta_ecommerce_sales_point_export.py`

**Interfaces:**
- Consumes: normalized tour/others frames and existing `build_dashboard_data()` inputs.
- Produces: private sales-point selection helper and optional `beta_sales_point: str | None` builder behavior, with default values preserving existing formal output.

- [ ] **Step 1: Write failing tests for the pure selection contract**

```python
import pipeline


def test_beta_selects_sales_point_and_keeps_all_salespeople():
    beta_tour, beta_others = pipeline._select_sales_point_frames(
        tour_frame(), others_frame(), sales_point="市場及電商部-電子商務組"
    )
    assert set(beta_tour["銷售員"]) == {"Alice", "未指定"}
    assert set(beta_others["銷售員"]) == {"Bob"}
    assert set(beta_tour["銷售點"]) == {"市場及電商部-電子商務組"}


def test_beta_does_not_use_department_as_fallback():
    frames = tour_frame().assign(團負責人部門="市場及電商部-電子商務組", 銷售點="銅鑼灣分社")
    beta_tour, _ = pipeline._select_sales_point_frames(
        frames, others_frame(), sales_point="市場及電商部-電子商務組"
    )
    assert beta_tour.empty


def test_beta_does_not_filter_by_legacy_sales_rep_list():
    frames = tour_frame().assign(銷售員="New Ecommerce Rep")
    beta_tour, _ = pipeline._select_sales_point_frames(
        frames, others_frame(), sales_point="市場及電商部-電子商務組"
    )
    assert "New Ecommerce Rep" in set(beta_tour["銷售員"])
```

The test module must define deterministic `tour_frame()` and `others_frame()` fixtures containing: two matching ecommerce rows (`Alice`, blank), one matching ecommerce others row (`Bob`), and one non-ecommerce row whose `團負責人部門` is ecommerce. It must also define `empty_frame()` with the same normalized columns and `branch_mapping()` returning `{ "I6": "市場及電商部-電子商務組", "225": "營銷運營中心-專職銷售組" }`. Every non-empty row must include `銷售點`, `銷售員`, `來源單據號`, `統一日期`, `收款原幣金額`, `收款類型`, `收款方式`, `數量`, and the columns required by the existing builders.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest tests/test_beta_ecommerce_sales_point_export.py -q`

Expected: FAIL because the Beta selection/normalization helper does not exist.

- [ ] **Step 3: Implement the smallest pure helper**

Add a private helper with this contract:

```python
def _select_sales_point_frames(
    tour: pd.DataFrame,
    others: pd.DataFrame,
    *,
    sales_point: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return only rows whose 銷售點 equals sales_point; never infer from department."""
```

The helper must copy inputs, use `COL_BRANCH`, add `COL_SALESPERSON` as an empty string when missing, and normalize empty salesperson values to `"未指定"`. It must not mutate inputs or inspect `SALES_REP_LIST`.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `pytest tests/test_beta_ecommerce_sales_point_export.py -q`

Expected: PASS for selection, no department fallback, all salesperson names, and input immutability.

- [ ] **Step 5: Checkpoint commit**

```bash
git add pipeline.py tests/test_beta_ecommerce_sales_point_export.py
git commit -m "test: define beta ecommerce sales point selection"
```

Checkpoint: submit the actual diff and test output to the read-only Review runner. Do not proceed if Review returns `CHANGES_REQUIRED`.

### Task 2: Generate Beta replacements for the specialist-like tables

**Files:**
- Modify: `pipeline.py:745-755, 895-912, 941-986, 1082-1087, 1210-1230`
- Modify: `tests/test_beta_ecommerce_sales_point_export.py`

**Interfaces:**
- Consumes: `_select_sales_point_frames()` and existing statistical builders.
- Produces: `build_dashboard_data(..., beta_sales_point: str | None = None)` where `None` preserves the current formal specialist path; a non-empty value replaces only the specialist-like Beta frames and sheet labels/content.

- [ ] **Step 1: Add failing workbook tests**

```python
def test_beta_workbook_replaces_specialist_sheets_with_ecommerce_data():
    buffer, _, facts = pipeline.build_dashboard_data(
        tour_frame(), others_frame(), branch_mapping(), [], ["郵輪部"], ["Legacy Rep"],
        make_workbook=False, return_facts=True, beta_sales_point="市場及電商部-電子商務組",
    )
    assert "市場及電商部-電子商務組_經營統計" in facts
    assert set(facts["市場及電商部-電子商務組_經營統計"]["銷售員"].astype(str)) >= {"Alice", "未指定"}
    assert "Legacy Rep" not in set(facts["市場及電商部-電子商務組_每天旅行團交易人數"]["文本"].astype(str))


def test_beta_empty_sales_point_keeps_schema_without_legacy_fallback():
    _, _, facts = pipeline.build_dashboard_data(
        empty_frame(), empty_frame(), branch_mapping(), [], [], ["Legacy Rep"],
        make_workbook=False, return_facts=True, beta_sales_point="市場及電商部-電子商務組",
    )
    assert facts["市場及電商部-電子商務組_旅行團統計"].empty
    assert list(facts["市場及電商部-電子商務組_旅行團統計"].columns) == ["文本", "天數", "日期", "月份", "交易人數"]
```

- [ ] **Step 2: Run tests and verify the new assertions fail**

Run: `pytest tests/test_beta_ecommerce_sales_point_export.py -q`

Expected: FAIL because `build_dashboard_data` has no Beta variant and specialist frames still use `TARGET_DEPT_FOR_REP`.

- [ ] **Step 3: Add an explicit optional Beta branch inside the builder**

Extend the signature without changing positional arguments:

```python
def build_dashboard_data(..., _already_normalized: bool = False, *, beta_sales_point: str | None = None):
```

When `beta_sales_point` is set, derive local `specialist_tour` and `specialist_others` from `_select_sales_point_frames`; otherwise derive them exactly as today using `TARGET_DEPT_FOR_REP`. Use all normalized salesperson values for Beta summary/daily/route groupings, while preserving the old `sales_rep_list` filter only in the non-Beta branch. Prefix every Beta replacement sheet with `市場及電商部-電子商務組_`; keep the formal sheet names and formal workbook output unchanged.

- [ ] **Step 4: Add refund-scope coverage**

Call the existing exclusion wrapper with `beta_sales_point` and assert that rows with `收款類型 == "掛賬核銷"` and `收款方式 == "TT 退款轉團款"` are absent from the Beta official-comparison frames while a normal row remains.

- [ ] **Step 5: Run focused tests**

Run: `pytest tests/test_beta_ecommerce_sales_point_export.py tests/test_official_export_workbook_contract.py -q`

Expected: PASS; the official contract test must still find the original branch/specialist sheets and original totals.

- [ ] **Step 6: Checkpoint commit**

```bash
git add pipeline.py tests/test_beta_ecommerce_sales_point_export.py tests/test_official_export_workbook_contract.py
git commit -m "feat: render beta ecommerce specialist tables"
```

Checkpoint: fresh Review against the checkpoint commit and source-bound focused test evidence.

### Task 3: Wire Beta export orchestration and identity isolation

**Files:**
- Modify: `app_workflows.py:1046-1145, 1150-1180, 1825-1845, 3635-3655`
- Modify: `backend/services/export_intermediate_service.py` only if the existing facts identity needs a variant field
- Modify: export/cache/manifest tests identified by `rg -n "_compute_export_workbooks|export_cache_version|official_export_schema|manifest" tests`

**Interfaces:**
- Consumes: `build_dashboard_data(..., beta_sales_point=...)` and existing official scope frames.
- Produces: a Beta export artifact family with a stable variant id such as `beta_ecommerce_sales_point_v1`, while existing keys `ex`, `ex_no_writeoff`, and `ex_no_writeoff_refund_transfer` remain formal-path compatible.

- [ ] **Step 1: Add failing orchestration tests**

```python
import app_workflows


def test_beta_export_has_distinct_variant_identity_and_does_not_replace_official(monkeypatch):
    payload = app_workflows._compute_beta_export_workbooks(tour_frame(), others_frame())
    assert payload["export_variant"] == "beta_ecommerce_sales_point_v1"
    assert payload["sales_point_filter"] == "市場及電商部-電子商務組"
    assert payload["ex_beta"] != app_workflows._compute_export_workbooks(tour_frame(), others_frame())["ex"]


def test_beta_cache_identity_changes_with_variant_and_rules_fingerprint():
    assert app_workflows._build_export_variant_key(
        "source", "rules", "beta_ecommerce_sales_point_v1"
    ) != app_workflows._build_export_variant_key("source", "rules", "official")
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/test_beta_ecommerce_sales_point_export.py -q`

Expected: FAIL because the Beta orchestration function and variant identity do not exist.

- [ ] **Step 3: Implement a separate Beta composition function**

Add:

```python
def _compute_beta_export_workbooks(
    db_tour: pd.DataFrame,
    db_others: pd.DataFrame,
    *,
    rules: tuple[dict, list[str], list[str], list[str], list[str]] | None = None,
) -> dict:
```

It must produce the three existing exclusion variants under Beta-specific keys, call the existing exclusion wrapper with `beta_sales_point`, and return `export_variant`, `sales_point_filter`, `rules_fingerprint`, source fingerprints, and a Beta schema version. It must not alter `_compute_export_workbooks` output.

Add this deterministic helper contract for cache/manifest identity:

```python
def _build_export_variant_key(source_fingerprint: str, rules_fingerprint: str, variant: str) -> str:
    """Return a stable key that differs for formal and Beta variants."""
```

The key must include all three inputs and must be used only for Beta artifact/cache paths; existing formal keys remain backward-compatible.

- [ ] **Step 4: Wire only the Beta UI/export action**

Add the Beta comparison export action at the existing export UI boundary. Do not change formal download labels or overwrite formal cache entries. Use a distinct filename containing `beta_ecommerce_sales_point`, and ensure the export manifest records the variant and filter.

- [ ] **Step 5: Run focused orchestration and regression tests**

Run: `pytest tests/test_beta_ecommerce_sales_point_export.py tests/test_export_fast_path.py tests/test_export_manifest_service.py -q`

Expected: PASS with formal artifact keys and totals unchanged, and Beta artifacts isolated.

- [ ] **Step 6: Checkpoint commit**

```bash
git add app_workflows.py backend/services/export_intermediate_service.py tests
git commit -m "feat: isolate beta ecommerce export artifacts"
```

Checkpoint: fresh Review must verify variant identity, cache isolation, no formal-path mutation, and no SQLite writes.

### Task 4: UI and contract documentation

**Files:**
- Modify: `app_pages.py` or the actual export-rendering module found by `rg -n "下載全維度|_render_ai_and_exports"`
- Modify: `tests/test_streamlit_export_serialization_contract.py` and the relevant Streamlit export contract test
- Modify: `docs/superpowers/specs/2026-09-07-beta-ecommerce-sales-point-export-design.md` only if implementation discovers a contract correction

**Interfaces:**
- Consumes: Beta artifact family and explicit variant metadata.
- Produces: a user-visible Beta download option whose label states the sales point and comparison nature without implying formal Dashboard/Forecast migration.

- [ ] **Step 1: Write the failing UI contract assertion**

```python
def test_export_ui_exposes_beta_ecommerce_comparison_without_replacing_official_action():
    source = _source("_render_ai_and_exports")
    assert "Beta comparison export" in source
    assert "市場及電商部-電子商務組" in source
    assert "ex_no_writeoff_refund_transfer" in source
```

- [ ] **Step 2: Run the UI contract test and verify failure**

Run: `pytest tests/test_streamlit_export_serialization_contract.py -q`

Expected: FAIL until the Beta action and label are wired.

- [ ] **Step 3: Add the minimum UI action and explicit explanatory copy**

The UI must state that the Beta export only replaces specialist-like tables inside the downloaded workbook; formal Dashboard and Forecast remain unchanged. On missing Beta artifact, show a bounded error and preserve formal download actions.

- [ ] **Step 4: Run UI contract tests**

Run: `pytest tests/test_streamlit_export_serialization_contract.py tests/test_streamlit_upload_feedback_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Checkpoint commit**

```bash
git add app_pages.py app_workflows.py tests
git commit -m "feat: expose beta ecommerce comparison export"
```

Checkpoint: fresh Review of UI copy, artifact selection, formal action preservation, and scope wording.

### Task 5: Full verification, release evidence, and handoff

**Files:**
- No source edits unless a verified finding requires a bounded fix.
- Create only generated read-only evidence under the existing runtime artifact locations.
- Modify: `NBS_ANALYTICS_HANDOFF.md` only after all required gates pass and with current commit/source fingerprints.

**Interfaces:**
- Consumes: all checkpoint commits, fresh Review verdicts, focused test output, current Git fingerprint, and existing gate scripts.
- Produces: independent Review, full pytest, Hermes, UI acceptance evidence and a concise live handoff snapshot.

- [ ] **Step 1: Run fresh Strict Review**

Use the local workflow CLI for one bounded Review invocation with `gpt-5.6-luna` and `medium` only if the contract classifies this export/business-rule change as high risk. Review must be findings-first and read-only.

- [ ] **Step 2: Run focused and full pytest**

Run focused Beta/regression tests first, then the repository full pytest command from the current release-gate documentation. Record failures separately; do not treat skipped tests as PASS without the established skip explanation.

- [ ] **Step 3: Run Hermes post-change check**

Run the existing `scripts/hermes_post_change_check.py` command against the current HEAD/worktree. Confirm it is read-only and source-bound.

- [ ] **Step 4: Run UI acceptance**

Use the project’s established local HTTP/Streamlit acceptance path. Verify the Beta action, downloaded filename, sheet names, salesperson values, empty-data behavior, and unchanged formal action.

- [ ] **Step 5: Reconcile handoff**

Update the live snapshot only with current commit SHA, source fingerprint, spec/plan paths, Beta variant identity, and independent gate verdicts. Do not claim feature completion if any gate is failed, stale, or missing.

- [ ] **Step 6: Final checkpoint commit**

```bash
git add NBS_ANALYTICS_HANDOFF.md
git commit -m "docs: record beta ecommerce export verification"
```

Final acceptance: formal export regression PASS, Beta focused tests PASS, full pytest PASS, Hermes PASS, UI acceptance PASS, and no unapproved business-state or database changes.
