# GMV Trusted Reference / Shadow Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改變正式 GMV 結果與 active pointer safety 的前提下，讓同一 input fingerprint 的 export rebuild 使用 trusted reference 做 warm fast validation，只有 cold miss 才執行一次 legacy seed。

**Architecture:** 新增獨立 trusted-reference service，使用 content fingerprint 對 reference manifest 做 exact lookup；fast candidate 在 private staging 產生，與 compact semantic reference 比對後才交給既有 generation-level cache publisher。Cold miss 以 legacy builder 建立一次 seed oracle，warm hit 不重建 legacy aggregation；任何 mismatch、stale、timeout 或 publication failure 都 fail closed 並保留舊 active pointer。

**Tech Stack:** Python 3、dataclasses、hashlib、JSON manifest、pandas、openpyxl、pytest、Streamlit、既有 cache publisher 與 Hermes。

**Spec:** `docs/superpowers/specs/2026-08-25-gmv-trusted-reference-shadow-validation-design.md`

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 必須維持 `HKD 12,057,968`。
- 不新增 SQLite table、migration、外部服務或新的 approval/workflow control。
- trusted reference 是 derived validation metadata，不是 SQLite、Dashboard、Forecast、WAPE 或 Memory Hub authority。
- active reader 只接受 verified generation manifest；reference miss/invalid 不得直接成為 `CURRENT`。
- canonical export artifact set 必須保持 exact 11 keys。
- 每個 Task 完成後先跑 targeted tests、findings-first Review，再由 Codex 跑 full verification/Hermes。

## File Map

- Create: `backend/services/gmv_trusted_reference_service.py` — content fingerprint、trusted manifest model、reference lookup/validation、atomic reference pointer。
- Modify: `backend/services/gmv_export_cache_service.py` — cache manifest metadata、reference provenance、active pointer metadata與retention validation。
- Modify: `backend/services/gmv_export_equivalence_service.py` — artifact semantic fingerprint contract與dynamic provenance normalization。
- Modify: `backend/services/gmv_export_serializer_service.py` — candidate staging identity與shadow publication gate輸入。
- Modify: `backend/services/gmv_refund_service.py` — warm trusted controller、cold legacy seed、fallback consistency。
- Modify: `app_pages.py` — trusted hit/seed/fallback progress與cache provenance顯示。
- Modify: `scripts/benchmark_gmv_refund_cache.py` — cold/warm modes、reference lookup/validation timing、實際manifest output。
- Create: `tests/test_gmv_trusted_reference_service.py` — reference schema、fingerprint、lookup與atomic pointer tests。
- Modify: `tests/test_gmv_export_equivalence_service.py` — semantic normalization與11-artifact tests。
- Modify: `tests/test_gmv_export_cache_service.py` — manifest/pointer/reference metadata與retention tests。
- Modify: `tests/test_gmv_export_fast_controller.py` — warm hit、cold seed、mismatch、fallback與no-legacy warm assertions。
- Modify: `tests/test_gmv_export_benchmark.py` — cold/warm benchmark schema與manifest-derived status。
- Modify: `tests/test_streamlit_gmv_formal_contract.py` — active download不重建、trusted status contract。

## Checkpoint 1: Input identity and trusted reference contract

### Task 1: Define deterministic content fingerprint and manifest model

**Files:**
- Create: `backend/services/gmv_trusted_reference_service.py`
- Create: `tests/test_gmv_trusted_reference_service.py`

**Interfaces:**
- `build_gmv_content_fingerprint(*, revenue_generation_token: str, refund_state_sha256: str, rule_version: str, export_schema_version: str, pipeline_fingerprint: str, serializer_version: str) -> str`
- `TrustedReferenceManifest.from_dict(payload: dict[str, object]) -> TrustedReferenceManifest`
- `TrustedReferenceManifest.to_dict() -> dict[str, object]`
- `validate_trusted_reference_manifest(manifest: TrustedReferenceManifest) -> None`

- [ ] Step 1: Write tests for deterministic identity, excluded `version_id`, exact artifact key contract, malformed fingerprints, unsorted keys, and source identity mismatch.
- [ ] Step 2: Run `pytest tests/test_gmv_trusted_reference_service.py -q`; expect failures for missing model/functions.
- [ ] Step 3: Implement compact dataclass model and canonical JSON fingerprint using the exact fields from the spec.
- [ ] Step 4: Run the focused test file and confirm all contract cases pass.
- [ ] Step 5: Run `git diff --check` and record the Task 1 diff for Review Agent.

