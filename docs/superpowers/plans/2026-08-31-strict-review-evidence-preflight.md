# Strict Review Evidence Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Strict Review 前自動產生與 source seal 綁定的完整 deterministic evidence，避免因漏 targeted test、compile/static 或 provenance 而重複送審。

**Architecture:** 新增 read-only Preflight Controller，使用既有 Evidence Collector、Validation Runner 與 Verification Chain。Preflight 只執行 approved deterministic checks，寫入 bounded runtime artifacts，產生既有相容的 `verification-v1`，不直接呼叫 Review runner。Governance Graph、Memory Hub、Memory Sidecar 只提供 read-only、non-authoritative observations。

**Tech Stack:** Python 3.10、既有 `backend/agents` models/services、`scripts/verification_chain.py`、pytest、JSON canonical fingerprints、`.nbs_agent_runtime` atomic artifacts。

**Spec:** `docs/superpowers/specs/2026-08-31-strict-review-evidence-preflight-design.md`

## Global Constraints

- Preflight 不修改 SQLite、baseline、revenue、business rules、正式 cache、export schema 或 application runtime。
- Preflight 不執行 commit、merge、push、reset、rebase、stash、service management 或 dependency install。
- Preflight 不直接呼叫 Strict Review runner；只有 `status=ready` 才允許上層明確呼叫 Review。
- `verification-v1` command item 維持既有 exact schema；provenance 放在 Preflight artifact 與 Verification Chain gate metadata。
- Context `contextFingerprint` 維持獨立 evidence identity，透過 session/source provenance 綁定，不強行等於 `sourceFingerprint`。
- Governance Graph、Memory Hub、Memory Sidecar 永遠 read-only；Memory 不得改變 canonical verdict。
- 所有 runtime output 必須位於 `.nbs_agent_runtime/verification_sessions/<sessionId>/`，使用 atomic write，拒絕 symlink 與 path escape。
- 每個 Task 完成後執行 targeted tests、`git diff --check`，並建立 checkpoint commit；不得在 Task 內自行進入下一個 Task。

---

## File Map

| File | Responsibility |
|---|---|
| `backend/agents/strict_review_preflight_models.py` | Preflight result、coverage、diagnostic 與 cache identity models |
| `backend/agents/strict_review_preflight.py` | deterministic coverage planner、freshness 與 result validation |
| `backend/agents/strict_review_evidence_service.py` | Validation Runner orchestration、verification artifact generation |
| `backend/agents/strict_review_evidence_cache.py` | bounded command evidence cache lookup／reuse |
| `scripts/strict_review_evidence_preflight.py` | operator CLI，stdout 單一 JSON，無 Review dispatch |
| `scripts/verification_chain.py` | optional explicit preflight handoff before `run-review` |
| `backend/agents/governance_graph_preflight_adapter.py` | Graph read-only observation adapter |
| `backend/agents/memory_preflight_adapter.py` | Memory Hub／Sidecar read-only observation adapter |
| `tests/test_strict_review_preflight_models.py` | model／schema／status tests |
| `tests/test_strict_review_preflight.py` | coverage planner／freshness tests |
| `tests/test_strict_review_evidence_service.py` | evidence generation／fail-closed tests |
| `tests/test_strict_review_evidence_cache.py` | cache identity／reuse tests |
| `tests/test_strict_review_evidence_preflight_cli.py` | CLI contract tests |
| `tests/test_verification_chain.py` | chain integration and gate ordering tests |
| `tests/test_governance_graph_preflight_adapter.py` | Graph boundary tests |
| `tests/test_memory_preflight_adapter.py` | Memory boundary and degraded fallback tests |

---

### Task 1: 建立 Preflight model 與 strict schema validator

**Files:**
- Create: `backend/agents/strict_review_preflight_models.py`
- Test: `tests/test_strict_review_preflight_models.py`

**Interfaces:**
- `PreflightStatus = Literal["ready", "blocked", "invalid_evidence", "verification_failed", "degraded"]`
- `CoverageResult` fields: `targeted_tests`, `compile_static`, `diff_check`, `runner_capability`, `context_compatibility`, `governance_lineage`, `memory_readiness`
- `PreflightResult.to_dict() -> dict`
- `validate_preflight_result(payload: object) -> dict`
- `build_preflight_fingerprint(payload_without_fingerprint: dict) -> str`

