# Data Export Serialization Acceleration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改變既有報表語義、業務規則或 SQLite 的前提下，讓三種完整 Data Export 共用 intermediate facts，並以 bounded serializer 降低首次報表生成等待時間。

**Architecture:** 保留 `_compute_export_workbooks()` 作為 legacy reference 與 fallback；以 `ExportIntermediateModel` 一次完成 normalization、classification 與共用 aggregates，再依 `all`、`no_writeoff`、`official` 產生 facts。每個 facts 交給 bounded serializer job，通過 semantic equivalence、checksum、schema、baseline 與 generation gates 後才以 versioned manifest atomic publish。

**Tech Stack:** Python 3、pandas、openpyxl、SQLite read-only frames、`concurrent.futures`、JSON manifest、ZIP、Streamlit、pytest。

**Spec:** `docs/superpowers/specs/2026-08-27-data-export-serialization-acceleration-design.md`

## Global Constraints

- 正式收入範圍固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite schema、原始業務資料、upload、rollback、baseline、revenue scope、Dashboard KPI、AI Forecast 或 WAPE。
- 既有 workbook sheet、欄位、檔名、排序與業務數值必須保持不變。
- legacy `_compute_export_workbooks()` 永遠保留為 reference 與 fallback。
- 只有 schema、checksum、semantic equivalence、baseline、generation identity 全部 PASS 才能發布 READY fast artifact。
- worker 不得讀 SQLite、Streamlit session state、Memory Hub 或 agent artifacts。
- Memory Hub 與 local agents 僅提供 read-only planning/context hints，不是資料或 rollout authority。
- 不新增外部服務、雲端 object storage、資料庫或 workflow control。
- 每個 Task 完成後先跑 targeted tests，再做 findings-first Review；完成整體後跑 full pytest、strict warnings、benchmark、Streamlit HTTP acceptance 與 Hermes。
- 保留既有未追蹤的 `.superpowers/brainstorm/` 與其他 spec/plan，不納入本功能 commit。

## Current State and Work Boundary

已完成並直接重用：

- `backend/services/export_intermediate_service.py`：已有 intermediate、scope enum、scope filtering 與 fingerprint；本計畫只擴充完整 facts。
- `backend/services/export_equivalence_service.py`：已有 XLSX canonicalization 與 bounded mismatch report。
- `backend/services/export_manifest_service.py`：已有 atomic artifact/manifest、checksum 與 ZIP 基礎。
- `backend/services/export_fast_path_service.py`：已有 bounded process pool、equivalence gate 與 legacy fallback。
- `backend/services/gmv_export_serializer_service.py`：已有 bounded serializer 與 timeout/parallel pattern。
- `scripts/run_gmv_ui_acceptance.py`：已有 bounded HTTP UI acceptance runner。

真正需要實作：

- 完整 report facts preparation 與 shared aggregate reuse。
- 一般 Data Export 的 serializer job adapter。
- per-artifact timing、package verification 與 manifest telemetry。
- Data Export Center 對 READY artifact 的完整接線。
- 固定 snapshot benchmark，證明 serialization 而非只有 aggregation 的改善。

---

### Task 1: Freeze Legacy Serialization Baseline

**Files:**
- Create: `tests/test_export_serialization_benchmark.py`
- Modify: `app_workflows.py` only if timing hook cannot be added without changing output
- Test: existing `tests/test_export_fast_path.py`, `tests/test_pipeline_preloaded_frames.py`

**Interfaces:**
- Consumes: `_compute_export_workbooks(raw_tour, raw_others)`。
- Produces: deterministic legacy artifact keys, sheet/data fingerprints and stage timing fields for later comparisons。

- [ ] **Step 1: Write the failing measurement contract test**

```python
def test_legacy_export_measurement_has_three_artifacts_and_stage_timings(fixture_frames):
    result = measure_legacy_export(fixture_frames.tour, fixture_frames.others)
    assert set(result.artifacts) == {"ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer"}
    assert all(result.artifacts[key].bytes_written > 0 for key in result.artifacts)
    assert result.timings["serialization_ms"] >= 0
```

- [ ] **Step 2: Run the test and confirm the missing helper**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serialization_benchmark.py`

Expected: FAIL because `measure_legacy_export` is not defined.

- [ ] **Step 3: Implement a read-only measurement helper**

Measure only elapsed durations and artifact sizes around the existing legacy builder. Do not alter the returned bytes, call SQLite, or persist the measurement to runtime/production paths.

- [ ] **Step 4: Verify legacy contracts**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serialization_benchmark.py tests/test_export_fast_path.py tests/test_pipeline_preloaded_frames.py`

Expected: all tests pass and the three legacy artifacts remain non-empty.

