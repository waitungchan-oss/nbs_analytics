# NBS Live Hermes A/B Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一次性 isolated `HERMES_HOME` 中，以真實 DeepSeek V4 Flash Max turns 產生同一 immutable HEAD 的 recall-off／recall-on receipts，並由既有 validator 判定 Live A/B `ready`、`acceptance_rejected` 或 `blocked_runner_capability`。

**Architecture:** 將 credential、Hermes config、sidecar plugin 與兩次 model turn 限制在 `.nbs_agent_runtime/live-ab/<acceptance-id>/`。Runner 只讀取現有 canonical fingerprints，使用 process-local credentials，產生 bounded receipts；既有 comparison service 與 Hermes acceptance 維持 read-only。

**Tech Stack:** Python 3、既有 `backend.agents.runner_capability_evidence`、Hermes `ChatCompletionsTransport`、pytest、`py_compile`、isolated `HERMES_HOME`、`.nbs_agent_runtime`。

## Global Constraints

- 不修改 `~/.hermes/config.yaml`、`~/.hermes/plugins`、正式 SQLite、baseline、Graph authority、Git history 或 production recall defaults。
- Credential 只接受 allowlist 的 `DEEPSEEK_API_KEY`／`DEEPSEEK_BASE_URL` environment variables 或 protected file descriptor；不得寫入任何 artifact、log 或 exception。
- Control／treatment 只允許 `recallMode=off/sequence=1` 與 `recallMode=on/sequence=2` 的差異。
- provider 必須是 `hermes`，model 必須是 `deepseek-v4-flash`，UI reasoning profile 必須是 `max`；`max` 必須跨 manifest、plugin envelope、turn input、receipt、validator、comparison 一致；writer、baseline、formal-scope、Review、Hermes flags 必須保持 safe。
- 每個 Task 先 TDD，再 focused tests、`py_compile`、`git diff --check`，完成後交 findings-first Review；Implementation Agent 不得自行 commit、push、PR 或 merge。
- 沒有兩個真實完整 receipts 時，結果只能是 `blocked_runner_capability` 或 `acceptance_rejected`，不可宣稱 PASS；control 必須有 canonical `status=disabled` receipt，treatment 必須有 canonical `status=activated` receipt。

---

### Task 1: Normalize live receipt and identity contracts

**Files:**
- Modify: `scripts/hermes_turn_receipt.py`
- Modify: `scripts/hermes_runner_capability_hook.py`
- Modify: `scripts/hermes_sidecar_activation.py`
- Modify: `integrations/hermes_nbs_sidecar/plugin.py`
- Modify: `backend/agents/runner_capability_evidence.py`
- Test: `tests/test_hermes_turn_receipt.py`
- Test: `tests/test_hermes_runner_capability_hook.py`
- Test: `tests/test_runner_capability_evidence.py`
- Test: `tests/test_hermes_sidecar_activation.py`
- Test: `tests/test_hermes_nbs_sidecar_plugin.py`

**Interfaces:**
- Consumes: existing manifest identity, `turn_input`, client config and Hermes transport response.
- Produces: validated receipt fields `reasoningProfile`, `responseId`, `priorResponseIds`, `provenanceSourceCount`, `provenanceCoveredCount`, `cleanWorktreeFingerprint`, `cacheReplayDetected` and canonical activation receipt.

- [ ] **Step 1: Write failing tests** for `reasoningProfile="max"`, response-ID replay detection, missing prior-response evidence, malformed usage, and exact immutable identity matching.
- [ ] **Step 2: Run focused tests to verify failure**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_hermes_turn_receipt.py tests/test_hermes_runner_capability_hook.py tests/test_runner_capability_evidence.py -q -p no:cacheprovider
```

Expected: FAIL on missing max identity / receipt contract behavior.

- [ ] **Step 3: Implement minimal contract changes**
  - Replace medium-only identity checks in hook, plugin and evidence model with an explicit `reasoningProfile=max` contract; reject contradictory medium values rather than silently fallback.
  - Validate `prior_response_ids` as bounded non-empty strings when replay evidence is required.
  - Derive `cacheReplayDetected` from the real response ID set; never use a fixed boolean in the live path.
  - Add `cleanWorktreeFingerprint` to manifest and receipt; re-check full HEAD, clean status and fingerprint in `record`.
  - Permit canonical activation status `disabled` only for control and `activated` only for treatment; reject missing or contradictory activation state.
  - Reject missing usage, response ID, source coverage, sensitive scan or activation receipt with exit 2 and no completed receipt.
- [ ] **Step 4: Run focused tests and static checks**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_hermes_turn_receipt.py tests/test_hermes_runner_capability_hook.py tests/test_runner_capability_evidence.py -q -p no:cacheprovider
.venv/bin/python -m py_compile scripts/hermes_turn_receipt.py scripts/hermes_runner_capability_hook.py backend/agents/runner_capability_evidence.py
git diff --check
```

