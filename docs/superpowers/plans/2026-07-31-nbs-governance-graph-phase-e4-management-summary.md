# NBS Governance Graph Phase E-4 Management Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 Agent Operations → Governance Graph section 內，建立符合 E-4 Design Spec 的 bounded、deterministic、read-only Management Summary、trend、preset 與 export projection。

**Architecture:** 新增獨立的 E-4 models／validator、source adapter 與 `GovernanceGraphManagementSummaryService`，只消費 caller 提供且已驗證的 D-1～D-4、E-1、E-3 read models。Streamlit 只透過 dependency-injected callback 消費 validated summary，不讀 snapshot/catalog path、不呼叫 writer、不建立新的 authority。E-4 不修改既有 Graph、canonical artifacts、SQLite、baseline、business rules 或 P2-5 Management Decision Layer。

**Tech Stack:** Python 3、dataclasses／typing、既有 `canonical_sha256`、pytest、Streamlit rendering、現有 Agent Operations service/rendering patterns、Hermes read-only acceptance。

## Plan Reconciliation（2026-07-31）

狀態：Implementation completed；Task 1–5 已完成 TDD、strict Review、full verification、system
acceptance 與 Hermes。Observation-only 邊界已確認；E-4 不建立 Graph snapshot、不新增 approval／
risk decision／workflow control，亦不讀寫 SQLite、baseline 或 canonical artifacts。

| Task | Status | Evidence |
|---|---|---|
| Task 1 — public models／strict validation | completed | `029370e`；model tests、strict Review PASS |
| Task 2 — source adapters／deterministic aggregation | completed | `4acaaa7`；adapter/service tests、strict Review PASS |
| Task 3 — trend／preset／export | completed | `8eb6c3f`、`fca8266`；targeted tests、strict Review PASS |
| Task 4 — Agent Operations rendering | completed | `07ba831`；UI/boundary tests、strict Review PASS |
| Task 5 — final boundary／acceptance | completed | `2e0b37b`、`fca8266`；full pytest 1413 passed、system acceptance PASS、Hermes PASS |

Spec review fixes carried into this plan：

- Trend envelope requires exact nested validated `summary`, exact `attentionCount`／`unknownCount`,
  and recomputed `summaryFingerprint` binding.
- Preset selection is canonical session state `{presetId, snapshotFingerprint}` or `null`; export maps
  it to `selectedPresetId`, stores filtered-view `summaryFingerprint`, and preserves explicit
  `originalSummaryFingerprint` provenance. Presets filter validated attention items only; coverage and
  diagnostics remain complete provenance.
- Invalid-source diagnostics use a closed code allowlist with exact `{code, summary}` keys and
  deterministic dedupe/order.

## Global Constraints

- Canonical artifacts 是唯一真相來源；E-4 只產生衍生、只讀 projection。
- E-4 public schemas 固定為 `governance-graph-management-summary-v1` 與 `governance-graph-management-summary-export-v1`。
- `managementPolicyVersion` 固定為 `e4-management-summary-v1`。
- `snapshot_fingerprint` 代表 D-2 `rightReference.snapshotFingerprint`；D-3/D-4 必須透過 comparison/risk fingerprints 做 transitive binding。
- D-1 是 optional context；缺失或 `unavailable` 的 D-1 不得使完整 D-2～E-3 required inputs 降級。
- Required coverage 只有在 D-2 comparison、D-3 risk、D-4 impact、E-1 lineage、E-3 catalog 全部可用時才是 sufficient。
- 不把 empty list 解讀為 complete、zero-risk、zero-impact、無 owner 或無 dependency。
- Status precedence 固定為 `invalid > stale > blocked > unknown > missing > unavailable > available`。
- Risk level 只允許 `R2`、`R1`、`R0`、`unknown`；不得計算 numeric risk score。
- Trend envelope 必須使用 exact nested validated `summary`、`attentionCount`、`unknownCount` 與
  recomputed `summaryFingerprint`；不得把 fingerprint 當作 opaque caller evidence。
