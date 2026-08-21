# GMV One-Click Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 GMV 排除訂單看板改成「上傳退款檔 → 一鍵合併 → 自動建立 active version → 直接展示最新版本與快取報表」，並以 version-scoped cache 消除重複報表生成。

**Architecture:** 沿用既有 `GmvRefundRepository`、`preview_refund_batch`、`confirm_refund_batch`、upload lease 與 immutable version tables。新增受控的 `gmv_export_cache_service`，以 JSON manifest、CSV detail 與 XLSX bytes 保存 derived artifacts；active page read 只讀 SQLite snapshots／cache，不在 render path 重算全量營收。Streamlit 只負責 uploader、單一 merge action、進度與 read-only 展示。

**Tech Stack:** Python 3.10、Streamlit、pandas、SQLite、openpyxl、pytest、既有 NBS system_manager／Hermes checks。

**Spec:** `docs/superpowers/specs/2026-08-21-gmv-one-click-merge-design.md`

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 正式淨 GMV 只扣減退款狀態為「已退款」的金額；總退款維度繼續保留作營運比較。
- warning 不要求人工 acknowledgement，允許 upsert 與建立 active version；blocking 不寫正式 SQLite、不建立 active version。
- 同一 refund identity 的「退款中 → 已退款」必須支援增量狀態更新。
- 不修改 frozen baseline、旅行團／票務數量規則或原營收表。
- 不新增 SQLite database、migration、FastAPI endpoint、background service、message queue 或外部服務。
- export cache 是 ignored derived artifact，不是 canonical business source，不進 Git。
- active version transaction 與 workbook serialization 不得共用長時間 SQLite transaction。
- 每個 Task 完成後先交 findings-first Review；Review PASS 後才進入完整驗證／Hermes。
- Implementation Agent 只執行一個已批准 Task，不得自行 commit、merge、push 或操作正式 SQLite；正式資料操作只由 Codex 在批准的 acceptance step 執行。

## File Map

| File | Responsibility in this plan |
|---|---|
| `backend/services/gmv_refund_models.py` | 保留／補強 Preflight、refund change、active read model 的 typed contract。 |
| `backend/services/gmv_refund_repository.py` | 只使用既有 GMV ledger、snapshot、version、event tables；必要時補充 read-only manifest event payload 讀取。 |
| `backend/services/gmv_refund_service.py` | merge idempotency、warning／blocking gate、status update、active version activation、fast active read contract。 |
| `backend/services/gmv_export_cache_service.py` | 新增安全、version-scoped、atomic 的 JSON／CSV／XLSX derived cache。 |
| `app_workflows.py` | 抽出一次性 adjusted frames／summary／export build，避免兩個維度重複掃描和重建。 |
| `app_pages.py` | 將 GMV tab 改為單一 merge action、直接 active display、cache status 與 download controls。 |
| `tests/test_gmv_refund_service.py` | merge policy、status transition、idempotency、stale token、active read model tests。 |
| `tests/test_gmv_refund_preflight.py` | blocking／warning classification tests。 |
| `tests/test_gmv_export_cache_service.py` | cache manifest、atomic write、cache hit／miss／invalid、failure isolation tests。 |
| `tests/test_streamlit_gmv_refund_contract.py` | 一鍵 UI contract、移除人工操作、warning／blocking copy tests。 |
| `tests/test_streamlit_gmv_formal_contract.py` | active read and export cache UI contract tests。 |
| `tests/test_gmv_one_click_merge_integration.py` | fixture-based end-to-end merge and cache integration tests。 |
| `tests/test_gmv_export_performance.py` | stage timing and repeat-download performance tests。 |

---

### Task 1: 固定 Preflight、Merge Policy 與 Idempotent Active Version Contract

**Files:**
- Modify: `backend/services/gmv_refund_models.py`
- Modify: `backend/services/gmv_refund_service.py`
- Modify: `backend/services/gmv_refund_repository.py`
- Modify: `tests/test_gmv_refund_preflight.py`
- Modify: `tests/test_gmv_refund_service.py`

