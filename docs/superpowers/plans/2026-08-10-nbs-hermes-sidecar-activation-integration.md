# NBS Hermes Sidecar Activation Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 以 standalone Hermes MemoryProvider plugin 在單一受控 session 實際啟用 NBS `recall_on`，並輸出 bounded activation/provenance evidence。

**Architecture:** plugin 只讀取 activation envelope 與 bounded `memory-hints-v1`；`is_available()` fail-closed 綁定 immutable HEAD、session、scope fingerprints、model/reasoning，並由 current project root 重算 workspace fingerprint；`prefetch()` 注入 non-authoritative hints，`sync_turn()` 永遠 no-op。

**Tech Stack:** Python 3、Hermes `MemoryProvider` ABC、既有 NBS memory hint models、pytest、JSON runtime evidence。

## Global Constraints

- 普通 workflow 不自動啟用；沒有 per-run activation envelope 就 disabled。
- provider/model/reasoning 固定 `hermes`／`deepseek-v4-flash`／`medium`。
- 只讀 hints；writer 永遠 disabled；不改 NBS `MemorySidecarFeatureFlags` defaults。
- 不寫 SQLite、baseline、canonical artifacts、Graph、Git、approval、dispatch 或 network。
- 所有 activation／telemetry 僅寫 ignored `.nbs_agent_runtime` bounded artifacts。

### Task 1: Implement standalone plugin and contract tests

**Files:**
- Create: `integrations/hermes_nbs_sidecar/__init__.py`
- Create: `integrations/hermes_nbs_sidecar/plugin.py`
- Create: `tests/test_hermes_nbs_sidecar_plugin.py`

**Interfaces:**
- `NbsHermesSidecarProvider.is_available() -> bool`
- `NbsHermesSidecarProvider.initialize(session_id: str, **kwargs) -> None`
- `NbsHermesSidecarProvider.prefetch(query: str, *, session_id: str = "") -> str`
- `NbsHermesSidecarProvider.sync_turn(...) -> None`

- [ ] Step 1: Write RED tests for disabled default, valid envelope activation, immutable mismatch, model/reasoning mismatch, stale/invalid hints, bounded non-authoritative prefetch and no-op writer.
- [ ] Step 2: Run focused pytest and observe missing plugin failure.
- [ ] Step 3: Implement minimal provider adapter using existing `MemoryHints.from_dict`; require matching initialized Hermes session, recompute workspace fingerprint from current project root, and reject symlink/out-of-root/over-cap files and canonical-bound activation mismatch.
- [ ] Step 4: Run focused tests, py_compile and diff check.

### Task 2: Add one-run activation envelope and Hermes runner compatibility hook

**Files:**
- Create: `scripts/hermes_sidecar_activation.py`
- Test: `tests/test_hermes_sidecar_activation.py`
- Runtime only: `.nbs_agent_runtime/runs/<run-id>/sidecar-activation.json`, `memory-hints.json`, `sidecar-telemetry.json`

- [ ] Step 1: Add CLI to create/validate per-run envelope from existing runner manifest/activation receipt; reject dirty HEAD, mismatched fingerprints and non-medium reasoning.
- [ ] Step 2: Add deterministic bounded hints fixture for the live probe; do not write canonical data or source files.
- [ ] Step 3: Add an explicit `probe` path that loads Hermes' real `MemoryProvider` ABC and executes `initialize → prefetch → sync_turn(no-op)` against the envelope; keep control session provider unset.
- [ ] Step 4: Emit bounded probe telemetry. A real DeepSeek model turn is deferred to the separate operator-controlled acceptance gate; this task must not alter global Hermes config or auto-enable recall.

### Task 3: Strict review and live acceptance

- [ ] Run focused plugin/activation tests, relevant sidecar tests, py_compile and diff check.
- [ ] Run findings-first Review Agent on tracked integration files.
- [ ] Run Hermes read-only acceptance and, only with an explicitly staged treatment runner, classify live A/B as ready, rejected or blocked. Never auto-enable ordinary recall.

## Rollback

Delete ignored per-run envelope/telemetry and unset the plugin provider. Keep all ordinary defaults unchanged.