- Preset selection 只允許 `null` 或 `{presetId, snapshotFingerprint}`；export 只輸出 bounded
  `selectedPresetId` 並保留原始 `summaryFingerprint`。
- Diagnostics 只允許 spec §8.1 的 closed code allowlist、exact keys 與 deterministic ordering。
- D-3 mapping 只接受既有 exact rule IDs：`D3-PROTECTED-NODE`、`D3-PROTECTED-SURFACE`、`D3-VERIFICATION-REGRESSION`、`D3-BEHAVIORAL-CHANGE`、`D3-DOCUMENTATION-ONLY`、`D3-BLOCKED-COMPARISON`、`D3-UNKNOWN-COVERAGE`。
- D-4 mapping 只接受既有 exact categories/states；`coverage_unknown` → `coverage_gap`，`documentation_only` 不產生 management attention。
- 所有 identity、reason code、preset id、sourceRef、drill-down identity 必須 bounded safe identifier；拒絕 path、URI、secret、prompt、command、raw payload、完整 log。
- E-4 不 import/call P2-5 decision services，也不接觸 target、forecast、attainment、WAPE、revenue decision data。
- Implementation Agent 一次只執行一個 Task；不得自行選擇下一 Task、commit、merge、push 或修改正式資料。
- 每個 Task 必須先有 failing tests，再做最小實作；Task 完成後交給本地 Review Agent findings-first 審閱。
- Task 的 commit 由 Codex 在該 Task Review PASS、targeted tests PASS 後執行；Implementation Agent 不得自行 commit。
- Review PASS 不取代 full pytest、system acceptance 或 Hermes；最終 acceptance 必須包含三者。
- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 baseline 固定為 `HKD 12,057,968`，本 Phase 不得修改。

---

### Task 1: E-4 public models, strict validation, and canonical fingerprints

**Files:**
- Create: `backend/agents/governance_graph_management_summary_models.py`
- Test: `tests/test_governance_graph_management_summary_models.py`
- Reference only: `backend/agents/governance_graph_policy.py`, `backend/agents/governance_graph_risk_models.py`, `backend/agents/governance_graph_impact_models.py`

**Interfaces:**
- Consumes: caller-provided mappings that claim one of the exact E-4 schemas.
- Produces:
  - `ManagementSummaryStatus` values: `available`, `partial`, `unknown`, `missing`, `unavailable`, `stale`, `blocked`, `invalid`.
  - `ManagementRiskLevel` values: `R2`, `R1`, `R0`, `unknown`.
  - `SourceRef`, `AttentionItem`, `CoverageSummary`, `TrendObservation`, `PresetDescriptor`.
  - `GovernanceGraphManagementSummary`.
  - `validate_management_summary_payload(payload: Mapping[str, Any]) -> ValidatedManagementSummary`.
  - `canonical_management_summary_payload(summary: Mapping[str, Any]) -> Mapping[str, Any]`.
  - `fingerprint_management_summary(summary: Mapping[str, Any]) -> str`.

- [ ] **Step 1: Write failing tests for exact schema and bounded fields**

  Cover:
  - exact top-level keys, `schemaVersion`, `managementPolicyVersion`;
  - closed sourceRef kinds/statuses and lowercase 64-character SHA-256;
  - closed status/risk/severity/state/drill-down enums;
  - path, URI, secret-like, raw payload and unknown-key rejection;
  - `attentionId = category:kind:identity:state:sourceIdentity`;
  - `selectedPresetId: null` when no preset is selected.

