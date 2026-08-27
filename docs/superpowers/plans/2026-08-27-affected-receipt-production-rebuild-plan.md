# Affected Receipt Production Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改變正式營收結果的前提下，讓 production refund rebuild 只重算 affected source receipt，完全跳過 unaffected rows 的 business aggregation，並以完整新 snapshot、trusted reference/shadow validation 與 atomic active-pointer publish 保持可回滾性。

**Architecture:** 在既有 `gmv_refund_service.py` 上方增加 deterministic planner 與 incremental rebuild orchestration；repository 以 set-based copy 將上一 READY version 的 unaffected snapshot rows 搬入新 version，affected receipts 由既有 reconciliation engine 重新計算。新 version 永遠自足，不依賴 retired version；publish 前以 trusted full rebuild 做 semantic equivalence，任何 gate 失敗都 fallback full rebuild 或保持上一 READY pointer。

**Tech Stack:** Python、pandas、SQLite、pytest、Streamlit、既有 GMV refund repository/service、既有 export cache manifest/active pointer、Hermes read-only acceptance、benchmark scripts。

**Spec:** `/Users/chanwaitung2025/Downloads/nbs_analytics/docs/superpowers/specs/2026-08-27-affected-receipt-production-rebuild-design.md`

## Global Constraints

- 正式收入範圍固定為「不含掛賬核銷與 `TT 退款轉團款`」。
- Frozen baseline 固定為 2026-05 `HKD 12,057,968`；不得修改 baseline 或 dashboard/export 顯示層湊數字。
- SQLite 仍是 canonical source；incremental snapshot、metrics、manifest 與 reports 都是 derived read model。
- `總退款` 與 `已退款` 必須各自計算；不得共用 adjusted amount、applied amount 或 over-refund amount。
- 退款 status、amount、退款方式變更必須 upsert 並進入 affected set；不得只 append。
- `TT 退款轉團款` 不得被正式 GMV refund deduction 重複計算；數量不因退款改寫。
- 新 version 的讀取不得依賴 `previous_version_id`；上一 READY pointer 必須在失敗時保持可用。
- 不新增外部 service、queue、database 或 migration 作為第一階段前提；若需 index，另開 migration change。
- Memory Hub 與本地 agents 只提供 read-only bounded context；不得寫 SQLite、baseline、runtime、active pointer 或 Git。
- 每個 implementation task 必須先寫 failing test、執行 targeted test、最小實作、再次驗證，再交 Review；Implementation Agent 不得自行執行下一 task、commit、merge、push 或 Hermes。
- 完成全部 task 後，分開提供 Review、full pytest、Hermes、benchmark 與正式 Streamlit/UI acceptance evidence。

---

## File Map

### Create

- `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_incremental_rebuild.py`：planner、eligibility decision、affected receipt orchestration result 與 stage telemetry；不負責 SQLite schema 建立或 pointer 寫入。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_service.py`：planner、gate、fallback 與 orchestration unit tests。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_repository.py`：版本隔離、set-based copy、affected replacement tests。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_equivalence.py`：incremental/full semantic fingerprint tests。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/scripts/benchmark_gmv_incremental_rebuild.py`：受限、可重複、只讀 benchmark harness；不修改正式 SQLite。

### Modify

- `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_models.py`：補足 planner/result/decision typed models，沿用既有 `RefundStateDelta`。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_repository.py`：增加新 version 的 set-based copy、affected receipt replacement、snapshot completeness 與 metric delta 所需 read/write primitives。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`：把 confirm/rebuild flow 接到 incremental planner；保留 full rebuild path 與既有 business rules。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_export_cache_service.py`：接收 incremental build provenance、equivalence evidence 與 fallback status；維持 manifest schema 相容及 atomic pointer 行為。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/scripts/benchmark_gmv_refund_cache.py`：共用既有 benchmark fixture/輸出格式，加入 incremental 與 full 對照欄位（若不需修改則保持原檔不變）。
- `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_refund_models.py`、`test_gmv_refund_repository.py`、`test_gmv_refund_service.py`、既有 integration tests：補回歸案例，不改既有 contract。

---

## Checkpoint 1：Contract、planner 與 eligibility gate

### Task 1: 建立 typed rebuild contract

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_models.py`
- Create: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_service.py`

**Interfaces:**
- Consumes: existing `RefundStateDelta`、active version metadata、fingerprints。
- Produces: `IncrementalRebuildPlan`、`IncrementalRebuildResult`、`RebuildDecision`、`RebuildReasonCode`；後續 planner、repository 與 service 僅使用這些 typed contracts。