- [ ] **Step 1: Write failing tests** for exact required keys, allowed statuses, bounded diagnostics, SHA-256 fingerprints, and rejection of unknown fields.
- [ ] **Step 2: Run tests to verify failure**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_strict_review_preflight_models.py -q
```

Expected: FAIL because the model module and validator do not exist.

- [ ] **Step 3: Implement minimal frozen dataclasses and validators** using the repository's canonical fingerprint helper; reject booleans where integers are expected and reject diagnostics over the existing bounded limit.
- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_strict_review_preflight_models.py -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/agents/strict_review_preflight_models.py tests/test_strict_review_preflight_models.py
git commit -m "feat: add strict review preflight models"
```

### Task 2: 實作 changed-surface coverage planner 與 freshness checks

**Files:**
- Create: `backend/agents/strict_review_preflight.py`
- Test: `tests/test_strict_review_preflight.py`

**Interfaces:**
- `plan_required_checks(changed_files: tuple[str, ...], test_files: tuple[str, ...]) -> tuple[str, ...]`
- `resolve_targeted_tests(changed_files: tuple[str, ...], available_tests: tuple[str, ...]) -> tuple[str, ...]`
- `validate_source_binding(preflight_source: str, verification_source: str, graph_source: str | None, session_source: str) -> None`
- `is_reusable(command_source: str, current_source: str, command_fingerprint: str, current_command_fingerprint: str, policy_fingerprint: str, current_policy_fingerprint: str, runner_fingerprint: str, current_runner_fingerprint: str) -> bool`

- [ ] **Step 1: Write failing tests** for backend Python, test Python, scripts, docs-only, mixed surfaces, missing targeted tests, source drift, and cache identity mismatch.
- [ ] **Step 2: Run the focused planner tests** and confirm failure.
- [ ] **Step 3: Implement deterministic mapping**: Python production files require compile, targeted tests, and diff check; test files require compile and targeted pytest; scripts require compile and CLI tests; docs-only uses docs validation.
- [ ] **Step 4: Implement source binding rules** so Context fingerprint stays independent while verification and Graph source fingerprints must match the session source fingerprint.
- [ ] **Step 5: Run tests and diff check**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_strict_review_preflight.py -q
git diff --check
```

- [ ] **Step 6: Commit**

```bash
git add backend/agents/strict_review_preflight.py tests/test_strict_review_preflight.py
git commit -m "feat: plan strict review evidence coverage"
```

### Task 3: 接入 existing Validation Runner 產生 deterministic evidence

**Files:**
- Create: `backend/agents/strict_review_evidence_service.py`
- Modify: `backend/agents/validation_runner.py` only where a reusable bounded result adapter is required
- Test: `tests/test_strict_review_evidence_service.py`

**Interfaces:**
- `run_preflight_checks(project_root: Path, plan: tuple[tuple[str, tuple[str, ...]], ...], *, source_fingerprint: str, runner: ValidationRunner) -> tuple[dict, ...]`；每個 plan item 明確帶 validation command id 與 arguments。
- `build_verification_v1(commands: tuple[dict, ...]) -> dict`
- `evaluate_check_results(commands: tuple[dict, ...]) -> PreflightStatus`

- [ ] **Step 1: Write failing tests** for targeted pytest pass/fail, compile pass/fail, diff check pass/fail, timeout, output cap, unapproved command, and tracked worktree unchanged.
- [ ] **Step 2: Run the tests and confirm failure.**
- [ ] **Step 3: Implement the adapter** by calling existing approved Validation Runner command IDs; normalize output to existing `verification-v1` command keys without adding fields to command items.
- [ ] **Step 4: Enforce fail-closed status mapping**: any required command failure returns `verification_failed`; command rejection or spawn failure returns `blocked`; no Review invocation is made.
- [ ] **Step 5: Run targeted tests and compile**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_strict_review_evidence_service.py -q
.venv/bin/python -m compileall -q backend/agents/strict_review_evidence_service.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/agents/strict_review_evidence_service.py backend/agents/validation_runner.py tests/test_strict_review_evidence_service.py
git commit -m "feat: generate strict review deterministic evidence"
```

### Task 4: 建立 bounded evidence artifact 與 cache reuse

**Files:**
- Create: `backend/agents/strict_review_evidence_cache.py`
- Test: `tests/test_strict_review_evidence_cache.py`
- Test: `tests/test_strict_review_evidence_service.py`

