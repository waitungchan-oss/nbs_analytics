# NBS Governance Graph Phase E-2 Streamlit UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 Streamlit `Agent Operations` selected-run section，加入 bounded Governance Graph workspace、D-1 query 與 E-1 canonical evidence lineage drill-down，並對尚未提供 validated D-2/D-3/D-4 read model 的區塊如實顯示 unavailable。

**Architecture:** `AgentOperationsService` 只增加 validated `governanceGraph.snapshotFingerprint` compact field；獨立 rendering helper 只消費 compact snapshot 與 callbacks，不讀檔或執行 writer。`app_pages.py` 注入 D-1 query 與 E-1 lineage callbacks，UI 只保存 bounded selected-evidence identity，所有 status semantics 仍由既有 read models 負責。

**Tech Stack:** Python 3、Streamlit、既有 `AgentOperationsService`、`GovernanceGraphQueryService`、`GovernanceGraphEvidenceLineageService`、pytest、system acceptance、Hermes。

## Global Constraints

- 保留現有 `Agent Operations` 入口、`AGENT_OPERATIONS_SNAPSHOT` cache 與 `AGENT_OPERATIONS_SELECTED_RUN_ID` lifecycle；不新增 top-level tab、API、Vue page、polling 或獨立 app。
- `snapshotFingerprint` 只能原樣來自 validated `GovernanceGraphSnapshot.graph_fingerprint`；renderer 不計算、猜測或直接讀檔。
- E-1 request 只允許 registry-owned `task-gate.json`、`terra-diagnosis.json`、`protected-incident.json`；`hermes.json`、`review.json` 等一般 Graph artifact 不得冒充 canonical evidence。
- Renderer 只透過 `query_graph(run_id, filters)` 與 `lineage_lookup(request)` callbacks 取得 read model；不得呼叫 CLI、subprocess、builder、persist、writer、approval、dispatch、repair、prune 或 download raw。
- Session state 固定使用 `AGENT_OPERATIONS_SELECTED_EVIDENCE` 保存 bounded `{runId,nodeId,path,sha256,snapshotFingerprint}` mapping；不得保存 lineage result、raw payload 或 diagnostics；run/fingerprint/node/gating 不相容時必須 pop。
- D-2、D-3、D-4 若沒有 approved validated callback/result，固定顯示 unavailable；不得顯示 zero changes、low risk、no impact 或 PASS。
- 所有 UI 顯示 bounded safe metadata；禁止 absolute path、raw JSON、prompt、command、stdout/stderr、secret、完整 log、SQLite/Excel rows。
- 不修改 SQLite、baseline `HKD 12,057,968`、正式口徑「不含掛賬核銷與TT退款轉團款」、revenue、business rules、rollback、export schema、workflow status、Graph snapshot 或 canonical artifacts。
- 每個 Task 先 TDD RED→GREEN，再交 strict findings-first Review；Implementation Agent 不 commit/merge，Codex 只在 Review PASS 後 commit。

## File Map

- Modify: `backend/services/agent_operations_service.py` — validated compact Graph additive `snapshotFingerprint`。
- Modify: `agent_operations_rendering.py` — selected-run Graph workspace orchestration與 callback injection；保留既有 Agent Operations lifecycle。
- Create: `governance_graph_rendering.py` — focused Graph summary/query/lineage/derived-status rendering helpers；Task 2 固定建立此單一責任 module，禁止無關拆檔。
- Modify: `app_pages.py` — 注入 D-1 `query_graph` 與 E-1 `lineage_lookup` read-only callbacks；不直接讀 runtime。
- Modify: `tests/test_agent_operations_service.py` — compact fingerprint extension and unavailable/invalid semantics。
- Modify: `tests/test_agent_operations_rendering.py` — existing Graph compatibility and callback wiring。
- Create: `tests/test_governance_graph_rendering.py` — E-2 rendering, canonical gating, bounded output and state isolation。
- Create: `tests/test_app_pages_governance_graph.py` — page callback boundary、session selection、refresh/no-write integration。

---

### Task 1: Add validated compact Graph snapshot fingerprint

**Files:**
- Modify: `backend/services/agent_operations_service.py:_compact_governance_graph`
- Modify: `tests/test_agent_operations_service.py`

**Interfaces:**
- Consumes: validated `GovernanceGraphSnapshot`.
- Produces: `governanceGraph` compact mapping with `snapshotFingerprint` equal to `snapshot.graph_fingerprint` only for `status=available`; existing fields and unavailable/invalid behavior remain unchanged. Later rendering tasks consume `graph["snapshotFingerprint"]` without recomputation.

- [x] **Step 1: Write failing service tests.** Extend the valid projection fixture to assert exact `snapshotFingerprint`; assert unavailable projection remains exactly `{"status": "unavailable"}`, invalid projection has no guessed fingerprint, and the compact field is a lowercase SHA-256 copied from the validated snapshot.
- [x] **Step 2: Run focused tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_agent_operations_service.py -q`

  Expected: the new valid-projection assertion fails because compact output currently omits `snapshotFingerprint`; existing tests remain otherwise green.
- [x] **Step 3: Implement the minimal additive field.** Add only `"snapshotFingerprint": snapshot.graph_fingerprint` to `_compact_governance_graph`; do not alter evidence mapping, status, freshness, file reading, or writer behavior. Keep unavailable/invalid branches unchanged so they cannot expose a fabricated fingerprint.
- [x] **Step 4: Run focused tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_agent_operations_service.py -q`

  Expected: all AgentOperationsService tests PASS and no runtime/Graph files are created by read-only fixtures.
