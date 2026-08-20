# Formal Net GMV SQLite Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不取代既有正式營收口徑、不增加首頁讀取負擔的前提下，於同一 SQLite 建立可稽核、可增量更新、可 rollback 的正式淨 GMV ledger，第一階段只接入 Dashboard 與完整報表匯出。

**Architecture:** 保留 `tour_data`／`others_data` 與既有正式營收 cache，新增以 `gmv_` 前綴隔離的 append-only refund observations、current projection、versioned reconciliation、adjustment／metric snapshots 與 active views。Upload 先做純讀 Preflight，再由人工確認在共享 upload lease 內一次 transaction 寫入及切換 active version；Dashboard 只有在使用者明確載入 GMV 區時才查 active snapshot，Export 按需從 active snapshot materialize，首頁 hot path 不 import、不查詢 GMV repository。

**Tech Stack:** Python 3、SQLite、pandas、Streamlit、openpyxl、pytest、既有 `database.py`、`app_workflows.py`、`upload_lock_service.py`、`cache_generation_service.py` 與 Hermes acceptance。

**Spec:** `docs/superpowers/specs/2026-08-20-formal-net-gmv-sqlite-ledger-design.md`

## Global Constraints

- 既有正式營收口徑固定為「不含掛賬核銷與TT退款轉團款」，2026-05 frozen baseline 固定為 `HKD 12,057,968`；不得由 GMV ledger 改寫。
- 正式淨 GMV = 既有正式營收 - 正式匹配且退款狀態精確等於 `已退款` 的實際扣減金額。
- `總退款` 繼續存在於 Dashboard 與完整報表，但只作營運維度，不得標示或驅動正式淨 GMV。
- 第一階段只接 Dashboard 與 Export；Forecast、WAPE、Backtest、FastAPI write contract、Governance Graph、Memory Hub、agent orchestration、外部服務均不在範圍。
- 退款只調整 `COL_MONEY`；`COL_QTY` 不得被比例調整、歸零或推算。所有數量 consumer 必須顯示「原交易人數／數量（未按退款調整）」。
- GMV business data 使用現有 SQLite 檔案與 `gmv_` prefix；不得新增 database、migration framework 或 render-time schema creation。
- 新金額欄位一律保存為 `INTEGER` minor units；不以 SQLite `REAL` 作 ledger 金額。
- `退款單號` 是 current projection 的穩定 business key；同一退款由「退款中」變成「已退款」是 update projection，不是 append-only duplicate。
- Observation、reconciliation、snapshot、event ledger 不可 UPDATE／DELETE；只有 `gmv_refund_current` 與 version active pointer 可在受控 transaction 中改變。
- Preflight 不得寫正式 SQLite。Confirm／rebuild／rollback／deactivate 必須共用既有 upload lease、fail closed，並在單一 transaction 中完成。
- GMV 不呼叫 `advance_cache_generation()`；active `version_id` 是 GMV read token。Revenue token 仍由既有 `load_cache_generation()` 取得，只用於 stale detection。
- Streamlit 首頁初次 render 不得 import 或呼叫 GMV repository；GMV tab 內也要經明確「載入正式淨 GMV」動作才讀 active snapshot。
- 私有檔 `/Users/chanwaitung2025/Downloads/退款明細數據.xlsx` 只作本地 acceptance，不複製或提交至 repo。
- 每次只執行一個經使用者批准的 Task。涉及正式 SQLite、business rules、revenue 或 export schema 的 Task 只能由主 Codex 執行，Implementation Agent 不得代行。
- 每個 Task 依序執行：TDD red → minimal implementation → targeted green → `git diff --check` → findings-first read-only Review → 主 Codex 處理 findings → 主 Codex commit。Implementation／Review runner 均不得 commit。
- 所有 Task 完成後才跑 full pytest、Hermes、實際 Streamlit UI、真實退款檔 parity 與 performance acceptance；targeted PASS 不等於正式完成。
- 保留目前 unrelated untracked `.superpowers/brainstorm/`，不得 stage、刪除或混入 commit。

---

## File Map

### Create

- `backend/services/gmv_refund_models.py`
  - Minor-unit conversion、canonical hashes、observation/current/change/preflight immutable models。
- `backend/services/gmv_refund_repository.py`
  - `gmv_` schema SQL、schema validation、read models、transactional writes、active views 與 immutable triggers。
- `backend/services/gmv_refund_service.py`
  - Preview、confirm、rebuild、rollback、deactivate application use cases；組合現有 pandas engine 與 repository。
- `scripts/migrate_gmv_refund_schema.py`
  - Explicit `--db-path`／`--dry-run` migration CLI；apply 前 hot backup，apply 後 integrity/schema validation。
- `scripts/benchmark_gmv_page_load.py`
  - 以固定 warm-run protocol 比較首頁與 GMV data-prep latency，不啟動 workbook generation。
- `tests/test_gmv_refund_models.py`
- `tests/test_gmv_refund_repository.py`
- `tests/test_gmv_refund_service.py`
- `tests/test_gmv_refund_migration_cli.py`
- `tests/test_streamlit_gmv_formal_contract.py`
- `tests/test_gmv_formal_export_contract.py`
- `tests/test_gmv_performance_contract.py`

### Modify

- `app_workflows.py`
  - 保留現有 session-only Preflight／refund engine；新增 row fingerprint、snapshot materialization、formal workbook provenance adapter。
- `app_pages.py`
  - 在既有 `_render_gmv_exclusion_tab()` 加入 lazy formal loader、confirm、stale/rebuild、version history、rollback/deactivate 與 export controls。
- `tests/test_gmv_refund_adjustment.py`
  - 補 parity、minor-unit rounding、status transition 與 quantity invariance regression。
