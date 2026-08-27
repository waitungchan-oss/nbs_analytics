# Production Rebuild Benchmark 與 Streamlit/UI Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 建立可重現的 production-like affected-receipt rebuild benchmark 與 Streamlit/UI acceptance evidence，證明效能、equivalence、pointer safety 與 restart/read-existing-ready-cache 行為。

**Architecture:** 使用 synthetic isolated SQLite fixture 與 disposable cache root，透過既有 repository/service/cache path 執行 full/cold、incremental/shadow、warm-read 對照；以 semantic fingerprint、stage telemetry、RSS 與 active-pointer invariants 形成 bounded evidence，再以 HTTP Streamlit acceptance 驗證使用者流程。Benchmark 不讀寫正式 SQLite，不自動改變 rollout mode。

**Tech Stack:** Python、pytest、SQLite、pandas、openpyxl、Streamlit、既有 GMV refund service/repository、既有 export cache manifest/active pointer、HTTP probes、Hermes read-only checks。

**Spec:** `docs/superpowers/specs/2026-08-27-production-rebuild-benchmark-ui-acceptance-design.md`

## Global Constraints

- 正式收入範圍固定為「不含掛賬核銷與 `TT 退款轉團款`」。
- Frozen baseline 固定為 2026-05 `HKD 12,057,968`，不得修改 baseline 或 dashboard/export 顯示層。
- Benchmark 只使用 isolated fixture DB/cache；不得讀取或寫入正式 SQLite、正式 runtime cache 或 active pointer。
- Candidate 未通過 semantic equivalence、conservation、baseline、artifact 與 RSS gates 時，不得 publish incremental。
- `unaffectedAggregationCalls == 0` 必須由 instrumentation 證明；無證據時 status 為 `INCONCLUSIVE`。
- `總退款` 與 `已退款` 分開計算；退款 status/amount/退款方式變更必須進 affected set。
- Memory Hub/local agents 只提供 read-only bounded context，不得寫入 SQLite、runtime、Git、approval 或 benchmark result。
- 每個 Task 先 failing test、再 targeted implementation、再 targeted verification；不得由 runner 自動決定下一 Task。

## File Map

### Create

- `backend/services/gmv_production_benchmark_service.py`：fixture-independent benchmark orchestration、case/run summary、median/p95、RSS、gate evaluation。
- `backend/services/gmv_ui_acceptance_service.py`：bounded UI acceptance evidence model、download/status contract validation；不啟動服務、不寫 production。
- `scripts/benchmark_gmv_production_rebuild.py`：只讀 CLI，建立 synthetic fixture、執行 matrix、輸出 deterministic JSON。
- `scripts/run_gmv_ui_acceptance.py`：HTTP/Streamlit acceptance runner，僅接受 temporary DB/cache root。
- `tests/test_gmv_production_benchmark_service.py`：matrix、summary、gate、immutability tests。
- `tests/test_gmv_ui_acceptance_service.py`：UI evidence schema/status/download tests。
- `tests/test_gmv_production_benchmark_cli.py`：CLI isolation and JSON output tests。

### Modify

- `backend/services/gmv_rebuild_benchmark_service.py`：若可共用既有 full/fast sample adapter，補 production-like stage adapter；否則保持不變。
- `backend/services/gmv_incremental_rebuild.py`：只補 typed stage evidence/gate helper，保持 existing planner semantics。
- `backend/services/gmv_export_cache_service.py`：只補 read-only manifest/artifact evidence extraction；不得放寬 publish validation。
- `tests/test_gmv_export_cache_service.py`、`tests/test_gmv_incremental_rebuild_service.py`：補 pointer immutability、semantic evidence regression。
- `tests/test_streamlit_app_contract.py` 或既有 Streamlit contract test：補 upload/merge/read-existing-ready-cache acceptance seam，不改 production default。

---

### Task 1: Fixture generator 與 immutable benchmark case contract

**Files:**
- Create: `backend/services/gmv_production_benchmark_service.py`
- Create: `tests/test_gmv_production_benchmark_service.py`

**Interfaces:**
- Consumes: receipt count、affected ratio、scenario flags。
- Produces: `BenchmarkCase`、`BenchmarkRunEvidence`、`BenchmarkSummary` typed immutable contracts。

