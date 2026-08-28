
# Revenue Pipeline 與正式 Cold Rebuild 優化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox ( - [ ] ) syntax for tracking.

**Goal:** 在不改變正式營收口徑、SQLite canonical data、退款 dimension 或既有報表結果的前提下，清除 90 個 pandas warnings，建立可量測的 cold rebuild 優化路徑，並以 shared intermediate、bounded serializer、equivalence gate、incremental refund rebuild 與 atomic active pointer 降低正式報表建立時間。

**Architecture:** 先以 dtype-safe cleanup 與 isolated benchmark 建立可信 baseline，再把既有 gmv_export_intermediate_service.py、gmv_export_serializer_service.py 與 gmv_export_equivalence_service.py 接入 gmv_refund_service.py 的 formal cache build。所有 fast artifacts 先寫入 version-scoped staging generation，通過 legacy trusted reference、schema、checksum、baseline 與 shadow equivalence 後才由 manifest/pointer atomic publish；任何 failure 保留上一個 READY generation 並 fallback 到 legacy path。

**Tech Stack:** Python 3、pandas、SQLite、Streamlit、openpyxl、pytest、JSON manifest、SHA-256 fingerprints、ThreadPoolExecutor、現有 Hermes／Review scripts。

**Spec:** docs/superpowers/specs/2026-08-26-revenue-pipeline-and-cold-rebuild-optimization-design.md

## Global Constraints

- 正式收入範圍固定為「不含掛賬核銷與 TT 退款轉團款」。
- 2026-05 frozen baseline 固定為 HKD 12,057,968。
- SQLite 是 canonical source；intermediate/cache 只能是 derived read model。
- 不修改正式 SQLite schema、baseline registry、外部服務、Migration 或 agent workflow control。
- 不把 TT 退款轉團款納入正式 GMV 扣減或超額退款基礎。
- 總退款與已退款共用 preparation，但不共用 adjusted monetary values。
- Fast path 必須先通過 trusted reference／shadow equivalence，否則使用 legacy fallback。
- active pointer 只能指向 checksum verified、status=ready 的 generation。
- 保留既有 artifact filenames、sheet names、欄位順序、下載 contract 與 v1 cache reader compatibility。
- Benchmark 只能使用 temporary 或 isolated benchmark cache，禁止寫入正式 runtime cache、正式 SQLite 或正式業務資料。
- 每個 Task 完成後依 docs/agents/REVIEW_AGENT_CONTRACT.md 做 findings-first review；Review PASS 後跑 targeted pytest、full pytest、scripts/hermes_post_change_check.py。
- 不得把 .superpowers/brainstorm/、.nbs_runtime/、.nbs_runtime_cache/ 或正式業務資料加入 commit。

---

## 1. Implementation Boundaries and File Map

### Existing files to modify

- pipeline.py：修正 3 個 pandas warning sites；抽取能被 GMV fast path 重用的 prepared dashboard facts boundary。
- backend/services/gmv_export_intermediate_service.py：保留現有 preparation/facts models，增加 shared preparation／dimension facts orchestration。
- backend/services/gmv_export_serializer_service.py：保留 bounded serializer、staging promotion、timeout 與 publication gate，補足完整 artifact job contract。
- backend/services/gmv_export_equivalence_service.py：覆蓋所有 canonical GMV artifacts 的 semantic comparison。
- backend/services/gmv_export_cache_service.py：擴展既有 gmv-formal-export-cache-v2 manifest 的 performance、fallback、refund-state metadata。
- backend/services/gmv_refund_service.py：接入 shared preparation、bounded serializer、equivalence、baseline gate 與 incremental affected-order path。
- backend/services/gmv_refund_repository.py：只增加既有 immutable ledger 的 read/query helper；不修改 schema。
- backend/services/upload_preflight_service.py：保存 refund delta classification 與 affected source receipt set。
- backend/services/upload_orchestrator_service.py：傳遞 cache build stage timings、fallback reason 與 final cache status。
- app_pages.py：只更新 GMV formal dashboard 的狀態顯示與 ready/fallback evidence，不在 UI 重建資料。
- scripts/benchmark_gmv_refund_cache.py：擴展 isolated cold/warm/incremental benchmark suite。
- scripts/hermes_post_change_check.py：只有在現有 Hermes 缺少新 manifest／benchmark gate 時才增加 read-only check。

### Files to create

- tests/test_pipeline_dtype_contract.py：3 個 warning sites 的 dtype、missing-value 與 output contract regression tests。
- backend/services/gmv_rebuild_benchmark_service.py：純 read-only benchmark suite orchestration。
- tests/test_gmv_rebuild_benchmark_service.py：benchmark mode、sample aggregation、cache isolation、memory/timing schema tests。
- tests/test_gmv_incremental_rebuild.py：NEW／UNCHANGED／STATUS_CHANGED／AMOUNT_CHANGED／identity conflict tests。
- tests/test_gmv_manifest_performance_contract.py：manifest backward compatibility、performance metadata、fallback/pointer contract tests。