- `tests/test_gmv_refund_preflight.py`
  - 補 `退款單號` business key、incremental change classification 與 identity conflict gate。
- `tests/test_streamlit_gmv_refund_contract.py`
  - 保留現有 session-only Preflight／Exception Center 行為並驗證 formal section 不破壞它。
- `tests/test_official_export_workbook_contract.py`
  - 證明既有正式報表保持 byte-contract／sheet contract，不被 GMV export 取代。

### Intentionally Unchanged

- `pipeline.py`：重用既有 `build_dashboard_data()` 並傳入 `make_workbook=False`，不建立第二套 KPI engine。
- `backend/services/cache_generation_service.py`：不把 GMV version 混入現有 revenue token。
- `backend/routes/`：第一階段不新增 FastAPI endpoints。
- `forecasting.py` 與所有 forecast/backtest/WAPE consumers：不接正式淨 GMV。
- 正式 SQLite 檔、baseline、rollback artifacts：測試只使用 temp copy；正式 migration 必須另經 checkpoint 授權。

---

## Task 0: 建立 read-only 效能量測基準

**Files:**
- Create: `scripts/benchmark_gmv_page_load.py`
- Create: `tests/test_gmv_performance_contract.py`

This Task must run before any application or GMV implementation file changes. The harness itself is the only source change; baseline output remains outside Git.

- [ ] **Step 1: Write failing benchmark-report tests**

```python
from scripts.benchmark_gmv_page_load import compare_benchmarks, summarize_samples


def test_summary_uses_warm_median_and_p95():
    summary = summarize_samples([0.100, 0.110, 0.120, 0.130, 0.140])
    assert summary["medianMs"] == 120.0
    assert summary["p95Ms"] == 140.0


def test_home_regression_must_satisfy_both_relative_and_absolute_gates():
    result = compare_benchmarks(
        baseline={"medianMs": 1000.0},
        candidate={"medianMs": 1060.0},
        absolute_limit_ms=300.0,
        relative_limit=0.05,
    )
    assert result["passed"] is False
    assert result["regressionMs"] == 60.0
    assert result["regressionRatio"] == 0.06
```

- [ ] **Step 2: Run red test**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_performance_contract.py
```

Expected: FAIL because the benchmark module does not exist.

- [ ] **Step 3: Implement a read-only dashboard data-preparation harness**

Call the existing `load_all_data_from_db()` and `_build_revenue_scope_frames()` path directly, with one untimed warm-up followed by 10 timed iterations. This avoids conflating the full Streamlit page's AI／Memory／all-tab rendering with the revenue data-preparation hot path. Record only elapsed milliseconds, resolved DB identity hash, `tour_data`／`others_data` row counts, formal row counts, Git HEAD and timestamp. Do not record business rows, trigger downloads, upload files or write SQLite.

CLI contract:

```bash
.venv/bin/python scripts/benchmark_gmv_page_load.py --mode baseline --iterations 10 --output /tmp/nbs-gmv-baseline.json
.venv/bin/python scripts/benchmark_gmv_page_load.py --mode candidate --iterations 10 --baseline /tmp/nbs-gmv-baseline.json --output /tmp/nbs-gmv-candidate.json
```

Candidate passes only when dashboard data-preparation median regression is both no more than `300 ms` and no more than `5%` of baseline. Missing or changing row counts fail the run.

- [ ] **Step 4: Capture the pre-change baseline**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_performance_contract.py
.venv/bin/python scripts/benchmark_gmv_page_load.py --mode baseline --iterations 10 --output /tmp/nbs-gmv-baseline.json
.venv/bin/python -m py_compile scripts/benchmark_gmv_page_load.py
git diff --check
```

Expected: tests pass, baseline contains 10 warm samples, stable formal row counts and no measurement errors.

- [ ] **Step 5: Findings-first Review and main-Codex commit**

After PASS:

```bash
git add scripts/benchmark_gmv_page_load.py tests/test_gmv_performance_contract.py
git commit -m "test: baseline gmv page performance"
```

Preserve `/tmp/nbs-gmv-baseline.json` through Task 8; if execution environment changes, recapture baseline from this Task 0 commit in a clean worktree before comparing the candidate.

---

## Task 1: 建立 immutable domain models、minor units 與增量分類

**Files:**
- Create: `backend/services/gmv_refund_models.py`
- Create: `tests/test_gmv_refund_models.py`
- Modify: `tests/test_gmv_refund_preflight.py`

**Interfaces:**

```text
money_to_minor(value: object) -> int
minor_to_money(value: int) -> Decimal
canonical_payload_sha256(payload: Mapping[str, object]) -> str
classify_refund_changes(incoming: Sequence[RefundObservation], current: Mapping[str, RefundCurrentState]) -> RefundChangeSet
```

`RefundChangeSet` 必須分開 `new`、`unchanged`、`status_changed`、`identity_conflicts`；相同 `退款單號`、來源單據號與金額相同但狀態改變，只能進入 `status_changed`。

- [ ] **Step 1: Write failing model tests**