**Interfaces:**
- Consumes: existing `GmvRefundPreview`, `preview_refund_batch`, `confirm_refund_batch`, `GmvRefundRepository`, `revenue_generation_token`。
- Produces: a tested merge contract where `preview.blocking_codes` alone blocks; warning codes are persisted in activation provenance; `confirm_refund_batch` remains the single writer for activation; repeated `(file_sha256, revenue_generation_token)` returns the existing activation result without creating a second ACTIVE version。

- [ ] **Step 1: Add local fixture builders and failing tests for warning-pass and blocking-stop behavior**

  Reuse the existing `_seed_database`, `_frames`, and `_seed_current` helpers in `tests/test_gmv_refund_service.py`; add only a test-local `_preview_with_warning(tmp_path: Path, code: str) -> GmvRefundPreview` helper. It must create the isolated database with `_seed_database`, create `GmvRefundRepository`, call `preview_refund_batch` with `_frames()` and a one-row refund DataFrame, and pass `warning_codes=(code,)`. It must not bypass the production preview or confirmation functions.

  ```python
  def test_warning_only_preview_can_be_confirmed_without_acknowledgement(tmp_path):
      preview = _preview_with_warning(tmp_path, code="SQLITE_SOURCE_NOT_FOUND")
      assert preview.blocking_codes == ()
      receipt = confirm_refund_batch(
          preview,
          actor="streamlit-auto-merge",
          acknowledgements=frozenset(),
          db_path=str(tmp_path / "nbs.db"),
          coordination_db_path=str(tmp_path / "coordination.db"),
          revenue_loader=_frames,
          revenue_generation_loader=lambda: preview.revenue_generation_token,
      )
      assert receipt.version_id
  ```

  Add the corresponding assertion that a preview containing `REFUND_IDENTITY_CONFLICT`, `EMPTY_SOURCE_RECEIPT_NO`, or `INVALID_REFUND_AMOUNT` raises before any ledger/version row is written. Also assert that `preview.warning_codes` is serialized into the activation event／provenance payload.

- [ ] **Step 2: Run the focused tests and verify the current behavior fails**

  Run:

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q \
    tests/test_gmv_refund_preflight.py \
    tests/test_gmv_refund_service.py
  ```

  Expected: the new warning test fails because the current confirmation path still expects the previous acknowledgement／manual-confirmation assumptions or does not expose idempotent behavior.

- [ ] **Step 3: Implement the smallest service contract change**

  In `confirm_refund_batch`:

  - Treat `preview.blocking_codes` as the only hard gate.
  - Accept an empty acknowledgement set for the automatic merge actor.
  - Add an optional `warning_codes: tuple[str, ...] = ()` argument to `preview_refund_batch`; populate it from the existing `_build_gmv_refund_preflight` report before calling the service preview. Preserve warning codes and the bounded `{code, count, amount, examples}` summary in the existing version／event provenance payload; do not add a new database.
  - Before creating a new batch, query the existing unique `(file_sha256, revenue_generation_token)` identity and return the existing `GmvActivationReceipt` when it is already activated.
  - Keep `STATUS_CHANGED` as a valid proposed state transition and keep identity conflicts blocking.
  - Keep all database writes inside the existing coordination and transaction boundary.

- [ ] **Step 4: Run focused tests and inspect SQLite assertions**

  Run the focused command from Step 2 plus:

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q tests/test_gmv_refund_repository.py
  ```

  Expected: all focused refund tests pass; repeated merge has exactly one ACTIVE version and no duplicate batch／observation for the same identity.

- [ ] **Step 5: Commit the task**

  ```bash
  git add backend/services/gmv_refund_models.py \
    backend/services/gmv_refund_service.py \
    backend/services/gmv_refund_repository.py \
    tests/test_gmv_refund_preflight.py \
    tests/test_gmv_refund_service.py
  git commit -m "feat: allow warning-only automatic GMV merge"
  ```

### Task 2: 建立安全的 Version-Scoped Export Cache Service

**Files:**
- Create: `backend/services/gmv_export_cache_service.py`
- Create: `tests/test_gmv_export_cache_service.py`
- Modify: `backend/services/gmv_refund_models.py`
- Modify: `tests/test_gmv_refund_service.py`