Expected: all focused tests PASS; no secret or raw output in receipt serialization.

- [ ] **Step 5: Stop for findings-first Review** using only this Task's diff and test output.

### Task 2: Build isolated Hermes home and credential boundary

**Files:**
- Create: `scripts/hermes_isolated_profile.py`
- Modify: `integrations/hermes_nbs_sidecar/plugin.py`
- Test: `tests/test_hermes_isolated_profile.py`
- Test: `tests/test_hermes_nbs_sidecar_plugin.py`

**Interfaces:**
- Consumes: project root, acceptance ID, immutable manifest, sidecar source, process environment.
- Produces: `IsolatedHermesProfile(home_dir, config_path, plugin_dir, credential_env_names, profile_fingerprint, plugin_checksum)` and bounded blocked evidence on failure.

- [ ] **Step 1: Write failing tests** for isolated directory creation, plugin copy allowlist, minimal config, global-home non-write guarantee, credential-name allowlist, and secret redaction.
- [ ] **Step 2: Run the isolated-profile test file and confirm collection/test failure.**
- [ ] **Step 3: Implement `create_isolated_profile(project_root, acceptance_id, manifest, env)`**:
  - Create only under `.nbs_agent_runtime/live-ab/<acceptance-id>/hermes-home`.
  - Copy only `integrations/hermes_nbs_sidecar/` and required static plugin files.
  - Write minimal config with `memory.provider=nbs_sidecar` and model identity; never touch `~/.hermes`.
  - Read only `DEEPSEEK_API_KEY` and `DEEPSEEK_BASE_URL`; protected FD transport is out of scope. Store only a boolean availability marker and fingerprint, never values.
  - Verify copied plugin checksum and Hermes loader discovery in the isolated profile before returning ready.
  - Allow only `https://api.deepseek.com/v1` as the live endpoint; reject symlinks, path traversal and acceptance-directory collisions.
  - Return a bounded status object with `ready` or `blocked_runner_capability` reason.
- [ ] **Step 4: Run tests and verify global config/plugin mtimes and hashes remain unchanged.**
- [ ] **Step 5: Stop for findings-first Review**; do not run a live network call in this Task.

### Task 3: Implement bounded control/treatment live runner

**Files:**
- Create: `scripts/hermes_live_ab_runner.py`
- Modify: `scripts/hermes_turn_receipt.py`
- Test: `tests/test_hermes_live_ab_runner.py`
- Test: `tests/test_hermes_turn_receipt.py`

**Interfaces:**
- Consumes: isolated profile, immutable manifest, bounded query/sourceRefs, `recallMode`, process-local credentials.
- Produces: `run_live_ab(profile, manifest, query, source_refs) -> LiveABRunResult` with control/treatment receipt paths and bounded status.

- [ ] **Step 1: Write failing tests** for same HEAD/fingerprint enforcement, exactly two arms, sequence/mode pairing, no credential persistence, subprocess timeout, and fail-closed missing usage.
- [ ] **Step 2: Run the focused runner tests and confirm expected failure.**
- [ ] **Step 3: Implement the runner**:
  - Re-check clean worktree and full HEAD immediately before each child process.
  - Launch exactly one bounded Hermes child for control and one for treatment with process-local `HERMES_HOME` and credential environment; use distinct session IDs.
  - Pass identical query/sourceRefs/fingerprints/cleanWorktreeFingerprint; vary only `recallMode`, sequence and activation state. Prove control sidecar disabled and treatment sidecar activated.
  - Capture only bounded receipt JSON; redact child stdout/stderr before writing diagnostics.
  - On any live failure, write a blocked marker without writing a completed receipt; never persist credential material.