- [ ] Step 1: 寫 failing tests，驗證 plan 的 deterministic affected set 與 result 欄位。

```python
def test_plan_normalizes_and_sorts_affected_receipts():
    plan = build_incremental_plan(
        base_version_id="v1",
        state_delta=delta_with_receipts([" R2 ", "R1", "R2"]),
        fingerprints=matching_fingerprints(),
    )
    assert plan.affected_source_receipt_nos == ("R1", "R2")
    assert plan.decision is RebuildDecision.INCREMENTAL_ELIGIBLE
```

- [ ] Step 2: 執行 `pytest tests/test_gmv_incremental_rebuild_service.py::test_plan_normalizes_and_sorts_affected_receipts -q`，確認先因 contract/function 不存在而 FAIL。
- [ ] Step 3: 實作 immutable dataclass/enum contract；集合輸出使用 tuple，reason codes 使用穩定排序，禁止把 raw dataframe 放入 contract。
- [ ] Step 4: 執行該 targeted test 與 `pytest tests/test_gmv_refund_models.py -q`，確認 PASS。
- [ ] Step 5: 執行 `git diff --check`，交 findings-first Review。
- [ ] Step 6: 將 targeted diff 交 Codex 做 findings-first review；Review PASS 後由 Codex 執行：`git add backend/services/gmv_refund_models.py tests/test_gmv_incremental_rebuild_service.py && git commit -m "feat: add incremental rebuild contracts"`。

### Task 2: 實作 affected-set planner 與 eligibility gate

**Files:**
- Create: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_incremental_rebuild.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_models.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_service.py`

**Interfaces:**
- Consumes: `RefundStateDelta`、current/base fingerprints、snapshot completeness summary、bounded thresholds。
- Produces: `build_incremental_plan(...) -> IncrementalRebuildPlan`；輸出 `INCREMENTAL_ELIGIBLE`、`FULL_REBUILD_REQUIRED` 或 `BLOCKED`，並提供穩定 reason codes。

- [ ] Step 1: 寫 tests 覆蓋 `NEW`、`STATUS_CHANGED`、`AMOUNT_CHANGED`、退款方式進入/離開 `TT 退款轉團款`、空 affected set、identity conflict、fingerprint mismatch、snapshot incomplete、超過 guardrail。
- [ ] Step 2: 執行 `pytest tests/test_gmv_incremental_rebuild_service.py -q`，確認新案例 FAIL。
- [ ] Step 3: 實作 planner：以 refund ID diff 產生 affected receipts union；清理、去重、排序；identity conflict 直接 BLOCKED；affected 過大轉 FULL；fingerprint/snapshot 不一致不得判為 unaffected。
- [ ] Step 4: 將 guardrail 設成明確設定物件，至少同時支援 receipt ratio 與 absolute count；不得在函式內散落 magic numbers。
- [ ] Step 5: 執行 targeted tests，確認 reason code 與 decision 全部 PASS；執行 `python -m compileall backend/services/gmv_incremental_rebuild.py backend/services/gmv_refund_models.py`。
- [ ] Step 6: 將 targeted diff 交 Codex 做 findings-first review；Review PASS 後由 Codex 執行：`git add backend/services/gmv_incremental_rebuild.py backend/services/gmv_refund_models.py tests/test_gmv_incremental_rebuild_service.py && git commit -m "feat: plan affected receipt rebuilds"`。

### Task 3: 接入 planner 的 full fallback decision

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_service.py`、`tests/test_gmv_refund_service.py`

**Interfaces:**
- Consumes: `IncrementalRebuildPlan`。
- Produces: confirm flow 在 incremental 尚未可執行時明確選擇既有 full rebuild 或 blocking；不改 active pointer 行為。

- [ ] Step 1: 寫 tests 驗證 eligible plan 進入 incremental dispatcher、large affected/fingerprint mismatch 進入既有 full path、identity conflict 不寫 active pointer。
- [ ] Step 2: 執行 targeted tests，確認新 routing assertion FAIL。
- [ ] Step 3: 在 `confirm_refund_batch(...)` 的 version build boundary 接上 planner；保留既有 full rebuild 作為 default fallback，避免半成品先 publish。
- [ ] Step 4: 執行 `pytest tests/test_gmv_refund_service.py tests/test_gmv_incremental_rebuild_service.py -q`，確認既有 refund contract 與新 routing PASS。
- [ ] Step 5: 將 targeted diff 交 Codex 做 findings-first review；Review PASS 後由 Codex 執行：`git add backend/services/gmv_refund_service.py tests/test_gmv_refund_service.py tests/test_gmv_incremental_rebuild_service.py && git commit -m "feat: route refund rebuilds through planner"`。