**Interfaces:**
- Consumes: `version_id`, `revenue_generation_token`, `rule_version`, `OFFICIAL_EXPORT_SCHEMA_CONTRACT`, total／paid workbook bytes, summary rows and adjusted detail frames。
- Produces:

  ```python
  @dataclass(frozen=True)
  class GmvExportCacheManifest:
      cache_key: str
      version_id: str
      revenue_generation_token: str
      rule_version: str
      schema_version: str
      status: str  # pending | ready | failed
      artifacts: dict[str, dict[str, object]]
      error: str | None

  def build_gmv_export_cache(
      *, version_id: str, revenue_generation_token: str, rule_version: str,
      total_workbooks: dict[str, bytes], paid_workbooks: dict[str, bytes],
      total_detail: pd.DataFrame, paid_detail: pd.DataFrame,
      summaries: list[dict], cache_dir: Path,
  ) -> GmvExportCacheManifest

  def load_gmv_export_cache(
      *, version_id: str, revenue_generation_token: str, rule_version: str,
      cache_dir: Path,
  ) -> GmvExportCacheManifest | None
  ```

- [ ] **Step 1: Write failing cache contract tests**

  Cover:

  - exact cache key changes when version／token／rule／schema changes;
  - successful manifest records every workbook byte size and build duration;
  - cache hit returns only the matching manifest;
  - partial temp directory is never returned as ready;
  - simulated workbook write failure returns `failed` without changing SQLite;
  - manifest and detail files are JSON／CSV／XLSX only; do not introduce `pickle.loads` for this cache.

- [ ] **Step 2: Run the cache tests to verify they fail**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q tests/test_gmv_export_cache_service.py
  ```

  Expected: import／contract failures because the new service does not exist.

- [ ] **Step 3: Implement atomic cache writes**

  Implement the service with:

  - deterministic key `gmv-formal-export-v1:<sha256>` from version, token, rule and official export schema;
  - a version-scoped directory under `.nbs_runtime_cache/` using a safe version id and key;
  - write-to-temp followed by `os.replace` for manifest, CSV detail and XLSX files;
  - manifest `status=ready` only after all artifacts exist and sizes are verified;
  - `status=failed` manifest with stage and exception type/message when serialization fails;
  - no raw source Excel copied into cache and no cache path outside the configured cache root.

- [ ] **Step 4: Run cache tests and verify artifact safety**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q tests/test_gmv_export_cache_service.py
  git diff --check
  ```

  Expected: all cache tests pass; no tracked files or SQLite files change.

- [ ] **Step 5: Commit the task**

  ```bash
  git add backend/services/gmv_export_cache_service.py \
    backend/services/gmv_refund_models.py \
    tests/test_gmv_export_cache_service.py \
    tests/test_gmv_refund_service.py
  git commit -m "feat: add versioned GMV export cache"
  ```

### Task 3: 抽出一次性調整計算與 Fast Active Read Model

**Files:**
- Modify: `backend/services/gmv_refund_service.py`
- Modify: `app_workflows.py`
- Modify: `tests/test_gmv_refund_service.py`
- Create: `tests/test_gmv_one_click_merge_integration.py`

**Interfaces:**
- Consumes: active version id, existing snapshots, cache manifest, `RevenueFrames` only during merge/cache build。
- Produces:

  ```python
  @dataclass(frozen=True)
  class GmvFormalArtifacts:
      total_adjusted: dict[str, object]
      paid_adjusted: dict[str, object]
      total_summary_rows: list[dict[str, object]]
      paid_summary_rows: list[dict[str, object]]
      cache_manifest: GmvExportCacheManifest

  def build_gmv_formal_artifacts(
      *, repository: GmvRefundRepository, version_id: str,
      revenue_frames: RevenueFrames, rule_version: str,
  ) -> GmvFormalArtifacts:

  def load_active_gmv_read_model(
      *, repository: GmvRefundRepository, cache_manifest: GmvExportCacheManifest | None,
      current_revenue_token: str,
  ) -> GmvActiveReadModel:
  ```

  `build_gmv_formal_artifacts` is called once after activation; `load_active_gmv_read_model` must not call `_apply_gmv_refund_adjustments` or scan the full revenue frames.

- [ ] **Step 1: Add failing tests for no-recompute active reads**

  Add a test that monkeypatches `_apply_gmv_refund_adjustments` to raise if called, then loads an active version with a ready cache manifest and asserts the read model returns the cached summary／detail and `can_export=True`.

  Add an integration test that builds total and paid artifacts once, writes the cache, loads the read model twice, and asserts the second load does not rebuild or change cache file mtimes.