- [ ] **Step 2: Run model tests and confirm failure**

  Run:
  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_management_summary_models.py -q
  ```
  Expected: FAIL because the E-4 model module and validators do not yet exist.

- [ ] **Step 3: Implement immutable models and validators**

  Implement frozen dataclasses or the repository’s established immutable model pattern. Enforce:
  - exact public keys and bounded string/collection caps;
  - no identity/path normalization that invents meaning;
  - deterministic ordering for attention items, source refs, categories, presets, diagnostics;
  - sourceRef exact keys `kind`, `identity`, `fingerprint`, `status`;
  - `canonical_sha256` serialization: UTF-8, `ensure_ascii=false`, `sort_keys=true`, compact separators, lowercase SHA-256;
  - fingerprint self-exclusion rules exactly as specified.

- [ ] **Step 4: Run model tests and confirm pass**

  Run:
  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_management_summary_models.py -q
  ```
  Expected: all model, boundedness, ordering and fingerprint tests PASS.

- [ ] **Step 5: Commit the immutable model scope**

  ```bash
  # Codex only, after Review Agent PASS and targeted tests PASS:
  git add backend/agents/governance_graph_management_summary_models.py tests/test_governance_graph_management_summary_models.py
  git commit -m "feat: add governance graph management summary models"
  ```

---

### Task 2: Source-specific adapters and deterministic summary aggregation

**Files:**
- Create: `backend/agents/governance_graph_management_summary_service.py`
- Create: `backend/agents/governance_graph_management_summary_adapters.py`
- Test: `tests/test_governance_graph_management_summary_service.py`
- Test: `tests/test_governance_graph_management_summary_adapters.py`
- Reference only: `backend/agents/governance_graph_comparison_models.py`, `backend/agents/governance_graph_risk_models.py`, `backend/agents/governance_graph_impact_models.py`, `backend/agents/governance_graph_evidence_lineage_models.py`, `backend/agents/governance_graph_catalog_models.py`

**Interfaces:**
- Consumes: validated source mappings for D-1 query, D-2 comparison, D-3 risk, D-4 impact, E-1 lineage and E-3 catalog.
- Produces:
  - `adapt_d1_coverage(source) -> SourceCoverage` with optional-query semantics and selected-snapshot binding;
  - `adapt_d2_coverage(source) -> SourceCoverage`;
  - `adapt_d3_coverage(source) -> SourceCoverage`;
  - `adapt_d4_coverage(source) -> SourceCoverage`;
  - `adapt_e1_coverage(source) -> SourceCoverage`;
  - `adapt_e3_coverage(source) -> SourceCoverage`;
  - `GovernanceGraphManagementSummaryService.compose(...)-> GovernanceGraphManagementSummary`.
- Adapters must emit only the closed diagnostics from spec §8.1 with exact keys and deterministic
  dedupe/order; invalid source payloads never become empty valid defaults.
- `compose` must accept the exact keyword interface from the spec:
  ```python
  compose(
      *,
      snapshot_fingerprint: str,
      query: Mapping[str, Any] | None,
      comparison: Mapping[str, Any] | None,
      risk: Mapping[str, Any] | None,
      impact: Mapping[str, Any] | None,
      lineage: Mapping[str, Any] | None,
      catalog: Mapping[str, Any] | None,
      trend_snapshots: Sequence[Mapping[str, Any]] = (),
  ) -> GovernanceGraphManagementSummary
  ```

- [ ] **Step 1: Write failing adapter and isolation tests**

  Test exact source-specific rules:
  - D-1 valid, unavailable, missing, invalid and wrong-snapshot query states; unavailable/missing D-1 is
    reported in query coverage but excluded from required overall status;
  - D-2 complete only with valid identities, `status=available`, both freshness fresh and valid summary;
  - D-3 complete only when observed/classified counts reconcile and unknown/invalid/blocked counts are zero;
  - D-4 maps `coverageStatus=available|blocked|unknown` to canonical states;
  - E-1 requires validated snapshot/lineage fingerprint and evidence state;
  - E-3 requires owner/dependency status and read-model fingerprint;
  - empty records never become complete;
  - D-1 unavailable is excluded from overall status;
  - invalid source fields are discarded without contaminating valid sources;
  - right-reference snapshot binding and D3→D2→D4 transitive binding reject stale combinations.