### Checkpoint 1 gate

- [ ] Review Agent findings-first review PASS。
- [ ] targeted model/service suite PASS。
- [ ] 確認沒有 SQLite write、baseline、active pointer 或正式 cache 的行為變更超出 routing gate。

---

## Checkpoint 2：Repository version isolation 與 affected recompute

### Task 4: 建立新 version 的 set-based copy primitives

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_repository.py`
- Create: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_repository.py`

**Interfaces:**
- Consumes: base version ID、new version ID、sorted unaffected receipt set、dimension。
- Produces: `copy_unaffected_snapshot_rows(...)`、`load_snapshot_completeness(...)`、copy counts；新 rows 只屬於 new version。

- [ ] Step 1: 寫 temporary SQLite tests，建立兩個 version、兩個 dimensions、affected/unaffected receipts，驗證 copy 後新 version rows 完整且不 join base version。
- [ ] Step 2: 執行 `pytest tests/test_gmv_incremental_rebuild_repository.py -q`，確認 FAIL。
- [ ] Step 3: 使用現有 transaction/parameterized SQL pattern 實作 set-based copy；copy 前檢查 base rows、copy 後驗證 row count 與 version ownership。
- [ ] Step 4: 寫 failure tests：base snapshot 缺 row、dimension 缺 row、receipt set 不一致時 raise typed repository error，且不產生 READY version。
- [ ] Step 5: 執行 repository targeted suite 與 `git diff --check`。
- [ ] Step 6: 將 targeted diff 交 Codex 做 findings-first review；Review PASS 後由 Codex 執行：`git add backend/services/gmv_refund_repository.py tests/test_gmv_incremental_rebuild_repository.py && git commit -m "feat: copy unaffected refund snapshots by version"`。

### Task 5: 只載入 affected receipt 的 source rows

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_repository.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_repository.py`、`tests/test_gmv_refund_service.py`

**Interfaces:**
- Consumes: sorted `affected_source_receipt_nos`、current refund state、current formal revenue source。
- Produces: `load_revenue_frames_for_receipts(...)` 或等價既有 repository primitive；affected engine 不可收到全量 rows。

- [ ] Step 1: 寫 spy/fixture test，對 1 個 affected receipt 與 999 個 unaffected receipts，assert repository query parameter 只包含 affected set。
- [ ] Step 2: 執行 targeted test，確認 FAIL 或現有 query 仍返回全量。
- [ ] Step 3: 實作 parameterized receipt filter；保留 empty-set fast return，禁止透過 Python 循環逐 receipt 查詢造成 N+1。
- [ ] Step 4: 將 affected frames 傳給既有 reconciliation calculation，確認正式 scope 與 TT exclusion 仍使用同一 rules snapshot。
- [ ] Step 5: 執行 repository/service targeted suite；確認 `TT 退款轉團款`、SQLite not found、multi-member receipt 案例 PASS。
- [ ] Step 6: 將 targeted diff 交 Codex 做 findings-first review；Review PASS 後由 Codex 執行：`git add backend/services/gmv_refund_repository.py backend/services/gmv_refund_service.py tests/test_gmv_incremental_rebuild_repository.py tests/test_gmv_refund_service.py && git commit -m "feat: load only affected receipt revenue rows"`。

### Task 6: 實作 affected dimension recompute 與 snapshot replacement

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_repository.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_equivalence.py`、`tests/test_gmv_refund_service.py`

**Interfaces:**
- Consumes: affected frames/states、base version、new version、`TOTAL_REFUND`/`REFUNDED` dimensions。
- Produces: new-version affected reconciliation/member/adjustment rows；unaffected rows只由 Task 4 copy 產生。