- [ ] **Step 5: Commit**

```bash
git add tests/test_export_serialization_benchmark.py app_workflows.py
git commit -m "test: freeze export serialization baseline"
```

### Task 2: Expand Shared Intermediate into Reusable Report Facts

**Files:**
- Modify: `backend/services/export_intermediate_service.py`
- Modify: `pipeline.py` only for a small pure helper that can be reused without changing workbook output
- Create/Modify: `tests/test_export_intermediate_service.py`

**Interfaces:**
- Consumes: `ExportIntermediateModel`, `ExportScope`。
- Produces:

```python
@dataclass(frozen=True, slots=True)
class DashboardReportFacts:
    scope_id: str
    tour: pd.DataFrame
    others: pd.DataFrame
    aggregates: Mapping[str, pd.DataFrame]
    schema_fingerprint: str
    data_fingerprint: str

def build_scope_report_facts(
    intermediate: ExportIntermediateModel,
    scope: ExportScope,
) -> DashboardReportFacts
```

- [ ] **Step 1: Add failing tests for shared work and immutability**

```python
def test_scope_facts_reuse_one_intermediate_and_include_all_required_aggregates(intermediate):
    facts = build_scope_report_facts(intermediate, ExportScope.OFFICIAL)
    assert facts.scope_id == "official"
    assert {"amount_by_date_branch", "quantity_by_date_branch"} <= set(facts.aggregates)
    assert facts.schema_fingerprint and facts.data_fingerprint

def test_scope_facts_do_not_mutate_intermediate(intermediate):
    before = intermediate.classified_tour.copy(deep=True)
    build_scope_report_facts(intermediate, ExportScope.NO_WRITEOFF)
    pd.testing.assert_frame_equal(before, intermediate.classified_tour)
```

- [ ] **Step 2: Run the focused tests and confirm missing facts contract**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_intermediate_service.py`

Expected: FAIL because `DashboardReportFacts` and `build_scope_report_facts` are not defined or required aggregates are absent.

- [ ] **Step 3: Implement facts from existing intermediate data**

Compute each reusable aggregate once from classified frames. Apply scope masks to facts inputs using the existing `掛賬核銷` and `TT 退款轉團款` constants. Return deep copies for downstream serializer isolation.

- [ ] **Step 4: Verify business scope regression**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_intermediate_service.py tests/test_pipeline_preloaded_frames.py tests/test_phase2_precheck_acceptance.py`

Expected: all pass; official facts exclude both formal-scope exclusions and preserve the baseline contract.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_intermediate_service.py pipeline.py tests/test_export_intermediate_service.py
git commit -m "perf: prepare reusable dashboard export facts"
```

### Task 3: Adapt a Bounded Serializer Job Contract

**Files:**
- Create: `backend/services/export_serializer_service.py`
- Create: `tests/test_export_serializer_service.py`
- Reuse: `backend/services/gmv_export_serializer_service.py` atomic write and timeout behavior where compatible

**Interfaces:**
- Consumes: `DashboardReportFacts` and existing workbook builder/schema adapter。
- Produces:

```python
@dataclass(frozen=True, slots=True)
class ExportSerializerJob:
    artifact_id: str
    scope_id: str
    facts: DashboardReportFacts
    target_path: Path
    schema_fingerprint: str
    data_fingerprint: str

def serialize_export_jobs_parallel(
    jobs: Sequence[ExportSerializerJob],
    *,
    max_workers: int = 3,
    timeout_seconds: float | None = None,
) -> tuple[SerializerResult, ...]
```

- [ ] **Step 1: Write failing tests**

Test six jobs (three scopes for the two report dimensions where applicable), deterministic result order, max worker cap, temporary XLSX replacement, timeout cancellation and no mutation of facts.

- [ ] **Step 2: Run focused tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serializer_service.py`

Expected: FAIL because the general serializer service does not exist.

- [ ] **Step 3: Implement bounded serialization**

Use a top-level picklable worker entrypoint. Each worker receives facts plus an explicit writer adapter, writes only to its temporary artifact path, validates XLSX sheet names, then returns artifact ID, bytes, duration and error. Cap workers at `min(max_workers, len(jobs), 3)` and support `max_workers=1` sequential fallback.

- [ ] **Step 4: Verify serializer safety**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serializer_service.py tests/test_gmv_export_serializer_service.py`

Expected: all pass; failed jobs never report READY and input facts remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_serializer_service.py tests/test_export_serializer_service.py
git commit -m "perf: add bounded export serializer jobs"
```

### Task 4: Connect Fast Controller to Shared Facts and Serializer

**Files:**
- Modify: `backend/services/export_fast_path_service.py`
- Modify: `app_workflows.py`
- Create/Modify: `tests/test_export_fast_path.py`