- [ ] **Step 2: Run tests and confirm failure**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_management_summary_adapters.py tests/test_governance_graph_management_summary_service.py -q
  ```
  Expected: FAIL because adapters/service do not exist.

- [ ] **Step 3: Implement adapters and pure compose service**

  Implement independent source parsing, exact schema/fingerprint/binding checks, status precedence and field-preserving aggregation. Use only existing validated values:
  - project D-3 exact rule IDs and D-4 exact categories/states;
  - compute protected/blocked/unknown counts only from explicit source signals;
  - set `overallRiskLevel=unknown` whenever required coverage is incomplete or risk is non-available without a safe level;
  - retain valid source findings when another source is missing/invalid;
  - never traverse graph, read filesystem, call subprocess/network/SQLite/writers, or infer dependency/causality/business impact.

- [ ] **Step 4: Run targeted service tests**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_management_summary_adapters.py tests/test_governance_graph_management_summary_service.py -q
  ```
  Expected: PASS, including mixed-validity isolation and wrong-snapshot cases.

- [ ] **Step 5: Commit the service scope**

  ```bash
  # Codex only, after Review Agent PASS and targeted tests PASS:
  git add backend/agents/governance_graph_management_summary_adapters.py backend/agents/governance_graph_management_summary_service.py tests/test_governance_graph_management_summary_adapters.py tests/test_governance_graph_management_summary_service.py
  git commit -m "feat: compose governance graph management summary"
  ```

---

### Task 3: Trend projection, presets, and bounded export serializer

**Files:**
- Modify: `backend/agents/governance_graph_management_summary_service.py`
- Modify: `backend/agents/governance_graph_management_summary_models.py`
- Create: `backend/agents/governance_graph_management_summary_export.py`
- Test: `tests/test_governance_graph_management_summary_trend.py`
- Test: `tests/test_governance_graph_management_summary_export.py`

**Interfaces:**
- Consumes: Task 1 models and Task 2 composed summary.
- Produces:
  - `build_trend(snapshots: Sequence[Mapping[str, Any]]) -> TrendProjection`;
  - `apply_preset(summary: GovernanceGraphManagementSummary, preset_id: str | None) -> PresetView`;
  - `serialize_management_summary_export(summary, preset_id: str | None) -> GovernanceGraphManagementSummaryExport`.

- [ ] **Step 1: Write failing trend/preset/export tests**

  Cover:
  - zero/one snapshots → `unknown/insufficient_comparable_snapshots`;
  - same schema, policy and caller-supplied snapshot family with unique valid fingerprints → available trend;
  - exact trend envelope keys, nested validated summary, matching summary fingerprint and headline counts;
  - duplicate fingerprint, family/policy/schema mismatch, malformed envelope → invalid;
  - caller order is preserved; reversing input reverses observations and first/last comparison;
  - exact preset predicates and `available=false` for empty matches;
  - canonical `{presetId, snapshotFingerprint}` selection lifecycle and export mapping to `selectedPresetId`;
  - run/snapshot mismatch clears selection;
  - selected preset never changes summaryFingerprint;
  - closed diagnostic code mapping, exact keys, dedupe and deterministic ordering;
  - export exact envelope, exportFingerprint self-exclusion and unknown/stale/blocked preservation;
  - no filesystem, runtime, SQLite, Git or browser-side file path writes.