```python
from decimal import Decimal

import pytest

from backend.services.gmv_refund_models import (
    RefundCurrentState,
    RefundObservation,
    classify_refund_changes,
    money_to_minor,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10.005", 1001), (Decimal("0.004"), 0), ("1,200.10", 120010)],
)
def test_money_to_minor_uses_decimal_half_up(raw, expected):
    assert money_to_minor(raw) == expected


def test_status_transition_is_update_not_new_observation_identity():
    current = {
        "R-1": RefundCurrentState(
            refund_order_no="R-1",
            source_receipt_no="S-1",
            refund_amount_minor=5000,
            refund_status="退款中",
            source_batch_id="B-0",
            state_sha256="old",
        )
    }
    incoming = [
        RefundObservation(
            refund_order_no="R-1",
            source_receipt_no="S-1",
            refund_amount_minor=5000,
            refund_status="已退款",
            raw_row_sha256="row-1",
        )
    ]

    changes = classify_refund_changes(incoming, current)

    assert [item.refund_order_no for item in changes.status_changed] == ["R-1"]
    assert changes.new == ()
    assert changes.identity_conflicts == ()


def test_same_refund_id_with_changed_source_or_amount_is_identity_conflict():
    current = {
        "R-1": RefundCurrentState("R-1", "S-1", 5000, "退款中", "B-0", "old")
    }
    incoming = [RefundObservation("R-1", "S-2", 6000, "已退款", "row-1")]

    changes = classify_refund_changes(incoming, current)

    assert len(changes.identity_conflicts) == 1
    assert changes.status_changed == ()
```

- [ ] **Step 2: Run red tests**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_models.py tests/test_gmv_refund_preflight.py
```

Expected: FAIL with missing `backend.services.gmv_refund_models` and missing business-key classification.

- [ ] **Step 3: Implement frozen dataclasses and deterministic hashing**

Use `@dataclass(frozen=True, slots=True)` for all domain records. Normalize strings with Unicode NFKC plus trim, reject empty refund/source IDs, reject negative amounts, and hash canonical JSON with sorted keys and UTF-8:

```python
def canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

Use `Decimal(str(value).replace(",", ""))` and `quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` before conversion to minor units. Do not convert ledger amounts through binary float.

- [ ] **Step 4: Add adapter coverage for required `退款單號`**

Update `tests/test_gmv_refund_preflight.py` so persisted-formal Preflight rejects files missing `退款單號`, while the existing session-only `_normalize_gmv_refund_rows()` compatibility path remains readable. The formal adapter belongs in `gmv_refund_service.py` in Task 3; this test initially targets the model-level observation factory.

- [ ] **Step 5: Run focused green checks**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_models.py tests/test_gmv_refund_preflight.py
.venv/bin/python -m py_compile backend/services/gmv_refund_models.py
git diff --check
```

Expected: all pass; no existing session-only refund test regresses.

- [ ] **Step 6: Findings-first Review and main-Codex commit**

Review only Task 1 diff against this contract. After Review PASS, main Codex commits:

```bash
git add backend/services/gmv_refund_models.py tests/test_gmv_refund_models.py tests/test_gmv_refund_preflight.py
git commit -m "feat: model incremental gmv refunds"
```

---

## Task 2: 建立 explicit migration、SQLite schema 與 repository read contract

**Files:**
- Create: `backend/services/gmv_refund_repository.py`
- Create: `scripts/migrate_gmv_refund_schema.py`
- Create: `tests/test_gmv_refund_repository.py`
- Create: `tests/test_gmv_refund_migration_cli.py`

**Repository surface:**

```text
GmvRefundRepository.validate_schema() -> GmvSchemaValidation
GmvRefundRepository.load_current_refunds() -> dict[str, RefundCurrentState]
GmvRefundRepository.load_active_scope() -> GmvScopeSummary | None
GmvRefundRepository.load_scope_history(limit: int = 20) -> Sequence[GmvScopeSummary]
GmvRefundRepository.load_metric_snapshot(version_id: str) -> pd.DataFrame
GmvRefundRepository.load_adjustment_snapshot(version_id: str) -> pd.DataFrame
```

Write methods accept an existing `sqlite3.Connection`; they must not silently open a second connection inside a transaction.

- [ ] **Step 1: Write failing migration and schema tests**

Tests must assert:

```python
EXPECTED_GMV_OBJECTS = {
    "gmv_refund_batches",
    "gmv_refund_observations",
    "gmv_refund_current",
    "gmv_reconciliation_results",
    "gmv_reconciliation_members",
    "gmv_scope_versions",
    "gmv_adjustment_snapshot",
    "gmv_metric_snapshot",
    "gmv_scope_events",
    "v_gmv_current_scope",
    "v_gmv_current_metrics",
    "v_gmv_current_adjustments",
}


def test_migration_is_explicit_idempotent_and_preserves_revenue_tables(tmp_path):
    db_path = seed_revenue_database(tmp_path / "nbs.db")
    before = revenue_table_digest(db_path)

    migrate_gmv_schema(db_path)
    migrate_gmv_schema(db_path)

    assert EXPECTED_GMV_OBJECTS <= sqlite_objects(db_path)
    assert revenue_table_digest(db_path) == before


def test_immutable_ledger_tables_reject_update_and_delete(migrated_db):
    seed_confirmed_ledger_rows(migrated_db)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        migrated_db.execute("DELETE FROM gmv_refund_observations")
```

Also test:

- partial unique index permits at most one `status='ACTIVE'` version;
- foreign keys are enabled per repository connection;
- `CHECK` constraints reject negative minor units and unknown dimensions/statuses;
- current views return no rows when no active version exists;
- repository import and readonly calls do not create tables;
- CLI `--dry-run` makes no database or backup changes;
- CLI apply creates a hot backup and passes `PRAGMA integrity_check`;
- failed migration leaves the source DB byte-valid and restorable from backup.

- [ ] **Step 2: Run red tests**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_repository.py tests/test_gmv_refund_migration_cli.py
```

Expected: FAIL because repository and migration CLI do not exist.

- [ ] **Step 3: Implement schema SQL exactly from the approved spec**

Create all nine tables, three stable views and required indexes from spec sections 6.1–6.11. Add `BEFORE UPDATE` and `BEFORE DELETE` triggers with `RAISE(ABORT, 'gmv ledger is immutable')` for:

- `gmv_refund_batches`
- `gmv_refund_observations`
- `gmv_reconciliation_results`
- `gmv_reconciliation_members`
- `gmv_adjustment_snapshot`
- `gmv_metric_snapshot`
- `gmv_scope_events`