- [ ] **Step 1: Write failing tests**：驗證 `ratio-0.001/0.010/0.100/over-guardrail` case manifest、formal scope、databaseMutated false 與 bounded fields。
- [ ] **Step 2: Run test**：`./.venv/bin/python -m pytest tests/test_gmv_production_benchmark_service.py -q`；預期因 models/function 不存在而 FAIL。
- [ ] **Step 3: Implement minimal contracts**：使用 frozen dataclass，case id、ratio、receipt/affected counts 與 scenario flags 做 deterministic normalization；禁止 raw DataFrame 進 evidence。
- [ ] **Step 4: Verify**：重跑 targeted test與 `git diff --check`。
- [ ] **Step 5: Commit**：`git add backend/services/gmv_production_benchmark_service.py tests/test_gmv_production_benchmark_service.py && git commit -m "test: define production rebuild benchmark contracts"`。

### Task 2: Isolated SQLite fixture 與 scenario matrix

**Files:**
- Modify: `backend/services/gmv_production_benchmark_service.py`
- Create: `tests/test_gmv_production_benchmark_service.py`（extend）

**Interfaces:**
- Consumes: `BenchmarkCase`。
- Produces: temporary fixture DB/cache paths，包含 status transition、amount change、TT method transition、over-refund、multi-member、unmatched cases。

- [ ] **Step 1: Write failing tests**：建立 temporary fixture，記錄 DB SHA-256/schema count，驗證 fixture teardown 後 production path 未被觸碰。
- [ ] **Step 2: Run targeted test**：預期 fixture builder 不存在或 scenario rows 不完整而 FAIL。
- [ ] **Step 3: Implement fixture builder**：沿用既有 test fixture/repository schema；所有 receipt/refund identity 使用 synthetic values；cache root 強制位於 temp/approved benchmark root。
- [ ] **Step 4: Verify**：驗證兩個 refund dimensions、formal scope 與 TT exclusion scenario 可被 runner 識別，並確認 DB bytes unchanged。
- [ ] **Step 5: Commit**：`git add backend/services/gmv_production_benchmark_service.py tests/test_gmv_production_benchmark_service.py && git commit -m "test: add isolated gmv rebuild benchmark fixtures"`。

### Task 3: Full/cold、incremental/shadow、warm-read production-like runner

**Files:**
- Modify: `backend/services/gmv_production_benchmark_service.py`
- Modify: `backend/services/gmv_rebuild_benchmark_service.py`
- Modify: `backend/services/gmv_incremental_rebuild.py`
- Test: `tests/test_gmv_production_benchmark_service.py`

**Interfaces:**
- Consumes: fixture DB/cache、`BenchmarkCase`、existing full/fast builders、trusted reference/equivalence service。
- Produces: `run_production_rebuild_benchmark(case, *, runs=3, warm_reads=3) -> BenchmarkSummary`。

- [ ] **Step 1: Write failing tests**：assert each run contains plan/affected/copy/metrics/equivalence/publish/warmRead timings、RSS、row counts、fallback reason。
- [ ] **Step 2: Run targeted test**：預期 stage evidence 或 runner 不存在而 FAIL。
- [ ] **Step 3: Implement runner**：使用 `time.perf_counter()` 與既有 cache/service functions；full/cold 作 trusted reference，candidate 走 shadow gate，warm-read 只讀 active manifest/artifacts；每個 mode 至少 3 samples。
- [ ] **Step 4: Verify**：candidate failure 不 publish；上一 READY pointer bytes/digest unchanged；unaffected aggregation instrumentation 缺失時回 `INCONCLUSIVE`。
- [ ] **Step 5: Commit**：`git add backend/services/gmv_production_benchmark_service.py backend/services/gmv_rebuild_benchmark_service.py backend/services/gmv_incremental_rebuild.py tests/test_gmv_production_benchmark_service.py && git commit -m "test: benchmark complete affected receipt rebuild flow"`。

### Task 4: Median/p95/RSS/equivalence gate evaluation

**Files:**
- Modify: `backend/services/gmv_production_benchmark_service.py`
- Modify: `backend/services/gmv_export_cache_service.py`
- Test: `tests/test_gmv_production_benchmark_service.py`、`tests/test_gmv_export_cache_service.py`