- [ ] **Step 4: Run deterministic tests, py_compile and diff check.**
- [ ] **Step 5: Stop for findings-first Review**; no real DeepSeek call until Task 4 approval.

### Task 4: Wire read-only comparison and acceptance result

**Files:**
- Create: `scripts/hermes_live_ab_acceptance.py`
- Modify: `backend/agents/runner_capability_evidence.py`
- Test: `tests/test_hermes_live_ab_acceptance.py`
- Test: `tests/test_runner_capability_evidence.py`

**Interfaces:**
- Consumes: two completed receipts from Task 3 and existing `compare_capability_runs`.
- Produces: `LiveABAcceptanceResult(status, reasons, metrics, evidence_paths)`; statuses are exactly `ready`, `acceptance_rejected`, or `blocked_runner_capability`.

- [ ] **Step 1: Write failing tests** for ready, reduction failure, latency failure, identity mismatch, missing receipt, replay, sensitive capture, provenance below 1.0, and canonical activation mismatch.
- [ ] **Step 2: Run focused acceptance tests and confirm failure.**
- [ ] **Step 3: Implement read-only adapter**:
  - Load only bounded receipt metadata.
  - Delegate metric math to `compare_capability_runs` and explicitly derive/report input reduction, output-token delta and p95 latency delta from the two bounded runs.
  - Map failures to explicit reasons without changing any runtime flags.
  - Ensure output excludes raw prompt/output, credentials and absolute paths.
- [ ] **Step 4: Run focused tests, py_compile and diff check.**
- [ ] **Step 5: Stop for findings-first Review** and resolve all findings before live execution.

### Task 5: Execute real isolated A/B and final acceptance

**Files:**
- Create only by approved runner: `.nbs_agent_runtime/live-ab/<acceptance-id>/`
- Modify only if approved by Review: `.superpowers/sdd/2026-08-11-nbs-live-hermes-ab-acceptance/acceptance-report.md`

**Interfaces:**
- Consumes: approved Task 1–4 code, operator-provided `DEEPSEEK_API_KEY`／`DEEPSEEK_BASE_URL`, immutable HEAD and isolated profile.
- Produces: control receipt, treatment receipt, comparison result, Hermes read-only acceptance report.

- [ ] **Step 1: Verify operator credential injection without printing values**; require both allowlisted environment variables and endpoint `https://api.deepseek.com/v1`; if absent, stop with `blocked_runner_capability / live_identity_missing`.
- [ ] **Step 2: Run control and treatment at the same full HEAD through isolated `HERMES_HOME`.**
- [ ] **Step 3: Validate both receipts using the existing runner validator and compare service; assert shared HEAD and `cleanWorktreeFingerprint`, control disabled activation, treatment activated activation, distinct sessions and output metric deltas.**
- [ ] **Step 4: Run focused/full verification and `scripts/hermes_post_change_check.py`; do not alter production flags.**
- [ ] **Step 5: Record findings-first acceptance result with exact evidence paths and metrics; PASS only if every spec gate is true.**
- [ ] **Step 6: If status is `ready`, stop and request explicit user authorization before any commit/push/PR/merge.**

## Verification Matrix

| Scope | Required command | Expected result |
|---|---|---|
| Task 1–4 focused | `.venv/bin/python -m pytest <affected tests> -q` | PASS |
| Python syntax | `.venv/bin/python -m py_compile <affected files>` | exit 0 |
| Patch hygiene | `git diff --check` | exit 0 |
| Cross-module | `.venv/bin/python -m pytest -q` | PASS or documented environment blocker |
| Hermes | `.venv/bin/python scripts/hermes_post_change_check.py` | PASS for completed change; blocked if no live evidence |
| Live A/B | `scripts/hermes_live_ab_acceptance.py` | `ready` only with two real receipts |

## Rollback

Delete the ignored isolated runtime directory and stop the runner. Production recall remains off; no SQLite, baseline, Graph, or global Hermes state is changed.
