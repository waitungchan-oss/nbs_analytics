# Cold Rebuild Reference 與 Equivalence 加速 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改變報表語義、SQLite、baseline 或正式收入規則的前提下，重用已驗證 trusted reference，並以 digest-first equivalence 降低 cold rebuild 等待時間。

**Architecture:** 保留 `_compute_export_workbooks()` 作為首次 reference materialization 與 fail-closed fallback。新增 deployment-local `trusted-reference-v1` snapshot 與 atomic active pointer；snapshot identity 綁定 source、generation、rules、schema、pipeline fingerprints。candidate 先做 bounded digest gate，只有 digest 不一致才進入既有 canonical deep diff，所有 gate 通過後才 atomic swap export manifest 與 trusted reference pointer。

**Tech Stack:** Python 3、pandas、openpyxl、JSON、SHA-256、SQLite read-only frames、Streamlit、pytest、Hermes。

**Spec:** `docs/superpowers/specs/2026-08-27-cold-rebuild-reference-validation-acceleration-design.md`

## Global Constraints

- 正式收入範圍固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite schema、正式業務資料、baseline、rollback、upload、revenue scope、Dashboard KPI、AI Forecast、WAPE 或既有 workbook schema。
- legacy `_compute_export_workbooks()` 永遠保留為 reference materialization 與 fallback。
- trusted snapshot 只可存在於 deployment-local derived cache，不得回寫 SQLite，不得包含 raw Excel、SQLite、customer/payment raw data、secrets 或完整 logs。
- Memory Hub、Context/Review/Hermes agents 僅 read-only；不得成為 business truth、reference authority 或 rollout authority。
- 每一 Task 使用 TDD：先寫 failing test，再做最小實作，再跑 targeted tests、findings-first Review。
- 未經本計畫的 benchmark PASS，不得把 rollout mode 從既有設定升級為 DEFAULT。

## Current State and Existing Interfaces

已完成並直接重用：

- `backend/services/export_fast_path_service.py:build_fast_export_job_from_facts()`：facts、bounded serializer、legacy reference、equivalence、manifest orchestration。
- `backend/services/export_equivalence_service.py:canonicalize_workbook()`、`compare_workbooks()`、`compare_export_sets()`：既有 deep semantic gate。
- `backend/services/export_manifest_service.py:publish_export_manifest()`、`load_ready_export_manifest()`、`verify_export_package()`：manifest/package verification。
- `app_workflows.py:_compute_export_workbooks()`、`_build_fast_export_job_for_cache()`：legacy reference 與 production UI controller。
- `scripts/benchmark_data_export_serialization.py`：fixed snapshot/production-shaped timing evidence。

本計畫真正新增：

- trusted reference snapshot/cache service 與 atomic pointer。
- digest-first equivalence API。
- controller 的 reference lookup/materialization/fallback wiring。
- manifest telemetry 與 benchmark scenarios。

---

### Task 1: Freeze Reference/Equivalence Stage Baseline

**Files:**
- Modify: `backend/services/export_benchmark_service.py`
- Modify: `scripts/benchmark_data_export_serialization.py`
- Modify: `tests/test_export_serialization_benchmark.py`

**Interfaces:**
- Consumes: existing `_compute_export_workbooks()` and `build_fast_export_job_from_facts()`.
- Produces: benchmark fields `reference_lookup_ms`, `reference_materialize_ms`, `equivalence_digest_ms`, `equivalence_deep_diff_ms`, `cache_hit_ms`, `database_mutated`, `equivalence_status`.

- [ ] **Step 1: Write the failing benchmark schema test**

```python
def test_benchmark_reports_reference_and_equivalence_stages():
    report = build_benchmark_report(*_frames(), samples=1, worker_count=1)
    assert {
        "reference_lookup_ms", "reference_materialize_ms",
        "equivalence_digest_ms", "equivalence_deep_diff_ms", "cache_hit_ms",
    } <= set(report["fast"])
```

- [ ] **Step 2: Run the focused test**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serialization_benchmark.py::test_benchmark_reports_reference_and_equivalence_stages`

Expected: FAIL because the stage fields are not yet emitted.

- [ ] **Step 3: Add measurement-only fields**

Extend the benchmark result projection without changing export bytes, DB access, or cache behavior. Keep legacy total and fast total separate.

- [ ] **Step 4: Verify the contract**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serialization_benchmark.py`

