# Scope-Aware Cache Generation Signature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for Inline Execution. Execute one Task at a time, stop at every checkpoint, and hand the actual diff to Review Agent before continuing.

**Goal:** 讓主 Dashboard/Forecast/Export 只依賴 core revenue semantic signature，避免 GMV ledger 寫入造成 false stale，同時保留完整 SQLite signature 作 diagnostics。

**Architecture:** 新增 `revenue_generation_service.py` 建立 deterministic `nbs-core-revenue-v1` token；`cache_generation_service.py` 讀寫 `nbs-data-generation-v2` sidecar，分開回報 `signatureMatched` 與 `fileSignatureMatched`。GMV 維持既有 `gmv-revenue-state-v1`、active version 與 export manifest contract，不改 SQLite schema。

**Tech Stack:** Python 3.10、SQLite、Pandas、FastAPI、Streamlit、pytest、JSON sidecar、Hermes read-only acceptance。

**Spec:** `docs/superpowers/specs/2026-08-26-scope-aware-cache-generation-signature-design.md`

## Global Constraints

- 正式口徑固定為 `不含掛賬核銷與TT退款轉團款`。
- 2026-05 frozen baseline 必須維持 `HKD 12,057,968`。
- 不新增 SQLite migration，不重寫正式業務資料，不改 export schema。
- `coreRevenueSignature` 是 Dashboard/Forecast/Export cache authority；full SQLite SHA 只作 diagnostics。
- GMV 既有 `gmv-revenue-state-v1` token、active version、trusted reference、manifest 與 artifact equivalence contract 不變。
- v1 `data_generation.json` 只可 read-compatible fallback；不得 silent overwrite。
- 所有 runtime sidecar write 必須 atomic；失敗時保留原 payload。
- 不使用 subagent 開發；Implementation Agent 一次只執行一個已批准 Task，不自行決定下一 Task，不自行 commit、merge 或修改正式 SQLite。
- 每個 Task 完成後先 targeted tests，再做 findings-first Review；Review PASS 後才進入下一 Task。
- 全部 Task 完成後才執行 full pytest、formal Hermes、baseline、service identity 與實際 UI/cache 驗收。

---

### Task 1: 建立 core signature contract 的 failing tests

**Files:**
- Create: `tests/test_revenue_generation_service.py`
- Modify: `tests/test_cache_generation_service.py`
- Modify: `tests/test_system_health_service.py`
- Modify: `tests/test_gmv_refund_service.py`

**Interfaces:**
- Consumes: 現有 `database.load_all_data_from_db()`、`revenue_scope_service.build_revenue_scope_frames()`、GMV revenue fixtures。
- Produces: 明確固定後續 implementation 必須提供的 `CoreRevenueSignature`、v2 loader fields 與 health severity 行為。

- [ ] **Step 1: 建立 temporary SQLite fixture，分離 core tables 與 GMV tables**

  Fixture 至少建立 `tour_data`、`others_data`、`gmv_refund_current`、`gmv_scope_versions`；core rows 使用正式 scope 欄位，GMV rows 使用最小合法 ledger row。Fixture 只存在 `tmp_path`，不得複製正式 DB。

- [ ] **Step 2: 撰寫 failing tests**

  必須包含以下測試名稱與 assertions：

  ```python
  def test_core_signature_ignores_gmv_only_write(tmp_path):
      db_path = make_scope_fixture(tmp_path)
      before = build_core_revenue_signature(db_path)
      insert_gmv_only_row(db_path)
      after = build_core_revenue_signature(db_path)
      assert after.token == before.token

  def test_core_signature_changes_when_revenue_row_changes(tmp_path):
      db_path = make_scope_fixture(tmp_path)
      before = build_core_revenue_signature(db_path)
      update_core_revenue_row(db_path)
      after = build_core_revenue_signature(db_path)
      assert after.token != before.token

  def test_v2_core_match_with_full_file_change_is_not_degraded(tmp_path):
      db_path = make_scope_fixture(tmp_path)
      runtime_dir = make_generation_fixture(tmp_path, db_path)
      cache_path = tmp_path / "cache"
      result = build_system_health(db_path, cache_path, runtime_dir=runtime_dir)
      assert result["dataGeneration"]["signatureMatched"] is True
      assert result["dataGeneration"]["fileSignatureMatched"] is False
      assert result["status"] == "ok"
  ```

  另加 v1 payload、core mismatch、core unavailable、atomic malformed payload、GMV active token compatibility tests。