**Interfaces:**
- `CommandEvidenceCache.load(cache_path: Path, *, identity: dict) -> dict | None`
- `CommandEvidenceCache.store(cache_path: Path, *, identity: dict, command: dict) -> None`
- `cache_identity(source_fingerprint: str, command: list[str], policy_fingerprint: str, runner_fingerprint: str) -> str`
- `write_preflight_artifacts(session_dir: Path, preflight: dict, verification: dict) -> tuple[Path, Path]`

- [ ] **Step 1: Write failing tests** for cache hit, source change miss, policy change miss, runner identity change miss, malformed cache, symlink path, atomic write, bounded output, and no cache reuse after failed command.
- [ ] **Step 2: Run focused tests and confirm failure.**
- [ ] **Step 3: Implement cache identity and atomic artifact writer** under the session runtime directory; preserve existing `verification-v1` shape and place provenance in the enclosing artifact.
- [ ] **Step 4: Add cache reuse** so unchanged checks are skipped only when source, command, policy, runner identity, and successful status all match.
- [ ] **Step 5: Run tests**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_strict_review_evidence_cache.py tests/test_strict_review_evidence_service.py -q
```

- [ ] **Step 6: Commit**

```bash
git add backend/agents/strict_review_evidence_cache.py tests/test_strict_review_evidence_cache.py tests/test_strict_review_evidence_service.py
git commit -m "feat: cache strict review evidence safely"
```

### Task 5: 加入 Governance Graph 與 Memory read-only observations

**Files:**
- Create: `backend/agents/governance_graph_preflight_adapter.py`
- Create: `backend/agents/memory_preflight_adapter.py`
- Test: `tests/test_governance_graph_preflight_adapter.py`
- Test: `tests/test_memory_preflight_adapter.py`

**Interfaces:**
- `read_governance_observation(project_root: Path, *, session_source: str) -> dict`
- `read_memory_observation(project_root: Path, *, session_source: str) -> dict`
- `merge_non_authoritative_observations(preflight: dict, *, governance: dict | None, memory: dict | None) -> dict`

- [ ] **Step 1: Write failing boundary tests** proving Graph and Memory are read-only, source-bound, bounded, and unable to approve, dispatch, write workflow state, start Gateway, or change canonical status.
- [ ] **Step 2: Run focused tests and confirm failure.**
- [ ] **Step 3: Implement Graph adapter** using existing snapshot/lineage readers; return `ready`, `degraded`, or `invalid_evidence` without modifying Graph artifacts.
- [ ] **Step 4: Implement Memory adapter** using existing Memory Hub/Sidecar models; stale, unavailable, or malformed hints become bounded observation diagnostics and never replace pytest/compile proof.
- [ ] **Step 5: Run tests and compile**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_governance_graph_preflight_adapter.py tests/test_memory_preflight_adapter.py -q
.venv/bin/python -m compileall -q backend/agents/governance_graph_preflight_adapter.py backend/agents/memory_preflight_adapter.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/agents/governance_graph_preflight_adapter.py backend/agents/memory_preflight_adapter.py tests/test_governance_graph_preflight_adapter.py tests/test_memory_preflight_adapter.py
git commit -m "feat: add read only graph and memory preflight observations"
```

### Task 6: 建立 operator CLI 與 output contract

**Files:**
- Create: `scripts/strict_review_evidence_preflight.py`
- Test: `tests/test_strict_review_evidence_preflight_cli.py`

**Interfaces:**
- CLI command `strict_review_evidence_preflight.py`
- `main(argv: list[str] | None = None) -> int`
- exit codes: `0=ready/degraded`, `1=verification_failed`, `2=blocked`, `3=invalid_evidence`

