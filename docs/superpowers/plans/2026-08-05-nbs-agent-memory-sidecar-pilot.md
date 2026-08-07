# NBS Agent Memory Sidecar Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 provider-neutral、read-only 對 Context Agent 的 bounded memory recall，以及只對 completed run 產生可追溯、去敏的 memory candidate，並以 A/B telemetry 驗證是否值得接入真實 TencentDB-Agent-Memory。

**Architecture:** 新增獨立 `memory_sidecar_*` domain modules，不把 sidecar 寫入 Governance Graph、canonical artifacts、正式 SQLite 或 Git authority。Recall 只產生 `memory-hints-v1` non-authoritative hints；distillation 只產生 `memory-candidate-v1`，由受控 adapter 寫入外部 sidecar。第一階段使用 fake adapter 與 shadow mode，真實 Gateway 放在後續 integration task。

**Tech Stack:** Python 3、既有 NBS dataclass/JSON model patterns、pytest、SHA-256 canonical fingerprint、現有 `EvidenceCollector`／`ContextAgentService`／`WorkflowStore`／Hermes scripts；不新增 Node.js 或 runtime dependency。

## Global Constraints

- Canonical artifacts remain the only source of truth；memory hints 永遠標示 `non_authoritative_memory`。
- 不修改正式 SQLite、baseline、revenue scope、business rules、rollback 或 export schema。
- 不修改 Governance Graph schema、建立 memory node/edge、approval、dispatch、snapshot 或 workflow control 入口。
- 只有具備 Review PASS、full verification PASS、Hermes PASS 與必要 documentation/no-doc outcome 的 completed run 才可產生 write candidate。
- 不保存 secrets、`.env`、auth home、SQLite/Excel/CSV rows、完整 logs、prompt、完整 diff 或內部推理。
- Recall 上限：最多 3 筆、最多 6,000 UTF-8 bytes、800 ms timeout；timeout/degraded/invalid/conflict 時 fail closed 並繼續 canonical pipeline。
- Memory sidecar data directory 必須與正式 SQLite、`.nbs_agent_runtime/` 和 Obsidian vault 分離。
- 第一階段不安裝或啟動真實 TencentDB Gateway、LLM extraction、remote embedding、Memory Hub、Wiki、CodeGraph 或 Short-term Offload。
- 每個 Task 必須先寫 failing tests，再做最小實作；完成後交本地 Review Agent findings-first review，再跑 targeted verification。
- 每個 Task 開始前只使用短 task brief 或已裁剪 bundle 執行 `scripts/context_agent.py --collect-only`；不得將完整 Design Spec 直接當 Context Agent objective，避免 `contextOverflow`。
- Review Agent 只讀實際 diff、測試與 evidence；Hermes 只做 read-only runtime／schema／fallback 驗收，兩者不可互換。

---

## File Map

### 新增

- `backend/agents/memory_sidecar_models.py` — `MemoryCandidate`、`MemoryHint`、`MemoryHints`、`MemorySourceRef` 的 strict models 與 fingerprints。
- `backend/agents/memory_sidecar_policy.py` — allowlist、byte/token/time caps、freshness、status 與 forbidden-field policy。
- `backend/agents/memory_sidecar_adapter.py` — provider-neutral adapter protocol、fake adapter 與 deterministic error states。
- `backend/agents/memory_sidecar_sanitizer.py` — completed-run gate 檢查、sourceRef 驗證、redaction 與 candidate 建立。
- `backend/agents/memory_sidecar_service.py` — bounded recall、candidate persistence orchestration、canonical fallback。
- `backend/agents/memory_sidecar_telemetry.py` — `memory-sidecar-telemetry-v1` event 與 A/B aggregation。
- `agent_config/memory_sidecar_policy.json` — schema version、limits、TTL、allowlisted memory kinds 與 denied patterns。
- `docs/briefs/memory-sidecar-task1-brief.md` — Context Agent 使用的短 Task brief，不注入完整 Design Spec。
- `docs/agents/MEMORY_SIDECAR_CONTRACT.md` — runtime contract、authority、security、failure semantics。
- `tests/test_memory_sidecar_models.py`
- `tests/test_memory_sidecar_policy.py`
- `tests/test_memory_sidecar_adapter.py`
- `tests/test_memory_sidecar_sanitizer.py`
- `tests/test_memory_sidecar_service.py`
- `tests/test_memory_sidecar_telemetry.py`
- `tests/test_memory_sidecar_context_integration.py`
- `tests/test_memory_sidecar_hermes_boundary.py`