- [ ] **Step 3: 執行 focused tests，確認它們以 contract 缺失而失敗**

  Run:

  ```bash
  .venv/bin/python -m pytest -q \
    tests/test_revenue_generation_service.py \
    tests/test_cache_generation_service.py \
    tests/test_system_health_service.py \
    tests/test_gmv_refund_service.py
  ```

  Expected: FAIL，失敗原因應是新 API/fields 尚未存在；不得因正式 SQLite、baseline 或服務環境缺失而失敗。

- [ ] **Step 4: Checkpoint**

  保存 focused failure output，執行 `git diff --check`，停止並交由 Review Agent 檢查測試 contract。Implementation Agent 不 commit 或 merge。

### Task 2: 實作 deterministic core revenue signature service

**Files:**
- Create: `backend/services/revenue_generation_service.py`
- Modify: `backend/services/gmv_refund_service.py:100-152`
- Test: `tests/test_revenue_generation_service.py`
- Test: `tests/test_gmv_refund_service.py`

**Interfaces:**
- Consumes: `database.load_all_data_from_db(db_path=db_path)`、`backend.services.revenue_scope_service.build_revenue_scope_frames()`。
- Produces:

  ```python
  @dataclass(frozen=True, slots=True)
  class CoreRevenueSignature:
      schema_version: str
      scope_label: str
      scope_contract_version: str
      source_tables: tuple[str, ...]
      row_counts: dict[str, int]
      raw_tour_sha256: str
      raw_others_sha256: str
      formal_tour_sha256: str
      formal_others_sha256: str
      sha256: str
      token: str

  def build_core_revenue_signature(
      db_path: str | Path,
      rule_version: str = REVENUE_SCOPE_LABEL,
  ) -> CoreRevenueSignature:
      pass

  def canonical_frame_sha256(frame: pd.DataFrame) -> str:
      pass
  ```

- [ ] **Step 1: 抽出不改語義的 canonical frame helper**

  將 GMV 現有 null、timestamp、string、column order、sorted rows normalization 抽到新 service。先以現有 GMV test fixtures 固定 expected digest，避免重構造成 `gmv-revenue-state-v1` token 漂移。

- [ ] **Step 2: 實作 core payload 與 token**

  ```python
  payload = {
      "schemaVersion": "nbs-core-revenue-signature-v1",
      "scopeLabel": rule_version,
      "scopeContractVersion": "revenue-scope-v1",
      "sourceTables": ["tour_data", "others_data"],
      "rowCounts": row_counts,
      "rawTour": raw_tour_sha256,
      "rawOthers": raw_others_sha256,
      "formalTour": formal_tour_sha256,
      "formalOthers": formal_others_sha256,
  }
  token = f"nbs-core-revenue-v1:{sha256(canonical_json(payload))}"
  ```

  不讀取 `gmv_*`、stability history、cache 或 runtime log。缺少 core table 時以 empty frame 的 deterministic digest 表示；資料庫不存在或無法讀取時 raise bounded error。

- [ ] **Step 3: 讓 GMV 既有 token 使用 shared helper 但保持 prefix 與 payload contract**

  `revenue_state_token()` 仍輸出 `gmv-revenue-state-v1:0000000000000000000000000000000000000000000000000000000000000000` 這種 prefix + digest 格式，只共用 frame normalization，不改既有 GMV payload key 或 token prefix。

- [ ] **Step 4: 執行 focused tests**

  Run:

  ```bash
  .venv/bin/python -m pytest -q \
    tests/test_revenue_generation_service.py \
    tests/test_gmv_refund_service.py
  ```

  Expected: core signature tests GREEN；既有 GMV token、active version 與 stale revenue tests GREEN。

- [ ] **Step 5: Checkpoint**

  交付 service diff、token compatibility evidence 與 `git diff --check` 給 Review Agent；Review PASS 後才進入 Task 3。

### Task 3: 升級 cache generation sidecar 至 v2 並保留 v1 fallback

**Files:**
- Modify: `backend/services/cache_generation_service.py`
- Modify: `tests/test_cache_generation_service.py`
- Modify: `tests/test_application_snapshot_service.py`

