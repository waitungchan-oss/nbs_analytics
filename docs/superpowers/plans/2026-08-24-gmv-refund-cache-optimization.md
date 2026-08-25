# GMV Refund Active-Version Cache Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 在不改變退款業務結果、active version 或既有 11 個 cache artifacts 的前提下，透過 shared preparation、一次性 facts aggregation、bounded parallel serialization 與 semantic fallback，縮短新退款 Excel 合併後的 cache rebuild 時間。

**Architecture:** 保留 build_gmv_formal_artifacts() 的 lifecycle 與 _compute_gmv_exclusion_workbooks() 作為 legacy reference。新增 GMV 專用 preparation/facts/serializer controller；total 與 paid 維度各自保留 monetary adjustment，但共用 normalized base layer 與 scope preparation。6 個 workbook serializer 以 bounded workers 執行，所有 fast artifacts 先完成 semantic equivalence、schema、checksum、baseline gate，再 atomic publish v2 manifest；任何 failure 回退 legacy。

**Tech Stack:** Python 3、pandas、openpyxl 現有 writer、concurrent.futures、SQLite read-only frames、JSON manifest、pytest、Streamlit。

**Spec:** docs/superpowers/specs/2026-08-24-gmv-refund-cache-optimization-design.md

## Global Constraints

- 正式收入範圍固定為「不含掛賬核銷與 TT 退款轉團款」。
- 不修改 SQLite schema、原始業務資料、退款 ledger、active-version lifecycle、Dashboard KPI、AI Forecast、WAPE 或 baseline。
- 既有 11 個 GMV cache artifact filename、sheet、欄位、數值與雙維度語義必須保持不變。
- total 與 paid adjusted monetary values 不可互用；只可共用 immutable base preparation 與可證明等價的 derived facts。
- semantic equivalence 必須為 0 mismatch；.xlsx binary bytes 不要求相同。
- Fast path 未通過 equivalence、checksum、schema、baseline、memory 或 timeout gate 時必須 fail closed 並使用 legacy。
- Download/read path 不得重新掃描完整營收或重新序列化 workbook。
- Benchmark 只讀正式 DB，寫入指定 temporary cache；不得改正式 SQLite、active version、baseline 或正式 cache。
- 保留既有未提交 tests/test_streamlit_gmv_formal_contract.py 與 .superpowers/brainstorm/，不得納入 feature commit。
- 每個 Task 完成後先跑 targeted tests、findings-first review，再進下一 Task；最後才跑 full pytest、Hermes 與 UI acceptance。

## File Map

### Create

- backend/services/gmv_export_intermediate_service.py: GMV base preparation、scope masks、dimension facts contract。
- backend/services/gmv_export_serializer_service.py: artifact-level serialization jobs、bounded worker pool、timings。
- backend/services/gmv_export_equivalence_service.py: total/paid × three scopes 的 workbook canonical comparison。
- scripts/benchmark_gmv_refund_cache.py: read-only legacy/fast benchmark CLI。
- tests/test_gmv_export_intermediate_service.py
- tests/test_gmv_export_serializer_service.py
- tests/test_gmv_export_equivalence_service.py
- tests/test_gmv_export_benchmark.py

### Modify

- backend/services/gmv_refund_service.py: fast controller integration、v2 manifest metadata、legacy fallback。
- backend/services/gmv_export_cache_service.py: v2 manifest fields、v1 read compatibility、atomic publication validation。
- app_workflows.py: expose reusable dashboard report-facts builder without changing legacy builder output。
- app_pages.py: progress/fallback/cache status text only; keep existing upload and active-version lifecycle。
- pipeline.py: extract small pure preparation/facts helpers only; do not rewrite workbook schema。
- tests/test_gmv_export_cache_service.py
- tests/test_gmv_export_performance.py
- tests/test_gmv_one_click_merge_integration.py
- tests/test_streamlit_gmv_refund_contract.py

## Checkpoint 1: Freeze baseline and build benchmark harness

### Task 1: Add deterministic legacy contract and timing benchmark

**Files:**
- Create: scripts/benchmark_gmv_refund_cache.py
- Create: tests/test_gmv_export_benchmark.py
- Modify: tests/test_gmv_export_performance.py
- Read: backend/services/gmv_refund_service.py:build_gmv_formal_artifacts