`gmv_refund_current` remains a mutable projection. `gmv_scope_versions` may transition status only through repository methods that also append a scope event in the same transaction.

- [ ] **Step 4: Implement explicit CLI with no render-time side effect**

CLI contract:

```bash
.venv/bin/python scripts/migrate_gmv_refund_schema.py --db-path /absolute/path/to/nbs.db --dry-run
.venv/bin/python scripts/migrate_gmv_refund_schema.py --db-path /absolute/path/to/nbs.db --apply
```

`--apply` sequence:

1. Resolve explicit path; reject missing/non-SQLite file.
2. Run `validate_sqlite_database()`.
3. Create `hot_backup_database()` and print its absolute path.
4. Open one connection, set `PRAGMA foreign_keys=ON`, `BEGIN IMMEDIATE`, apply schema, validate expected objects, commit.
5. Re-run integrity and schema validation; on failure rollback and print restore instructions.

Do not call this CLI or `migrate_gmv_schema()` from `app_pages.py`, repository constructor or any import path.

- [ ] **Step 5: Run repository green checks**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_repository.py tests/test_gmv_refund_migration_cli.py
.venv/bin/python -m py_compile backend/services/gmv_refund_repository.py scripts/migrate_gmv_refund_schema.py
git diff --check
```

- [ ] **Step 6: Findings-first Review and main-Codex commit**

Review schema, constraints, trigger coverage, explicit migration and unchanged revenue digests. After PASS:

```bash
git add backend/services/gmv_refund_repository.py scripts/migrate_gmv_refund_schema.py tests/test_gmv_refund_repository.py tests/test_gmv_refund_migration_cli.py
git commit -m "feat: add versioned gmv sqlite ledger"
```

---

## Task 3: 建立純讀 Preflight 與 current-state 對帳 projection

**Files:**
- Create: `backend/services/gmv_refund_service.py`
- Create: `tests/test_gmv_refund_service.py`
- Modify: `tests/test_gmv_refund_preflight.py`
- Modify: `tests/test_gmv_refund_adjustment.py`

**Service surface:**

```text
preview_refund_batch(refund_rows: pd.DataFrame, *, repository: GmvRefundRepository, revenue_frames: RevenueFrames, revenue_generation_token: str, rule_version: str, file_sha256: str) -> GmvRefundPreview
```

The preview token contains `file_sha256`, current-state hash, revenue generation token, rule version, proposed-state hash, reconciliation hash, issue acknowledgements required and expiry timestamp. It contains no raw private workbook bytes.

- [ ] **Step 1: Write failing pure-preview tests**

Cover these scenarios:

```python
def test_preview_uses_existing_current_plus_incoming_status_change(service_fixture):
    # Existing R-1 is 退款中; incoming changes it to 已退款.
    preview = service_fixture.preview_status_change(
        refund_order_no="R-1",
        previous_status="退款中",
        incoming_status="已退款",
        amount_minor=5000,
    )

    assert preview.change_counts == {
        "NEW": 0,
        "UNCHANGED": 0,
        "STATUS_CHANGED": 1,
        "REFUND_IDENTITY_CONFLICT": 0,
    }
    assert preview.dimensions["總退款"].refund_minor == 5000
    assert preview.dimensions["已退款"].refund_minor == 5000
    assert preview.official_net_gmv_minor == preview.formal_revenue_minor - 5000


def test_preview_is_read_only(service_fixture):
    before = service_fixture.gmv_table_digests()
    service_fixture.preview_new_refund()
    assert service_fixture.gmv_table_digests() == before
```

Also assert:

- only exact `退款狀態 == 已退款` contributes to official net GMV;
- `總退款` includes all valid statuses;
- unmatched and revenue-scope-excluded refunds do not deduct official GMV;
- over-refund is capped at original receipt amount;
- missing/duplicate `退款單號`, negative amount, invalid date/status and identity conflict are classified per blocking/warning rules;
- same file/current/revenue/rule inputs produce identical hashes;
- changing any protected input changes the token;
- `COL_QTY` values are exactly equal before/after both dimensions.

- [ ] **Step 2: Run red tests**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_service.py tests/test_gmv_refund_preflight.py tests/test_gmv_refund_adjustment.py
```

- [ ] **Step 3: Implement formal adapter and reuse the existing engine**

Convert each valid workbook row into `RefundObservation`; do not rewrite `_apply_gmv_refund_adjustments()` as a second reconciliation engine. Build proposed current state as:

```python
proposed = dict(repository.load_current_refunds())
for change in (*changes.new, *changes.status_changed):
    proposed[change.refund_order_no] = change.to_current_state(batch_id="PREVIEW")
```

Identity conflicts remain blocked unless the preview records a specific conflict acknowledgement; the actual override is persisted only during confirm. Run both existing dimensions against all `proposed` current rows, not only the uploaded increment.

- [ ] **Step 4: Add deterministic revenue row fingerprints**

In `app_workflows.py`, add:

```python
def _gmv_revenue_row_fingerprint(table_name: str, row: pd.Series) -> str:
    payload = {
        "table": table_name,
        "source_receipt_no": str(row.get(COL_ORDER_ID, "")),
        "receipt_time": str(row.get(COL_DATE, "")),
        "amount_minor": money_to_minor(row.get(COL_MONEY, 0)),
        "branch": str(row.get(COL_BRANCH, "")),
        "salesperson": str(row.get(COL_SALESPERSON, "")),
    }
    return canonical_payload_sha256(payload)
```

The same helper must be used when writing and later materializing adjustment snapshots. Collision or missing fingerprint is blocking.

