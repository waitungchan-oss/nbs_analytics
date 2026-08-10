# NBS Runner Capability Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一條 evidence-only、可重現的 Runner Capability Evidence 通道，獨立證明 Hermes live model identity、recall-on 第二次執行與 token reduction，供 Memory Sidecar Task 5 消費。

**Architecture:** Hermes desktop 只負責產生兩次 bounded runner result；本地 deterministic validator 只讀取 allowlisted JSON，驗證 immutable head、scope fingerprints、run sequence、live provider/model、cache replay、provenance、latency 與 token metrics，並輸出 `runner-capability-evidence-v1`。Validator 不啟動 Hermes、不寫 canonical state、不修改 recall flag；Task 5 只接受 `ready` evidence。

**Tech Stack:** Python 3 dataclasses／JSON、既有 `canonical_fingerprint`、pytest、`py_compile`、既有 `.nbs_agent_runtime` evidence path。

## Global Constraints

- `recall_enabled=false`、`writer_enabled=false`、`shadow_mode=true` 維持安全預設；本 Task 不做 rollout。
- 只接受 immutable 40-character `gitHead`；branch name、縮寫 SHA 或 model config 不能代替 live identity evidence。
- Control 與 treatment 必須使用相同 head、task／brief／allowed-files／commands fingerprints；唯一差異是 `recallMode`。
- Treatment 必須是 `sequence=2` 的獨立 run，`runId` 不得重用，`cacheReplayDetected` 必須為 false。
- `tokenReductionRatio >= 0.20` 或 bounded、明確的 approved alternative evidence；缺 token usage 時輸出 `blocked_runner_capability`。
- provenance coverage 必須為 `1.0`、sensitive capture 必須為 `0`、p95 latency 必須 `<=800ms`。
- 不修改 SQLite、baseline、revenue scope、business rules、export schema、Governance Graph、approval／dispatch、Git state 或 Hermes acceptance state。
- 不執行 `scripts/hermes_post_change_check.py`；Hermes post-change acceptance 是後續獨立 gate。
- Evidence 不得包含 raw prompt、raw model output、credentials、runner command、absolute path、full logs、customer data 或原始 hints。

---

### Task 1: Define immutable runner capability evidence model

**Files:**
- Create: `backend/agents/runner_capability_evidence.py`
- Test: `tests/test_runner_capability_evidence.py`
- Reference: `backend/agents/evidence_models.py` (`canonical_fingerprint`)

**Interfaces:**
- Consumes: two bounded run dictionaries containing `runId`, `sequence`, `recallMode`, `gitHead`, task／brief／scope／commands fingerprints, live `provider`／`model`, completion status, cache flag, token counts, latency and provenance counters.
- Produces: immutable `RunnerCapabilityRun`, `RunnerCapabilityComparison`, `RunnerCapabilityEvidence`; `build_capability_evidence(control: Mapping[str, Any], treatment: Mapping[str, Any], *, expected_git_head: str, expected_task_fingerprint: str) -> RunnerCapabilityEvidence` and `RunnerCapabilityEvidence.to_dict()`.

- [ ] **Step 1: Write failing schema and identity tests**

  Add tests proving that a valid control/treatment pair round-trips, fingerprints deterministically, and rejects abbreviated SHA, branch-only identity, missing provider/model, unknown fields, raw-content fields, unbounded strings and non-boolean cache flags.

- [ ] **Step 2: Run the focused tests to verify RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_runner_capability_evidence.py -q -p no:cacheprovider`

  Expected: FAIL because the model and builder do not yet exist.

- [ ] **Step 3: Implement bounded immutable models**

  Implement strict dataclasses and constants:

  ```python
  RUNNER_CAPABILITY_SCHEMA = "runner-capability-evidence-v1"
  ALLOWED_PROVIDER = "hermes"
  ALLOWED_MODEL = "deepseek-v4-flash"
  RECALL_MODES = frozenset({"off", "on"})
  CAPABILITY_RESULTS = frozenset({"ready", "blocked_runner_capability", "acceptance_rejected"})
  ```

  Bind every fingerprint and the final evidence ID with `canonical_fingerprint`; reject mutation, unknown fields, raw-content fields and values over the bounded caps.

- [ ] **Step 4: Run the focused tests to verify GREEN**

  Run the same pytest command. Expected: all model, fingerprint and schema tests pass.

- [ ] **Step 5: Run compile and diff checks**

  Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile backend/agents/runner_capability_evidence.py` and `git diff --check`. Expected: exit code 0.