- [x] **Step 5: Submit Task 1 to strict Review.** Review only the service method and its focused tests; confirm additive schema compatibility, validated-source provenance and no-write boundary.
- [x] **Step 6: After Review PASS, Codex commits Task 1.**

  Run: `git add backend/services/agent_operations_service.py tests/test_agent_operations_service.py && git commit -m "feat: expose validated graph fingerprint in operations snapshot"`

### Task 2: Implement focused Graph rendering workspace

**Files:**
- Create: `governance_graph_rendering.py`
- Modify: `agent_operations_rendering.py`
- Create: `tests/test_governance_graph_rendering.py`
- Modify: `tests/test_agent_operations_rendering.py`

**Interfaces:**
- Consumes: compact `governanceGraph`, `query_graph(run_id, filters)`, optional `lineage_lookup(request)`, and optional future derived-result callbacks.
- Produces: `render_governance_graph_workspace(run, *, query_graph, lineage_lookup=None, comparison_lookup=None, risk_summary_lookup=None, impact_lookup=None) -> None`; it renders only bounded read models and maintains `AGENT_OPERATIONS_SELECTED_EVIDENCE` as bounded selection metadata.

- [x] **Step 1: Write failing rendering tests.** Cover valid summary/lineage/query, canonical node selection creating the exact E-1 request with run ID and compact fingerprint, non-canonical `hermes.json` disabled state, all E-1 statuses, missing/invalid Graph state, malformed callback payload, raw/absolute-path/secret non-rendering, max 12 evidence refs, and D-2/D-3/D-4 absent callbacks showing unavailable.
- [x] **Step 2: Run rendering tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_rendering.py tests/test_agent_operations_rendering.py -q`

  Expected: import or assertion failures because the focused workspace and lineage controls do not yet exist.
- [x] **Step 3: Implement bounded rendering helpers.** Extract only Graph-specific functions; preserve existing summary, query callback, refresh and selected-run behavior. Render safe tables/captions, validate callback result schema before display, construct E-1 input only from the selected compact canonical node row, gate registry filenames, and `st.pop("AGENT_OPERATIONS_SELECTED_EVIDENCE", None)` on run/fingerprint/node/gating mismatch. Never read a path, run a command, or store a lineage result.
- [x] **Step 4: Run rendering tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_governance_graph_rendering.py tests/test_agent_operations_rendering.py -q`

  Expected: all focused rendering and legacy Agent Operations tests PASS; malformed one-run data does not prevent other panels/runs from rendering.
- [x] **Step 5: Submit Task 2 to strict Review.** Review only the rendering helper, integration points and focused tests; verify no raw leak, no inference, callback-only data flow and session-state bounds.
- [x] **Step 6: After Review PASS, Codex commits Task 2.**

  Run: `git add governance_graph_rendering.py agent_operations_rendering.py tests/test_governance_graph_rendering.py tests/test_agent_operations_rendering.py && git commit -m "feat: add governance graph lineage rendering workspace"`

### Task 3: Wire app page callbacks and session/no-write boundary

**Files:**
- Modify: `app_pages.py:_render_agent_operations_tab`
- Create: `tests/test_app_pages_governance_graph.py`
- Modify: `tests/test_agent_operations_rendering.py` only for shared page fixtures if required

**Interfaces:**
- Consumes: `PROJECT_ROOT`, selected-run compact Graph result, `GovernanceGraphQueryService`, `GovernanceGraphEvidenceLineageService`, and Task 2 renderer signature.
- Produces: `query_graph(run_id, filters)` unchanged; `lineage_lookup(request)` that calls `EvidenceLineageInput.from_dict(request)` and E-1 service `.resolve(...).to_dict()` only; future comparison/risk/impact callbacks remain `None` until a validated source is wired.

- [x] **Step 1: Write failing page-boundary tests.** Assert app page injects both callbacks, lineage request reaches E-1 service with exact run/node/path/SHA/fingerprint, no CLI/subprocess/builder/persist call occurs, selected run/evidence keys are cleaned on incompatibility, and refresh preserves unrelated dashboard/upload/export session caches.
- [x] **Step 2: Run page tests to verify RED.**

  Run: `.venv/bin/python -m pytest tests/test_app_pages_governance_graph.py tests/test_agent_operations_rendering.py -q`

  Expected: missing callback injection or signature failures because the page currently injects only `query_graph`.