- [ ] **Step 5: Run focused green and parity checks**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_service.py tests/test_gmv_refund_preflight.py tests/test_gmv_refund_adjustment.py
.venv/bin/python -m py_compile backend/services/gmv_refund_service.py app_workflows.py
git diff --check
```

- [ ] **Step 6: Findings-first Review and main-Codex commit**

After PASS:

```bash
git add backend/services/gmv_refund_service.py app_workflows.py tests/test_gmv_refund_service.py tests/test_gmv_refund_preflight.py tests/test_gmv_refund_adjustment.py
git commit -m "feat: preview incremental formal gmv refunds"
```

---

## Task 4: 實作 atomic confirm、snapshot activation 與 stale guards

**Files:**
- Modify: `backend/services/gmv_refund_repository.py`
- Modify: `backend/services/gmv_refund_service.py`
- Modify: `tests/test_gmv_refund_repository.py`
- Modify: `tests/test_gmv_refund_service.py`

**Protected surface:** This Task modifies the formal SQLite write contract and must be executed directly by main Codex after explicit Task 4 authorization.

**Service surface:**

```text
confirm_refund_batch(preview: GmvRefundPreview, *, actor: str, acknowledgements: frozenset[str], db_path: Path, coordination_db_path: Path, revenue_loader: Callable[[], RevenueFrames], revenue_generation_loader: Callable[[], str]) -> GmvActivationReceipt
```

- [ ] **Step 1: Write failing transaction tests with fault injection**

Tests must prove:

- shared lease busy → no GMV writes;
- preflight blocking issue or missing warning acknowledgement → no writes;
- stale file hash, current-state hash, revenue token, rule version or reconciliation hash → no writes;
- duplicate `(file_sha256, revenue_generation_token)` → fail closed;
- `UNCHANGED` observations remain auditable in batch/reconciliation evidence but do not update current projection;
- `NEW` and `STATUS_CHANGED` update current projection;
- one new version changes prior `ACTIVE` to `RETIRED`, inserts all snapshots/events and becomes the sole `ACTIVE` row;
- injected failure after each write stage rolls back the whole transaction and preserves the previous active version;
- successful commit returns IDs, hashes, actor, timestamp, prior version and backup path;
- successful GMV commit does not call or mutate existing `advance_cache_generation()` state.

Use a parametrized fault hook:

```python
@pytest.mark.parametrize(
    "stage",
    [
        "after_batch",
        "after_observations",
        "after_current_projection",
        "after_reconciliation",
        "after_adjustments",
        "after_metrics",
        "after_activation_event",
    ],
)
def test_confirm_rolls_back_every_stage(stage, service_fixture):
    before = service_fixture.active_state_digest()
    with pytest.raises(InjectedGmvFailure):
        service_fixture.confirm(fail_after=stage)
    assert service_fixture.active_state_digest() == before
```

- [ ] **Step 2: Run red tests**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_repository.py tests/test_gmv_refund_service.py
```

- [ ] **Step 3: Implement confirm sequence under one lease and transaction**

Required order:

1. Acquire `acquire_upload_lease()` using the existing coordination DB.
2. Re-read revenue generation, rule version and current refund state.
3. Recompute all protected hashes from source data; compare with preview.
4. Create hot backup before opening write transaction.
5. `BEGIN IMMEDIATE` on one SQLite connection with foreign keys enabled.
6. Insert batch and observations.
7. Update only approved `NEW`／`STATUS_CHANGED`／acknowledged conflict rows in `gmv_refund_current`.
8. Insert both reconciliation dimensions and members.
9. Insert one new version plus adjustment and metric snapshots.
10. Retire previous active version, activate new version, append event.
11. Validate one active version and snapshot checksums, then commit.
12. Return immutable receipt; release lease in `finally`.

Do not call `advance_cache_generation()`. The new active `version_id` becomes the cache key only for GMV consumers.

- [ ] **Step 4: Build metric snapshots from existing dashboard engine**

For both `總退款` and `已退款`, call `build_dashboard_data()` with `make_workbook=False` only during confirm/rebuild. Store monetary metrics with `quantity_basis='NOT_APPLICABLE'`; store travel/ticket quantities with `quantity_basis='ORIGINAL_TRANSACTION'`. Official scope rows use only `已退款` adjustments.

- [ ] **Step 5: Run protected write-contract checks**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_repository.py tests/test_gmv_refund_service.py tests/test_database_rollback.py tests/test_upload_rollback_service.py tests/test_cache_generation_service.py
.venv/bin/python -m py_compile backend/services/gmv_refund_repository.py backend/services/gmv_refund_service.py
git diff --check
```

- [ ] **Step 6: Findings-first Review and main-Codex commit**

Review must explicitly cover atomicity, lease, stale guards, old active preservation and cache isolation. After PASS:

```bash
git add backend/services/gmv_refund_repository.py backend/services/gmv_refund_service.py tests/test_gmv_refund_repository.py tests/test_gmv_refund_service.py
git commit -m "feat: activate formal net gmv atomically"
```

---

## Task 5: 實作 current read model、rebuild、rollback 與 deactivate

**Files:**
- Modify: `backend/services/gmv_refund_repository.py`
- Modify: `backend/services/gmv_refund_service.py`
- Modify: `tests/test_gmv_refund_repository.py`
- Modify: `tests/test_gmv_refund_service.py`

**Protected surface:** This Task changes formal version state and must be executed directly by main Codex after explicit Task 5 authorization.

- [ ] **Step 1: Write failing lifecycle tests**

Cover:

- no active version returns an explicit `NOT_INITIALIZED` state;
- active revenue token equals current revenue token → `CURRENT`;
- revenue token mismatch → `STALE_REVENUE`, but old active snapshot remains readable and visibly stale;
- `rebuild_gmv_scope()` uses all `gmv_refund_current`, creates no observations, writes a new version and activates only after complete snapshots;
- rollback switches to an existing checksum-valid version and appends `ROLLBACK` event;
- rollback to missing/corrupt/incomplete version fails without changing active pointer;
- deactivate leaves no active version and appends `DEACTIVATE` event;
- every lifecycle write uses shared lease and one transaction;
- history order is deterministic and limited.

- [ ] **Step 2: Run red tests**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_repository.py tests/test_gmv_refund_service.py
```