**Interfaces:**
- Consumes: `build_core_revenue_signature()` 與現有 full-file `_db_signature()`。
- Produces:

  ```python
  def load_cache_generation(
      path: str | Path | None = None,
      *,
      db_path: str | Path | None = None,
      core_signature_loader=build_core_revenue_signature,
  ) -> dict:
      pass

  def advance_cache_generation(
      *,
      db_path: str | Path,
      operation_id: str,
      status: str,
      path: str | Path | None = None,
      core_signature: CoreRevenueSignature | None = None,
  ) -> dict:
      pass

  def refresh_cache_generation_signature(
      *,
      db_path: str | Path,
      path: str | Path | None = None,
      core_signature: CoreRevenueSignature | None = None,
  ) -> dict:
      pass
  ```

- [ ] **Step 1: 寫 v2 payload validator 與 derived result tests**

  Test exact fields：`schemaVersion`、`signatureScope`、`coreRevenueSignature`、`dbSignature`、`signatureMatched`、`fileSignatureMatched`、`cacheToken`、`legacyMode`、`migrationRequired`。

- [ ] **Step 2: 實作 v2 atomic writer**

  `advance_cache_generation()` 寫入 core signature 與當下 file signature；`os.replace()` 前先完成 JSON serialization，寫入失敗不可截斷原檔。`cacheToken` 必須由 `generation` + core token 產生，不得由 full DB SHA 產生。

- [ ] **Step 3: 實作 v1 read-compatible fallback**

  v1 payload 不自動改寫；回傳 `legacyMode=True`、`migrationRequired=True`，並暫時沿用 full-file token 作 conservative fallback。未知 schema、malformed JSON、core signature unavailable 必須 fail-closed。

- [ ] **Step 4: 保留 file signature diagnostics**

  v2 loader 計算 current full-file signature，但只填 `fileSignatureMatched`；core matched + file mismatch 不可改寫 `signatureMatched`。

- [ ] **Step 5: 執行 focused tests**

  Run:

  ```bash
  .venv/bin/python -m pytest -q \
    tests/test_cache_generation_service.py \
    tests/test_application_snapshot_service.py
  ```

  Expected: v1/v2、atomic failure、core/file split、cache token tests GREEN。

- [ ] **Step 6: Checkpoint**

  Review sidecar schema、legacy fallback、atomic write evidence；不得在正式 `.nbs_runtime` 執行 apply。

### Task 4: 將 primary upload/rollback 接到 core generation authority

**Files:**
- Modify: `backend/services/upload_orchestrator_service.py:150-218`
- Modify: `app_workflows.py:2050-2075`
- Modify: `tests/test_upload_orchestrator_service.py`
- Modify: `tests/test_upload_single_writer_integration.py`

**Interfaces:**
- Consumes: Task 2 的 core signature 與 Task 3 的 generation writer。
- Produces: accepted primary write、successful rollback 都更新 core token；GMV-only writer 不呼叫 primary generation advance。

- [ ] **Step 1: 先補 upload order assertions**

  Assert order remains `upsert -> reload -> gate -> rollback -> advance core generation -> cache rebuild -> history -> refresh observed file signature`；history write 不得改變 core token。

- [ ] **Step 2: 更新 accepted/rejected_rolled_back path**

  `generation_advancer` 收到同一個 `live_path` 與 core signature；`generation_signature_refresher` 只補最新 file observation，不能把 core signature 替換成 full-file SHA。

- [ ] **Step 3: 保持 error contract**

  generation build 失敗時回傳 `cacheState=refresh_required`、`cacheError` 與 degraded public status；不得聲稱 `streamlit_rebuilt`。rollback_failed 與 history failure 沿用現有 severity。

- [ ] **Step 4: 執行 upload focused pack**

  ```bash
  .venv/bin/python -m pytest -q \
    tests/test_upload_orchestrator_service.py \
    tests/test_upload_single_writer_integration.py \
    tests/test_database_rollback.py
  ```

- [ ] **Step 5: Checkpoint**

  Review transaction order、single-writer boundary、rollback evidence；不寫正式 DB。

### Task 5: 接通 Dashboard、Forecast 與 Application Snapshot 的 core cache token

**Files:**
- Modify: `app_workflows.py:1897-1995`
- Modify: `backend/routers/dashboard.py:28-41`
- Modify: `backend/services/dashboard_facts_service.py`
- Modify: `backend/services/application_snapshot_service.py:78-121`
- Modify: `tests/test_dashboard_service.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_application_snapshot_service.py`

