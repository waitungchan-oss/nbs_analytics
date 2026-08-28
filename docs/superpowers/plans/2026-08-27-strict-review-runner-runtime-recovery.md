# Strict Review Runner Runtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Strict Review 能以相容、可診斷且 fail-closed 的 runner 取得本輪 `verification-v1` evidence，並消除 cache schema mismatch 與 runner timeout 造成的 degraded runtime。

**Architecture:** 在 Review 前加入 read-only runner preflight，使用固定且已驗證可用的 model profile；由實際命令生成 bounded verification-v1 bundle，再交給受控 Review runner。任何 cache、model、freshness 或 timeout 問題都停止並輸出 `blocked_runtime`，不放寬 PASS gate。

**Tech Stack:** Python 3.10、pytest、現有 `scripts/review_agent.py`、`backend/agents/agent_runtime.py`、JSON runtime evidence、Codex CLI。

**Spec:** `docs/superpowers/specs/2026-08-27-strict-review-runner-runtime-recovery-design.md`

## Global Constraints

- 正式口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 不修改正式 SQLite、baseline、revenue scope、business rules、export schema 或 active business data。
- `verification-v1` top-level 只能包含 `commands`，不得加入 parser 不接受的欄位。
- Review、full pytest、Hermes、UI acceptance 是獨立 gate，不互相替代。
- Memory Hub、Context Agent、Review Agent 與 Hermes 均不得寫 SQLite、baseline、runtime business state 或 Git。
- 不以 synthetic response、舊 artifact 或手工 PASS 取代本輪真實 runner evidence。

### Task 1: 建立 runner preflight contract

**Files:**
- Create: `backend/agents/review_runner_profile.py`
- Test: `tests/test_review_runner_profile.py`
- Modify: `scripts/review_agent.py`

**Interfaces:**
- `load_runner_profile(path: Path) -> RunnerProfile`
- `preflight_runner(profile: RunnerProfile) -> RunnerPreflightResult`
- Result status only `ready` or `blocked_runtime`.

- [x] Write tests for supported model, missing executable, incompatible cache schema, and unsupported default model.
- [x] Run `pytest tests/test_review_runner_profile.py -q` and verify the new tests fail before implementation.
- [x] Implement fixed profile parsing, executable allowlist check, CLI version capture, and bounded diagnostics without installing dependencies.
- [x] Re-run the focused tests and assert all preflight failure modes are fail-closed.
- [x] Run `git diff --check` and record the result in the task evidence.

### Task 2: Make verification-v1 evidence fresh and reproducible

**Files:**
- Create: `backend/agents/verification_evidence_writer.py`
- Test: `tests/test_verification_evidence_writer.py`
- Modify: `scripts/review_agent.py`

**Interfaces:**
- `write_verification_v1(commands: Sequence[CommandResult], output: Path) -> Path`
- `validate_verification_v1(path: Path) -> tuple[CommandResult, ...]`

- [x] Add red tests for exact top-level schema, required command fields, bounded output tails, and non-zero exit codes.
- [x] Implement deterministic writer using only actual command results; preserve the existing parser contract.
- [x] Add freshness binding through the Review artifact rather than changing the verification-v1 JSON shape.
- [x] Run focused writer and Review parser tests.

### Task 3: Add bounded runner timeout diagnostics

**Files:**
- Modify: `backend/agents/agent_runtime.py`
- Modify: `backend/agents/review_agent_service.py`
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_review_agent_service.py`

**Interfaces:**
- Preserve `SubprocessAgentRunner.run(payload) -> dict`.
- Normalize timeout to a bounded Review `blocked` result containing phase, elapsed time, and redacted stderr tail.

- [x] Add timeout and non-zero runner tests with deterministic fake executables.
- [x] Implement diagnostics without retry loops or secret/raw payload capture.
- [x] Verify existing allowlist and read-only behavior tests remain green.

### Task 4: Compact Review payload and bind evidence fingerprints

**Files:**
- Modify: `backend/agents/review_agent_service.py`
- Test: `tests/test_review_agent_service.py`

**Interfaces:**
- Add an internal bounded projection function that consumes `EvidenceBundle`, context summary, verification-v1, and diff summary.
- Do not include raw SQLite rows, Excel bytes, complete logs, or secrets.

- [x] Add tests proving payload contains required contract fields and excludes sensitive/raw data.
- [x] Implement compact projection and fingerprint checks for current head, brief, and worktree.
- [x] Verify stale verification is blocked before runner invocation.

### Task 5: Repair and guard local Codex cache runtime

**Files:**
- Create: `scripts/codex_runner_preflight.py`
- Test: `tests/test_codex_runner_preflight.py`
- Modify: `NBS_HERMES_MONITORING.md` only if command contract needs documentation.

- [x] Add read-only inspection tests for cache file existence, JSON validity, required `base_instructions`, client version, and model entry.
- [x] Implement recoverable cache repair helper that writes only a runtime cache backup and never touches project data.
- [x] Require explicit compatible model selection; report newer-model incompatibility instead of silently falling back.
- [x] Run the helper against the local runtime and preserve the old cache as a timestamped backup.

### Task 6: Integrate preflight into Strict Review CLI

**Files:**
- Modify: `scripts/review_agent.py`
- Modify: `backend/agents/review_agent_service.py`
- Test: `tests/test_review_agent_cli.py`
- Test: `tests/test_review_agent_service.py`

- [x] Add CLI tests for ready, blocked cache, blocked model, and timeout outcomes.
- [x] Execute preflight before any agent subprocess call.
- [x] Keep `--strict` fail-closed and return non-zero only for actual blocked/invalid review outcomes according to existing CLI conventions.
- [x] Confirm a real `gpt-5.4` runner is attempted with the compact payload under the configured budget; runtime incompatibility remains explicitly blocked.

### Task 7: End-to-end evidence and failure-injection acceptance

**Files:**
- Create: `tests/test_strict_review_runner_acceptance.py`
- Modify: `tests/test_review_agent_service.py`

- [x] Test a real command-generated verification-v1 bundle and current worktree fingerprints.
- [x] Inject cache mismatch, stale evidence, runner timeout, and invalid model responses.
- [x] Assert every failure remains `blocked_runtime`/`blocked` and no SQLite, baseline, export, or trusted-reference pointer changes.
- [x] Run targeted acceptance tests.

### Task 8: Full verification and rollout decision

**Files:**
- Create: `.nbs_agent_runtime/reports/strict-review-runner-verification.json` (runtime-only)
- No tracked source changes unless findings require them.

- [x] Run full pytest and record the actual output tail in verification-v1.
- [x] Run `scripts/hermes_post_change_check.py --json` and record the actual PASS/degraded status.
- [x] Run system acceptance, compileall, and `git diff --check`.
- [x] Run Strict Review with the approved runner and record the actual blocked/context-overflow artifact.
- [x] Leave rollout blocked because Strict Review did not PASS; preserve the exact cache/runtime and diff-budget recovery reasons.