- [ ] **Step 2: Run the new tests to confirm current behavior fails**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q \
    tests/test_gmv_refund_service.py \
    tests/test_gmv_one_click_merge_integration.py
  ```

  Expected: the current active read model invokes the full adjustment path when reading an active version.

- [ ] **Step 3: Implement one-time artifact preparation**

  Refactor the existing total／paid calculation so the same loaded `RevenueFrames`, normalized refund rows, summary rows and adjusted frames feed both:

  - existing metric／adjustment snapshot persistence;
  - the new versioned cache service;
  - the post-merge read model.

  Keep the existing official workbook sheet names and filenames. Do not change the formal export schema while improving reuse.

- [ ] **Step 4: Implement cache-aware active read**

  Change the read path so it:

  - compares active version token with current revenue token before showing formal net GMV;
  - loads summary／adjusted detail from matching cache or existing snapshots;
  - returns `STALE_REVENUE_GENERATION` with no formal values when tokens differ;
  - never reconstructs a full 35k-row adjustment during Streamlit page rendering.

- [ ] **Step 5: Run service and integration tests**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q \
    tests/test_gmv_refund_service.py \
    tests/test_gmv_one_click_merge_integration.py
  ```

  Expected: active read tests pass, stale token remains fail-closed, and repeated reads do not rebuild artifacts.

- [ ] **Step 6: Commit the task**

  ```bash
  git add backend/services/gmv_refund_service.py \
    app_workflows.py \
    tests/test_gmv_refund_service.py \
    tests/test_gmv_one_click_merge_integration.py
  git commit -m "perf: reuse GMV formal artifacts for active reads"
  ```

### Task 4: 改造 Streamlit GMV Tab 為一鍵合併流程

**Files:**
- Modify: `app_pages.py`
- Modify: `tests/test_streamlit_gmv_refund_contract.py`
- Modify: `tests/test_streamlit_gmv_formal_contract.py`
- Modify: `tests/test_gmv_one_click_merge_integration.py`

**Interfaces:**
- Consumes: Task 1 automatic merge contract, Task 2 cache service, Task 3 `build_gmv_formal_artifacts` and `load_active_gmv_read_model`。
- Produces: one Streamlit action that runs the complete flow, then reruns into direct active display and download buttons backed by ready cache artifacts.

- [ ] **Step 1: Update Streamlit contract tests first**

  Assert source contains:

  - exactly one primary action labelled `上傳並合併退款資料庫`;
  - `st.status` stages for read／preflight／merge／cache;
  - warning-only flow does not render `確認人員` or `GMV_FORMAL_WARNING_ACK`;
  - no `載入正式淨 GMV` button;
  - active read uses the cache-aware read model;
  - download buttons consume cached artifacts and do not call the synchronous export builder on page render.

- [ ] **Step 2: Run the UI contract tests to see the expected failures**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q \
    tests/test_streamlit_gmv_refund_contract.py \
    tests/test_streamlit_gmv_formal_contract.py
  ```

- [ ] **Step 3: Replace the current multi-step controls**

  In `_render_gmv_exclusion_tab`:

  - keep the uploader;
  - replace the load／actor／acknowledgement／confirm controls with one merge button;
  - call Preflight before any formal write;
  - stop on `blocking_codes` and render the report;
  - call the existing coordination lease and `confirm_refund_batch` with the fixed automatic local actor and empty acknowledgement set;
  - call Task 3 artifact preparation only after activation succeeds;
  - store cache manifest in session state and call `st.rerun()`.

- [ ] **Step 4: Make initial and post-merge page reads direct**

  - remove the `GMV_FORMAL_SCOPE_LOADED` gate;
  - load the unique active version on tab render;
  - show stale-token message only when current revenue token differs;
  - show warning summary and both dimensions as read-only tables;
  - show cache `pending`／`ready`／`failed` status;
  - expose download buttons only for verified ready artifacts;
  - never generate full workbooks from a render-only branch.

- [ ] **Step 5: Run UI and integration tests**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q \
    tests/test_streamlit_gmv_refund_contract.py \
    tests/test_streamlit_gmv_formal_contract.py \
    tests/test_gmv_one_click_merge_integration.py
  ```

  Expected: one-click flow tests pass; blocking upload leaves DB/version/cache unchanged; warning upload creates active version and exposes anomalies.