**Interfaces:**
- Consumes: Task 3 `load_cache_generation()` derived `cacheToken`。
- Produces: 所有主營收 read model 使用 `coreRevenueToken`；GMV-only DB write 不清除 `PROCESSED_DATA_CACHE`。

- [ ] **Step 1: 補 token source contract tests**

  使用同一 temporary DB：先 build snapshot，再只寫 GMV table，assert second snapshot `generationToken` 相同、`snapshotAttemptCount` 不因 file signature 改變而增加。

- [ ] **Step 2: 替換 Dashboard/API cache key source**

  保留 `load_cache_generation(db_path=database_module.DB_FILE)` 作唯一入口；禁止 router、facts service 或 app workflow 直接組 `generation:db_sha`。

- [ ] **Step 3: 更新 session invalidation**

  `_invalidate_session_cache_if_generation_changed()` 只比較 core-derived `cacheToken`；GMV-only file mutation 不清除主營收 session cache。

- [ ] **Step 4: 執行 dashboard/application focused pack**

  ```bash
  .venv/bin/python -m pytest -q \
    tests/test_dashboard_service.py \
    tests/test_dashboard_api.py \
    tests/test_application_snapshot_service.py \
    tests/test_streamlit_gmv_formal_contract.py
  ```

- [ ] **Step 5: Checkpoint**

  Review read-only data flow、cache key provenance、GMV/formal boundary；確認沒有將 GMV ledger rows 混入主營收 cache。

### Task 6: 更新 system health、operational monitor 與 Hermes severity

**Files:**
- Modify: `backend/services/system_health_service.py:63-164`
- Modify: `backend/services/operational_monitor_service.py:30-52`
- Modify: `backend/schemas/dashboard.py:215-260`
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `tests/test_system_health_service.py`
- Modify: `tests/test_operational_monitor_service.py`
- Modify: `tests/test_hermes_post_change_check.py`

**Interfaces:**
- Consumes: Task 3 v2 derived status與 Task 4 history evidence。
- Produces: health payload 同時暴露 core/file status；只有 core unavailable/mismatch、cacheError、missing evidence、integrity failure 進入 degraded/critical。

- [ ] **Step 1: 撰寫 severity regression tests**

  Cases：`core matched + file changed => ok`、`core mismatch => degraded`、`core unavailable => degraded`、`legacy metadata => degraded + migrationRequired`、`integrity failure => critical`、`missing operation evidence => degraded`。

- [ ] **Step 2: 更新 `build_system_health()`**

  `dataGeneration.signatureMatched` 對應 core；增加 `fileSignatureMatched`、`legacyMode`、`migrationRequired`；file-only mismatch 放入 bounded diagnostics，不覆蓋既有 upload/history issues。

- [ ] **Step 3: 更新 compact health 與 Hermes parser**

  FastAPI health、operational monitor、Hermes report 必須保留同一命名；unknown/missing fields 不得被 parser 當作 PASS。

- [ ] **Step 4: 執行 health/Hermes focused pack**

  ```bash
  .venv/bin/python -m pytest -q \
    tests/test_system_health_service.py \
    tests/test_operational_monitor_service.py \
    tests/test_hermes_post_change_check.py
  ```

- [ ] **Step 5: Checkpoint**

  Review findings-first；確認 Hermes 不會把 full file mismatch 誤判 PASS，也不會把 core mismatch 降成 informational。

### Task 7: 驗證 GMV active version 與 export cache compatibility

**Files:**
- Modify: `backend/services/gmv_refund_service.py:550-620, 1020-1090`
- Modify: `tests/test_gmv_one_click_merge_integration.py`
- Modify: `tests/test_gmv_export_cache_service.py`
- Modify: `tests/test_gmv_export_fast_controller.py`
- Modify: `tests/test_gmv_trusted_reference_service.py`

**Interfaces:**
- Consumes: Task 2 shared canonical helper、Task 5 core token behavior。
- Produces: GMV active version仍以 `gmv-revenue-state-v1` 判斷 stale；GMV-only write 後 active export manifest 可 reopen；主營收改變後仍 fail-closed。