Expected: PASS; `database_mutated` remains `False`.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_benchmark_service.py scripts/benchmark_data_export_serialization.py tests/test_export_serialization_benchmark.py
git commit -m "test: instrument cold export reference stages"
```

### Task 2: Implement Trusted Reference Identity and Snapshot Cache

**Files:**
- Create: `backend/services/export_reference_cache_service.py`
- Create: `tests/test_export_reference_cache_service.py`

**Interfaces:**
- Consumes: source fingerprint, generation token, rules/schema/pipeline fingerprints, canonical workbooks.
- Produces: `TrustedReferenceIdentity`, `TrustedReferenceSnapshot`, `load_trusted_reference()`, `materialize_trusted_reference()`, `publish_trusted_reference()`.

- [ ] **Step 1: Write failing exact-contract tests**

```python
def test_snapshot_round_trip_is_identity_bound_and_bounded(tmp_path):
    identity = TrustedReferenceIdentity("source", "generation", "rules", "schema", "pipeline")
    snapshot = materialize_trusted_reference(tmp_path, identity, _artifacts())
    publish_trusted_reference(tmp_path, snapshot)
    assert load_trusted_reference(tmp_path, identity) == snapshot
    assert load_trusted_reference(tmp_path, replace(identity, rules_fingerprint="other")) is None
```

- [ ] **Step 2: Run the test to confirm missing service**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_reference_cache_service.py`

Expected: FAIL because the service and contract do not exist.

- [ ] **Step 3: Implement immutable snapshot and pointer validation**

Use JSON metadata plus canonical artifact/digest payloads under a cache-root-relative directory. Reject unknown schema, identity mismatch, path escape, symlink, non-regular files, checksum mismatch and missing artifact. Publish via temporary file and `os.replace` only after complete validation.

- [ ] **Step 4: Verify failure safety**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_reference_cache_service.py`

Expected: PASS; corrupt or interrupted publication leaves the previous active pointer readable.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_reference_cache_service.py tests/test_export_reference_cache_service.py
git commit -m "feat: add identity-bound trusted reference cache"
```

### Task 3: Add Digest-First Equivalence

**Files:**
- Modify: `backend/services/export_equivalence_service.py`
- Modify: `tests/test_export_equivalence_service.py`

**Interfaces:**
- Consumes: XLSX bytes and trusted/candidate artifact mappings.
- Produces: `build_workbook_metric_digest()` and `compare_export_digests()`; existing deep diff remains unchanged and callable on digest mismatch.

- [ ] **Step 1: Write failing digest tests**

```python
def test_equal_semantic_workbooks_have_equal_metric_digest():
    first, second = _equivalent_workbooks_with_different_xlsx_metadata()
    assert compare_export_digests(
        {"ex": build_workbook_metric_digest(first)},
        {"ex": build_workbook_metric_digest(second)},
    )

def test_digest_mismatch_does_not_hide_deep_diff():
    assert not compare_export_digests({"ex": {"row_count": 1}}, {"ex": {"row_count": 2}})
```

- [ ] **Step 2: Run the tests and confirm missing helpers**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_equivalence_service.py`

Expected: FAIL until the digest functions exist.

- [ ] **Step 3: Implement bounded canonical digest**

Reuse existing canonical value normalization. Include sheet order, headers, row counts, stable-key hash and Decimal-normalized money/quantity totals. Do not remove or weaken `compare_workbooks()`.

- [ ] **Step 4: Verify deep-diff fallback**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_equivalence_service.py tests/test_export_intermediate_service.py`

Expected: PASS; exact mismatches still return bounded examples.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_equivalence_service.py tests/test_export_equivalence_service.py
git commit -m "perf: add digest-first export equivalence gate"
```

### Task 4: Integrate Trusted Reference Lookup and Atomic Swap

**Files:**
- Modify: `backend/services/export_fast_path_service.py`
- Modify: `backend/services/export_manifest_service.py`
- Modify: `tests/test_export_fast_controller.py`
- Modify: `tests/test_export_manifest_service.py`

**Interfaces:**
- Consumes: `TrustedReferenceIdentity`, snapshot cache, digest gate, existing candidate artifacts.
- Produces: fail-closed reference lookup/materialization path with telemetry and atomic publication of manifest/pointer.

- [ ] **Step 1: Write failing controller tests**

```python
def test_same_identity_reuses_trusted_reference_without_legacy_builder(tmp_path):
    calls = []
    result = build_fast_export_job_from_facts(
        raw_tour,
        raw_others,
        generation_token="generation-1",
        rules_fingerprint="rules-1",
        export_schema_version="schema-1",
        cache_root=tmp_path,
        reference_builder=lambda *_: calls.append(1),
        facts_builder=build_facts,
        writer=write_facts,
        trusted_reference=existing_snapshot,
    )
    assert result.status == "READY"
    assert calls == []
    assert result.timings["reference_lookup_ms"] >= 0
```

- [ ] **Step 2: Run focused tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_fast_controller.py tests/test_export_manifest_service.py`

Expected: FAIL because the controller has no trusted-reference path.

- [ ] **Step 3: Implement lookup, legacy materialization and gates**