- [ ] **Step 1: Write failing CLI tests** for valid ready output, missing session, stale source, failed validation, unsafe output path, symlink runtime, malformed context, and stdout single-JSON behavior.
- [ ] **Step 2: Run CLI tests and confirm failure.**
- [ ] **Step 3: Implement argument parsing** for `--session`, `--brief`, `--base`, `--head`, `--output`, and `--strict`; resolve all paths through existing runtime safety helpers.
- [ ] **Step 4: Implement the controller call**: load session, collect evidence, plan checks, execute checks, enrich read-only observations, write artifacts, and emit one bounded JSON object. Do not invoke Review.
- [ ] **Step 5: Run tests and manual contract probe**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_strict_review_evidence_preflight_cli.py -q
PYTHONPATH=. .venv/bin/python scripts/strict_review_evidence_preflight.py --help
```

- [ ] **Step 6: Commit**

```bash
git add scripts/strict_review_evidence_preflight.py tests/test_strict_review_evidence_preflight_cli.py
git commit -m "feat: add strict review evidence preflight cli"
```

### Task 7: 接入 Verification Chain，但維持 Review gate 分離

**Files:**
- Modify: `scripts/verification_chain.py`
- Test: `tests/test_verification_chain.py`

**Interfaces:**
- Add optional `run-preflight` subcommand to the existing CLI.
- `cmd_run_preflight(args) -> int`
- `run-review` accepts an optional preflight artifact path and rejects non-ready artifacts before runner construction.

- [ ] **Step 1: Write failing integration tests** for preflight ready → Review allowed, failed/blocked/invalid → Review not invoked, source drift → stale, degraded Graph/Memory → Review allowed when canonical evidence is complete, and old sessions not reusable.
- [ ] **Step 2: Run integration tests and confirm failure.**
- [ ] **Step 3: Add `run-preflight`** without changing existing `seal`, `run-review`, `run-full`, `run-hermes`, or `attest` behavior.
- [ ] **Step 4: Add explicit preflight validation before Review runner construction**; reject non-ready canonical evidence before spawning the model runner, while preserving the existing Review gate and session transitions.
- [ ] **Step 5: Run chain tests and compile**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_verification_chain.py tests/test_strict_review_evidence_preflight_cli.py -q
.venv/bin/python -m compileall -q scripts/verification_chain.py
```

- [ ] **Step 6: Commit**

```bash
git add scripts/verification_chain.py tests/test_verification_chain.py
git commit -m "feat: gate strict review on evidence preflight"
```

### Task 8: 完成 full verification、Hermes 與 rollout acceptance

**Files:**
- Modify: `docs/agents/REVIEW_AGENT_CONTRACT.md` only for the finalized preflight input contract
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md` only for the explicit preflight step
- Test: `tests/test_strict_review_preflight.py`
- Test: `tests/test_strict_review_evidence_service.py`
- Test: `tests/test_verification_chain.py`

**Interfaces:**
- Documentation must state that Preflight does not invoke Review and that `ready` is required before Strict Review.
- Final acceptance artifact is existing `completion-attestation-v1` with `strictReview=pass`, `fullPytest=pass`, and `hermes=pass`.

- [ ] **Step 1: Add end-to-end regression tests** covering fresh session → preflight → Review, full pytest, Hermes, and completion attestation; assert no SQLite/baseline/runtime business state changes.
- [ ] **Step 2: Run the focused complete preflight suite**

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_strict_review_preflight_models.py tests/test_strict_review_preflight.py tests/test_strict_review_evidence_service.py tests/test_strict_review_evidence_cache.py tests/test_strict_review_evidence_preflight_cli.py tests/test_verification_chain.py -q
```

- [ ] **Step 3: Update only the two agent contract documents** with the accepted preflight sequencing and fallback statuses; run docs/link checks.
- [ ] **Step 4: Run full verification**

```bash
PYTHONPATH=. .venv/bin/pytest tests/ -q
```

- [ ] **Step 5: Run Hermes read-only acceptance**

```bash
PYTHONPATH=. .venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

- [ ] **Step 6: Run a fresh production-like chain**: seal a new session, run Preflight, then Strict Review, full pytest, Hermes, and `attest`; record all results under the same source fingerprint.
- [ ] **Step 7: Perform rollout in Shadow mode** first; verify Preflight does not increase Streamlit page load or alter SQLite/business data, then document the switch to Advisory mode. Fail-closed enforcement requires a separate explicit rollout decision.
- [ ] **Step 8: Commit**

```bash
git add docs/agents/REVIEW_AGENT_CONTRACT.md docs/agents/CODEX_AGENT_DISPATCH.md tests/test_strict_review_preflight.py tests/test_strict_review_evidence_service.py tests/test_verification_chain.py
git commit -m "docs: finalize strict review preflight acceptance"
```

---

## Plan Self-Review

- Spec coverage: architecture and responsibility are covered by Tasks 1–3; cache and idempotency by Task 4; Graph/Memory boundaries by Task 5; CLI and fallback by Task 6; Review separation and source freshness by Task 7; testing, rollout, rollback and acceptance by Task 8.
- Existing `verification-v1` exact command schema is preserved; additional provenance is explicitly placed outside command items.
- Context fingerprint is intentionally distinct from source fingerprint and is validated through source/session provenance.
- No Task writes SQLite, baseline, revenue, business rules, formal cache, or application runtime.
- No Task contains placeholders, unspecified commands, or an implicit follow-on Task.
- Each Task has an independent test command and checkpoint commit.