**Interfaces:**
- Consumes: `build_export_intermediate`, `build_scope_report_facts`, `serialize_export_jobs_parallel`。
- Produces: `ExportJobResult` with `intermediate_ms`, per-artifact `serialization_ms`, `equivalence_ms`, `package_ms`, `total_ms`, `worker_count`。

- [ ] **Step 1: Write failing controller tests**

Assert the intermediate builder is called once, candidate serializers receive facts rather than raw frames, all three legacy artifact keys are emitted, and a serializer exception returns `FALLBACK` without publishing a manifest.

- [ ] **Step 2: Run focused tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_fast_path.py`

Expected: FAIL because the controller still invokes the old candidate path and lacks per-artifact serialization telemetry.

- [ ] **Step 3: Implement the controller pipeline**

Implement this exact sequence:

```text
READY manifest lookup
→ build intermediate once
→ build three scope facts
→ bounded serializer jobs
→ legacy reference artifacts
→ semantic equivalence
→ checksum/schema/package validation
→ atomic READY manifest
```

Keep the legacy builder as the reference and fallback. Do not allow a download request to invoke this controller.

- [ ] **Step 4: Verify equivalence and fallback**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_fast_path.py tests/test_export_equivalence_service.py tests/test_export_manifest_service.py`

Expected: all pass; mismatch, timeout and invalid artifact paths return fallback with the old active pointer untouched.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_fast_path_service.py app_workflows.py tests/test_export_fast_path.py
git commit -m "perf: connect export controller to shared facts"
```

### Task 5: Complete Manifest Telemetry, ZIP Verification and Cache Read

**Files:**
- Modify: `backend/services/export_manifest_service.py`
- Modify: `backend/services/export_fast_path_service.py`
- Create/Modify: `tests/test_export_manifest_service.py`

**Interfaces:**
- Consumes: serializer results and equivalence report。
- Produces: `export-manifest-v2` with stage timings, artifact fingerprints, package checksum and bounded fallback reason.

- [ ] **Step 1: Write failing manifest tests**

Assert manifest contains `telemetry.intermediate_ms`, `telemetry.serialization_ms`, `telemetry.equivalence_ms`, `telemetry.package_ms`, `telemetry.total_ms`, `worker_count`, and that ZIP contents are checked before READY publication.

- [ ] **Step 2: Run tests to verify the missing fields**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_manifest_service.py`

Expected: FAIL because the existing manifest omits the new telemetry/package verification fields.

- [ ] **Step 3: Implement manifest and package verification**

Write artifacts to job staging, verify required workbook names and ZIP entries, write `equivalence-report.json`, then atomically replace the manifest last. Reject path traversal and any raw SQLite/Excel/log payload in package members.

- [ ] **Step 4: Verify failure preservation**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_manifest_service.py tests/test_gmv_export_cache_service.py tests/test_export_fast_path.py`

Expected: all pass; malformed artifact/package/checksum never replaces the prior READY pointer.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_manifest_service.py backend/services/export_fast_path_service.py tests/test_export_manifest_service.py
git commit -m "feat: add export telemetry and package verification"
```

### Task 6: Wire Data Export Center to Verified READY Artifacts

**Files:**
- Modify: `app_workflows.py:_ensure_export_workbooks`
- Modify: `app_pages.py` Data Export Center
- Create: `tests/test_streamlit_export_serialization_contract.py`

**Interfaces:**
- Consumes: verified manifest, individual XLSX artifacts and ZIP package。
- Produces: UI states `PREPARING`, `VERIFYING`, `READY`, `FALLBACK`, `FAILED`。

- [ ] **Step 1: Write failing UI contract tests**

Assert initial page does not serialize, READY exposes one ZIP and the three existing XLSX filenames, downloads only read verified paths, and fallback preserves legacy downloads.

- [ ] **Step 2: Run the UI tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_streamlit_export_serialization_contract.py tests/test_export_fast_ui_contract.py`

Expected: FAIL until Data Export uses the verified manifest/package path.

- [ ] **Step 3: Implement status-driven UI**

Show bounded progress text and telemetry summary. Make `一鍵下載完整報表包 ZIP` the primary action and retain individual XLSX buttons. On refresh, perform manifest lookup only. Display a bounded fallback reason without raw data.

- [ ] **Step 4: Verify UI contracts**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_streamlit_export_serialization_contract.py tests/test_streamlit_gmv_formal_contract.py tests/test_export_fast_ui_contract.py`

Expected: all pass; download handlers contain no call to `_compute_export_workbooks()`.

- [ ] **Step 5: Commit**

```bash
git add app_workflows.py app_pages.py tests/test_streamlit_export_serialization_contract.py
git commit -m "feat: serve verified export artifacts from Data Export Center"
```