**Interfaces:**
- run_gmv_cache_benchmark(*, db_path, version_id, cache_dir, mode, workers) -> dict
- JSON keys: mode, basePreparationMs, adjustmentMs, factsMs, serializationMs, equivalenceMs, cacheWriteMs, totalMs, artifactBytes, equivalenceStatus.

- [ ] Step 1: Write failing CLI contract tests

~~~python
def test_benchmark_writes_json_only_to_requested_cache(tmp_path, active_gmv_fixture):
    result = run_gmv_cache_benchmark(
        db_path=active_gmv_fixture.db_path,
        version_id=active_gmv_fixture.version_id,
        cache_dir=tmp_path / "cache",
        mode="legacy",
        workers=1,
    )
    assert result["mode"] == "legacy"
    assert result["totalMs"] >= 0
    assert result["artifactBytes"] > 0
    assert active_gmv_fixture.sqlite_sha256_before == active_gmv_fixture.sqlite_sha256_after()
~~~

- [ ] Step 2: Run and confirm failure

Run: .venv/bin/python -m pytest tests/test_gmv_export_benchmark.py -q

Expected: FAIL because run_gmv_cache_benchmark is not implemented.

- [ ] Step 3: Implement read-only legacy benchmark wrapper

Use GmvRefundRepository and database.load_all_data_from_db() only for reading. Pass a caller-provided temporary cache directory to build_gmv_formal_artifacts; never assign database.DB_FILE, call upload mutation functions, or publish to .nbs_runtime_cache.

- [ ] Step 4: Add CLI and stable JSON output

Support --db-path, --version-id, --cache-dir, --mode, --workers, and --output.

- [ ] Step 5: Run baseline tests

Run: .venv/bin/python -m pytest tests/test_gmv_export_benchmark.py tests/test_gmv_export_performance.py -q

Expected: PASS; no production cache or SQLite file changes.

### Task 2: Establish report semantics fixture and legacy fingerprints

**Files:**
- Create: tests/fixtures/gmv_export_semantic_fixture.py
- Modify: tests/test_gmv_export_performance.py
- Modify: tests/test_gmv_formal_export_contract.py

- [ ] Step 1: Include ordinary payment, 掛賬核銷, TT 退款轉團款, 待退款, 已退款, partial refund, over-refund, zero amount, duplicate source ID, empty optional columns, and specialist rows.
- [ ] Step 2: Assert the unchanged legacy path produces all 11 current artifact IDs.
- [ ] Step 3: Add read_gmv_workbook_semantics(content: bytes) -> dict for sheet names, headers, row counts, stable keys, and normalized values.
- [ ] Step 4: Run .venv/bin/python -m pytest tests/test_gmv_export_performance.py tests/test_gmv_formal_export_contract.py -q; expected PASS against current legacy behavior.

## Checkpoint 2: Shared preparation and report facts

### Task 3: Implement GMV base preparation model

**Files:**
- Create: backend/services/gmv_export_intermediate_service.py
- Create: tests/test_gmv_export_intermediate_service.py

**Interfaces:**

~~~python
@dataclass(frozen=True, slots=True)
class GmvExportBaseKey:
    version_id: str
    revenue_generation_token: str
    rules_fingerprint: str
    export_schema_version: str
    pipeline_fingerprint: str

@dataclass(frozen=True, slots=True)
class GmvExportBasePreparation:
    key: GmvExportBaseKey
    tour: pd.DataFrame
    others: pd.DataFrame
    scope_masks: Mapping[str, tuple[pd.Series, pd.Series]]
    source_fingerprints: Mapping[str, str]

def build_gmv_export_base_preparation(...) -> GmvExportBasePreparation
~~~

- [ ] Step 1: Test deterministic fingerprints, key invalidation, and input/session-state non-mutation.
- [ ] Step 2: Run .venv/bin/python -m pytest tests/test_gmv_export_intermediate_service.py -q; expected FAIL before implementation.
- [ ] Step 3: Implement normalization, derived columns, and explicit all, no_writeoff, official scope masks using existing revenue-scope constants.
- [ ] Step 4: Add bounded cache lookup under the job/cache directory; invalid or stale entries return a miss.
- [ ] Step 5: Run targeted tests; expected PASS.

### Task 4: Extract one facts build per dimension and scope

**Files:**
- Modify: pipeline.py
- Modify: app_workflows.py
- Modify: tests/test_gmv_export_intermediate_service.py

**Interfaces:**