- [ ] **Step 2: Run tests and confirm failure**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_management_summary_trend.py tests/test_governance_graph_management_summary_export.py -q
  ```
  Expected: FAIL because trend/preset/export functions do not exist.

- [ ] **Step 3: Implement pure trend, preset and serializer functions**

  Use explicit caller order; compare only first/last observations; never discover history. Presets filter validated attention/coverage items only. Export uses in-memory JSON/browser download payload and preserves original summaryFingerprint while computing a separate exportFingerprint.

- [ ] **Step 4: Run targeted tests**

  ```bash
  .venv/bin/python -m pytest tests/test_governance_graph_management_summary_trend.py tests/test_governance_graph_management_summary_export.py -q
  ```
  Expected: PASS.

- [ ] **Step 5: Commit the projection/export scope**

  ```bash
  # Codex only, after Review Agent PASS and targeted tests PASS:
  git add backend/agents/governance_graph_management_summary_models.py backend/agents/governance_graph_management_summary_service.py backend/agents/governance_graph_management_summary_export.py tests/test_governance_graph_management_summary_trend.py tests/test_governance_graph_management_summary_export.py
  git commit -m "feat: add management summary trends presets and export"
  ```

---

### Task 4: Agent Operations Governance Graph rendering integration

**Files:**
- Modify: `agent_operations_rendering.py`
- Modify: `governance_graph_rendering.py`
- Modify: `app_pages.py` only if the existing page requires callback threading
- Test: `tests/test_agent_operations_rendering.py`
- Test: `tests/test_governance_graph_rendering.py`
- Test: `tests/test_app_pages_governance_graph.py`

**Interfaces:**
- Consumes: Task 3 validated summary callback:
  ```python
  management_summary_lookup(
      run_id: str,
      snapshot_fingerprint: str,
      preset_id: str | None = None,
  ) -> Mapping[str, Any]
  ```
- Produces: read-only Management Summary panel inside the existing Governance Graph section, with bounded unavailable/invalid messages and browser download only.

- [ ] **Step 1: Write failing rendering and boundary tests**

  Test:
  - callback None renders explicit unavailable state;
  - callback exception, wrong schema, wrong snapshot, raw/path/secret field renders bounded invalid/unavailable state;
  - valid summary renders headline counts, coverage, attention items, trend and preset availability;
  - selected preset clears after run/snapshot mismatch;
  - deterministic panel order is metadata/lineage → E-4 summary → E-3 catalog → D-2 comparison → D-3 risk → D-4 impact;
  - existing panels remain visible when E-4 fails;
  - download payload is generated in memory only;
  - static boundary checks find no P2-5 decision service import/call and no target/forecast/attainment fields.

- [ ] **Step 2: Run rendering tests and confirm failure**

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py tests/test_governance_graph_rendering.py tests/test_app_pages_governance_graph.py -q
  ```
  Expected: FAIL because the E-4 callback and panel are not wired.

- [ ] **Step 3: Implement minimal callback wiring and bounded rendering**

  Keep aggregation outside Streamlit. The renderer only validates the public summary, displays bounded fields, preserves independent existing panels, and exposes an in-memory/browser download action without creating a file path or calling a writer/CLI/subprocess/network/SQLite.

- [ ] **Step 4: Run rendering tests and confirm pass**

  ```bash
  .venv/bin/python -m pytest tests/test_agent_operations_rendering.py tests/test_governance_graph_rendering.py tests/test_app_pages_governance_graph.py -q
  ```
  Expected: PASS.

- [ ] **Step 5: Commit the UI scope**

  ```bash
  # Codex only, after Review Agent PASS and targeted tests PASS:
  git add agent_operations_rendering.py governance_graph_rendering.py app_pages.py tests/test_agent_operations_rendering.py tests/test_governance_graph_rendering.py tests/test_app_pages_governance_graph.py
  git commit -m "feat: render governance graph management summary"
  ```

---

### Task 5: Contract documentation, local-agent review, and final acceptance

**Files:**
- Modify: `docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md` only if E-4 contract index needs a link/reference
- Create: `tests/test_governance_graph_management_summary_boundary.py`
- No production SQLite, baseline, runtime, Git workflow or export-schema modifications

**Interfaces:**
- Consumes: Tasks 1–4 implementation and test evidence.
- Produces: executable no-write/boundary regression coverage and final acceptance evidence.

- [ ] **Step 1: Add boundary regression tests**

  Snapshot runtime/SQLite/Git/canonical artifact identities before and after pure service, serializer and renderer calls. Assert equality. Static-scan E-4 modules for forbidden P2-5 decision imports/calls, subprocess/network/filesystem/SQLite/writer paths and prohibited business fields.