### Task 7: Production-Shaped Serialization Benchmark

**Files:**
- Modify: `scripts/benchmark_gmv_page_load.py` or create `scripts/benchmark_data_export_serialization.py`
- Create: `tests/test_export_serialization_benchmark.py` benchmark contract cases

**Interfaces:**
- Consumes: disposable fixed SQLite-shaped frames, fixed rules fingerprint and identical machine run。
- Produces: bounded JSON benchmark with legacy/fast timings, artifact sizes, worker count, peak RSS, equivalence status and cache-hit latency。

- [ ] **Step 1: Write benchmark gate tests**

Assert output schema includes `intermediate_ms`, `serialization_ms`, `equivalence_ms`, `package_ms`, `total_ms`, `cache_hit_ms`, `equivalence_status`, `database_mutated`, and `formal_scope`.

- [ ] **Step 2: Run contract tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serialization_benchmark.py`

Expected: FAIL until the benchmark emits the complete schema.

- [ ] **Step 3: Implement isolated benchmark runner**

Use only disposable temp directories and synthetic or copied read-only fixtures. Run legacy sequential serialization and fast shared-intermediate serialization against the same frames. Verify `database_mutated == false` and compare all three artifact semantics.

- [ ] **Step 4: Run benchmark matrix**

Run: `PYTHONPATH=. ./.venv/bin/python scripts/benchmark_data_export_serialization.py --samples 3 --workers 1,2,3`

Expected: equivalence PASS for every sample; cache hit preparation below 1 second; serializer improvement is reported separately from total job time.

- [ ] **Step 5: Commit**

```bash
git add scripts/benchmark_data_export_serialization.py tests/test_export_serialization_benchmark.py
git commit -m "test: benchmark shared export serialization"
```

### Task 8: Rollout, Full Verification and Hermes Evidence

**Files:**
- Modify: `tests/test_export_rollout.py` only if a new serialization gate is missing
- Evidence: temporary benchmark output and existing read-only Hermes artifacts

**Interfaces:**
- Consumes: export manifest, rollout mode and benchmark JSON。
- Produces: explicit rollout decision: `shadow`, `opt_in`, `default` or `disabled`。

- [ ] **Step 1: Run complete focused export suite**

Run:

```bash
PYTHONPATH=. ./.venv/bin/pytest -q \
  tests/test_export_intermediate_service.py \
  tests/test_export_equivalence_service.py \
  tests/test_export_serializer_service.py \
  tests/test_export_manifest_service.py \
  tests/test_export_fast_path.py \
  tests/test_export_rollout.py \
  tests/test_streamlit_export_serialization_contract.py
```

Expected: all pass with zero semantic mismatches.

- [ ] **Step 2: Run strict full pytest**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q -W error::FutureWarning`

Expected: all tests pass and no FutureWarning is emitted.

- [ ] **Step 3: Run compile and diff checks**

Run: `PYTHONPATH=. ./.venv/bin/python -m compileall -q backend scripts && git diff --check`

Expected: exit code 0.

- [ ] **Step 4: Run actual HTTP UI acceptance**

Run the bounded runner against the running HTTP Streamlit URL with disposable bounded evidence. Verify HTTP 200, READY status, ZIP and three XLSX artifacts, refresh version equality, and no raw business rows in evidence.

- [ ] **Step 5: Run Hermes**

Run: `PYTHONPATH=. ./.venv/bin/python scripts/hermes_post_change_check.py`

Expected: `Overall status: PASS`; report any pre-existing system-monitor degraded cache signature separately without changing production DB/cache.

- [ ] **Step 6: Perform final findings-first review**

Review the full diff against the approved spec. Confirm no SQLite/schema/baseline/revenue-scope changes, no raw data in evidence/package, and no download-time recomputation.

- [ ] **Step 7: Commit the final verification-only changes if any**

```bash
git status --short
git diff --check
```

Only commit an approved allowlist change required by a failed verification; do not commit runtime, benchmark output, SQLite, raw Excel or generated cache artifacts.

## Plan Self-Review

- [x] Existing intermediate/equivalence/manifest/fast-controller work is marked complete and not duplicated blindly.
- [x] Remaining work covers facts, bounded serializers, telemetry, UI connection, benchmark and rollout.
- [x] Every Task has files, interfaces, failing-test or verification action, expected result and commit boundary.
- [x] Legacy path remains reference/fallback throughout.
- [x] No step changes SQLite schema, formal scope, baseline or business rules.
- [x] Performance acceptance separates serialization improvement from total job time.
- [x] Memory Hub/local agents remain read-only context helpers and cannot affect business truth or rollout authority.