## Checkpoint 2: Reference storage and atomic lookup

### Task 2: Add immutable reference repository with fail-closed pointer validation

**Files:**
- Modify: `backend/services/gmv_trusted_reference_service.py`
- Modify: `tests/test_gmv_trusted_reference_service.py`

**Interfaces:**
- `write_trusted_reference(*, cache_dir: Path, manifest: TrustedReferenceManifest) -> TrustedReferenceManifest`
- `load_trusted_reference(*, cache_dir: Path, content_fingerprint: str, expected_source: dict[str, str]) -> TrustedReferenceManifest | None`
- `invalidate_trusted_reference(*, cache_dir: Path, content_fingerprint: str, reason: str) -> None`

- [ ] Step 1: Add failing tests for unique staging, manifest-last write, reference pointer checksum, path traversal, stale source identity, and concurrent same-fingerprint seed.
- [ ] Step 2: Run the focused tests and confirm they fail at storage/lookup behavior.
- [ ] Step 3: Implement `references/<fingerprint>/generations/<uuid>` staging, `trusted.json` pointer swap, checksum validation, and atomic winner selection without SQLite writes.
- [ ] Step 4: Add retention invalidation when `seedProvenance.generationPath` is missing or checksum-invalid.
- [ ] Step 5: Run focused reference/cache tests and confirm the old active pointer remains readable after failed reference publication.

## Checkpoint 3: Semantic reference extraction

### Task 3: Store and compare compact artifact semantics

**Files:**
- Modify: `backend/services/gmv_export_equivalence_service.py`
- Modify: `backend/services/gmv_export_serializer_service.py`
- Modify: `tests/test_gmv_export_equivalence_service.py`
- Modify: `tests/test_gmv_export_serializer_service.py`

**Interfaces:**
- `build_gmv_artifact_semantic_records(artifacts: Mapping[str, bytes], kinds: Mapping[str, str]) -> dict[str, dict[str, object]]`
- `compare_gmv_artifact_semantics(reference: Mapping[str, dict[str, object]], candidate: Mapping[str, dict[str, object]]) -> EquivalenceResult`

- [ ] Step 1: Add tests proving version/timestamp provenance normalization does not create a false mismatch, while money, row, sheet, schema, or artifact-key changes do.
- [ ] Step 2: Run equivalence/serializer tests and capture the expected red failures.
- [ ] Step 3: Implement bounded semantic records using existing workbook reader and exact 11-key contract; never load a second full workbook set for warm validation.
- [ ] Step 4: Connect `SerializerPublicationGate` to `shadow_status` and reject non-PASS publication.
- [ ] Step 5: Run the focused equivalence/serializer suite and verify deterministic records.

## Checkpoint 4: Warm controller and cold seed fallback

### Task 4: Integrate trusted reference into the GMV controller

**Files:**
- Modify: `backend/services/gmv_refund_service.py`
- Modify: `backend/services/gmv_export_cache_service.py`
- Modify: `tests/test_gmv_export_fast_controller.py`
- Modify: `tests/test_gmv_export_cache_service.py`

**Interfaces:**
- `build_gmv_formal_artifacts_fast_or_legacy(..., validation_mode: str = "trusted_warm") -> GmvFormalArtifacts`
- Internal `_build_fast_candidate(...) -> FastCandidateResult`
- Internal `_seed_trusted_reference_once(...) -> TrustedReferenceManifest`

- [ ] Step 1: Add tests that a valid reference hit does not call `build_gmv_formal_artifacts`, while a reference miss calls legacy exactly once.
- [ ] Step 2: Add tests for candidate mismatch, invalid reference, serializer timeout, baseline failure, legacy failure, and active pointer preservation.
- [ ] Step 3: Run controller tests and confirm the new warm/cold assertions fail before implementation.
- [ ] Step 4: Implement lookup → fast candidate → shadow validation → publish; on miss use isolated legacy seed and persist reference only after equivalence PASS.
- [ ] Step 5: Ensure fallback returns the manifest that is actually readable from disk; never return a ready object after a failed same-key publication.
- [ ] Step 6: Run controller, cache, integration, and existing GMV/export targeted tests.