- [x] **Step 3: Implement read-only callback wiring.** Instantiate E-1 service inside the callback using `PROJECT_ROOT`; validate request through the E-1 model; return only bounded `.to_dict()`. Pass callbacks into the renderer without adding a writer, CLI, subprocess or new snapshot cache. Keep `AGENT_OPERATIONS_SNAPSHOT`, selected-run and Refresh lifecycle unchanged.
- [x] **Step 4: Run page/no-write tests to verify GREEN.**

  Run: `.venv/bin/python -m pytest tests/test_app_pages_governance_graph.py tests/test_agent_operations_rendering.py -q`

  Expected: callback boundary, session selection, malformed result isolation and tree/runtime no-write tests PASS.
- [x] **Step 5: Submit Task 3 to strict Review.** Review only `app_pages.py` and allowlisted tests; confirm app layer cannot become approval/dispatch/runtime/SQLite/Git writer and D-2/D-3/D-4 remain explicit unavailable without adapters.
- [x] **Step 6: After Review PASS, Codex commits Task 3.**

  Run: `git add app_pages.py tests/test_app_pages_governance_graph.py tests/test_agent_operations_rendering.py && git commit -m "feat: wire governance graph query and lineage callbacks"`

### Task 4: Full verification, final Review, Hermes and plan reconciliation

**Files:**
- Modify only if a strict Review finding requires it: Task 1–3 allowlisted files.
- Modify: this plan to mark completed steps and record evidence; no other documentation or source scope.

**Interfaces:**
- Consumes: Task 1–3 commits, Review PASS artifacts, approved E-2 spec and compact/read-model contracts.
- Produces: final acceptance evidence, reconciled plan, clean branch ready for separately authorized push/PR/merge.

- [x] **Step 1: Run compile and focused E-2/E-1 verification.**

  Run: `.venv/bin/python -m py_compile agent_operations_rendering.py governance_graph_rendering.py app_pages.py backend/services/agent_operations_service.py && .venv/bin/python -m pytest tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_governance_graph_rendering.py tests/test_app_pages_governance_graph.py tests/test_governance_graph_evidence_lineage_models.py tests/test_governance_graph_evidence_lineage_service.py tests/test_governance_graph_cli.py -q`

  Expected: compile succeeds and all focused suites PASS.
- [x] **Step 2: Run full project verification.**

  Run: `.venv/bin/python -m pytest -q && .venv/bin/python scripts/system_manager.py acceptance`

  Expected: full pytest and system acceptance PASS; any timeout/failure remains explicitly blocked.
- [x] **Step 3: Run strict final Review and Hermes.**

  Run the approved Review runner over the immutable Task 1–3 diff, preserve its findings-first PASS artifact, then run `.venv/bin/python scripts/hermes_post_change_check.py`.

  Expected: explicit Review PASS plus Hermes PASS; missing/unknown runner, timeout or degraded result blocks completion and is never relabeled PASS.
- [x] **Step 4: Verify invariants and clean worktree.** Confirm tracked worktree, runtime, Graph snapshots, canonical artifacts, SQLite integrity, baseline, formal revenue scope and Git state are unchanged except intended E-2 files. Separately verify Streamlit rerun/Refresh preserves dashboard、upload、AI、export session caches and only changes the approved snapshot/selection UX keys. Run `git diff --check` and `git status --short`.
- [x] **Step 5: Reconcile this plan against the E-2 spec.** Mark only completed tasks, record focused/full/system/Review/Hermes evidence, and leave D-2/D-3/D-4 adapter implementation out of scope. Do not push, create PR, merge or delete branch without separate authorization.

## Reconciliation evidence

- Task 1–3 implementation and focused reviews are complete in immutable commits `154eeb7`, `ef56984`, and `4b4f583`; each received strict Review PASS.
- Focused E-2/E-1 verification: `182 passed`.
- Full project verification: `1328 passed`; system acceptance status `passed` with Streamlit/API/Vue ready.
- Final strict Review: PASS; `git diff --check` clean and no P0/P1/P2 findings.
- Hermes: exit `0`, `Overall status: PASS`; 2026-05 baseline and formal scope matched; Governance Graph report remained read-only with `writes: 0`.
- D-2/D-3/D-4 remain explicit unavailable without validated adapters; no Graph snapshot, SQLite, baseline, canonical artifact, runtime or Git writer was introduced.

## Agent and Review Protocol

1. Run Context Agent with `--collect-only` before implementation and pass only its compact bundle to the worker; Context remains read-only.
2. Execute one approved Task at a time with TDD RED→GREEN in an isolated `codex/` branch/worktree.
3. Run the configured approved Review Agent after every Task against actual immutable diff; missing runner is blocked, never PASS.
4. Codex fixes concrete findings and commits only after Review PASS; Implementation/Review agents never commit or merge.
5. Task 4 requires full pytest, system acceptance, strict final Review and Hermes. Hermes is read-only and does not replace Review.

## Spec Coverage Check

- Validated compact fingerprint: Task 1.
- Graph summary, query, E-1 lineage drill-down, canonical gating, bounded rendering and safe states: Task 2.
- App callbacks, selected evidence key, refresh/session preservation and no-write boundary: Task 3.
- Focused/full tests, system acceptance, Review, Hermes, protected invariants and reconciliation: Task 4.
- D-2/D-3/D-4 remain unavailable unless an approved validated read model callback is supplied; no UI inference is introduced.