- [ ] Step 1: 寫 tests：status `退款中 -> 已退款`、amount change、TT method change、over-refund cap、multiple members；assert both dimensions 的 applied/detail/over values。
- [ ] Step 2: 執行 targeted tests，確認 FAIL。
- [ ] Step 3: 讓 affected engine 只呼叫既有 `_insert_reconciliation_rows(...)` 的 bounded subset；不複製 legacy full dataframe aggregation 到新函式。
- [ ] Step 4: 對 new version 先寫 affected rows，再以 receipt key 驗證沒有 duplicate；同一 receipt 多 member 必須整組替換。
- [ ] Step 5: 執行 `pytest tests/test_gmv_incremental_equivalence.py tests/test_gmv_refund_service.py -q`，確認 PASS。
- [ ] Step 6: 將 targeted diff 交 Codex 做 findings-first review；Review PASS 後由 Codex 執行：`git add backend/services/gmv_refund_service.py backend/services/gmv_refund_repository.py tests/test_gmv_incremental_equivalence.py tests/test_gmv_refund_service.py && git commit -m "feat: recompute affected refund dimensions"`。

### Checkpoint 2 gate

- [ ] Review Agent PASS，特別檢查 version isolation、scope exclusion、status upsert 與 no-N+1。
- [ ] repository/service/incremental equivalence targeted suite PASS。
- [ ] instrumentation evidence 顯示 unaffected rows 未進入 business aggregation。

---

## Checkpoint 3：Metrics、equivalence、publish 與 fallback

### Task 7: 實作 metric delta 與 complete new snapshot validation

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_repository.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_service.py`、`tests/test_gmv_incremental_equivalence.py`

**Interfaces:**
- Consumes: base metrics、base affected snapshot、new affected snapshot、copied unaffected counts。
- Produces: `build_incremental_metric_snapshot(...)`、conservation report、new snapshot completeness result。

- [ ] Step 1: 在 `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_service.py` 新增 metric delta tests；此 test path 已與 File Map 一致。
- [ ] Step 2: 寫 failing tests，驗證 `new_metric == base_metric - old_affected_metric + new_affected_metric`，並驗證兩個 dimension、日/月/branch/product aggregates。
- [ ] Step 3: 執行 targeted tests，確認 FAIL。
- [ ] Step 4: 實作 metric delta；若 key/rounding/schema 不允許安全 delta，回傳 `FULL_REBUILD_REQUIRED`，不得默默全量重算。
- [ ] Step 5: 驗證 new version 每個 required dimension、receipt/member/adjustment rows 完整，且所有 arithmetic conservation rules 成立。
- [ ] Step 6: 執行 targeted equivalence/repository suite；交 Codex 做 findings-first review，Review PASS 後由 Codex 執行：`git add backend/services/gmv_refund_repository.py backend/services/gmv_refund_service.py tests/test_gmv_incremental_rebuild_service.py tests/test_gmv_incremental_equivalence.py && git commit -m "feat: build incremental refund metric snapshots"`。

### Task 8: Trusted reference semantic fingerprint 與 shadow comparison

**Files:**
- Create: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_equivalence.py`（若 Task 6 已建立則修改）
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_incremental_rebuild.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`

**Interfaces:**
- Consumes: incremental snapshot/metrics、trusted full rebuild snapshot/metrics、canonical field list。
- Produces: `compare_incremental_to_reference(...) -> EquivalenceReport`，含 first mismatch、digest、dimension、receipt、field、expected/actual digest，禁止 raw customer data 外送。

- [ ] Step 1: 寫 tests 覆蓋完全相同、member allocation 不同、金額 rounding 不同、TT exclusion 不同、報表 schema 不同；assert first mismatch reason 可定位。
- [ ] Step 2: 執行 `pytest tests/test_gmv_incremental_equivalence.py -q`，確認 FAIL。
- [ ] Step 3: 實作 canonical sort/normalize/fingerprint；比較 row-level、metric-level、schema-level semantic fields，不比較 XLSX binary bytes。
- [ ] Step 4: 在 shadow mode 保存 bounded evidence：version IDs、counts、digests、timings；不保存完整原始 rows。
- [ ] Step 5: 執行 targeted tests 與 `python -m compileall backend/services/gmv_incremental_rebuild.py`。
- [ ] Step 6: 將 targeted diff 交 Codex 做 findings-first review；Review PASS 後由 Codex 執行：`git add backend/services/gmv_incremental_rebuild.py tests/test_gmv_incremental_equivalence.py backend/services/gmv_refund_service.py && git commit -m "feat: validate incremental rebuild against trusted reference"`。

### Task 9: Atomic publish、active pointer 與 fallback circuit breaker

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_export_cache_service.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_repository.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_refund_service.py`、`tests/test_gmv_export_cache_service.py`、`tests/test_gmv_incremental_rebuild_repository.py`

**Interfaces:**
- Consumes: `EquivalenceReport`、baseline/conservation/artifact gate result、new manifest。
- Produces: `READY` publish 或 typed fallback/blocking；上一 READY pointer 在所有 failure path 保持不變。