## Checkpoint 5: Cache manifest and UI integration

### Task 5: Surface validation provenance without changing active read behavior

**Files:**
- Modify: `backend/services/gmv_export_cache_service.py`
- Modify: `app_pages.py`
- Modify: `tests/test_streamlit_gmv_formal_contract.py`
- Modify: `tests/test_gmv_export_cache_service.py`

**Interfaces:**
- `GmvExportCacheManifest.content_fingerprint: str`
- `GmvExportCacheManifest.reference_id: str | None`
- `GmvExportCacheManifest.validation_mode: str`
- `GmvExportCacheManifest.shadow_status: str`

- [ ] Step 1: Add manifest round-trip tests and UI source-contract tests for trusted hit, legacy seed, fallback, and active-download cache reads.
- [ ] Step 2: Run the focused tests and verify old v1/v2 manifests still load as non-trusted legacy metadata.
- [ ] Step 3: Add fields to manifest and active pointer with defaults; preserve exact workbook names and download buttons.
- [ ] Step 4: Update Streamlit status text to show reference hit/seed/fallback and elapsed phases without exposing stack traces.
- [ ] Step 5: Run Streamlit contract, cache, and integration tests.

## Checkpoint 6: Benchmark and rollout controls

### Task 6: Add cold/warm benchmark and feature modes

**Files:**
- Modify: `scripts/benchmark_gmv_refund_cache.py`
- Modify: `tests/test_gmv_export_benchmark.py`
- Modify: `backend/services/gmv_refund_service.py`

**Interfaces:**
- CLI `--mode legacy|shadow|trusted_warm`
- Benchmark result fields: `contentFingerprint`, `referenceStatus`, `validationMode`, `shadowStatus`, `lookupMs`, `candidateMs`, `validationMs`, `publishMs`, `totalMs`.

- [ ] Step 1: Add tests rejecting hard-coded PASS and requiring actual manifest-derived status for cold and warm modes.
- [ ] Step 2: Run benchmark tests and confirm the new output fields are missing.
- [ ] Step 3: Implement `off`, `shadow`, `trusted_warm` mode handling; default remains `shadow` until gates pass.
- [ ] Step 4: Run fixture cold/warm benchmark twice and assert warm run reuses the same content fingerprint and does not call legacy builder.
- [ ] Step 5: Add a production read-only benchmark command under `.nbs_agent_runtime/benchmarks`, never under `.nbs_runtime_cache`.

## Checkpoint 7: Full verification and acceptance

### Task 7: Regression, Hermes, and UI acceptance

**Files:**
- Modify: `.nbs_agent_runtime/reports/trusted-reference-verification.json` (ignored evidence only)
- Modify: `.nbs_agent_runtime/reports/trusted-reference-review.json` (ignored evidence only)

- [ ] Step 1: Run targeted GMV/export suite including reference, serializer, equivalence, cache, controller, benchmark, Streamlit, and integration tests.
- [ ] Step 2: Run full pytest and record pass/fail/warning counts separately; existing pandas warnings remain a separate cleanup task unless behavior changes.
- [ ] Step 3: Run findings-first Review Agent with an allowlisted Task 1–6 contract and fresh Context Agent evidence.
- [ ] Step 4: Run Hermes with read-only production/runtime profile and confirm SQLite integrity, revenue scope, baseline, reference provenance, active pointer, and cache readiness.
- [ ] Step 5: Perform UI acceptance for cold seed, warm rebuild, reload without upload, downloads, and candidate mismatch fallback.
- [ ] Step 6: Enable `trusted_warm` default only when semantic equivalence, baseline, full pytest, Hermes, UI acceptance, and warm benchmark ≥40% all pass; otherwise leave `shadow` mode enabled.

## Execution Notes

- Do not commit or merge until the implementation checkpoint has Review PASS, full verification PASS, and Hermes/UI acceptance evidence.
- If a Task exposes a new failure, stop at that Task, preserve the evidence, and fix only within its allowlist before moving on.
- Memory Hub may supply compact context only; it must not write reference manifests, cache pointers, SQLite, baseline, runtime state, or Git.