~~~python
@dataclass(frozen=True, slots=True)
class GmvReportFacts:
    dimension: str
    scope_id: str
    sheets: Mapping[str, pd.DataFrame]
    row_counts: Mapping[str, int]
    schema_fingerprint: str
    data_fingerprint: str

def build_gmv_report_facts(
    *,
    adjusted_tour: pd.DataFrame,
    adjusted_others: pd.DataFrame,
    scope_id: str,
    rules: tuple[dict, list[str], list[str], list[str], list[str]],
    include_branch_salesperson_sheet: bool,
) -> GmvReportFacts
~~~

- [ ] Step 1: Test facts generation without XLSX bytes; monkeypatch pd.ExcelWriter to prove it is not called.
- [ ] Step 2: Compare facts to legacy workbook semantics for each fixture dimension × scope.
- [ ] Step 3: Extract pure facts generation while preserving existing sheet names, columns, transformations, sorting, and legacy builder.
- [ ] Step 4: Build all three scopes once per dimension using Task 3 masks; do not call the full builder three times in the fast path.
- [ ] Step 5: Run .venv/bin/python -m pytest tests/test_gmv_export_intermediate_service.py tests/test_official_export_workbook_contract.py tests/test_pipeline_preloaded_frames.py -q.

## Checkpoint 3: Serializer and equivalence

### Task 5: Implement artifact-level bounded serializers

**Files:**
- Create: backend/services/gmv_export_serializer_service.py
- Create: tests/test_gmv_export_serializer_service.py

**Interfaces:**

~~~python
def serialize_gmv_report_facts(facts, *, artifact_path: Path, writer: str = "openpyxl") -> SerializerResult

def serialize_gmv_workbooks_parallel(
    jobs: Sequence[SerializerJob],
    *,
    max_workers: int = 3,
    timeout_seconds: float | None = None,
) -> tuple[SerializerResult, ...]
~~~

- [ ] Step 1: Serialize one facts object and read it with openpyxl; assert exact sheet names, headers, canonical rows, and unchanged inputs.
- [ ] Step 2: Test bounded concurrency and deterministic artifact ordering.
- [ ] Step 3: Implement temporary-path serialization, validation, fsync, and atomic replace.
- [ ] Step 4: Add SERIALIZER_EXCEPTION, SERIALIZER_TIMEOUT, and SERIALIZER_INVALID_ARTIFACT result codes; never mark READY.
- [ ] Step 5: Run .venv/bin/python -m pytest tests/test_gmv_export_serializer_service.py -q.

### Task 6: Implement semantic equivalence service

**Files:**
- Create: backend/services/gmv_export_equivalence_service.py
- Create: tests/test_gmv_export_equivalence_service.py

**Interfaces:**

~~~python
def compare_gmv_workbook_semantics(
    *,
    reference_bytes: bytes,
    candidate_bytes: bytes,
    artifact_id: str,
) -> EquivalenceResult

def compare_gmv_artifact_sets(
    reference: Mapping[str, bytes],
    candidate: Mapping[str, bytes],
) -> EquivalenceReport
~~~

- [ ] Step 1: Test PASS when XLSX metadata differs but business data is equal.
- [ ] Step 2: Test FAIL for column, row, amount, quantity, and scope mismatches with bounded examples.
- [ ] Step 3: Implement canonical reading, Decimal 2-place money normalization, numeric quantity normalization, stable ordering, and Chinese field preservation.
- [ ] Step 4: Reject unknown dimension/scope and compare total/paid × all/no_writeoff/official.
- [ ] Step 5: Run .venv/bin/python -m pytest tests/test_gmv_export_equivalence_service.py -q.

## Checkpoint 4: Manifest, controller, fallback

### Task 7: Extend cache manifest with v2 metadata and v1 compatibility

**Files:**
- Modify: backend/services/gmv_export_cache_service.py
- Modify: tests/test_gmv_export_cache_service.py

- [ ] Step 1: Add v2 read/write tests and prove v1 remains readable by the active read model.
- [ ] Step 2: Add incomplete/stale manifest tests; expect CACHE_INVALID or CACHE_NOT_READY, never CURRENT.
- [ ] Step 3: Implement atomic artifact publication, path confinement, checksum verification, and manifest-last write.
- [ ] Step 4: Preserve all current artifact names and download behavior; add metadata only.
- [ ] Step 5: Run .venv/bin/python -m pytest tests/test_gmv_export_cache_service.py tests/test_gmv_export_performance.py -q.

### Task 8: Wire fast controller with legacy fallback