- [ ] **Step 6: Commit the task**

  ```bash
  git add app_pages.py \
    tests/test_streamlit_gmv_refund_contract.py \
    tests/test_streamlit_gmv_formal_contract.py \
    tests/test_gmv_one_click_merge_integration.py
  git commit -m "feat: make GMV refund merge one click"
  ```

### Task 5: Performance Benchmark、UI Acceptance 與完整驗證

**Files:**
- Create: `tests/test_gmv_export_performance.py`
- Modify: `tests/test_gmv_one_click_merge_integration.py`
- Modify: `tests/test_streamlit_gmv_refund_contract.py` only if an acceptance assertion is missing
- Runtime evidence: `.nbs_runtime/` and `.nbs_agent_runtime/` generated by existing commands; do not commit

**Interfaces:**
- Consumes: Tasks 1–4 complete merge, read model and cache artifacts。
- Produces: stage timing evidence, full verification result, Hermes result and final acceptance report。

- [ ] **Step 1: Add the performance test before measuring the optimized path**

  Record `read`, `normalize`, `preflight`, `adjustment`, `activation`, `cache_build`, `cache_load`, and `repeat_download` durations. Assert:

  ```python
  assert repeat_download_seconds < 2.0
  assert cache_load_seconds < 2.0
  ```

  For first build, record the result against the 60-second target; if the real dataset exceeds it, report the exact stage and do not weaken correctness gates.

- [ ] **Step 2: Run targeted tests and performance benchmark**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q \
    tests/test_gmv_refund_preflight.py \
    tests/test_gmv_refund_service.py \
    tests/test_gmv_export_cache_service.py \
    tests/test_gmv_one_click_merge_integration.py \
    tests/test_streamlit_gmv_refund_contract.py \
    tests/test_streamlit_gmv_formal_contract.py \
    tests/test_gmv_export_performance.py
  ```

- [ ] **Step 3: Perform actual Streamlit UI acceptance with the refund workbook**

  Use the existing local service at `http://127.0.0.1:8502/`:

  1. Open GMV 排除訂單看板.
  2. Select `退款明細數據.xlsx`.
  3. Click `上傳並合併退款資料庫` once.
  4. Verify warning-only Preflight continues automatically.
  5. Verify latest active version, total refund, refunded-only net GMV, anomalies and cache status appear after rerun.
  6. Download both total and 已退款 workbooks twice; verify second download uses ready cache and does not show build spinner.
  7. Verify a blocking fixture shows error and leaves active version unchanged.

- [ ] **Step 4: Run project verification**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest -q
  ./.venv/bin/python scripts/system_manager.py acceptance
  PYTHONPATH=. ./.venv/bin/python scripts/phase2j_baseline_check.py
  PYTHONPATH=. ./.venv/bin/python scripts/hermes_post_change_check.py --json
  git diff --check
  git status --short --branch
  ```

  Expected: full pytest, service acceptance, frozen baseline, SQLite／runtime checks and Hermes pass; no business data, backups or ignored cache is staged.

- [ ] **Step 5: Review the final diff before integration**

  Check:

  - no formal revenue scope or baseline changes;
  - no new database／migration／API／external service;
  - no unsafe deserialization in the new cache service;
  - no active version write from page render;
  - no synchronous full export call on cache hit;
  - existing total refund and 已退款 exports both remain available.

- [ ] **Step 6: Commit verification-only test changes if any**

  ```bash
  git add tests/test_gmv_export_performance.py tests/test_gmv_one_click_merge_integration.py
  git commit -m "test: verify GMV one-click merge and export cache"
  ```

## Review and Execution Gates

1. Before Task 1, create an isolated `codex/` worktree and run read-only preflight; do not reuse the dirty main worktree for implementation.
2. Execute exactly one Task at a time.
3. After each Task, provide actual diff and targeted test output to Review Agent; findings must be handled before the next Task.
4. After Task 5, run full pytest, system acceptance, baseline check and Hermes.
5. Only after all gates pass decide whether to push, create PR, merge or return to local main; this plan does not authorize automatic integration.