- [ ] **Step 1: 補 GMV-only write integration test**

  在 temporary DB 先建立 core generation、GMV active version，再寫入一個只屬於 GMV ledger 的 row；assert `load_active_gmv_read_model()` 保持 `CURRENT`、manifest `equivalenceStatus=PASS`、trusted pointer 不被替換。

- [ ] **Step 2: 補 core-change stale test**

  修改 `tour_data` 金額後重新計算 current GMV revenue token；assert 舊 active version 回傳 `STALE_REVENUE_GENERATION`，不下載舊報表。

- [ ] **Step 3: 執行 GMV/cache focused pack**

  ```bash
  .venv/bin/python -m pytest -q \
    tests/test_gmv_one_click_merge_integration.py \
    tests/test_gmv_export_cache_service.py \
    tests/test_gmv_export_fast_controller.py \
    tests/test_gmv_trusted_reference_service.py
  ```

- [ ] **Step 4: Checkpoint**

  Review artifact manifest、trusted reference、shadow validation 與 cache pointer；不得建立新的正式 active version。

### Task 8: 建立 runtime metadata migration CLI

**Files:**
- Create: `scripts/migrate_cache_generation_v2.py`
- Create: `tests/test_migrate_cache_generation_v2.py`
- Modify: `backend/services/cache_generation_service.py` only for shared migration writer helpers

**Interfaces:**
- Consumes: v1/v2 sidecar、core signature、SQLite read-only integrity check、existing runtime paths。
- Produces:

  ```bash
  .venv/bin/python scripts/migrate_cache_generation_v2.py \
    --db-path nbs_marketing_data.db \
    --runtime-dir .nbs_runtime \
    --dry-run --json

  .venv/bin/python scripts/migrate_cache_generation_v2.py \
    --db-path nbs_marketing_data.db \
    --runtime-dir .nbs_runtime \
    --apply --json
  ```

- [ ] **Step 1: 寫 dry-run tests**

  Assert dry-run 不改 `data_generation.json`、不開啟 SQLite write transaction、不刪除 cache，並輸出 current core token、file signature comparison、migration decision 與 bounded error code。

- [ ] **Step 2: 寫 apply atomicity tests**

  使用 `tmp_path`：先保存 v1 sidecar，apply 後生成 v2 sidecar backup 與 v2 payload；模擬 write failure 時 original bytes 必須完全保留。

- [ ] **Step 3: 實作 CLI guard**

  `--dry-run` 與 `--apply` 必須 mutually exclusive；未指定其中一個直接 exit 2。`--apply` 只允許更新 runtime JSON，不允許修改 `nbs_marketing_data.db`、cache artifacts、rules 或 baseline。

- [ ] **Step 4: 執行 migration focused pack**

  ```bash
  .venv/bin/python -m pytest -q tests/test_migrate_cache_generation_v2.py
  .venv/bin/python scripts/migrate_cache_generation_v2.py \
    --db-path nbs_marketing_data.db --runtime-dir .nbs_runtime --dry-run --json
  ```

  Expected: dry-run report 可辨識現行 v1/v2 state；正式 runtime 不因 dry-run 改變。

- [ ] **Step 5: Checkpoint**

  先交付 migration dry-run evidence；正式 `--apply` 必須另由 Codex 在使用者明確授權後執行，不由 Implementation Agent 自行執行。

### Task 9: 建立 performance benchmark 與 shadow comparison

**Files:**
- Create: `scripts/benchmark_cache_generation_scope.py`
- Create: `tests/test_cache_generation_scope_benchmark.py`
- Modify: `docs/superpowers/specs/2026-08-26-scope-aware-cache-generation-signature-design.md` only if measured contract requires clarification

**Interfaces:**
- Consumes: legacy full-file signature path、v2 core signature path、temporary/explicit DB path。
- Produces: bounded JSON report containing samples, p50, p95, core/file status、cache token equality、no-business-row evidence。

- [ ] **Step 1: 寫 benchmark output contract test**

  Report 必須包含 `schemaVersion`、`mode`、`iterations`、`legacyP95Ms`、`coreP95Ms`、`fileP95Ms`、`tokenStableAfterGmvWrite`、`coreChangedAfterRevenueWrite`、`outputPath`。

- [ ] **Step 2: 實作 read-only benchmark**

  使用 bounded iterations；只讀 DB，GMV-only/revenue-write comparison 使用 temporary copy。Report 只能寫到命令指定的 output path，禁止預設寫入正式 cache。