**Files:**
- Modify: backend/services/gmv_refund_service.py
- Modify: app_workflows.py
- Create: tests/test_gmv_export_fast_controller.py

**Interface:**

~~~python
def build_gmv_formal_artifacts_fast_or_legacy(
    *,
    repository: GmvRefundRepository,
    version_id: str,
    revenue_frames: RevenueFrames,
    rule_version: str,
    cache_dir: Path,
    worker_count: int = 3,
) -> GmvFormalArtifacts
~~~

- [ ] Step 1: Test fast success: equivalence PASS, manifest READY, all 11 artifacts present, builderMode fast.
- [ ] Step 2: Test serializer exception, mismatch, stale input, timeout, and memory gate; assert legacy is called and fast manifest is not READY.
- [ ] Step 3: Implement controller around existing independent total/paid adjustments; do not change repository writes, confirm_refund_batch, or active-version creation order.
- [ ] Step 4: Preserve returned GmvFormalArtifacts fields and current callers.
- [ ] Step 5: Run .venv/bin/python -m pytest tests/test_gmv_export_fast_controller.py tests/test_gmv_one_click_merge_integration.py -q.

## Checkpoint 5: Benchmark, UI, rollout controls

### Task 9: Add real benchmark comparison and writer/worker selection

**Files:**
- Modify: scripts/benchmark_gmv_refund_cache.py
- Modify: backend/services/gmv_export_serializer_service.py
- Modify: tests/test_gmv_export_benchmark.py

- [ ] Step 1: Benchmark legacy, fast sequential, and fast workers 2/3 on the same read-only active version and temporary cache.
- [ ] Step 2: Benchmark available xlsxwriter or write-only candidate; if unavailable, record not_available and do not add a dependency automatically.
- [ ] Step 3: Enable default fast mode only when equivalence is PASS and total duration improves at least 40% over the 334.6s baseline; otherwise keep shadow/opt-in.
- [ ] Step 4: Run .venv/bin/python -m pytest tests/test_gmv_export_benchmark.py -q.

### Task 10: Update Streamlit progress and cache-read contract

**Files:**
- Modify: app_pages.py
- Modify: tests/test_streamlit_gmv_refund_contract.py

- [ ] Step 1: Add source contracts for progress, fast/fallback status, elapsed time, total/paid downloads, and no build call on download rendering.
- [ ] Step 2: Show 準備 intermediate → 建立總退款／已退款 facts → 序列化報表 → 驗證 cache → 完成; preserve current upload and active-version copy.
- [ ] Step 3: Verify READY v1/v2 reload renders reports directly without another upload.
- [ ] Step 4: Run .venv/bin/python -m pytest tests/test_streamlit_gmv_refund_contract.py tests/test_streamlit_gmv_formal_contract.py -q.

## Checkpoint 6: Full verification

### Task 11: Targeted regression, Review, full pytest, Hermes, and UI acceptance

**Files:** No source changes expected; inspect evidence artifacts and dirty-file inventory.

- [ ] Step 1: Run GMV/export targeted suite covering intermediate, serializer, equivalence, cache, controller, integration, and performance tests.
- [ ] Step 2: Run revenue/baseline regression: phase2 precheck, dashboard/API contracts, official workbook contract, and rollback tests; confirm 2026-05 baseline HKD 12,057,968.
- [ ] Step 3: Run .venv/bin/python -m pytest -q; report passed, failed, skipped, and warnings separately.
- [ ] Step 4: Run findings-first Review Agent with implementation diff, targeted output, benchmark JSON, manifest sample, and dirty-file inventory.
- [ ] Step 5: Run .venv/bin/python scripts/hermes_post_change_check.py; confirm SQLite integrity, formal scope, baseline, service health, cache readiness, and no unexpected runtime writes.
- [ ] Step 6: Perform authorized Streamlit UI acceptance: upload refund Excel, merge once, verify total/paid reports/downloads, reload without upload, and verify cache-hit rendering does not rebuild.
- [ ] Step 7: Enable default fast mode only if semantic equivalence PASS, baseline PASS, full regression PASS, Hermes PASS, and benchmark achieves the 40% improvement without memory-cap violation; otherwise retain shadow/opt-in with the documented fallback reason.

## Handoff

After approval, execute one Task at a time in an isolated codex/ worktree. For each Task: tests first, failing-test run, minimal implementation, targeted tests, findings-first review, then next Task. Do not commit or merge until the user explicitly requests Git integration after all acceptance gates pass.