### 修改

- `backend/agents/context_agent_service.py` — 只加入 optional `memory_hints`，保留原有 bundle fingerprint 與 token caps；不得讓 hints 改變 canonical evidence payload。
- `backend/agents/workflow_store.py` — 只讀取 completed run gate evidence 給 sanitizer；不把 sidecar memory 寫入 run artifacts。
- `backend/agents/agent_runtime.py` — 只在既有 bounded telemetry interface 上增加 sidecar event aggregation；不放寬 executable/working-tree allowlist。
- `scripts/hermes_post_change_check.py` — 只驗證 sidecar schema、caps、permission 與 fallback report；不得啟動 Gateway 或寫入 sidecar。
- `docs/agents/NBS_AGENT_ARCHITECTURE.md` — 加入 sidecar 的責任矩陣與非權威邊界。
- `docs/agents/CODEX_AGENT_DISPATCH.md` — 加入短 brief、memory hint 分離與 completed-run distillation 規則。
- `tests/test_context_agent_service.py`、`tests/test_agent_runtime.py`、`tests/test_hermes_post_change_check.py` — 增加 regression assertions。

---

### Task 1: Memory contracts、policy 與 short-brief evidence boundary

**Files:**

- Create: `backend/agents/memory_sidecar_models.py`
- Create: `backend/agents/memory_sidecar_policy.py`
- Create: `agent_config/memory_sidecar_policy.json`
- Create: `tests/test_memory_sidecar_models.py`
- Create: `tests/test_memory_sidecar_policy.py`

**Interfaces:**

- `MemorySourceRef.from_dict(payload: Mapping[str, object]) -> MemorySourceRef`
- `MemoryCandidate.from_dict(payload: Mapping[str, object]) -> MemoryCandidate`
- `MemoryCandidate.from_parts(*, kind: str, summary: str, source_refs: Sequence[MemorySourceRef], source_status: str, generated_at: str, expires_at: str, confidence: str, policy_version: str) -> MemoryCandidate`
- `MemoryCandidate.to_dict() -> dict[str, object]`
- `MemoryHints.from_dict(payload: Mapping[str, object]) -> MemoryHints`
- `MemoryHints.empty(*, query_fingerprint: str, status: str = "empty") -> MemoryHints`
- `MemoryHints.to_dict() -> dict[str, object]`
- `MemorySidecarPolicy.from_file(path: Path) -> MemorySidecarPolicy`
- `MemorySidecarPolicy.validate_limits(*, max_items: int, max_bytes: int, timeout_ms: int) -> None`
- `MemorySidecarPolicy.is_allowed_kind(kind: str) -> bool`

- [ ] **Step 1: Write failing schema tests**

  Cover exact top-level keys, lowercase SHA-256 format, allowed kinds, bounded summary bytes, sourceRef path restrictions, freshness order, confidence/status enums, deterministic `memoryId` and `memoryFingerprint`, and rejection of unexpected keys.

  ```python
  def test_candidate_fingerprint_is_deterministic_and_source_bound():
      candidate = MemoryCandidate.from_parts(
          kind="verification_pattern",
          summary="Use the focused Hermes pack before full acceptance.",
          source_refs=(source_ref("run-1", "verification.json"),),
          source_status="completed",
          generated_at="2026-08-05T00:00:00+00:00",
          expires_at="2026-11-05T00:00:00+00:00",
          confidence="high",
          policy_version="memory-freshness-v1",
      )
      assert len(candidate.memory_id) == 64
      assert candidate.memory_fingerprint == candidate.recompute_fingerprint()
  ```

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_models.py tests/test_memory_sidecar_policy.py -q`

  Expected: FAIL because the new models and policy do not exist.

- [ ] **Step 3: Implement strict models and policy**

  Use immutable dataclasses or the repository's existing strict model pattern. Hash only canonical JSON, sort source refs before hashing, enforce regular-file relative refs, reject absolute/traversal/symlink paths, and apply the exact limits from `agent_config/memory_sidecar_policy.json`.

- [ ] **Step 4: Re-run focused tests**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_models.py tests/test_memory_sidecar_policy.py -q`

  Expected: PASS, including malformed payload, secret field, over-cap, stale and fingerprint mismatch cases.