- [ ] **Step 3: Implement lifecycle methods**

```text
load_gmv_scope_status(repository: GmvRefundRepository, current_revenue_token: str) -> GmvScopeStatus
rebuild_gmv_scope(*, reason: str, actor: str, db_path: Path, coordination_db_path: Path) -> GmvActivationReceipt
rollback_gmv_scope(target_version_id: str, *, reason: str, actor: str, db_path: Path, coordination_db_path: Path) -> GmvActivationReceipt
deactivate_gmv_scope(*, reason: str, actor: str, db_path: Path, coordination_db_path: Path) -> GmvLifecycleReceipt
```

Rebuild reuses the Task 4 snapshot builder. Rollback validates stored checksums before changing version status. Deactivate never deletes observations, current projection, snapshots or events.

- [ ] **Step 4: Run lifecycle green checks**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_refund_repository.py tests/test_gmv_refund_service.py
.venv/bin/python -m py_compile backend/services/gmv_refund_repository.py backend/services/gmv_refund_service.py
git diff --check
```

- [ ] **Step 5: Findings-first Review and main-Codex commit**

After PASS:

```bash
git add backend/services/gmv_refund_repository.py backend/services/gmv_refund_service.py tests/test_gmv_refund_repository.py tests/test_gmv_refund_service.py
git commit -m "feat: manage formal gmv scope lifecycle"
```

---

## Task 6: 接入 lazy Streamlit Dashboard 與人工確認流程

**Files:**
- Modify: `app_pages.py`
- Modify: `app_workflows.py`
- Create: `tests/test_streamlit_gmv_formal_contract.py`
- Modify: `tests/test_streamlit_gmv_refund_contract.py`

**Protected surface:** This Task connects business-rule writes to Streamlit and must be executed directly by main Codex after explicit Task 6 authorization.

- [ ] **Step 1: Write failing source/AST and render-contract tests**

Assert all of the following:

- app startup and `_render_dashboard_tab()` contain no GMV repository load;
- `_render_gmv_exclusion_tab()` does not call migration;
- when there is no refund upload and no explicit formal-load action, `_render_gmv_exclusion_tab()` returns before `load_all_data_from_db()`;
- active snapshot is not loaded until the user clicks `載入正式淨 GMV` or uploads a refund file for formal Preflight;
- session-only `總退款`／`已退款` Preflight and Exception Center still render;
- formal preview shows `NEW`、`UNCHANGED`、`STATUS_CHANGED`、`REFUND_IDENTITY_CONFLICT` counts and blocking/warning acknowledgements;
- confirmation button is disabled on blocking issues and requires actor/reason plus warning confirmations;
- successful receipt shows batch/version/prior-version IDs, revenue token, rule version, hashes and timestamp;
- stale active scope shows a rebuild action and never silently recomputes on render;
- history rollback and deactivate both require explicit confirmation text;
- labels distinguish `正式營收`、`已退款實際扣減`、`正式淨 GMV` and `總退款`；
- all travel/ticket quantity labels include `原交易` and `未按退款調整`.

Example AST guard:

```python
def test_home_render_does_not_load_gmv_repository():
    source = Path("app_pages.py").read_text(encoding="utf-8")
    home = function_source(source, "_render_dashboard_tab")
    assert "GmvRefundRepository" not in home
    assert "load_active_scope" not in home
```

- [ ] **Step 2: Run red UI-contract tests**

```bash
.venv/bin/python -m pytest -q tests/test_streamlit_gmv_formal_contract.py tests/test_streamlit_gmv_refund_contract.py
```

- [ ] **Step 3: Implement a lazy formal panel inside the existing tab**

Keep current session-only upload UX, but render the uploader and formal-load button before any database load. If there is neither an upload nor a formal-load action, clear only the existing session-only upload state, render the informational panel, and return before `load_all_data_from_db()`. Add a separate `正式淨 GMV` panel with this gate:

```python
formal_loaded = bool(st.session_state.get("GMV_FORMAL_SCOPE_LOADED"))
if st.button("載入正式淨 GMV", key="GMV_FORMAL_LOAD", width="stretch"):
    st.session_state["GMV_FORMAL_SCOPE_LOADED"] = True
    formal_loaded = True

if formal_loaded:
    _render_formal_gmv_scope_panel()