### Existing tests to extend

- tests/test_gmv_export_intermediate_service.py
- tests/test_gmv_export_serializer_service.py
- tests/test_gmv_export_equivalence_service.py
- tests/test_gmv_export_cache_service.py
- tests/test_gmv_export_fast_controller.py
- tests/test_gmv_export_benchmark.py
- tests/test_gmv_one_click_merge_integration.py
- tests/test_streamlit_gmv_formal_contract.py
- tests/test_streamlit_gmv_refund_contract.py
- tests/test_upload_preflight_service.py
- tests/test_upload_orchestrator_service.py

---

## 2. Task 1: Pandas dtype-safe cleanup

**Outcome:** 3 個 warning sites 完成明確 dtype conversion；新 regression suite 在 -W error::FutureWarning 下通過。

**Files:**
- Modify: pipeline.py:820-823、842-845、866-870、889-893
- Create: tests/test_pipeline_dtype_contract.py
- Extend: tests/test_pipeline_preloaded_frames.py

**Interfaces:**

- Consumes: 現有 build_dashboard_data()、build_dashboard_data_excluding_receipt_types() 與 normalized runtime columns。
- Produces: 相同欄位名稱、資料列、排序、numeric totals 與 workbook schema；日期欄位明確為 pandas string/datetime-compatible dtype，數量欄位為 numeric dtype。

- [ ] Step 1: Write failing tests

  建立最小 tour/others fixtures，包含有效交易時間、無效交易時間、統一日期 fallback、空值、掛賬核銷與 TT 退款轉團款，並加入：

  ~~~python
  def test_dashboard_pipeline_has_no_futurewarning_and_preserves_dtype_contract():
      with warnings.catch_warnings():
          warnings.simplefilter("error", FutureWarning)
          branch, specialist, facts = pipeline.build_dashboard_data(
              tour, others, branch_mapping, target_branches,
              cruise_departments, sales_reps, make_workbook=False,
              return_facts=True,
          )

      assert facts["分社每天旅行團交易人數"]["交易人數"].dtype.kind in {"i", "u", "f"}
      assert facts["分社每天票務交易數量"]["交易數量"].dtype.kind in {"i", "u", "f"}
      assert set(facts["分社經營統計"]["月份"].dropna()) == {"2026-05"}
  ~~~

  另外測試日期 fallback、merge 後非 numeric 欄位不被 fillna(0) 轉成數字，以及 empty frame 的既有 columns。

- [ ] Step 2: Run tests to verify failure

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_pipeline_dtype_contract.py -W error::FutureWarning
  ~~~

  Expected: FAIL，原始 fillna 觸發 pandas FutureWarning。

- [ ] Step 3: Implement minimal dtype-safe changes

  日期欄位使用明確 string fallback：

  ~~~python
  parsed = pd.to_datetime(source, errors="coerce").dt.strftime("%Y-%m-%d").astype("string")
  fallback = frame["統一日期"].astype("string")
  frame["日期"] = parsed.fillna(fallback)
  ~~~

  merge 後只對指定 numeric columns 補零：

  ~~~python
  for column in ("交易人數", "郵輪交易人數"):
      result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0)
  ~~~

  不得對整個 DataFrame 呼叫 fillna(0)。

- [ ] Step 4: Run focused regression tests

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_pipeline_dtype_contract.py tests/test_pipeline_preloaded_frames.py tests/test_gmv_one_click_merge_integration.py -W error::FutureWarning
  ~~~

  Expected: PASS，且不產生 pandas FutureWarning。

- [ ] Step 5: Review and commit

  ~~~bash
  git diff --check
  git add pipeline.py tests/test_pipeline_dtype_contract.py tests/test_pipeline_preloaded_frames.py
  git commit -m "fix: make pipeline pandas dtypes explicit"
  ~~~

  完成 Review Agent findings-first review，確認沒有改變 business output。

---

## 3. Task 2: Cold rebuild benchmark and evidence

**Outcome:** 建立可重複的 legacy/fast/warm/incremental benchmark，輸出每個 stage 的 median、p95、artifact fingerprint 與 peak RSS。

**Files:**
- Create: backend/services/gmv_rebuild_benchmark_service.py
- Modify: scripts/benchmark_gmv_refund_cache.py
- Create: tests/test_gmv_rebuild_benchmark_service.py
- Extend: tests/test_gmv_export_benchmark.py
- Extend: tests/test_gmv_performance_contract.py

**Interfaces:**

~~~python
@dataclass(frozen=True, slots=True)
class GmvBenchmarkSample:
    mode: str
    workers: int
    total_ms: float
    stage_ms: Mapping[str, float]
    peak_rss_bytes: int | None
    artifact_fingerprints: Mapping[str, str]
    equivalence_status: str
    fallback_reason: str | None