- [ ] **Step 5: Run local Context Agent with a short Task 1 brief**

  Create `docs/briefs/memory-sidecar-task1-brief.md` with exactly this bounded content:

  ```markdown
  # Memory Sidecar Task 1 Brief
  Objective: define strict memory-candidate and memory-hints contracts for a non-authoritative sidecar.
  Allowed files: backend/agents/memory_sidecar_models.py, backend/agents/memory_sidecar_policy.py, agent_config/memory_sidecar_policy.json, tests/test_memory_sidecar_models.py, tests/test_memory_sidecar_policy.py.
  Required evidence: existing evidence_models fingerprint/token helpers, evidence allowlist, Context Agent limits.
  Forbidden: SQLite, baseline, runtime writes, Git operations, external Gateway, raw logs, secrets.
  Recommended tests: schema validation, fingerprint stability, byte caps, path safety, stale/freshness states.
  ```

  Run: `.venv/bin/python scripts/context_agent.py --brief docs/briefs/memory-sidecar-task1-brief.md --collect-only --format json --output .nbs_agent_runtime/memory-sidecar-task1-context.json`

  Expected: `schemaVersion=context-evidence-v1`, `contextOverflow=false`, and only Task 1 allowlisted files included. The brief is an implementation artifact to be created in this Task and must contain no full Design Spec.

- [ ] **Step 6: Commit the contract task**

  ```bash
  git add backend/agents/memory_sidecar_models.py backend/agents/memory_sidecar_policy.py agent_config/memory_sidecar_policy.json tests/test_memory_sidecar_models.py tests/test_memory_sidecar_policy.py docs/briefs/memory-sidecar-task1-brief.md
  git commit -m "feat: add memory sidecar contracts"
  ```

  After commit, provide the actual diff and targeted test output to Review Agent. Do not proceed if findings are not resolved.

### Task 2: Provider-neutral adapter、fake provider 與 bounded recall

**Files:**

- Create: `backend/agents/memory_sidecar_adapter.py`
- Create: `backend/agents/memory_sidecar_service.py`
- Create: `tests/test_memory_sidecar_adapter.py`
- Create: `tests/test_memory_sidecar_service.py`
- Modify: `backend/agents/memory_sidecar_models.py` only if Task 1 review identifies an interface gap

**Interfaces:**

- `class MemorySidecarProvider(Protocol)`
- `MemorySidecarProvider.recall(*, query: str, query_fingerprint: str, limits: RecallLimits) -> MemoryHints`
- `MemorySidecarProvider.write_candidate(candidate: MemoryCandidate) -> WriteResult`
- `FakeMemorySidecarProvider(recall_results: Mapping[str, MemoryHints] | None = None, write_results: Mapping[str, WriteResult] | None = None)`
- `FakeMemorySidecarProvider(..., raise_error: MemorySidecarProviderError | None = None)`
- `MemorySidecarProviderError(code: str, summary: str)`
- `MemorySidecarService.recall(*, query: str, provider: MemorySidecarProvider) -> MemoryHints`

- [ ] **Step 1: Write failing adapter tests**

  Test deterministic ready/empty/timeout/degraded/invalid results, max three hints, six-thousand-byte cap, provider exceptions converted to `degraded`, and no retry loop that blocks the caller.

  ```python
  def test_recall_timeout_returns_degraded_empty_hints():
      provider = FakeMemorySidecarProvider(raise_error=MemorySidecarProviderError("timeout", "provider timed out"))
      result = MemorySidecarService(policy).recall(query="review runtime", provider=provider)
      assert result.status == "timeout"
      assert result.hints == ()
  ```

- [ ] **Step 2: Run adapter tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_adapter.py tests/test_memory_sidecar_service.py -q`

  Expected: FAIL because the provider protocol and service do not exist.

- [ ] **Step 3: Implement fake provider and bounded service**

  Keep the provider interface independent from TencentDB SDK types. Validate every response through `MemoryHints.from_dict()`, clamp only values explicitly allowed by policy, reject stale/invalid hints, and return an empty non-blocking result on timeout/degraded states.

- [ ] **Step 4: Re-run adapter tests**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_adapter.py tests/test_memory_sidecar_service.py -q`

  Expected: PASS, with no network access and no files written outside the test `tmp_path`.