On identity HIT, load the canonical reference from cache. On miss, call the existing legacy builder once, materialize/publish the snapshot only after candidate equivalence and package gates pass. Do not update either active pointer on failure; return bounded fallback reason.

- [ ] **Step 4: Verify atomic failure behavior**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_fast_controller.py tests/test_export_manifest_service.py tests/test_export_reference_cache_service.py`

Expected: PASS; previous READY manifest and trusted pointer remain unchanged after injected failure.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_fast_path_service.py backend/services/export_manifest_service.py tests/test_export_fast_controller.py tests/test_export_manifest_service.py
git commit -m "perf: reuse trusted export references safely"
```

### Task 5: Add Production Telemetry and Benchmark Scenarios

**Files:**
- Modify: `scripts/benchmark_data_export_serialization.py`
- Modify: `tests/test_export_serialization_benchmark.py`
- Create: `tests/test_export_reference_benchmark_contract.py`

**Interfaces:**
- Consumes: fixed production-shaped frames and disposable cache roots.
- Produces: separate first materialization, same-identity HIT, stale identity, affected-only and READY cache-hit reports.

- [ ] **Step 1: Write scenario contract tests**

```python
def test_benchmark_distinguishes_materialization_hit_and_stale_scenarios():
    report = build_reference_benchmark_report(*_frames())
    assert {"first_materialization", "same_identity_hit", "stale_identity"} <= set(report)
    assert report["same_identity_hit"]["reference_status"] == "HIT"
```

- [ ] **Step 2: Run and confirm missing scenarios**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_reference_benchmark_contract.py`

Expected: FAIL until scenario output exists.

- [ ] **Step 3: Implement disposable benchmark scenarios**

Use the same frames/rules for each scenario, never the production DB path for writes. Record timings, peak RSS if available, equivalence status and `database_mutated=False`. Do not print raw rows or persist generated reports in the repo.

- [ ] **Step 4: Verify benchmark gate calculations**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_serialization_benchmark.py tests/test_export_reference_benchmark_contract.py`

Expected: PASS; a stale identity never reports a cache HIT.

- [ ] **Step 5: Commit**

```bash
git add scripts/benchmark_data_export_serialization.py tests/test_export_serialization_benchmark.py tests/test_export_reference_benchmark_contract.py
git commit -m "test: benchmark trusted reference reuse"
```

### Task 6: Wire Data Export UI Telemetry and Cache-Hit Download

**Files:**
- Modify: `app_workflows.py`
- Modify: `app_pages.py`
- Modify: `streamlit_rendering.py`
- Modify: `tests/test_export_fast_ui_contract.py`
- Modify: `tests/test_streamlit_export_serialization_contract.py`

**Interfaces:**
- Consumes: manifest `reference` status and telemetry.
- Produces: bounded UI states `REFERENCE HIT`, `REFERENCE MATERIALIZED`, `DEEP DIFF SKIPPED`, `FALLBACK`; download remains read-only.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_ready_ui_surfaces_reference_hit_and_deep_diff_status():
    source = (ROOT / "streamlit_rendering.py").read_text(encoding="utf-8")
    assert "REFERENCE HIT" in source
    assert "DEEP DIFF SKIPPED" in source
```

- [ ] **Step 2: Run focused UI tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_fast_ui_contract.py tests/test_streamlit_export_serialization_contract.py`

Expected: FAIL until telemetry is displayed.

- [ ] **Step 3: Implement bounded status rendering**

Show only status, durations, artifact counts and bounded reason codes. On READY download, read verified package bytes; never call legacy builder, reference materializer or serializer from the download handler.

- [ ] **Step 4: Verify UI no-recompute contract**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_fast_ui_contract.py tests/test_streamlit_export_serialization_contract.py tests/test_streamlit_gmv_formal_contract.py`

Expected: PASS; refresh and download use manifest lookup/read-only artifacts.

- [ ] **Step 5: Commit**

```bash
git add app_workflows.py app_pages.py streamlit_rendering.py tests/test_export_fast_ui_contract.py tests/test_streamlit_export_serialization_contract.py
git commit -m "feat: expose trusted reference export telemetry"
```

### Task 7: Rollout Gate and Stale/Failure Matrix

**Files:**
- Modify: `backend/services/export_fast_path_service.py`
- Modify: `tests/test_export_rollout.py`
- Create: `tests/test_export_reference_rollout.py`

**Interfaces:**
- Consumes: benchmark scenario report, manifest/reference status and `ExportRolloutMode`.
- Produces: explicit decision among `shadow`, `opt_in`, `default`, `disabled`; no implicit promotion.

- [ ] **Step 1: Write failing rollout matrix tests**

```python
def test_reference_cache_failure_never_promotes_default():
    decision = decide_reference_rollout({"equivalence_status": "PASS", "reference_status": "INVALID"})
    assert decision.mode in {"shadow", "opt_in"}