- [ ] Step 1: 寫 failure-injection tests：equivalence fail、baseline fail、artifact checksum fail、timeout、memory guard、process exception、concurrent stale base；assert active pointer/manifest unchanged。
- [ ] Step 2: 執行 targeted tests，確認 FAIL。
- [ ] Step 3: 將 pointer swap 放在最後一個 transaction/atomic publish boundary；new version 在所有 artifacts READY 前不得成為 active。
- [ ] Step 4: 將 decision/fallback reason、base/new version、affected/copied/recomputed counts 寫入既有 bounded provenance；不改 manifest schema，除非先提供相容性 migration design。
- [ ] Step 5: 驗證 full fallback 仍使用既有可信路徑，incremental 失敗不會留下可被 UI 讀到的半成品。
- [ ] Step 6: 執行 service/cache/repository targeted suite；交 Codex 做 findings-first review，Review PASS 後由 Codex 執行：`git add backend/services/gmv_refund_service.py backend/services/gmv_export_cache_service.py backend/services/gmv_refund_repository.py tests/test_gmv_refund_service.py tests/test_gmv_export_cache_service.py tests/test_gmv_incremental_rebuild_repository.py && git commit -m "feat: publish incremental rebuilds atomically"`。

### Checkpoint 3 gate

- [ ] Review Agent PASS，確認 no partial publish、no retired-version dependency、fallback matrix 完整。
- [ ] targeted equivalence、cache、repository、service tests PASS。
- [ ] 以 temporary SQLite 驗證 active pointer crash rollback，不碰正式 production DB。

---

## Checkpoint 4：Concurrency、observability、benchmark 與 rollout

### Task 10: Lease、stale-plan guard 與 stage telemetry

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_incremental_rebuild.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`
- Test: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_incremental_rebuild_service.py`

**Interfaces:**
- Consumes: base version/fingerprint snapshot、rebuild plan、runtime clock/lease abstraction。
- Produces: single-flight guard、stale-plan decision、bounded stage telemetry；不自行啟動 background service。

- [ ] Step 1: 寫 tests：同 base version concurrent rebuild 只允許一個；merge 後 refund state fingerprint 改變會 stale abort；stage timings 不含 raw data。
- [ ] Step 2: 執行 targeted tests，確認 FAIL。
- [ ] Step 3: 實作受控 lease/compare-and-swap guard，將 lease loss、stale plan、timeout 轉為 typed decision。
- [ ] Step 4: 為 plan、affected recompute、copy、metrics、equivalence、publish 記錄 monotonic elapsed milliseconds、counts、fallback reason。
- [ ] Step 5: 執行 targeted tests 與 `git diff --check`；交 Codex 做 findings-first review，Review PASS 後由 Codex 執行：`git add backend/services/gmv_incremental_rebuild.py backend/services/gmv_refund_service.py tests/test_gmv_incremental_rebuild_service.py && git commit -m "feat: guard incremental rebuild concurrency"`。

### Task 11: 建立 read-only benchmark harness

**Files:**
- Create: `/Users/chanwaitung2025/Downloads/nbs_analytics/scripts/benchmark_gmv_incremental_rebuild.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/scripts/benchmark_gmv_refund_cache.py`（只有共用既有 fixture/輸出 helper 時才修改）
- Create: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_benchmark_gmv_incremental_rebuild.py`

**Interfaces:**
- Consumes: isolated fixture DB、synthetic affected ratios、existing full rebuild benchmark contract。
- Produces: JSON benchmark evidence：plan/recompute/copy/metric/equivalence/publish timings、median/p95、RSS、row counts、fallback rate。

- [ ] Step 1: 寫 tests 驗證 0.1%、1%、10%、over-guardrail cases 的 output schema、至少 3 samples 的 median/p95 與 `unaffected_aggregation_calls == 0`。
- [ ] Step 2: 執行 `pytest tests/test_benchmark_gmv_incremental_rebuild.py -q`，確認 FAIL。
- [ ] Step 3: 實作 isolated fixture generator；benchmark 不讀或寫正式業務 DB，不產生 raw customer data。
- [ ] Step 4: 實作 CLI，例如：`python scripts/benchmark_gmv_incremental_rebuild.py --fixture isolated --samples 3 --ratios 0.001,0.01,0.1`。
- [ ] Step 5: 將 incremental/full 結果以 semantic fingerprint 比較，輸出 deterministic JSON；不以單次最快時間判定通過。
- [ ] Step 6: 執行 benchmark tests 與至少一組 isolated CLI run；交 Codex 做 findings-first review，Review PASS 後由 Codex 執行：`git add scripts/benchmark_gmv_incremental_rebuild.py tests/test_benchmark_gmv_incremental_rebuild.py scripts/benchmark_gmv_refund_cache.py && git commit -m "test: benchmark affected receipt rebuilds"`。

### Task 12: Feature flag、rollout evidence 與 UI acceptance contract

**Files:**
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_incremental_rebuild.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_refund_service.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/backend/services/gmv_export_cache_service.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_refund_service.py`
- Modify: `/Users/chanwaitung2025/Downloads/nbs_analytics/tests/test_gmv_export_cache_service.py`