### Task 2: Implement deterministic two-run capability gate

**Files:**
- Modify: `backend/agents/runner_capability_evidence.py`
- Test: `tests/test_runner_capability_evidence.py`

**Interfaces:**
- Consumes: `RunnerCapabilityRun` control/treatment records from Task 1.
- Produces: `compare_capability_runs(control: RunnerCapabilityRun, treatment: RunnerCapabilityRun) -> RunnerCapabilityComparison`; explicit result classification and bounded reasons.

- [ ] **Step 1: Add RED tests for run protocol and status classification**

  Cover these cases explicitly: same immutable inputs／distinct run IDs／sequence 1→2／off→on passes structural checks; different head or task fingerprint; treatment sequence not 2; reused run ID; cache replay; missing completion; live identity mismatch; missing token usage; reduction below 20%; provenance below 1; sensitive capture non-zero; p95 above 800ms.

- [ ] **Step 2: Run the new tests and confirm each failure**

  Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_runner_capability_evidence.py -q -p no:cacheprovider`. Expected: each new gate test fails before implementation.

- [ ] **Step 3: Implement fail-closed comparison logic**

  Compute `tokenReductionRatio = (control.inputTokens - treatment.inputTokens) / control.inputTokens` only when both counts are present and bounded. Return `blocked_runner_capability` for unprovable capability facts; return `acceptance_rejected` only when capability is proven but a numeric acceptance gate fails; return `ready` only when all gates pass.

- [ ] **Step 4: Run focused tests and inspect serialized evidence**

  Run the same pytest command and assert that serialized output contains no prompt, secret, absolute path, runner command or raw hint content. Expected: all tests pass and status transitions are deterministic.

### Task 3: Add read-only CLI for bounded runner result ingestion

**Files:**
- Create: `scripts/runner_capability_evidence.py`
- Test: `tests/test_runner_capability_evidence_cli.py`
- Modify: `agent_config/evidence_allowlist.json` only if the existing read roots do not already allow the dedicated runtime evidence path.

**Interfaces:**
- Consumes: two JSON files under `.nbs_agent_runtime/runs/<run-id>/capability-input.json`, supplied by the approved runner; CLI flags `--control`, `--treatment`, `--git-head`, `--task-fingerprint`, `--output`.
- Produces: one `runner-capability-evidence-v1` JSON file under the approved `.nbs_agent_runtime` path; non-zero exit for malformed, out-of-root, symlinked or over-sized input.

- [ ] **Step 1: Add CLI RED tests**

  Test valid pair output, missing file, symlink input, absolute/out-of-root path, oversized JSON, unknown schema field, malformed JSON and output fingerprint stability. Test that CLI never shells out or writes outside the requested runtime output.

- [ ] **Step 2: Run CLI tests and verify RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_runner_capability_evidence_cli.py -q -p no:cacheprovider`. Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement allowlisted read-only CLI**

  Resolve paths under the repository’s `.nbs_agent_runtime` only, reject symlinks and files over the bounded byte cap, parse exact JSON schema, call Task 2 comparison, and write only the output evidence file. Do not import or invoke Hermes, subprocess, network clients or Git write operations.