```

Do not instantiate the repository before `formal_loaded` or a formal-preview action. Keep workbook generation behind its own button.

- [ ] **Step 4: Wire preview/confirm without storing raw files in session state**

Store only canonical preview metadata and hashes in `st.session_state`. On confirm, re-read/re-hash the uploaded object, reacquire current revenue/rules and call `confirm_refund_batch()`. If the uploaded object or any protected generation changes, show stale failure and require a new Preflight.

- [ ] **Step 5: Run focused Streamlit regressions**

```bash
.venv/bin/python -m pytest -q tests/test_streamlit_gmv_formal_contract.py tests/test_streamlit_gmv_refund_contract.py tests/test_streamlit_upload_feedback_contract.py tests/test_gmv_refund_service.py
.venv/bin/python -m py_compile app_pages.py app_workflows.py
git diff --check
```

- [ ] **Step 6: Findings-first Review and main-Codex commit**

After PASS:

```bash
git add app_pages.py app_workflows.py tests/test_streamlit_gmv_formal_contract.py tests/test_streamlit_gmv_refund_contract.py
git commit -m "feat: expose formal net gmv dashboard"
```

---

## Task 7: 接入 active-snapshot Export 與 provenance

**Files:**
- Modify: `app_workflows.py`
- Modify: `app_pages.py`
- Create: `tests/test_gmv_formal_export_contract.py`
- Modify: `tests/test_official_export_workbook_contract.py`

**Protected surface:** This Task changes GMV export schema and must be executed directly by main Codex after explicit Task 7 authorization.

- [ ] **Step 1: Write failing export contract tests**

Test that a formal GMV export:

- materializes only from the selected active version adjustment snapshot;
- blocks when revenue generation is stale instead of mixing generations;
- includes both `總退款` and `已退款` complete workbook sets;
- includes an explicit `正式淨GMV摘要` sourced only from `已退款`;
- keeps `總退款` as operational comparison and never labels it official net GMV;
- includes provenance: version ID, revenue token, rule version, file/current/reconciliation hashes, generated timestamp and quantity basis;
- reproduces session-only engine totals for refund detail, applied deduction, over-refund and exception rows;
- keeps `COL_QTY` exactly unchanged and labels it `原交易人數／數量（未按退款調整）`;
- does not alter the existing official revenue workbook sheet contract.

```python
def test_formal_export_uses_paid_refunds_for_official_net_only(export_fixture):
    workbooks = export_fixture.build_formal_gmv_workbooks()
    official = read_sheet(workbooks["formal"], "正式淨GMV摘要")
    total = read_sheet(workbooks["total"], "總退款摘要")
    paid = read_sheet(workbooks["paid"], "已退款摘要")

    assert official.loc[0, "實際扣減金額"] == paid.loc[0, "實際扣減金額"]
    assert total.loc[0, "退款明細金額"] >= paid.loc[0, "退款明細金額"]
```

- [ ] **Step 2: Run red export tests**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_formal_export_contract.py tests/test_official_export_workbook_contract.py
```

- [ ] **Step 3: Implement snapshot materialization**

Add:

```text
_apply_gmv_adjustment_snapshot(formal_tour: pd.DataFrame, formal_others: pd.DataFrame, adjustment_snapshot: pd.DataFrame, *, refund_dimension: str) -> tuple[pd.DataFrame, pd.DataFrame]
```

Match every snapshot row by the deterministic revenue row fingerprint from Task 3. Missing/duplicate fingerprints are blocking; do not fall back to source order alone. Subtract exact minor units, then expose money as decimal-compatible values to the existing workbook engine. Assert row counts and quantity digests before/after.

- [ ] **Step 4: Build all three export products on demand**

Use existing `_compute_gmv_exclusion_workbooks()` for the two complete report sets. Extend `_build_gmv_audit_workbook()` or add a focused provenance workbook helper so that `total`, `paid`, and `formal` downloads all contain traceable metadata. Do not precompute any workbook during page render.

- [ ] **Step 5: Run export and existing report regressions**

```bash
.venv/bin/python -m pytest -q tests/test_gmv_formal_export_contract.py tests/test_official_export_workbook_contract.py tests/test_gmv_refund_adjustment.py tests/test_streamlit_gmv_formal_contract.py
.venv/bin/python -m py_compile app_workflows.py app_pages.py
git diff --check
```

- [ ] **Step 6: Findings-first Review and main-Codex commit**

After PASS:

```bash
git add app_workflows.py app_pages.py tests/test_gmv_formal_export_contract.py tests/test_official_export_workbook_contract.py tests/test_streamlit_gmv_formal_contract.py
git commit -m "feat: export versioned formal net gmv"
```

---

## Task 8: 效能 gate、完整驗證、Hermes 與實際 UI acceptance

**Files:**
- Modify: `scripts/benchmark_gmv_page_load.py`
- Modify: `tests/test_gmv_performance_contract.py`
- Modify only if verification exposes an in-scope defect: files already allowlisted by Tasks 1–7

- [ ] **Step 1: Validate the preserved pre-change baseline identity**

Confirm `/tmp/nbs-gmv-baseline.json` came from Task 0, uses the same DB identity hash, row counts, Python/Streamlit versions and entrypoint. If any identity differs, check out Task 0 commit in an isolated worktree and recapture the baseline there before returning to the candidate branch.

```bash
.venv/bin/python scripts/benchmark_gmv_page_load.py --mode candidate --iterations 10 --baseline /tmp/nbs-gmv-baseline.json --output /tmp/nbs-gmv-candidate.json
```

- [ ] **Step 2: Write and run performance contract tests**

Tests assert:

- home render performs zero GMV repository calls;
- import/render never performs migration;
- one active scope summary query is supported by indexes and has no full scan over observation/reconciliation ledgers;
- workbook builder is not invoked by Dashboard data preparation;
- benchmark comparison fails if dashboard data-preparation median regression exceeds either `300 ms` or `5%` of baseline;
- indexed active read target is `< 100 ms` median;
- GMV Dashboard data preparation target is `< 1.5 s` median, excluding workbook generation.

```bash
.venv/bin/python -m pytest -q tests/test_gmv_performance_contract.py tests/test_streamlit_gmv_formal_contract.py
.venv/bin/python scripts/benchmark_gmv_page_load.py --mode candidate --iterations 10 --baseline /tmp/nbs-gmv-baseline.json --output /tmp/nbs-gmv-candidate.json
```

