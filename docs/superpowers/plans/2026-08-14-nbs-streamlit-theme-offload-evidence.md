# NBS Streamlit Theme Repair and Short-term Offload Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正 Streamlit 深色主題的 CSS 覆蓋，並建立可驗證的真實 Short-term Offload off/on workflow evidence。

**Architecture:** 保持 `_theme_tokens()` 為唯一 theme source，移除 static CSS 對 dynamic token 的覆蓋；新增獨立 immutable A/B evidence builder，消費 control/treatment receipts 與 offload references，只讀產出比較結果。Runner 仍 opt-in，沒有 evidence 就 fail-closed。

**Tech Stack:** Python 3、Streamlit、CSS contract tests、pytest、SHA-256、既有 Hermes read-only runner、`.nbs_agent_runtime` isolated artifacts。

## Global Constraints

- Canonical revenue、baseline、SQLite、Graph、approval、dispatch、workflow authority 不得修改。
- Short-term Offload 仍是 explicit opt-in；ordinary workflow 與 Memory Sidecar defaults 不變。
- Evidence schema 固定為 `short-term-offload-ab-evidence-v1`。
- 真實 token reduction 只可由 live receipts 的 observed usage 計算；fixture 不得宣稱 real evidence。
- 缺少 usage、identity、provenance、receipt 或 fingerprint 時固定輸出 `blocked_runner_capability` 或 `completion_missing`。
- 所有 artifacts 僅可寫入 `.nbs_agent_runtime/short-term-offload` 或專用 evidence 子目錄，不得寫 SQLite 或 canonical artifacts。

---

### Task 1: Theme token override contract

**Files:**
- Modify: `app_styles.py`（移除 static `:root` 對 dynamic theme tokens 的重複宣告）
- Test: `tests/test_streamlit_upload_feedback_contract.py`
- Create: `tests/test_streamlit_theme_runtime_contract.py`

**Interfaces:**
- Consumes: `streamlit_rendering._theme_tokens(theme)` and `_render_dynamic_theme_css()`。
- Produces: a static-style source contract proving the CSS layer does not overwrite dynamic page/surface/text/sidebar tokens。

- [ ] **Step 1: Write RED tests** for light/dark token differences and for absence of unconditional assignments to `--nbs-page-bg`, `--nbs-surface`, `--nbs-text`, `--nbs-sidebar-bg` in the static style block.
- [ ] **Step 2: Run focused tests and confirm the current static light `:root` block fails the contract.**
- [ ] **Step 3: Remove only duplicated static color token declarations from `app_styles.py`; retain typography/layout/component selectors and dynamic CSS invocation order.**
- [ ] **Step 4: Run the focused theme contract tests and `py_compile app_styles.py streamlit_rendering.py`.**
- [ ] **Step 5: Run browser smoke verification at `http://127.0.0.1:8502/`: select 深色, verify dark shell/sidebar/surfaces; select 淺色, verify the original light palette returns.**
- [ ] **Step 6: Findings-first Review for Task 1; no evidence builder changes in this Task.**

### Task 2: Immutable A/B evidence schema and comparison service

**Files:**
- Create: `backend/agents/short_term_offload_ab_models.py`
- Create: `backend/agents/short_term_offload_ab_service.py`
- Test: `tests/test_short_term_offload_ab_models.py`
- Test: `tests/test_short_term_offload_ab_service.py`

**Interfaces:**
- `ShortTermOffloadABEvidence.from_dict(payload: Mapping[str, object]) -> ShortTermOffloadABEvidence`
- `compare_short_term_offload_runs(control: RunnerCapabilityRun, treatment: RunnerCapabilityRun, *, provenance_refs: tuple[str, ...]) -> ShortTermOffloadABEvidence`
- `ShortTermOffloadABEvidence.to_dict() -> dict[str, object]`

- [ ] **Step 1: Write RED tests** for exact keys, positive observed token/latency fields, shared workload identity, ratio arithmetic, missing usage and tampered receipt rejection.
- [ ] **Step 2: Run focused tests and confirm missing-module collection failure.**
- [ ] **Step 3: Implement frozen evidence model and comparison service; require live receipt identities and reject synthetic/partial usage.**
- [ ] **Step 4: Add deterministic tests for `pass`, `no_reduction`, `blocked_runner_capability` and `completion_missing`.**
- [ ] **Step 5: Run focused tests, `py_compile`, and `git diff --check`.**
- [ ] **Step 6: Findings-first Review for Task 2.**

### Task 3: Bounded real-workflow evidence operator

**Files:**
- Create: `scripts/short_term_offload_ab_operator.py`
- Modify: `scripts/hermes_live_ab_runner.py` only to pass the explicit evidence output path and preserve opt-in behavior.
- Test: `tests/test_short_term_offload_ab_operator.py`
- Test: `tests/test_hermes_live_ab_runner.py`

**Interfaces:**
- `run_bounded_ab_workload(..., short_term_offload: Literal["off", "on"], evidence_root: Path) -> ABRunReceipt`
- `record_ab_evidence(control_receipt: Path, treatment_receipt: Path, *, evidence_root: Path) -> Path`
- CLI: `python scripts/short_term_offload_ab_operator.py run --mode off|on ...` and `compare --control ... --treatment ...`。

- [ ] **Step 1: Write RED tests** for same workload binding, two arm separation, explicit mode, artifact presence in treatment, no artifact in control, redacted diagnostics and exact isolated evidence root.
- [ ] **Step 2: Run tests and confirm the operator module/CLI is absent.**
- [ ] **Step 3: Implement bounded operator using the existing runner and ShortTermOffloadService; never include secrets, full prompts or internal reasoning in evidence.**
- [ ] **Step 4: Persist only immutable receipts and evidence fingerprints; reject dirty head, mismatched workload, missing provider usage and invalid offload artifacts before comparison.**
- [ ] **Step 5: Add three-pair execution ledger; a blocked pair is recorded as blocked and cannot be converted to a token result.**
- [ ] **Step 6: Run focused tests, compile and diff check; then findings-first Review for Task 3.**

### Task 4: Real workflow evidence and final acceptance

**Files:**
- Modify: `scripts/hermes_post_change_check.py` with a read-only A/B evidence report step.
- Create: `tests/test_short_term_offload_ab_hermes_boundary.py`
- Create: `.superpowers/sdd/2026-08-14-nbs-streamlit-theme-offload-evidence/real-workflow-report.md` (ignored evidence artifact)

**Interfaces:**
- `short_term_offload_ab_artifact_report(project_root: Path) -> dict[str, object]`

- [ ] **Step 1: Write RED boundary tests** for read-only reporting, exact evidence root, missing/tampered evidence, and no writes.
- [ ] **Step 2: Implement the Hermes read-only report; it only reads evidence and reports PASS/blocked/rejected.**
- [ ] **Step 3: Execute the same bounded workflow three times with off and on arms, using the approved local runner and live receipts; do not fabricate token/latency fields.**
- [ ] **Step 4: Re-read each evidence envelope offline, verify fingerprints and calculate token reduction only for complete pairs.**
- [ ] **Step 5: Run full pytest, `system_manager.py acceptance`, `scripts/hermes_post_change_check.py`, and browser theme verification.**
- [ ] **Step 6: Report actual observed token reduction, latency and any blocked reason; if no complete live pair exists, explicitly report that token reduction is unproven.**