- [ ] **Step 4: Run CLI tests and compile**

  Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_runner_capability_evidence_cli.py -q -p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile scripts/runner_capability_evidence.py`. Expected: PASS.

### Task 4: Add deterministic fixtures and Task 5 consumption contract

**Files:**
- Create: `tests/fixtures/memory_sidecar/runner_capability/control-ready.json`
- Create: `tests/fixtures/memory_sidecar/runner_capability/treatment-ready.json`
- Create: `tests/fixtures/memory_sidecar/runner_capability/blocked-cache-replay.json`
- Modify: `docs/agents/MEMORY_SIDECAR_CONTRACT.md`
- Test: `tests/test_runner_capability_evidence.py`

**Interfaces:**
- Consumes: Task 3 CLI output and deterministic fixture inputs.
- Produces: contract wording that Task 5 may consume only `result=ready`; blocked/rejected evidence keeps recall-off and cannot be reused as acceptance proof.

- [ ] **Step 1: Add fixture and contract RED tests**

  Assert the ready fixture has live identity, sequence 1→2, distinct run IDs, no replay, valid provenance and reduction ≥20%; assert the cache-replay fixture yields `blocked_runner_capability`; assert the contract documents the three result states and no auto-enable behavior.

- [ ] **Step 2: Run fixture tests to verify RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_runner_capability_evidence.py -q -p no:cacheprovider`. Expected: FAIL until fixtures and wording are present.

- [ ] **Step 3: Add bounded fixtures and minimal contract clarification**

  Use fake SHA-256/40-character values and non-sensitive source fingerprints only. Do not put prompts, secrets, absolute paths, runner commands or raw hints in fixtures. Document that Task 5 must bind its own `memory-sidecar-ab-acceptance-v1` to the same immutable inputs.

- [ ] **Step 4: Run fixture, sidecar regression and full tests**

  Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_runner_capability_evidence.py tests/test_runner_capability_evidence_cli.py tests/test_memory_sidecar_*.py -q -p no:cacheprovider` and then `.venv/bin/python -m pytest -q -p no:cacheprovider`. Expected: all pass.

### Task 5: Strict Review and evidence-channel acceptance

**Files:**
- No production files beyond approved Task 1–4 changes.
- Runtime evidence only: `.nbs_agent_runtime/runs/<run-id>/capability-input.json` and `runner-capability-evidence.json`.

**Interfaces:**
- Consumes: immutable Task 1–4 diff, focused/full verification, and live Hermes runner evidence when available.
- Produces: findings-first Review report plus a final bounded capability evidence record; no rollout decision.

- [ ] **Step 1: Collect immutable diff and verification evidence**

  Run focused pytest, `py_compile`, `git diff --check`, and the read-only CLI with the two approved runtime input paths (`--control <runtime-control-json> --treatment <runtime-treatment-json> --git-head <full-sha> --task-fingerprint <sha256> --output <runtime-evidence-json>`). Confirm `gitHead` is the committed full SHA.

- [ ] **Step 2: Run approved Review Agent in strict mode**

  Review only the approved Task 1–4 files and evidence schema. Any dirty file outside the contract blocks Review; no Review Agent writes files.

- [ ] **Step 3: Execute the two controlled Hermes runs when authorized**

  Use the same immutable HEAD, brief, allowed files and commands. Record live identity and completion metadata; do not enter credentials or change Hermes security settings. If any capability fact is missing, write `blocked_runner_capability` and stop.

- [ ] **Step 4: Validate the pair and classify the result**

  Run the CLI against the two runner results. Preserve `recall_enabled=false` for `blocked_runner_capability` or `acceptance_rejected`.

- [ ] **Step 5: Report acceptance boundary**

  Report the evidence ID, immutable head, provider/model, cohort sequence, token ratio, provenance, p95 latency, replay flag and result. Do not call this Hermes PASS or Task 5 A/B acceptance; Task 5 remains the next separate implementation gate.

## Verification Matrix

| Gate | Command / evidence | Expected |
|---|---|---|
| Model/schema | `pytest tests/test_runner_capability_evidence.py -q` | PASS |
| CLI boundary | `pytest tests/test_runner_capability_evidence_cli.py -q` | PASS |
| Sidecar regression | `pytest tests/test_memory_sidecar_*.py -q` | PASS |
| Full Python | `pytest -q -p no:cacheprovider` | PASS |
| Static | `py_compile` affected modules; `git diff --check` | PASS |
| Runner capability | bounded pair evidence | `ready`, or explicit blocked/rejected state |
| Hermes post-change | intentionally not run in this Task | independent later gate |

## Rollback

Delete or retain only the ignored runtime evidence record, disable any test-only recall flag, and keep production `recall_enabled=false`. No source rollback, SQLite mutation, baseline change or canonical artifact deletion is permitted.