- [ ] **Step 3: Run all targeted business-feature tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_gmv_refund_models.py \
  tests/test_gmv_refund_repository.py \
  tests/test_gmv_refund_service.py \
  tests/test_gmv_refund_migration_cli.py \
  tests/test_gmv_refund_adjustment.py \
  tests/test_gmv_refund_preflight.py \
  tests/test_streamlit_gmv_refund_contract.py \
  tests/test_streamlit_gmv_formal_contract.py \
  tests/test_gmv_formal_export_contract.py \
  tests/test_official_export_workbook_contract.py \
  tests/test_gmv_performance_contract.py \
  tests/test_database_explicit_path.py \
  tests/test_database_rollback.py \
  tests/test_upload_rollback_service.py \
  tests/test_cache_generation_service.py
```

Expected: all pass.

- [ ] **Step 4: Run final findings-first Review over the complete implementation diff**

Review against the approved spec and this plan. It must report requirement coverage, targeted evidence, residual risk, baseline risk, changed-surface attribution and Hermes required checks. Fix every finding, rerun affected targeted tests, and obtain Review PASS before proceeding.

- [ ] **Step 5: Run full verification and Hermes**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/hermes_post_change_check.py --markdown
git diff --check
git status --short
```

Acceptance requires full pytest PASS and Hermes overall PASS. If unrelated failures exist, report them separately and do not claim completion.

- [ ] **Step 6: Run temp-copy migration and transaction acceptance**

Never use the formal DB for this step:

```bash
export GMV_ACCEPTANCE_DIR="$(mktemp -d)"
cp data/nbs_analytics.db "$GMV_ACCEPTANCE_DIR/nbs_analytics.db"
.venv/bin/python scripts/migrate_gmv_refund_schema.py --db-path "$GMV_ACCEPTANCE_DIR/nbs_analytics.db" --apply
.venv/bin/python -c "from database import validate_sqlite_database; import os; print(validate_sqlite_database(os.environ['GMV_ACCEPTANCE_DIR'] + '/nbs_analytics.db'))"
```

Use the actual resolved DB path from `resolve_db_path()` if `data/nbs_analytics.db` is not the configured source; print and validate the exact temp-copy target before migration. Keep the temp directory until UI/export evidence is captured, then remove only that explicit temp path.

- [ ] **Step 7: Run private refund-file parity acceptance on the temp DB**

With `/Users/chanwaitung2025/Downloads/退款明細數據.xlsx`:

- Preflight all rows and confirm E-column status distribution.
- Verify `退款單號` uniqueness/business-key classification.
- Confirm a temp-DB version and compare `總退款`／`已退款` totals, applied deductions, over-refunds and exceptions against the existing session-only engine.
- Simulate one refund status transition from `退款中` to `已退款`; confirm it becomes `STATUS_CHANGED` and only the new active version changes official net GMV.
- Verify travel/ticket quantity digests are unchanged.
- Generate all three export products and inspect required sheets/provenance.
- Do not log raw refund rows or copy the workbook into the repo.

- [ ] **Step 8: Run actual Streamlit UI acceptance**

Start the existing app through the project’s normal service manager, then verify in browser:

1. Home renders before any GMV query and existing formal KPI remains unchanged.
2. GMV tab keeps session-only Preflight and Exception Center.
3. Formal data appears only after `載入正式淨 GMV`.
4. Upload preview displays both dimensions and incremental classification.
5. Confirm receipt, stale state, rebuild, history, rollback and deactivate controls are explicit and understandable.
6. `總退款` remains visible; official net GMV only uses `已退款`.
7. Quantity wording is `原交易人數／數量（未按退款調整）`.
8. All downloads open and contain expected sheets/provenance.

Capture screenshots and service logs without exposing private refund rows.

- [ ] **Step 9: Main-Codex final commit**

After Review PASS, full verification PASS, Hermes PASS and UI acceptance:

```bash
git add scripts/benchmark_gmv_page_load.py tests/test_gmv_performance_contract.py
git commit -m "test: enforce formal gmv performance gates"
```

Do not stage `.superpowers/brainstorm/`, runtime JSON, temp DBs, private Excel files, logs or screenshots unless a separate evidence path is explicitly approved.

---

## Production Migration and Rollout Checkpoint

This is not automatically authorized by implementation completion. After Tasks 1–8 pass, present the exact resolved formal DB path, hot-backup target, current integrity result, expected schema diff, rollback command and measured performance evidence. Only after a new explicit user approval may main Codex:

1. stop or place the write path in maintenance mode using the existing project procedure;
2. export the validated path as `NBS_FORMAL_DB_PATH`, then run `scripts/migrate_gmv_refund_schema.py --db-path "$NBS_FORMAL_DB_PATH" --apply`;
3. validate integrity and unchanged revenue-table digests;
4. restart/verify services;
5. create the first formal GMV version through the Streamlit confirmation flow;
6. rerun baseline, formal-scope and Hermes checks.

Documentation updates to `NBS_ANALYTICS_SYSTEM_MAP.md` and `NBS_ANALYTICS_HANDOFF.md` occur only after Review PASS, full verification PASS and Hermes PASS, through the project’s approved documentation target flow; they are not silently included in implementation commits.

## Definition of Done

- Existing formal revenue and 2026-05 frozen baseline are unchanged.
- Same SQLite contains validated `gmv_` schema with immutable ledger protections and one active version maximum.
- Incremental refund uploads update current state by `退款單號`; status transitions do not become duplicate append-only business state.
- Official net GMV deducts only exact `已退款`; `總退款` remains available as an operational dimension.
- Confirm/rebuild/rollback/deactivate are lease-protected, atomic, auditable and fail closed on stale evidence.
- Dashboard uses lazy active snapshots and does not regress the measured dashboard data-preparation median beyond `300 ms` or `5%`.
- Exports contain total, paid and formal products with provenance; quantity values remain original and visibly labelled.
- Targeted tests, complete pytest, findings-first Review, Hermes, temp migration, private-file parity, actual Streamlit UI and performance gates all pass with captured evidence.