- [ ] **Step 5: Local Review Agent checkpoint**

  Run: `.venv/bin/python scripts/review_agent.py --help` to select the existing read-only review invocation, then review only the Task 2 immutable diff, targeted tests and Task 2 context bundle. Expected verdict: `pass` or explicit findings; do not treat provider availability as Review evidence.

- [ ] **Step 6: Commit the adapter task**

  ```bash
  git add backend/agents/memory_sidecar_adapter.py tests/test_memory_sidecar_adapter.py tests/test_memory_sidecar_service.py
  git commit -m "feat: add bounded memory sidecar adapter"
  ```

### Task 3: Completed-run sanitizer、sourceRefs、freshness 與 fail-closed writer

**Files:**

- Create: `backend/agents/memory_sidecar_sanitizer.py`
- Modify: `backend/agents/workflow_store.py` only through a read-only gate reader if existing API cannot expose required artifacts
- Create: `tests/test_memory_sidecar_sanitizer.py`

**Interfaces:**

- `CompletedRunGate.from_run(root: Path, run_id: str) -> CompletedRunGate`
- `CompletedRunGate.is_memory_eligible() -> bool`
- `MemorySanitizer.sanitize_completed_run(*, gate: CompletedRunGate, allowed_kinds: Sequence[str]) -> tuple[MemoryCandidate, ...]`
- `MemorySanitizer.validate_source_ref(*, source_ref: MemorySourceRef, run_root: Path) -> None`
- `MemorySanitizer.redact_summary(summary: str) -> str`

- [ ] **Step 1: Write failing sanitizer tests**

  Cover missing gate, awaiting authorization, Review non-pass, verification non-pass, Hermes non-pass, missing documentation/no-doc outcome, stale upstream artifact, protected incident, secrets, absolute/traversal/symlink refs, baseline/business-rule content, byte cap and deterministic candidate fingerprints.

- [ ] **Step 2: Run sanitizer tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_sanitizer.py -q`

  Expected: FAIL because sanitizer and completed-run gate reader do not exist.

- [ ] **Step 3: Implement read-only gate reader and sanitizer**

  Read only canonical run artifacts. Never write candidate output into `.nbs_agent_runtime/runs/`; return explicit `blocked_*` status or an empty tuple. Redaction must remove values rather than replace them with guessed summaries. Preserve only bounded source identity, not raw content.

- [ ] **Step 4: Re-run sanitizer tests**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_sanitizer.py -q`

  Expected: PASS with zero writes to project runtime, SQLite or Git.

- [ ] **Step 5: Review and Hermes boundary checkpoint**

  Review Agent checks only source allowlist, read-only behavior and negative tests. Run `.venv/bin/python scripts/hermes_post_change_check.py` with the focused test pack; expected result is PASS or a documented environment-only blocker.

- [ ] **Step 6: Commit the sanitizer task**

  ```bash
  git add backend/agents/memory_sidecar_sanitizer.py tests/test_memory_sidecar_sanitizer.py backend/agents/workflow_store.py
  git commit -m "feat: add fail-closed memory sanitizer"
  ```

### Task 4: Context Agent non-authoritative integration

**Files:**

- Modify: `backend/agents/context_agent_service.py`
- Create: `tests/test_memory_sidecar_context_integration.py`
- Modify: `tests/test_context_agent_service.py`

**Interfaces:**

- `build_context_evidence_payload(bundle: EvidenceBundle, *, memory_hints: MemoryHints | None = None) -> dict`
- `ContextAgentService.summarize(..., memory_hints: MemoryHints | None = None) -> dict`
- `context_summary_from_evidence_payload(payload: dict) -> dict` must keep `memoryHints` separate from `evidence`.

- [ ] **Step 1: Write failing integration tests**

  Assert memory hints are labeled `non_authoritative_memory`, excluded from bundle fingerprint inputs used for canonical evidence identity, bounded by policy, and ignored when stale/conflict/timeout/degraded. Assert existing calls with `memory_hints=None` produce byte-for-byte compatible context evidence.