**Interfaces:**
- Consumes: mode `shadow|opt_in|default`、equivalence report、benchmark gates。
- Produces: rollout-safe behavior：shadow 不 publish、opt-in 需明確啟用、default 具 circuit breaker；UI 讀到最後 READY cache。

- [ ] Step 1: 寫 tests 驗證三種 mode、flag invalid、equivalence fail、fallback、restart/read-existing-ready-cache。
- [ ] Step 2: 執行 targeted tests，確認 FAIL。
- [ ] Step 3: 實作 bounded configuration parsing；未設定或 invalid 時使用安全的 full/shadow 行為，不默默 default-on。
- [ ] Step 4: 接入既有 UI/cache read contract，只顯示 active version、build mode、fallback/equivalence bounded status；不增加 UI write authority。
- [ ] Step 5: 執行 service/cache/UI contract tests；交 Codex 做 findings-first review，Review PASS 後由 Codex 執行：`git add backend/services/gmv_incremental_rebuild.py backend/services/gmv_refund_service.py backend/services/gmv_export_cache_service.py tests/test_gmv_refund_service.py tests/test_gmv_export_cache_service.py && git commit -m "feat: add safe incremental rebuild rollout modes"`。

### Checkpoint 4 gate

- [ ] Review Agent PASS。
- [ ] isolated benchmark 顯示低 affected ratio 下 improvement，且 RSS/latency guardrail 未超標。
- [ ] shadow/opt-in/default 三種 mode 的 fallback 與 pointer safety 有測試 evidence。

---

## Final Verification and Release Gate

- [ ] 執行全部 affected-rebuild targeted tests：

```bash
pytest tests/test_gmv_incremental_rebuild_service.py \
       tests/test_gmv_incremental_rebuild_repository.py \
       tests/test_gmv_incremental_equivalence.py \
       tests/test_benchmark_gmv_incremental_rebuild.py \
       tests/test_gmv_refund_models.py \
       tests/test_gmv_refund_repository.py \
       tests/test_gmv_refund_service.py \
       tests/test_gmv_export_cache_service.py -q
```

- [ ] 執行 full pytest，並以 `-W error::FutureWarning` 確認沒有新增 pandas warning。
- [ ] 執行 `python -m compileall backend scripts`。
- [ ] 執行既有 Review runner；只在 Review PASS 後進入完整驗證。
- [ ] 執行 `scripts/hermes_post_change_check.py`；Hermes 只 read-only 驗證 runtime、SQLite integrity、baseline、service、Git 與 workflow evidence。
- [ ] 在正式 runtime 做受控 Streamlit/UI acceptance：上傳退款明細、觀察 affected/copied/recomputed bounded status、確認兩個報表可下載、refresh 後仍讀 active READY；正式 production rebuild 只在 rollout gate 明確 PASS 後執行。
- [ ] 記錄 baseline `HKD 12,057,968`、formal scope、TT exclusion、兩個 refund dimensions、equivalence digest、benchmark median/p95/RSS 與 fallback evidence。
- [ ] 只有所有 gates PASS 後，才由 Codex 依 finishing workflow 決定 commit/push/PR/merge；本 plan 本身不授權自動整合。

## Self-review checklist

- [x] 覆蓋 planner、affected set、unaffected copy、兩個 dimensions、metrics、equivalence、fallback、pointer、concurrency、benchmark、rollout、UI acceptance。
- [x] 明確禁止跨 retired version runtime dependency、TT double-count、baseline 修改與半成品 publish。
- [x] 每個 task 都有 files、interfaces、failing test、run command、implementation boundary、passing verification 與 commit point。
- [x] Task 7 的 test path 已與 File Map 一致。
- [x] 沒有使用 `TBD`、`TODO` 或未定義的下一步 placeholder。