**Interfaces:**
- Consumes: run evidence、trusted reference digests、active pointer snapshots。
- Produces: deterministic summary status `PASS|FAIL|INCONCLUSIVE` 與 stable failure reasons。

- [ ] **Step 1: Write failing tests**：覆蓋 equivalence fail、RSS > 1.5x、missing instrumentation、checksum failure、over-guardrail fallback。
- [ ] **Step 2: Run targeted tests**：預期 gate 未實作而 FAIL。
- [ ] **Step 3: Implement gates**：用 nearest-rank p95；只有 equivalence 100%、unaffected calls 0、RSS guard、pointer safety 全成立才 PASS；不以最快單次值判定。
- [ ] **Step 4: Verify**：在 temporary cache 驗證 publish failure 保持原 active pointer，並驗證 manifest/artifact evidence 不含 raw rows。
- [ ] **Step 5: Commit**：`git add backend/services/gmv_production_benchmark_service.py backend/services/gmv_export_cache_service.py tests/test_gmv_production_benchmark_service.py tests/test_gmv_export_cache_service.py && git commit -m "test: add rebuild benchmark safety gates"`。

### Task 5: Deterministic CLI 與 JSON evidence artifact

**Files:**
- Create: `scripts/benchmark_gmv_production_rebuild.py`
- Create: `tests/test_gmv_production_benchmark_cli.py`

**Interfaces:**
- Consumes: `--fixture synthetic`、`--samples 3`、`--warm-reads 3`、`--ratios 0.001,0.01,0.1`、optional isolated output directory。
- Produces: one JSON document with schema version、matrix summaries、databaseMutated false、formal scope、frozen baseline reference、status。

- [ ] **Step 1: Write failing tests**：subprocess CLI output parse、invalid broad cache dir reject、no production DB path accepted、matrix and sample counts correct。
- [ ] **Step 2: Run targeted test**：預期 CLI 不存在而 FAIL。
- [ ] **Step 3: Implement CLI**：只允許 temporary/approved benchmark roots；JSON keys sorted；任何 runner exception 轉 bounded failure evidence，不吞掉安全錯誤。
- [ ] **Step 4: Verify**：執行 `./.venv/bin/python scripts/benchmark_gmv_production_rebuild.py --fixture synthetic --samples 3 --warm-reads 3 --ratios 0.001,0.01,0.1`，保存 stdout artifact。
- [ ] **Step 5: Commit**：`git add scripts/benchmark_gmv_production_rebuild.py tests/test_gmv_production_benchmark_cli.py && git commit -m "test: add production rebuild benchmark cli"`。

### Task 6: UI acceptance evidence contract

**Files:**
- Create: `backend/services/gmv_ui_acceptance_service.py`
- Create: `tests/test_gmv_ui_acceptance_service.py`

**Interfaces:**
- Consumes: bounded HTTP/status/download observations。
- Produces: `UiAcceptanceEvidence`、`validate_ui_acceptance_evidence(...) -> UiAcceptanceResult`；不啟動 Streamlit、不寫 DB。

- [ ] **Step 1: Write failing tests**：驗證 upload/merge/active READY/兩維度 download/restart read、blocking error、missing artifact、raw-data rejection。
- [ ] **Step 2: Run targeted test**：預期 contract/function 不存在而 FAIL。
- [ ] **Step 3: Implement evidence validator**：只保留 route、status、version id、manifest digest、artifact names/bytes、bounded error codes；拒絕 raw row/customer/payment fields。
- [ ] **Step 4: Verify**：測試完整 pass、partial fail、blocking fail 三種結果，並確認不改任何 filesystem/DB。
- [ ] **Step 5: Commit**：`git add backend/services/gmv_ui_acceptance_service.py tests/test_gmv_ui_acceptance_service.py && git commit -m "test: define gmv streamlit acceptance evidence"`。

### Task 7: HTTP Streamlit acceptance runner

**Files:**
- Create: `scripts/run_gmv_ui_acceptance.py`
- Modify: `tests/test_streamlit_app_contract.py`（若不存在則建立最小 contract test）

**Interfaces:**
- Consumes: isolated DB/cache root、synthetic refund Excel、running HTTP Streamlit URL。
- Produces: bounded UI acceptance JSON；不接受 production DB/cache path。