- [ ] **Step 2: Run integration tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_context_integration.py tests/test_context_agent_service.py -q`

  Expected: FAIL on the new optional behavior while existing context tests continue to identify regressions.

- [ ] **Step 3: Implement optional hints path**

  Add the smallest optional parameter; do not change existing schema keys or token limits. Place hints in a clearly named non-authoritative field and ensure canonical collector evidence remains the only input to `bundleFingerprint`.

- [ ] **Step 4: Re-run integration and regression tests**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_context_integration.py tests/test_context_agent_service.py -q`

  Expected: PASS; `contextOverflow` behavior remains unchanged for canonical evidence, and hints cannot bypass the 12,000 input / 1,500 output caps.

- [ ] **Step 5: Local Review Agent checkpoint**

  Review the actual diff for evidence authority leakage. Any finding that hints alter canonical fingerprint, formal status, or review evidence is blocking.

- [ ] **Step 6: Commit the Context integration**

  ```bash
  git add backend/agents/context_agent_service.py tests/test_memory_sidecar_context_integration.py tests/test_context_agent_service.py
  git commit -m "feat: add non-authoritative memory hints to context"
  ```

### Task 5: Telemetry、shadow mode、A/B fixture 與 feature flags

**Files:**

- Create: `backend/agents/memory_sidecar_telemetry.py`
- Create: `tests/test_memory_sidecar_telemetry.py`
- Modify: `backend/agents/agent_runtime.py`
- Modify: `agent_config/memory_sidecar_policy.json`

**Interfaces:**

- `MemorySidecarTelemetryEvent.from_parts(*, run_id: str, mode: str, query_fingerprint: str, status: str, latency_ms: int, hint_count: int, input_bytes: int, fallback: bool, redaction_count: int) -> MemorySidecarTelemetryEvent`
- `MemorySidecarTelemetryAggregator.aggregate(events: Iterable[MemorySidecarTelemetryEvent]) -> dict[str, object]`
- `MemorySidecarFeatureFlags.recall_enabled: bool`
- `MemorySidecarFeatureFlags.writer_enabled: bool`
- `MemorySidecarFeatureFlags.shadow_mode: bool`

- [ ] **Step 1: Write failing telemetry tests**

  Cover exact schema, no raw query text, no summaries, bounded integer ranges, p95 latency aggregation, ready/empty/timeout/degraded/stale/conflict counts, recall-on/off cohort separation, and disabled flags producing no sidecar calls.

- [ ] **Step 2: Run telemetry tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_telemetry.py -q`

  Expected: FAIL because telemetry types and aggregator do not exist.

- [ ] **Step 3: Implement bounded telemetry and flags**

  Reuse existing runtime telemetry rotation and ignored runtime storage. Record only identities, counts, status, latency, caps and fingerprints. Never write raw query, memory summary or source content.

- [ ] **Step 4: Re-run telemetry tests**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_telemetry.py tests/test_agent_runtime.py -q`

  Expected: PASS, and existing agent telemetry tests remain green.

- [ ] **Step 5: Build shadow A/B fixture**

  Add deterministic fixtures for 10 non-R2 task profiles with recall off/on, equal brief/HEAD/allowed files/commands, and aggregation output for token, latency, exploration count, findings and fallback. The fixture must not call a real LLM or Gateway.

- [ ] **Step 6: Commit telemetry task**

  ```bash
  git add backend/agents/memory_sidecar_telemetry.py backend/agents/agent_runtime.py agent_config/memory_sidecar_policy.json tests/test_memory_sidecar_telemetry.py
  git commit -m "feat: add memory sidecar telemetry and shadow mode"
  ```

### Task 6: Documentation、Hermes read-only contract 與 final pilot acceptance

**Files:**

- Create: `docs/agents/MEMORY_SIDECAR_CONTRACT.md`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Modify: `scripts/hermes_post_change_check.py`
- Create: `tests/test_memory_sidecar_hermes_boundary.py`
- Modify: `tests/test_hermes_post_change_check.py`

**Interfaces:**

- Hermes report schema: `memory-sidecar-hermes-report-v1`
- Required fields: `policy="read-only"`, `invocations=0`, `writes=0`, `status`, `artifactCounts`, `fallbackChecks`, `diagnostics`.

- [ ] **Step 1: Write failing documentation and Hermes boundary tests**

  Assert docs state sidecar is non-authoritative, NBS Hermes is not Tencent Hermes provider, Gateway is never started by Hermes check, memory artifacts are bounded, and writes/invocations remain zero. Assert malformed, stale, over-cap, absolute-path and permission evidence returns invalid/blocked.