- [ ] **Step 2: Run compile and targeted tests**

  ```bash
  .venv/bin/python -m py_compile \
    backend/agents/governance_graph_management_summary_models.py \
    backend/agents/governance_graph_management_summary_adapters.py \
    backend/agents/governance_graph_management_summary_service.py \
    backend/agents/governance_graph_management_summary_export.py \
    governance_graph_rendering.py \
    agent_operations_rendering.py \
    app_pages.py

  .venv/bin/python -m pytest \
    tests/test_governance_graph_management_summary_models.py \
    tests/test_governance_graph_management_summary_adapters.py \
    tests/test_governance_graph_management_summary_service.py \
    tests/test_governance_graph_management_summary_trend.py \
    tests/test_governance_graph_management_summary_export.py \
    tests/test_governance_graph_management_summary_boundary.py \
    tests/test_governance_graph_rendering.py \
    tests/test_agent_operations_rendering.py \
    tests/test_app_pages_governance_graph.py -q
  ```
  Expected: compile succeeds and all targeted tests PASS.

- [ ] **Step 3: Run independent local Review Agent findings-first review**

  Provide the immutable Task diff, test output and E-4 spec to the approved local Review Agent. It must be read-only and return findings-first. Any finding blocks the next gate until fixed and re-reviewed.

- [ ] **Step 4: Run full verification and system acceptance**

  ```bash
  .venv/bin/python -m pytest -q
  .venv/bin/python scripts/system_manager.py acceptance
  ```
  Expected: full pytest and system acceptance PASS; no baseline/revenue/SQLite/export contract drift.

- [ ] **Step 5: Run Hermes post-change check**

  ```bash
  .venv/bin/python scripts/hermes_post_change_check.py
  ```
  Expected: Hermes PASS. Timeout/degraded/failure must be reported as incomplete with exact stage/error; never converted to PASS.

- [ ] **Step 6: Update plan reconciliation and commit only approved documentation changes**

  Record completed Tasks, review findings, verification, Hermes result and any blocked item. Do not auto-apply Documentation Agent proposals. Commit only after the user-approved integration scope.

---

## Task Sequencing and Agent Use

1. Before each Task, Codex runs `scripts/context_agent.py --collect-only` with only the relevant spec/plan and source contracts.
2. Each implementation Task uses one bounded local Implementation Agent only if explicitly dispatched; it receives an allowlisted file set and cannot choose the next Task.
3. After each Task, the local Review Agent receives only the immutable Task diff, targeted tests and relevant contract evidence; it does not modify files.
4. Codex resolves findings, reruns targeted tests, then proceeds only after Review PASS.
5. Hermes runs only after all Tasks, full pytest and system acceptance pass.
6. No Task may commit or merge on behalf of an agent; Codex owns integration and final acceptance.

## Final Verification Commands

```bash
.venv/bin/python -m py_compile \
  backend/agents/governance_graph_management_summary_models.py \
  backend/agents/governance_graph_management_summary_adapters.py \
  backend/agents/governance_graph_management_summary_service.py \
  backend/agents/governance_graph_management_summary_export.py \
  governance_graph_rendering.py agent_operations_rendering.py app_pages.py

.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
git diff --check
git status --short
```

## Completion Criteria

- All five Tasks have their own immutable diff, targeted tests and Review PASS.
- Full pytest, system acceptance and Hermes PASS.
- E-4 Management Summary remains read-only, canonical-artifact-first and deterministic.
- No P2-5 decision-layer coupling, no business target fields, no write path and no baseline/revenue/SQLite/export-schema drift.
- Worktree is clean and final acceptance evidence is recorded.
- Boundary tests must record before/after equality for runtime, SQLite, baseline, canonical artifact tree and
  Git identity; `git status --short` must produce no output before final acceptance.