def run_gmv_rebuild_benchmark(
    *, db_path: str | Path, version_id: str,
    cache_dir: str | Path, mode: str, workers: int,
) -> GmvBenchmarkSample:
    # Returns one isolated sample with stage timing and artifact evidence.

def run_gmv_rebuild_benchmark_suite(
    *, db_path: str | Path, version_id: str,
    cache_dir: str | Path, modes: Sequence[str],
    samples: int = 3, workers: int = 1,
) -> dict[str, object]:
    # Returns per-mode samples, median/p95, equivalence rate and memory evidence.
~~~

- Consumes: 現有 run_gmv_cache_benchmark()、GmvRefundRepository、build_gmv_formal_artifacts() 與 fast controller。
- Produces: legacy-cold、fast-cold、trusted-warm、status-change、cache-missing、cache-corrupt、non-core-db-change 的 isolated JSON report。

- [ ] Step 1: Write failing benchmark contract tests

  測試 suite 必須：
  - 只接受 temporary 或 .nbs_agent_runtime/benchmarks path。
  - samples >= 3。
  - 每個 mode 有 medianMs、p95Ms、samples、equivalenceRate。
  - 不修改 SQLite file hash。
  - 不寫入 .nbs_runtime_cache。

  ~~~python
  def test_benchmark_suite_reports_median_p95_and_keeps_db_unchanged(tmp_path):
      before = sha256(db_path)
      report = run_gmv_rebuild_benchmark_suite(
          db_path=db_path, version_id="active-v1",
          cache_dir=tmp_path / "bench",
          modes=("legacy-cold", "fast-cold"),
          samples=3, workers=1,
      )
      assert report["sampleCount"] == 3
      assert report["modes"]["legacy-cold"]["p95Ms"] >= report["modes"]["legacy-cold"]["medianMs"]
      assert sha256(db_path) == before
  ~~~

- [ ] Step 2: Run tests to verify failure

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_rebuild_benchmark_service.py
  ~~~

  Expected: FAIL，新的 suite interface 尚未存在。

- [ ] Step 3: Implement isolated benchmark runner

  實作 stage timing wrapper，至少收集 preparationMs、adjustmentMs、factsMs、serializationMs、equivalenceMs、baselineMs、manifestWriteMs、totalMs、peakRssBytes。suite 使用 statistics.median() 與 nearest-rank p95，輸出 deterministic JSON。所有 output path 先通過現有 _validate_benchmark_cache_dir()。

- [ ] Step 4: Run benchmark tests

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_rebuild_benchmark_service.py tests/test_gmv_export_benchmark.py tests/test_gmv_performance_contract.py
  ~~~

  Expected: PASS；benchmark report 不觸碰正式 cache。

- [ ] Step 5: Review and commit

  ~~~bash
  git add backend/services/gmv_rebuild_benchmark_service.py scripts/benchmark_gmv_refund_cache.py tests/test_gmv_rebuild_benchmark_service.py tests/test_gmv_export_benchmark.py tests/test_gmv_performance_contract.py
  git commit -m "test: add formal GMV rebuild benchmark suite"
  ~~~

---

## 4. Task 3: Shared intermediate preparation and report facts

**Outcome:** total/paid fast path 共用 normalization、scope masks、classification 與 reusable report preparation；不再為每份 workbook 重跑相同 preparation。

**Files:**
- Modify: backend/services/gmv_export_intermediate_service.py
- Modify: pipeline.py
- Modify: backend/services/gmv_refund_service.py:_run_fast_export_gate()
- Extend: tests/test_gmv_export_intermediate_service.py
- Extend: tests/test_gmv_export_fast_controller.py
- Extend: tests/test_gmv_export_performance.py

**Interfaces:**

~~~python
@dataclass(frozen=True, slots=True)
class GmvReportFactSet:
    dimension: str
    facts_by_scope: Mapping[str, GmvReportFacts]
    preparation_fingerprint: str
    aggregation_count: int

def build_gmv_report_fact_set(
    *, preparation: GmvExportBasePreparation,
    adjusted_tour: pd.DataFrame,
    adjusted_others: pd.DataFrame,
    dimension: str,
    rules: tuple[dict, list[str], list[str], list[str], list[str]],
    include_branch_salesperson_sheet: bool,
) -> GmvReportFactSet:
    # Returns facts for all, no_writeoff and official scopes.
~~~

pipeline extraction interface：

~~~python
def build_dashboard_intermediate(
    tour: pd.DataFrame, others: pd.DataFrame,
    *, branch_mapping: dict, target_branches_s3: list[str],
    cruise_depts: list[str], sales_rep_list: list[str],
) -> DashboardIntermediate:
    # Returns normalized, classified and fingerprinted reusable preparation.