- [ ] **Step 2: Run tests to verify failure**

  Run: `.venv/bin/python -m pytest tests/test_memory_sidecar_hermes_boundary.py tests/test_hermes_post_change_check.py -q`

  Expected: FAIL on the new report and contract assertions.

- [ ] **Step 3: Implement read-only Hermes report and documentation**

  Extend Hermes only to inspect bounded sidecar evidence or test fixtures. Do not add Gateway startup, provider install, network calls, prune, apply, approval or runtime writes.

- [ ] **Step 4: Run focused final acceptance**

  ```bash
  .venv/bin/python -m pytest tests/test_memory_sidecar_*.py tests/test_context_agent_service.py tests/test_agent_runtime.py tests/test_hermes_post_change_check.py -q
  .venv/bin/python -m py_compile backend/agents/memory_sidecar_models.py backend/agents/memory_sidecar_policy.py backend/agents/memory_sidecar_adapter.py backend/agents/memory_sidecar_sanitizer.py backend/agents/memory_sidecar_service.py backend/agents/memory_sidecar_telemetry.py
  .venv/bin/python scripts/hermes_post_change_check.py
  ```

  Expected: all focused tests PASS, compile PASS, Hermes `overallStatus=pass`; any unrelated full-suite failure must be reported separately and cannot be hidden.

- [ ] **Step 5: Run system-level acceptance**

  Run: `.venv/bin/python -m pytest -q` and `.venv/bin/python scripts/system_manager.py acceptance`.

  Expected: no canonical SQLite/baseline/workflow regression, no tracked worktree mutation from validation, and sidecar failure fallback remains explicit.

- [ ] **Step 6: Review Agent final findings-first review**

  Review the complete immutable implementation diff against the approved Task contracts. Required result: `verdict=pass`, no baseline risk, no evidence-authority leakage, and all residual risks listed.

- [ ] **Step 7: Decide pilot outcome**

  Mark the pilot `completed` only if schema/security/fallback tests, A/B evidence, Review PASS, full verification PASS, Hermes PASS and documentation/no-doc outcome all agree. Otherwise mark the specific blocked reason and do not install the real TencentDB Gateway.

- [ ] **Step 8: Commit documentation and acceptance task**

  ```bash
  git add docs/agents/MEMORY_SIDECAR_CONTRACT.md docs/agents/NBS_AGENT_ARCHITECTURE.md docs/agents/CODEX_AGENT_DISPATCH.md scripts/hermes_post_change_check.py tests/test_memory_sidecar_hermes_boundary.py tests/test_hermes_post_change_check.py
  git commit -m "docs: add memory sidecar governance contract"
  ```

---

## Execution Checkpoints

每個 Task 都必須依以下順序停留在 checkpoint：

1. 建立短 Task brief，執行 local Context Agent `--collect-only`。
2. 寫 failing tests，確認 red。
3. 只修改該 Task 的 allowed files。
4. 跑 targeted tests、`py_compile` 和 `git diff --check`。
5. 將實際 diff、test output 與 evidence 交本地 Review Agent 做 findings-first review。
6. 修正 findings；Review PASS 仍不等於完成。
7. 需要時執行 Hermes read-only focused check。
8. Codex 整理 evidence 並 commit；Implementation Agent 不得自行選擇下一 Task。

### Local agent usage

- Context Agent：只收集短 brief、allowlisted files、symbols、tests 和 policy；不得修改任何檔案。
- Review Agent：只讀 immutable diff、Task contract、tests 和 verification evidence；不得把 memory hint 當正式證據。
- Hermes：只做 read-only schema、permission、freshness、fallback、system acceptance；不安裝或啟動 Tencent Gateway。
- Documentation Agent：只有 Review PASS、full verification PASS、Hermes PASS 後才可處理 contract backfill；輸出 proposal，不自動 apply。

## Out of Scope After Pilot

即使方案 A 通過，也不自動進入方案 B 或 C。Short-term Offload、Mermaid canvas、真實 TencentDB Gateway、Memory Hub、Wiki、CodeGraph、Skill memory、FastAPI/Streamlit memory UI 必須另立 Design Spec 與 Implementation Plan。
