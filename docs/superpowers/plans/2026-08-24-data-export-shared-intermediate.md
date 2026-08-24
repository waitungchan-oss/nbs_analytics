# Data Exports Shared Intermediate + Parallel Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 在不改變既有報表語義、業務規則或原始 SQLite 的前提下，以 Shared Intermediate Model、bounded parallel serialization、versioned artifacts 與 ZIP package 加速 Data Exports。

**Architecture:** 保留既有 \`_compute_export_workbooks()\` 作為 reference/legacy path；新增 generation-scoped intermediate model，依三個 scope 產生 report inputs，再由 bounded worker pool 分別輸出 XLSX。所有 fast artifacts 先經 semantic equivalence gate，通過後才以 atomic manifest 發布 READY，並提供個別 XLSX 與完整 ZIP 下載。

**Tech Stack:** Python 3、pandas、openpyxl / 現有 Excel writer、SQLite read-only frames、\`concurrent.futures\`、JSON manifest、ZIP、pytest、Streamlit。

**Spec:** \`docs/superpowers/specs/2026-08-24-data-export-shared-intermediate-design.md\`

## Global Constraints

- 正式收入範圍固定為「不含掛賬核銷與 TT 退款轉團款」。
- 不修改 SQLite schema、原始業務資料、Dashboard KPI、AI Forecast、WAPE 或 baseline。
- 既有三份 workbook 的 sheet、欄位、檔名與業務數值必須保持不變。
- 結果 gate 採 semantic equivalence；不要求 \`.xlsx\` binary bytes 完全相同。
- 只有 equivalence PASS、schema PASS、checksum PASS 與 baseline PASS 才能發布 fast READY manifest。
- Fast path 失敗、不一致、超時或 stale 時必須 fail closed 並回退 legacy export path。
- Download request 只能讀取已發布 artifact，不得重新執行 aggregation 或 workbook serialization。
- 不新增外部服務、雲端 object storage、資料庫或 agent workflow control。
- 在獨立 \`codex/\` worktree 執行；每個 Task 完成後先 targeted tests、findings-first review，再進下一 Task。
- 保留 main 現有未提交 \`tests/test_streamlit_gmv_formal_contract.py\` 與 \`.superpowers/brainstorm/\`，不得納入本 feature commit。

## File Map

### New files

- \`backend/services/export_intermediate_service.py\`：shared intermediate 與三個 scope inputs。
- \`backend/services/export_equivalence_service.py\`：XLSX canonicalization、semantic comparison、bounded mismatch report。
- \`backend/services/export_manifest_service.py\`：manifest、atomic artifact publication、checksum、ZIP、cache validation。
- \`backend/services/export_fast_path_service.py\`：bounded worker pool、job controller、legacy fallback。
- \`tests/test_export_intermediate_service.py\`、\`tests/test_export_equivalence_service.py\`、\`tests/test_export_manifest_service.py\`、\`tests/test_export_fast_path.py\`：各 service 的 TDD contract。
- \`tests/test_export_rollout.py\`：shadow/opt-in/default/disabled rollout gates。

### Existing files

- \`app_workflows.py\`：保留 \`_compute_export_workbooks()\` reference path，接入 fast controller 與 manifest lookup。
- \`app_pages.py\`：Data Export Center 的 job status、ZIP download、individual XLSX downloads、fallback message。
- \`pipeline.py\`：只抽出必要的 pure aggregation helper，不重寫既有 workbook schema。
- \`tests/test_streamlit_upload_feedback_contract.py\`：補充 Data Export Center UI contract。
- \`tests/test_phase2_precheck_acceptance.py\`、dashboard/export contract tests：保護 baseline 與既有輸出契約。

---

### Task 1: Freeze Legacy Export Baseline and Measurement Contract

**Files:**
- Create: \`tests/test_export_fast_path.py\`
- Modify: \`tests/test_streamlit_upload_feedback_contract.py\` only for export source contracts
- Read: \`app_workflows.py:_compute_export_workbooks\`, \`pipeline.py:build_dashboard_data\`

**Interfaces:**
- Consumes existing \`_compute_export_workbooks(raw_tour, raw_others) -> dict\`.
- Produces a deterministic reference fixture, output-key contract and timing record for later Tasks.

- [ ] **Step 1: Write the failing/reference test**

Use a fixture containing ordinary receipt, \`掛賬核銷\`, \`TT 退款轉團款\`, specialist row, zero amount and empty values. Assert the legacy builder returns non-empty bytes for \`ex\`, \`ex_no_writeoff\` and \`ex_no_writeoff_refund_transfer\`.

\`\`\`python
def test_legacy_export_produces_three_workbooks(fixture_frames):
    payload = _compute_export_workbooks(fixture_frames.tour, fixture_frames.others)
    keys = ("ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer")
    assert all(isinstance(payload[key], bytes) and payload[key] for key in keys)
\`\`\`

- [ ] **Step 2: Run the reference test**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_fast_path.py::test_legacy_export_produces_three_workbooks -q
\`\`\`

Expected: PASS against the unchanged legacy path.

- [ ] **Step 3: Add timing fields**

Record aggregation, serialization, total duration, artifact sizes and peak RSS. Do not assert machine-specific absolute time; assert non-negative fields and non-zero artifacts.

- [ ] **Step 4: Run baseline contracts**

\`\`\`bash
.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py tests/test_dashboard_service.py tests/test_report_export_api.py -q
\`\`\`

- [ ] **Step 5: Commit**

\`\`\`bash
git add tests/test_export_fast_path.py tests/test_streamlit_upload_feedback_contract.py
git commit -m "test: freeze dashboard export baseline"
\`\`\`

### Task 2: Build Shared Intermediate Model

**Files:**
- Create: \`backend/services/export_intermediate_service.py\`
- Create: \`tests/test_export_intermediate_service.py\`
- Modify: \`pipeline.py\` only for small pure helpers that cannot be reused

**Interfaces:**

\`\`\`python
@dataclass(frozen=True, slots=True)
class ExportIntermediateModel:
    generation_token: str
    rules_fingerprint: str
    schema_version: str
    normalized_tour: pd.DataFrame
    normalized_others: pd.DataFrame
    classified_tour: pd.DataFrame
    classified_others: pd.DataFrame
    shared_aggregates: Mapping[str, pd.DataFrame]
    source_fingerprints: Mapping[str, str]

def build_export_intermediate(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    generation_token: str,
    rules_fingerprint: str,
    schema_version: str,
) -> ExportIntermediateModel

def build_scope_report_inputs(
    intermediate: ExportIntermediateModel,
    scope: ExportScope,
) -> DashboardReportInputs
\`\`\`

- [ ] **Step 1: Write failing tests**

Assert deterministic fingerprints, no source-frame mutation, explicit scope IDs and correct official exclusions. Patch the SQLite connection factory and assert it is never called.

- [ ] **Step 2: Run the new tests**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_intermediate_service.py -q
\`\`\`

Expected: FAIL because the new service/types do not exist.

- [ ] **Step 3: Implement minimal shared computation**

Reuse existing normalization, mapping helpers and revenue-scope constants. Compute normalized/classified frames and reusable groupby outputs once. Scope filtering must return copies and never mutate the base model.

- [ ] **Step 4: Verify and regress**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_intermediate_service.py tests/test_pipeline_preloaded_frames.py tests/test_phase2_precheck_acceptance.py -q
\`\`\`

- [ ] **Step 5: Commit**

\`\`\`bash
git add backend/services/export_intermediate_service.py tests/test_export_intermediate_service.py pipeline.py
git commit -m "perf: add shared export intermediate model"
\`\`\`

### Task 3: Implement Semantic Workbook Equivalence

**Files:**
- Create: \`backend/services/export_equivalence_service.py\`
- Create: \`tests/test_export_equivalence_service.py\`

**Interfaces:**

\`\`\`python
@dataclass(frozen=True, slots=True)
class WorkbookEquivalenceReport:
    status: Literal["PASS", "FAIL"]
    schema_fingerprint: str
    data_fingerprint: str
    row_counts: Mapping[str, int]
    metric_summary: Mapping[str, object]
    mismatch_count: int
    mismatch_examples: tuple[Mapping[str, object], ...]

def canonicalize_workbook(data: bytes) -> CanonicalWorkbook
def compare_workbooks(reference: bytes, candidate: bytes, *, money_columns=(), stable_key_columns=None) -> WorkbookEquivalenceReport
def compare_export_sets(reference: Mapping[str, bytes], candidate: Mapping[str, bytes]) -> ExportEquivalenceReport
\`\`\`

- [ ] **Step 1: Write failing tests**

Create equivalent workbooks with different metadata and assert PASS. Create changed amount, missing row, changed sheet name and changed column order and assert FAIL with bounded examples.

- [ ] **Step 2: Run tests to verify failure**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_equivalence_service.py -q
\`\`\`

- [ ] **Step 3: Implement canonicalization**

Read XLSX in read-only mode, preserve sheet names/order, normalize headers/types, sort only by declared stable keys, normalize money to two decimals and quantities deterministically. Do not compare arbitrary row order unless the existing contract requires it.

- [ ] **Step 4: Implement bounded diagnostics**

Return at most 20 examples per workbook with sheet, row key, column, reference value and candidate value. Never include full raw frames or secrets.

- [ ] **Step 5: Verify and commit**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_equivalence_service.py tests/test_phase2_precheck_acceptance.py -q
git add backend/services/export_equivalence_service.py tests/test_export_equivalence_service.py
git commit -m "test: add semantic export equivalence gate"
\`\`\`

### Task 4: Add Versioned Manifest and ZIP Package

**Files:**
- Create: \`backend/services/export_manifest_service.py\`
- Create: \`tests/test_export_manifest_service.py\`

**Interfaces:**

\`\`\`python
EXPORT_MANIFEST_SCHEMA = "export-manifest-v2"

@dataclass(frozen=True, slots=True)
class ExportArtifact:
    artifact_id: str
    scope_id: str
    relative_path: str
    content_type: str
    bytes: int
    sha256: str
    schema_fingerprint: str
    data_fingerprint: str
    row_counts: Mapping[str, int]
    build_duration_ms: int
    status: Literal["READY", "FAILED", "FALLBACK"]

def publish_export_manifest(job_dir: Path, manifest: ExportManifest, *, cache_root: Path) -> Path
def build_export_package(artifacts: Mapping[str, Path], manifest: ExportManifest, *, output_path: Path) -> Path
def load_ready_export_manifest(cache_root: Path, *, generation_token: str, rules_fingerprint: str, export_schema_version: str) -> ExportManifest | None
\`\`\`

- [ ] **Step 1: Write failing tests**

Cover manifest round-trip, checksum/byte-size validation, path traversal rejection, incomplete artifacts not loading as READY, stale generation/rules/schema misses, and ZIP membership containing exactly the three XLSX files plus manifest/equivalence JSON.

- [ ] **Step 2: Run tests**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_manifest_service.py -q
\`\`\`

Expected: FAIL before implementation.

- [ ] **Step 3: Implement atomic publication**

Write to a job-specific temporary directory, validate all paths below cache root, publish artifact files atomically, and write the manifest last. Do not delete old artifacts in this Task.

- [ ] **Step 4: Implement deterministic package verification**

Use the existing workbook filenames, deterministic ZIP member names and bounded JSON metadata. Verify the ZIP before marking it READY.

- [ ] **Step 5: Verify and commit**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_manifest_service.py tests/test_gmv_export_cache_service.py -q
git add backend/services/export_manifest_service.py tests/test_export_manifest_service.py
git commit -m "feat: add versioned export artifacts and package manifest"
\`\`\`

### Task 5: Build Fast Controller and Parallel Serializers

**Files:**
- Create: \`backend/services/export_fast_path_service.py\`
- Modify: \`app_workflows.py\`
- Modify: \`tests/test_export_fast_path.py\`

**Interfaces:**

\`\`\`python
@dataclass(frozen=True, slots=True)
class ExportJobResult:
    job_id: str
    status: Literal["READY", "FALLBACK", "FAILED"]
    manifest_path: Path | None
    fallback_reason: str | None
    timings: Mapping[str, int]

def build_fast_export_job(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    generation_token: str,
    rules_fingerprint: str,
    export_schema_version: str,
    cache_root: Path,
    worker_count: int = 3,
    reference_builder: Callable | None = None,
) -> ExportJobResult

def load_fast_export_job(*, cache_root: Path, generation_token: str, rules_fingerprint: str, export_schema_version: str) -> ExportManifest | None
\`\`\`

- [ ] **Step 1: Write failing controller tests**

Test bounded three-scope submission, all-artifact requirement, equivalence mismatch, worker exception, timeout, cache hit without rebuilding, and input-frame immutability.

- [ ] **Step 2: Run tests to verify failure**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_fast_path.py -q
\`\`\`

- [ ] **Step 3: Implement top-level worker serializer**

Use a picklable top-level serializer and bounded \`ProcessPoolExecutor\` by default. Support worker count 2 and sequential mode for low-memory machines. Workers write only temporary XLSX files and return metadata; they do not access SQLite or Streamlit state.

- [ ] **Step 4: Implement controller gates**

Execute:

\`\`\`text
matching manifest lookup
→ shared intermediate
→ three scope inputs
→ bounded parallel serialization
→ legacy-vs-fast equivalence
→ ZIP verification
→ atomic READY manifest
\`\`\`

Any failure marks the job FALLBACK and invokes the existing legacy builder. Never publish a partial READY manifest.

- [ ] **Step 5: Add bounded telemetry**

Record aggregation, per-artifact serialization, equivalence, package and total milliseconds, worker count, artifact sizes and peak RSS in manifest metadata.

- [ ] **Step 6: Verify and commit**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_fast_path.py tests/test_export_equivalence_service.py tests/test_export_manifest_service.py -q
git add backend/services/export_fast_path_service.py app_workflows.py tests/test_export_fast_path.py
git commit -m "perf: add equivalence-gated parallel export job"
\`\`\`

### Task 6: Integrate Data Export Center

**Files:**
- Modify: \`app_pages.py\` Data Export Center
- Modify: \`app_workflows.py:_ensure_export_workbooks()\`
- Create: \`tests/test_streamlit_export_fast_path_contract.py\`
- Modify: \`tests/test_streamlit_upload_feedback_contract.py\`

**Interfaces:**
- Consumes \`ExportJobResult\`, \`load_fast_export_job()\`, verified artifact reader and package reader.
- Produces UI states \`PREPARING\`, \`VERIFYING\`, \`READY\`, \`FALLBACK\`, \`FAILED\`.

- [ ] **Step 1: Write failing UI contract tests**

Assert ZIP exists only for READY package, three existing filenames remain, download handlers read artifacts, and the download path does not call \`_compute_export_workbooks()\`.

- [ ] **Step 2: Run UI tests**

\`\`\`bash
.venv/bin/python -m pytest tests/test_streamlit_export_fast_path_contract.py tests/test_streamlit_upload_feedback_contract.py -q
\`\`\`

Expected: FAIL before integration.

- [ ] **Step 3: Implement status-driven UI**

Keep initial dashboard lazy. Preparation starts/polls the local job. READY shows \`一鍵下載完整報表包 ZIP\`, three individual XLSX buttons, last build time, short generation token and cache status. Download handlers only read verified files.

- [ ] **Step 4: Implement visible fallback**

When fast path falls back, preserve legacy downloads and show \`高速匯出驗證失敗，已使用相容匯出路徑\` with only bounded reason code.

- [ ] **Step 5: Verify and commit**

\`\`\`bash
.venv/bin/python -m pytest tests/test_streamlit_export_fast_path_contract.py tests/test_streamlit_upload_feedback_contract.py tests/test_streamlit_gmv_formal_contract.py -q
git add app_pages.py app_workflows.py tests/test_streamlit_export_fast_path_contract.py tests/test_streamlit_upload_feedback_contract.py
git commit -m "feat: add one-click export package downloads"
\`\`\`

### Task 7: Add Shadow Rollout Controls

**Files:**
- Modify: \`app_workflows.py\`
- Modify: \`app_pages.py\`
- Create: \`tests/test_export_rollout.py\`

**Interfaces:**

\`\`\`python
EXPORT_FAST_PATH_MODE = "shadow | opt_in | default | disabled"
def resolve_export_mode() -> str
def should_publish_fast_export(*, mode: str, equivalence_status: str, baseline_status: str) -> bool
\`\`\`

- [ ] **Step 1: Write failing rollout tests**

Assert shadow measures but downloads legacy, opt-in exposes fast only after PASS, default uses fast only after PASS, disabled always uses legacy, and any equivalence/baseline non-PASS blocks fast publication.

- [ ] **Step 2: Implement safe default**

Default to \`shadow\` until real formal-shaped evidence proves equivalence. The mode must not become approval, dispatch or agent control.

- [ ] **Step 3: Verify and commit**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_rollout.py tests/test_export_fast_path.py -q
git add app_workflows.py app_pages.py tests/test_export_rollout.py
git commit -m "feat: add export fast-path rollout gates"
\`\`\`

### Task 8: Full Verification and UI Acceptance

**Files:**
- Modify: none unless a defect is within the approved allowlist.
- Evidence: \`.nbs_agent_runtime/\` and existing Hermes artifacts only.

- [ ] **Step 1: Run focused suite**

\`\`\`bash
.venv/bin/python -m pytest tests/test_export_intermediate_service.py tests/test_export_equivalence_service.py tests/test_export_manifest_service.py tests/test_export_fast_path.py tests/test_export_rollout.py -q
\`\`\`

Expected: all focused tests pass with zero equivalence mismatches.

- [ ] **Step 2: Run regression and baseline suite**

\`\`\`bash
.venv/bin/python -m pytest tests/test_pipeline_preloaded_frames.py tests/test_dashboard_service.py tests/test_report_export_api.py tests/test_streamlit_upload_feedback_contract.py tests/test_phase2_precheck_acceptance.py -q
\`\`\`

Expected: existing export contracts and 2026-05 baseline remain unchanged.

- [ ] **Step 3: Run full pytest against a disposable copy of formal SQLite**

Use a copy for production-shaped data; never replace or write the formal main DB. Record total tests, baseline, equivalence, fast-vs-legacy timings, peak RSS and cache-hit latency.

- [ ] **Step 4: Run compile and diff checks**

\`\`\`bash
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py pipeline.py backend/services/export_intermediate_service.py backend/services/export_equivalence_service.py backend/services/export_manifest_service.py backend/services/export_fast_path_service.py
git diff --check
\`\`\`

- [ ] **Step 5: Run actual Streamlit acceptance**

Verify initial page does not generate workbooks, status progression appears, READY shows ZIP and three XLSX downloads, reload reopens READY, download does not rebuild, and forced mismatch displays fallback while preserving legacy download.

- [ ] **Step 6: Run Review Agent and Hermes**

After each approved implementation Task, run findings-first Review. After the complete suite passes:

\`\`\`bash
.venv/bin/python scripts/hermes_post_change_check.py --json
\`\`\`

Report targeted tests, full pytest, equivalence, baseline, UI and Hermes separately.

## Plan Self-Review

- [x] Spec coverage: shared intermediate, parallel serialization, ZIP, manifest, equivalence, fallback, tests, performance and rollout map to Tasks 1–8.
- [x] No SQLite migration or business-rule change.
- [x] Legacy path remains reference and fallback.
- [x] Every fast artifact is versioned by generation/rules/schema/pipeline fingerprints.
- [x] Download does not compute.
- [x] Tests cover equivalent-but-different XLSX bytes and real mismatches.
- [x] Main dirty files are excluded from feature commits.
- [x] No \`TBD\`, \`TODO\` or vague untestable step.

## Execution Handoff

The plan is for one approved Task at a time in an isolated \`codex/\` worktree. After the user selects an execution mode, invoke the corresponding Superpowers execution skill and stop at the first checkpoint before replacing the legacy export path.