- [ ] **Step 1: Write failing tests**：驗證 runner 要求 HTTP URL、禁止 `file://`、禁止 production runtime path、missing report download 會 FAIL。
- [ ] **Step 2: Run targeted test**：預期 runner/HTTP guard 不存在而 FAIL。
- [ ] **Step 3: Implement runner**：以既有 browser/HTTP test seam 操作上傳與 merge；執行一次 merge，檢查 active READY、兩維度報表下載與 refresh read。
- [ ] **Step 4: Verify**：在 isolated app process 執行 acceptance，輸出 bounded status/download evidence，不保存 raw Excel。
- [ ] **Step 5: Commit**：`git add scripts/run_gmv_ui_acceptance.py tests/test_streamlit_app_contract.py && git commit -m "test: add isolated gmv streamlit acceptance runner"`。

### Task 8: Failure injection 與 previous READY pointer safety

**Files:**
- Modify: `backend/services/gmv_export_cache_service.py`
- Modify: `backend/services/gmv_ui_acceptance_service.py`
- Test: `tests/test_gmv_export_cache_service.py`、`tests/test_gmv_ui_acceptance_service.py`

**Interfaces:**
- Consumes: isolated active pointer、tampered artifact/checksum/equivalence failure。
- Produces: acceptance evidence `activePointerUnchangedOnFailure=true`、previous reports still readable。

- [ ] **Step 1: Write failing tests**：在 publish 前篡改 artifact、模擬 timeout/equivalence fail，assert active pointer bytes unchanged。
- [ ] **Step 2: Run targeted test**：預期 failure evidence 尚未標準化而 FAIL。
- [ ] **Step 3: Implement validator/probe**：只在 disposable fixture 執行 failure injection；不放寬 production publish guard。
- [ ] **Step 4: Verify**：focused cache/UI tests pass，並確認不存在半成品 READY。
- [ ] **Step 5: Commit**：`git add backend/services/gmv_export_cache_service.py backend/services/gmv_ui_acceptance_service.py tests/test_gmv_export_cache_service.py tests/test_gmv_ui_acceptance_service.py && git commit -m "test: verify gmv rebuild pointer rollback safety"`。

### Task 9: Review、benchmark matrix、full verification 與 Hermes evidence

**Files:**
- No production code changes unless findings require targeted fix.
- Evidence: benchmark JSON、UI acceptance JSON、pytest/Hermes outputs。

**Interfaces:**
- Consumes: Tasks 1–8 artifacts and isolated fixtures。
- Produces: findings-first Review report、full pytest result、compileall result、Hermes report、benchmark/UI acceptance summary。

- [ ] **Step 1: Run focused suite**：執行 benchmark/UI/cache/incremental tests，確認全綠。
- [ ] **Step 2: Run CLI matrix**：至少 `0.001,0.01,0.1` 加 over-guardrail，cold/warm 各 3 samples。
- [ ] **Step 3: Run isolated Streamlit acceptance**：驗證 upload/merge/兩報表/refresh-read 與 failure injection。
- [ ] **Step 4: Run full verification**：`./.venv/bin/python -m pytest -W error::FutureWarning -q`、`compileall -q backend scripts`、既有 Review runner、`scripts/hermes_post_change_check.py --skip-monitor --markdown`。
- [ ] **Step 5: Commit only targeted fixes**：若 findings 需要修復，先 failing test，再修復、重跑全部受影響驗證；不把 evidence/generated files 放入 source commit。

## Final Release Gate

- [ ] Benchmark matrix 每 case 有完整 JSON evidence，且 cold/warm sample count >= 3。
- [ ] Candidate semantic equivalence rate = 1.0；兩退款維度與 formal scope/TT exclusion PASS。
- [ ] `unaffectedAggregationCalls == 0`；否則 status 為 `INCONCLUSIVE` 且禁止 rollout recommendation。
- [ ] RSS 與 latency guardrails 有 median/p95 evidence。
- [ ] UI acceptance upload/merge/active READY/download/restart-read PASS。
- [ ] Failure injection 證明上一 READY pointer unchanged。
- [ ] Full pytest、Review、Hermes 分開報告，未以任一者取代其他 gate。
- [ ] 未修改正式 SQLite、baseline、production cache 或 rollout default。