def build_dashboard_data_from_intermediate(
    intermediate: DashboardIntermediate, *,
    scope_id: str, make_workbook: bool = False,
    include_branch_salesperson_sheet: bool = True,
    return_facts: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    # Returns the legacy-compatible branch, specialist and facts tuple.
~~~

DashboardIntermediate 至少保存 normalized tour/others、parsed dates、classification columns、branch/salesperson keys、quantity bases、money bases 與 stable source fingerprints。

- Consumes: GmvExportBasePreparation、RevenueFrames、_current_rules()。
- Produces: 每個 dimension 的 all、no_writeoff、official facts，facts 只由同一份 preparation 產生。

- [ ] Step 1: Write failing reuse tests

  建立 spy builder，驗證 normalization/preparation 只呼叫一次：

  ~~~python
  def test_report_fact_set_reuses_one_preparation_for_three_scopes(monkeypatch):
      calls = {"prepare": 0, "aggregate": 0}
      preparation, adjusted = make_preparation_fixture()
      result = build_gmv_report_fact_set(
          preparation=preparation,
          adjusted_tour=adjusted["tour"],
          adjusted_others=adjusted["others"],
          dimension="總退款",
          rules=make_rules_fixture(),
          include_branch_salesperson_sheet=True,
      )
      assert set(result.facts_by_scope) == {"all", "no_writeoff", "official"}
      assert calls["prepare"] == 1
      assert result.aggregation_count == 1
  ~~~

  加入 total/paid isolation test，確認兩個 fact sets 的 dimension 不同且 adjusted amount fingerprint 不相同。

- [ ] Step 2: Run tests to verify failure

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_export_intermediate_service.py tests/test_gmv_export_performance.py
  ~~~

  Expected: FAIL，current service 沒有 fact-set orchestration 或 aggregation reuse counter。

- [ ] Step 3: Extract minimal pipeline intermediate boundary

  從既有 build_dashboard_data() 抽出 normalization/classification/shared bases；保留原 function 作 compatibility wrapper：

  ~~~python
  intermediate = build_dashboard_intermediate(
      tour, others,
      branch_mapping=branch_mapping,
      target_branches_s3=target_branches_s3,
      cruise_depts=cruise_depts,
      sales_rep_list=sales_rep_list,
  )
  return build_dashboard_data_from_intermediate(
      intermediate, scope_id="all",
      make_workbook=make_workbook,
      include_branch_salesperson_sheet=include_branch_salesperson_sheet,
      return_facts=return_facts,
  )
  ~~~

  既有 public return tuple、sheet names、row ordering、numeric totals 與 empty-frame columns 必須保持不變。

- [ ] Step 4: Integrate fact set into fast gate

  _run_fast_export_gate() 先建立一份 GmvExportBasePreparation，再為 total 與 paid 各建立一個 GmvReportFactSet；serializer jobs 只讀 facts，不呼叫 raw-frame aggregation。加入 basePreparationMs、totalFactsMs、paidFactsMs 與 aggregationCount stage evidence。

- [ ] Step 5: Run focused regression and benchmark

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_export_intermediate_service.py tests/test_gmv_export_fast_controller.py tests/test_gmv_export_performance.py tests/test_gmv_one_click_merge_integration.py
  ~~~

  Expected: PASS；legacy output semantic fingerprints unchanged，fast path preparation count bounded。

- [ ] Step 6: Review and commit

  ~~~bash
  git add pipeline.py backend/services/gmv_export_intermediate_service.py backend/services/gmv_refund_service.py tests/test_gmv_export_intermediate_service.py tests/test_gmv_export_fast_controller.py tests/test_gmv_export_performance.py
  git commit -m "perf: reuse GMV report intermediate data"
  ~~~

---

## 5. Task 4: Bounded serializer and artifact equivalence integration

**Outcome:** six dashboard workbooks 使用 bounded serializer，detail/audit/summary 共用同一 generation；所有 artifacts 通過 semantic equivalence 後才可 publish。

**Files:**
- Modify: backend/services/gmv_export_serializer_service.py
- Modify: backend/services/gmv_export_equivalence_service.py
- Modify: backend/services/gmv_refund_service.py:_run_fast_export_gate()
- Extend: tests/test_gmv_export_serializer_service.py
- Extend: tests/test_gmv_export_equivalence_service.py
- Extend: tests/test_gmv_export_fast_controller.py
- Extend: tests/test_gmv_export_cache_service.py

**Interfaces:**

保留 serialize_gmv_workbooks_parallel(jobs, max_workers=3, timeout_seconds=None)。新增：

~~~python
def build_gmv_serializer_jobs(
    *, total_facts: GmvReportFactSet,
    paid_facts: GmvReportFactSet,
    staging_dir: Path,
    publication_gate: SerializerPublicationGate,
) -> tuple[SerializerJob, ...]:
    # Returns the fixed six-workbook job list in canonical artifact order.
~~~

canonical artifact IDs：

~~~text
total.detail
paid.detail
total.workbook.ex.xlsx
total.workbook.ex_no_writeoff.xlsx
total.workbook.ex_no_writeoff_refund_transfer.xlsx
paid.workbook.ex.xlsx
paid.workbook.ex_no_writeoff.xlsx
paid.workbook.ex_no_writeoff_refund_transfer.xlsx
total.workbook.audit.xlsx
paid.workbook.audit.xlsx
summaries
~~~

- Consumes: Task 3 的 GmvReportFactSet。
- Produces: ordered SerializerResult、artifact semantic records、bounded mismatch examples、serialization timings。

- [ ] Step 1: Write failing serializer contract tests

  覆蓋 duplicate artifact IDs、invalid dimension/scope identity、worker count 4、timeout partial publication、XLSX metadata-only difference、2-decimal money comparison、sheet/column/row mismatch。

- [ ] Step 2: Run tests to verify failure

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_export_serializer_service.py tests/test_gmv_export_equivalence_service.py
  ~~~

- [ ] Step 3: Implement bounded job creation

  build_gmv_serializer_jobs() 使用固定 artifact IDs 與 scope mapping 建立 6 個 workbook jobs；每個 job 只接收 GmvReportFacts，不接受 raw database frames。

  serialize_gmv_workbooks_parallel() 必須：
  - max_workers = min(max_workers, 3, len(jobs))。
  - timeout 時停止未 publish job。
  - 所有 job READY 後才由 staging directory promotion。
  - 任一 job failure 時不 promote partial artifact。
  - return results 保持 input order。

- [ ] Step 4: Integrate semantic gate before cache publish

  _run_fast_export_gate() 只有在 checksum、schema、baseline、shadow/equivalence 全部 PASS 且 serializer results 全部 READY 時，才回傳 GmvFastCandidate。

- [ ] Step 5: Run focused tests

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_export_serializer_service.py tests/test_gmv_export_equivalence_service.py tests/test_gmv_export_fast_controller.py tests/test_gmv_export_cache_service.py
  ~~~

- [ ] Step 6: Review and commit

  ~~~bash
  git add backend/services/gmv_export_serializer_service.py backend/services/gmv_export_equivalence_service.py backend/services/gmv_refund_service.py tests/test_gmv_export_serializer_service.py tests/test_gmv_export_equivalence_service.py tests/test_gmv_export_fast_controller.py tests/test_gmv_export_cache_service.py
  git commit -m "perf: gate bounded GMV workbook serialization"
  ~~~

---

## 6. Task 5: Manifest performance metadata and active pointer safety

**Outcome:** cache manifest 可追蹤 cold rebuild stage timings/fallback，reader 只接受 verified ready generation，且 legacy v1 仍可讀取。

**Files:**
- Modify: backend/services/gmv_export_cache_service.py
- Modify: backend/services/gmv_refund_service.py
- Create: tests/test_gmv_manifest_performance_contract.py
- Extend: tests/test_gmv_export_cache_service.py
- Extend: tests/test_gmv_export_fast_controller.py

**Interfaces:**

擴展 GmvExportCacheManifest，保留現有 constructor compatibility：

~~~python
@dataclass(frozen=True, slots=True)
class GmvExportCacheManifest:
    # Existing cache identity, artifact and validation fields remain unchanged.
    performance: Mapping[str, object] = field(default_factory=dict)
    fallback: Mapping[str, object] = field(default_factory=dict)
    refund_state_sha256: str | None = None
~~~

擴展 build_gmv_export_cache()：

~~~python
def build_gmv_export_cache(
    *,
    version_id: str,
    revenue_generation_token: str,
    rule_version: str,
    total_workbooks: Mapping[str, bytes],
    paid_workbooks: Mapping[str, bytes],
    total_detail: pd.DataFrame,
    paid_detail: pd.DataFrame,
    summaries: list[dict[str, object]],
    cache_dir: Path,
    builder_mode: str = "legacy",
    equivalence_status: str = "NOT_RUN",
    publish_active: bool = True,
    content_fingerprint: str | None = None,
    reference_id: str | None = None,
    validation_mode: str = "legacy",
    shadow_status: str = "NOT_RUN",
    reference_manifest_sha256: str | None = None,
    reference_status: str = "N/A",
    ready_error: str | None = None,
    performance: Mapping[str, object] | None = None,
    fallback: Mapping[str, object] | None = None,
    refund_state_sha256: str | None = None,
) -> GmvExportCacheManifest:
    # Writes the version-scoped manifest and returns its read model.
~~~

manifest status 使用現有 lowercase values：

~~~text
preparing | verifying | ready | fallback | failed
~~~

- Consumes: Task 4 的 artifact result/timing、active version source、refund state fingerprint。
- Produces: gmv-formal-export-cache-v2 backward-compatible manifest、version-scoped manifest、atomic active.json。

- [ ] Step 1: Write failing manifest tests

  驗證 v1 payload 可讀、新欄位 round-trip、failed manifest 不更新 pointer、pointer checksum mismatch 回傳 CACHE_INVALID、fast failure 後 previous ready 仍可讀、只有 status=ready 才可被 load_active_gmv_read_model() 接受。

- [ ] Step 2: Run tests to verify failure

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_manifest_performance_contract.py tests/test_gmv_export_cache_service.py
  ~~~

- [ ] Step 3: Implement backward-compatible fields

  新增欄位使用 defaults，不改既有 artifact key、path、checksum 或 gmv-formal-export-cache-v2 schema string。active pointer 只在 READY manifest 完整寫入後 atomic swap。

- [ ] Step 4: Enforce reader and pointer safety

  load_gmv_export_cache() 與 load_active_gmv_read_model() 依序驗證 pointer、manifest、status、version/revenue/refund/rules fingerprints、artifact containment 與每個 artifact checksum。任一失敗返回上一個 READY 或 CACHE_INVALID，不把 failed manifest 變 active。

- [ ] Step 5: Run cache/controller tests

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_manifest_performance_contract.py tests/test_gmv_export_cache_service.py tests/test_gmv_export_fast_controller.py tests/test_gmv_one_click_merge_integration.py
  ~~~

- [ ] Step 6: Review and commit

  ~~~bash
  git add backend/services/gmv_export_cache_service.py backend/services/gmv_refund_service.py tests/test_gmv_manifest_performance_contract.py tests/test_gmv_export_cache_service.py tests/test_gmv_export_fast_controller.py
  git commit -m "fix: publish only verified GMV cache generations"
  ~~~

---

## 7. Task 6: Incremental refund status and affected-order rebuild

**Outcome:** 新 refund batch 支援 update/upsert 與 affected source receipt rebuild；退款中變已退款不再只 append，也不必無條件重算所有 refund rows。

**Files:**
- Modify: backend/services/upload_preflight_service.py
- Modify: backend/services/gmv_refund_service.py
- Modify: backend/services/gmv_refund_repository.py
- Create: tests/test_gmv_incremental_rebuild.py
- Extend: tests/test_gmv_refund_preflight.py
- Extend: tests/test_gmv_refund_repository.py
- Extend: tests/test_gmv_one_click_merge_integration.py

**Interfaces:**

~~~python
@dataclass(frozen=True, slots=True)
class RefundStateDelta:
    new_refund_order_nos: tuple[str, ...]
    unchanged_refund_order_nos: tuple[str, ...]
    status_changed_refund_order_nos: tuple[str, ...]
    amount_changed_refund_order_nos: tuple[str, ...]
    identity_conflict_refund_order_nos: tuple[str, ...]
    affected_source_receipt_nos: tuple[str, ...]
    classification_counts: Mapping[str, int]

def classify_refund_state_delta(
    current: Mapping[str, RefundCurrentState],
    proposed: Sequence[RefundCurrentState],
) -> RefundStateDelta:
    # Returns deterministic state classifications and affected source receipts.

def load_reconciliation_snapshot_for_receipts(
    self, version_id: str, refund_dimension: str,
    source_receipt_nos: Sequence[str],
) -> pd.DataFrame:
    # Returns only the requested source receipts for the requested dimension.
~~~

- Consumes: existing gmv_refund_current state、preflight proposed states、source receipt identity。
- Produces: delta evidence、affected source receipt set、updated current projection、new version reconciliation/adjustment snapshot。

- [ ] Step 1: Write failing delta tests

  覆蓋 NEW、UNCHANGED、STATUS_CHANGED、AMOUNT_CHANGED、REFUND_IDENTITY_CONFLICT、shared affected receipt、TT exclusion。

  ~~~python
  def test_status_change_is_upsert_and_marks_source_receipt_affected():
      delta = classify_refund_state_delta(current, proposed)
      assert delta.status_changed_refund_order_nos == ("R-1",)
      assert delta.affected_source_receipt_nos == ("S-1",)
  ~~~

- [ ] Step 2: Run tests to verify failure

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_incremental_rebuild.py
  ~~~

- [ ] Step 3: Implement delta classification and current projection checks

  保留 immutable gmv_refund_observations 與 immutable batch；只允許 gmv_refund_current current projection 由既有 upsert contract 更新。identity conflict 返回 blocking classification，不得靜默覆蓋 source receipt。

- [ ] Step 4: Implement affected receipt rebuild

  在 confirm_gmv_refund()／rebuild_gmv_scope() 中先計算 delta。若有 identity conflict，停止 activation。對 unaffected rows 沿用上一個 version 的 immutable snapshot；對 affected source receipts 重新計算 total/paid reconciliation、adjustment 與 metrics。新 version 仍必須完整、可獨立讀取，不能依賴 retired version 的 active status。

- [ ] Step 5: Validate persistence and business behavior

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_incremental_rebuild.py tests/test_gmv_refund_preflight.py tests/test_gmv_refund_repository.py tests/test_gmv_one_click_merge_integration.py
  ~~~

  驗證 current state、immutable observations、total/paid summaries、status change no-duplicate、TT exclusion、over-refund cap 與 active version activation gate。

- [ ] Step 6: Review and commit

  ~~~bash
  git add backend/services/upload_preflight_service.py backend/services/gmv_refund_service.py backend/services/gmv_refund_repository.py tests/test_gmv_incremental_rebuild.py tests/test_gmv_refund_preflight.py tests/test_gmv_refund_repository.py tests/test_gmv_one_click_merge_integration.py
  git commit -m "feat: rebuild affected refund receipts incrementally"
  ~~~

---

## 8. Task 7: Upload/cache observability and Streamlit read model

**Outcome:** formal upload response、stability history 與 GMV UI 能顯示 rebuild stage、fast/fallback、equivalence 與 artifact readiness；UI 不重新計算資料。

**Files:**
- Modify: backend/services/upload_orchestrator_service.py
- Modify: backend/services/upload_profiling_service.py
- Modify: app_pages.py:GMV formal section
- Extend: tests/test_upload_orchestrator_service.py
- Extend: tests/test_upload_profiling_service.py
- Extend: tests/test_streamlit_gmv_formal_contract.py
- Extend: tests/test_streamlit_gmv_refund_contract.py

**Interfaces:**

保留 cacheState values，新增 bounded cacheBuild metadata：

~~~json
{
  "cacheState": "streamlit_rebuilt | invalidated | refresh_required | fallback",
  "cacheBuild": {
    "builderMode": "legacy | fast | fallback",
    "validationMode": "legacy | trusted_warm | shadow",
    "equivalenceStatus": "PASS | FAIL | NOT_RUN",
    "stageTimings": [],
    "fallbackReason": null,
    "activePointerSwapped": true
  }
}
~~~

- Consumes: Task 5 manifest、Task 6 delta summary、upload stage timings。
- Produces: API/Streamlit read-only status、stability history context、download readiness。

- [ ] Step 1: Write failing UI/response contract tests

  驗證 READY cache 直接顯示兩個 download buttons；fast failure 顯示 fallback reason 且上一個 READY artifact 仍可下載；building/failed 不觸發 _load_and_compute_cache()；refresh 後只讀 active pointer；stage timings 包含 preparation、facts、serialization、equivalence、publish。

- [ ] Step 2: Run tests to verify failure

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_upload_orchestrator_service.py tests/test_upload_profiling_service.py tests/test_streamlit_gmv_formal_contract.py tests/test_streamlit_gmv_refund_contract.py
  ~~~

- [ ] Step 3: Thread manifest evidence through upload response

  accepted_cache_rebuilder 仍由 orchestrator 呼叫，但必須返回 cache build result，不以 side effect 丟失 timing。history context 只保存 builderMode、validationMode、equivalenceStatus、shadowStatus、totalMs、fallbackReason、activePointerSwapped。

- [ ] Step 4: Keep Streamlit read path read-only

  GMV page load path 只呼叫 active version reader、active pointer/manifest reader、artifact checksum validation 與 download artifact reader；不得在 page render 中呼叫 raw Excel parser、full aggregation 或 cache builder。

- [ ] Step 5: Run focused tests

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_upload_orchestrator_service.py tests/test_upload_profiling_service.py tests/test_streamlit_gmv_formal_contract.py tests/test_streamlit_gmv_refund_contract.py tests/test_gmv_one_click_merge_integration.py
  ~~~

- [ ] Step 6: Review and commit

  ~~~bash
  git add backend/services/upload_orchestrator_service.py backend/services/upload_profiling_service.py app_pages.py tests/test_upload_orchestrator_service.py tests/test_upload_profiling_service.py tests/test_streamlit_gmv_formal_contract.py tests/test_streamlit_gmv_refund_contract.py
  git commit -m "feat: expose GMV rebuild readiness and timings"
  ~~~

---

## 9. Task 8: Rollout gates, full verification and formal acceptance

**Outcome:** fast path 在 isolated benchmark、formal main/runtime 與實際 Streamlit UI 都通過；rollback path 可驗證。

**Files:**
- Extend: tests/test_hermes_post_change_check.py
- Extend: tests/test_gmv_export_rollout.py
- Extend: tests/test_gmv_export_fast_ui_contract.py

- [ ] Step 1: Run targeted suite

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_pipeline_dtype_contract.py tests/test_gmv_rebuild_benchmark_service.py tests/test_gmv_export_intermediate_service.py tests/test_gmv_export_serializer_service.py tests/test_gmv_export_equivalence_service.py tests/test_gmv_manifest_performance_contract.py tests/test_gmv_incremental_rebuild.py tests/test_gmv_export_cache_service.py tests/test_gmv_export_fast_controller.py tests/test_gmv_export_rollout.py tests/test_gmv_one_click_merge_integration.py tests/test_streamlit_gmv_formal_contract.py tests/test_streamlit_gmv_refund_contract.py
  ~~~

  Expected: 0 failed；warning cleanup tests 使用 -W error::FutureWarning。

- [ ] Step 2: Run full pytest with warning gate

  ~~~bash
  .venv/bin/python -m pytest -q -p no:cacheprovider -W error::FutureWarning
  ~~~

  Expected: all tests passed，0 pandas FutureWarnings。若其他第三方 warning 存在，列出來源、數量與 scope。

- [ ] Step 3: Run isolated benchmark suite

  Task 2 的 fixture test 會在 pytest temporary directory 建立 isolated SQLite、active version 與 cache root；在合併後 main 執行：

  ~~~bash
  .venv/bin/python -m pytest -q tests/test_gmv_rebuild_benchmark_service.py -k production_sized_fixture
  ~~~

  該 test 對 legacy-cold、fast-cold、trusted-warm、status-change 各執行至少 3 samples，確認 semantic equivalence rate=100%、fast cold median 達到 spec target 或留下 evidence-based exception、peak RSS 在 1.5x legacy guard 內，且正式 cache/DB 未被 benchmark 改寫。

- [ ] Step 4: Run Review Agent and Hermes

  ~~~bash
  .venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
  ~~~

  Expected: overallStatus=pass；formal baseline、service identity、targeted pack、full verification 與 artifact boundary 均通過。

- [ ] Step 5: Run formal runtime acceptance

  ~~~bash
  .venv/bin/python scripts/system_manager.py status
  .venv/bin/python scripts/system_manager.py acceptance
  .venv/bin/python scripts/phase2j_baseline_check.py --db nbs_marketing_data.db
  ~~~

  Expected：Streamlit/API/Vue ready and identity matched；baseline status matched；formatted actual total=HKD 12,057,968；revenue scope unchanged。

- [ ] Step 6: Actual Streamlit UI acceptance

  使用已授權退款 Excel：

  ~~~text
  upload refund file
  -> preflight summary visible
  -> click upload/merge once
  -> active version/cache status visible
  -> total refund download succeeds
  -> paid refund download succeeds
  -> reload browser
  -> both downloads remain available without re-upload
  ~~~

  另外驗證 TT 退款轉團款不被正式扣減、退款中變已退款只更新受影響狀態，以及 fallback 時上一個 READY report 仍可下載。

- [ ] Step 7: Final findings-first review

  ~~~bash
  git diff --check
  git status --short
  ~~~

  Step 7 不產生新 implementation commit，只保留 verification evidence。若 Hermes contract test 需要新增 read-only gate，先在 test 中明確固定 contract，再另開小型 maintenance Task；不得在 final acceptance 中偷偷擴大 scope。任何 commit 前不得 stage 正式 SQLite、runtime cache、Excel、CSV、.superpowers/brainstorm/ 或 agent runtime artifacts。

---

## 10. Checkpoint Policy

每個 checkpoint 固定輸出：

~~~text
Checkpoint N
- scope completed:
- files changed:
- tests:
- warnings:
- Review:
- Hermes:
- formal data/database mutation:
- known fallback or blocker:
- next checkpoint:
~~~

執行順序固定為：

~~~text
Task 1 warning cleanup
  -> Task 2 benchmark evidence
  -> Task 3 shared intermediate
  -> Task 4 serializer/equivalence
  -> Task 5 manifest/pointer
  -> Task 6 incremental refund
  -> Task 7 UI/observability
  -> Task 8 full acceptance
~~~

Task 2 若證明瓶頸不是 aggregation，Task 3 仍需完成 preparation contract，但不得無證據擴大成大型 pipeline rewrite。Task 4 只有在 Task 3 facts fingerprint 穩定後才能進入 default/shadow rollout。

## 11. Definition of Done

- [ ] Spec requirements 逐項有對應 Task 與 test。
- [ ] 90 pandas FutureWarnings 已清除。
- [ ] Full pytest 全部通過。
- [ ] legacy 與 fast artifacts semantic equivalence PASS。
- [ ] cold rebuild benchmark 有 3+ samples、median/p95、peak RSS 與 stage timings。
- [ ] incremental refund status update 有完整 audit 與 no-duplicate evidence。
- [ ] active pointer 不會指向半成品。
- [ ] fallback 能保留上一個 READY generation。
- [ ] formal baseline 與 revenue scope unchanged。
- [ ] Hermes overall status=pass。
- [ ] 正式 Streamlit UI 可在 refresh 後直接下載 total/paid reports。