- [ ] **Step 3: 執行 benchmark 與 shadow comparison**

  ```bash
  .venv/bin/python scripts/benchmark_cache_generation_scope.py \
    --db-path nbs_marketing_data.db \
    --iterations 5 \
    --output .nbs_agent_runtime/cache-generation-benchmark.json
  ```

  Expected：GMV-only write 的 core token stable；revenue write 的 core token changed；`/api/health`、`/api/facts` p95 不超過 spec 的 10% regression budget。

- [ ] **Step 4: Checkpoint**

  Review benchmark output、token equality、baseline unchanged 與 artifact cleanliness；不把 benchmark JSON 當 canonical business data。

### Task 10: 文件 contract、Review、full pytest 與 formal Hermes

**Files:**
- Modify: `NBS_HERMES_MONITORING.md`
- Modify: `NBS_ANALYTICS_SYSTEM_MAP.md`
- Modify: `NBS_ANALYTICS_HANDOFF.md`
- Test: all relevant test suites and formal runtime commands

**Interfaces:**
- Consumes: Tasks 1-9 的 code、reports、migration dry-run 與 review evidence。
- Produces: 可追溯的 core/file signature monitoring contract、formal acceptance report；不由 Hermes 自動修改任何資料。

- [ ] **Step 1: 更新 monitoring/documentation contract**

  明確寫出：`signatureMatched` 是 core semantic status；`fileSignatureMatched=false` 且 core matched 是 informational；legacy metadata 是 migration required；Hermes 仍維持 read-only。

- [ ] **Step 2: 執行 findings-first Review**

  使用：

  ```bash
  .venv/bin/python scripts/review_agent.py --head WORKTREE --json
  ```

  Review 必須檢查 allowed paths、formal scope、baseline、cache key provenance、fallback、no SQLite mutation 與 export schema unchanged。

- [ ] **Step 3: 修復 Review findings，再跑 targeted regression**

  只修復本 spec allowlist 內 findings；每次修復後重跑對應 Task focused pack，不跨 Task 擴大 scope。

- [ ] **Step 4: 執行 full pytest 與 compile checks**

  ```bash
  .venv/bin/python -m pytest -q
  .venv/bin/python -m py_compile \
    backend/services/revenue_generation_service.py \
    backend/services/cache_generation_service.py \
    backend/services/system_health_service.py \
    backend/services/application_snapshot_service.py \
    scripts/migrate_cache_generation_v2.py
  git diff --check
  ```

- [ ] **Step 5: 執行 baseline、service identity、Hermes**

  ```bash
  .venv/bin/python scripts/phase2j_baseline_check.py
  .venv/bin/python scripts/system_manager.py status
  .venv/bin/python scripts/system_manager.py acceptance
  .venv/bin/python scripts/hermes_post_change_check.py --json
  ```

  Required evidence：SQLite integrity PASS、2026-05 `HKD 12,057,968` matched、Streamlit/FastAPI/Vue owner/identity match、GMV manifest equivalence/shadow PASS、Hermes overall PASS。

- [ ] **Step 6: 實際 UI/cache acceptance**

  只在 formal runtime readiness PASS 後：

  1. 重新載入經營分析大盤，確認 core cache 正常顯示。
  2. 開啟 GMV active version，確認不需重新上傳退款 Excel。
  3. 以退款 Excel 建立一個 temporary/preflight-only GMV write path，確認 file signature 變更不使 Dashboard cache degraded。
  4. 確認總退款、已退款報表下載與 active pointer 仍可用。
  5. 不執行新的 production upload 或 active version apply，除非另有明確授權。

- [ ] **Step 7: Final checkpoint**

  交付 code diff、test counts、Review verdict、Hermes report、baseline evidence、runtime migration status 與未解決風險；Git commit/push/PR/merge 另待使用者明確要求。

## Execution Checkpoint Policy

每個 Task 的 checkpoint 必須包含：

```text
Task status:
Changed files:
Focused tests:
Review status:
Formal data/runtime touched: yes/no
Blocking issue:
Next approved Task:
```

若出現下列情況，立即停在 checkpoint：core token 無法計算、baseline 不匹配、SQLite integrity failure、active GMV manifest equivalence fail、Review finding 涉及 scope/authority、Hermes identity 不匹配或正式 runtime migration 需要額外授權。