```

- [ ] **Step 2: Run the matrix tests**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_reference_rollout.py tests/test_export_rollout.py`

Expected: FAIL until the explicit gate exists.

- [ ] **Step 3: Implement fail-closed rollout decision**

Require equivalence PASS, database_mutated false, stale/corrupt count zero, same-identity HIT benchmark PASS and no unresolved fallback before allowing DEFAULT. Keep existing environment mode as the upper bound.

- [ ] **Step 4: Verify all failure cases**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_export_reference_rollout.py tests/test_export_rollout.py tests/test_export_fast_controller.py`

Expected: PASS; invalid reference always uses legacy/fallback.

- [ ] **Step 5: Commit**

```bash
git add backend/services/export_fast_path_service.py tests/test_export_rollout.py tests/test_export_reference_rollout.py
git commit -m "feat: gate trusted reference rollout"
```

### Task 8: Full Verification, Production-Shaped Acceptance and Hermes

**Files:**
- Modify: no production data files.
- Evidence: disposable benchmark output and read-only Hermes output only.

**Interfaces:**
- Consumes: all reference cache, equivalence, controller, UI and rollout contracts.
- Produces: verified benchmark evidence and explicit rollout recommendation.

- [ ] **Step 1: Run focused reference/export suite**

```bash
PYTHONPATH=. ./.venv/bin/pytest -q \
  tests/test_export_reference_cache_service.py \
  tests/test_export_equivalence_service.py \
  tests/test_export_fast_controller.py \
  tests/test_export_manifest_service.py \
  tests/test_export_rollout.py \
  tests/test_export_reference_rollout.py \
  tests/test_export_serialization_benchmark.py \
  tests/test_export_fast_ui_contract.py \
  tests/test_streamlit_export_serialization_contract.py
```

Expected: PASS; digest short-circuit and deep-diff fallback both covered.

- [ ] **Step 2: Run strict full pytest**

Run: `PYTHONPATH=. ./.venv/bin/pytest -q -W error::FutureWarning`

Expected: all tests pass with no FutureWarning failure.

- [ ] **Step 3: Run production-shaped scenarios**

Run each worker count separately against the fixed snapshot:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/benchmark_data_export_serialization.py --samples 1 --workers 1 --output /tmp/nbs-export-workers-1.json
PYTHONPATH=. ./.venv/bin/python scripts/benchmark_data_export_serialization.py --samples 1 --workers 2 --output /tmp/nbs-export-workers-2.json
PYTHONPATH=. ./.venv/bin/python scripts/benchmark_data_export_serialization.py --samples 1 --workers 3 --output /tmp/nbs-export-workers-3.json
```

Expected: all semantic equivalence PASS, `database_mutated=false`; compare first materialization, same-identity HIT and stale rebuild separately.

- [ ] **Step 4: Run compile/diff and HTTP UI acceptance**

```bash
PYTHONPATH=. ./.venv/bin/python -m compileall -q backend scripts
git diff --check
PYTHONPATH=. ./.venv/bin/python scripts/run_gmv_ui_acceptance.py --url http://127.0.0.1:8502/ --fixture-root "$FIXTURE_ROOT" --evidence "$FIXTURE_ROOT/evidence.json"
```

Expected: compile/diff exit 0, HTTP 200 and bounded UI evidence PASS. Fixture root must come from Python `tempfile.gettempdir()` so the macOS `/private` path guard is respected.

- [ ] **Step 5: Run Hermes**

Run: `PYTHONPATH=. ./.venv/bin/python scripts/hermes_post_change_check.py`

Expected: `Overall status: PASS`; report pre-existing system-monitor signature drift separately and do not modify production DB/cache to make it green.

- [ ] **Step 6: Findings-first review and final evidence**

Review the full diff against the spec. Confirm no SQLite/schema/baseline/revenue-scope changes, no raw business data in snapshot/package/evidence, atomic pointer preservation on failure, and no download-time recomputation.

- [ ] **Step 7: Commit only required verification fixes**

```bash
git status --short
git diff --check
```

Do not commit benchmark JSON, runtime cache, SQLite, raw Excel, or temporary UI fixtures.

## Plan Self-Review

- [x] The plan separates first legacy materialization from same-identity reference HIT.
- [x] Existing deep semantic comparison remains the fallback; digest cannot hide mismatches.
- [x] Atomic trusted pointer and export manifest behavior is explicitly tested.
- [x] Stale identity, corruption, path escape, checksum failure and worker failure are covered.
- [x] Performance acceptance distinguishes reference, digest, deep diff, serialization and cache-hit time.
- [x] UI, benchmark, full pytest, strict warning and Hermes evidence are included.
- [x] No task changes SQLite, baseline, business rules, revenue scope or workbook schema.
