# NBS Hermes Runner Capability Hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 建立受控 Hermes runner hook，從 immutable HEAD 產生 `recall_off`／`recall_on` bounded live receipts，供既有 capability validator 驗證。

**Architecture:** `prepare` 建立不可變 run manifest；`record` 驗證 Hermes completion receipt 並寫入 ignored runtime `capability-input.json`。Hook 不啟動 Hermes、不修改 recall 預設、不寫正式狀態。

**Tech Stack:** Python 3、dataclasses、JSON、`canonical_fingerprint`、pytest、既有 `runner_capability_evidence`。

## Global Constraints

- 只接受完整 40-character immutable Git SHA，且 prepare 時必須與工作樹 HEAD 一致。
- provider/model 固定 `hermes`／`deepseek-v4-flash`；UI reasoning 必須 medium。
- cohort 只能是 `off` 或 `on`；sequence 只能是 1 或 2，run id 不得重用。
- writer disabled、baseline/formal scope unchanged；普通流程不會自動呼叫 hook。
- 不執行 network、任意 shell、SQLite write、Git write、approval、dispatch 或 Hermes post-change check。
- raw prompt/output、credentials、absolute path、full logs、customer data、raw hints 不得進入 receipt。

### Task 1: Implement bounded prepare/record hook

**Files:**
- Create: `scripts/hermes_runner_capability_hook.py`
- Create: `tests/test_hermes_runner_capability_hook.py`

**Interfaces:**
- `prepare` flags: `--recall-mode off|on`, `--sequence 1|2`, `--git-head`, `--project-id`, `--workspace-kind`, `--task-fingerprint`, `--brief-fingerprint`, `--allowed-files-fingerprint`, `--commands-fingerprint`, `--output`.
- `record` flags: `--manifest`, `--receipt`, `--output`.
- Output: exact bounded `RunnerCapabilityRun` JSON under `.nbs_agent_runtime`.

- [ ] Step 1: Write RED tests for HEAD binding, mode/sequence validation, allowlisted runner, receipt identity, token/latency bounds, activation receipt requirement, and no raw fields.
- [ ] Step 2: Run `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_hermes_runner_capability_hook.py -q -p no:cacheprovider` and confirm missing-module failures.
- [ ] Step 3: Implement minimal prepare/record CLI using only `pathlib`, `json`, `argparse`, `subprocess.run` for `git rev-parse HEAD` (read-only), and existing runner model. Reject all other commands and symlink/out-of-root paths.
- [ ] Step 4: Re-run focused tests; expected all pass with blocked status when activation receipt is absent.
- [ ] Step 5: Run `py_compile` and `git diff --check`.

### Task 2: Integrate live Hermes UI receipts

**Files:**
- Runtime only: `.nbs_agent_runtime/runs/<run-id>/manifest.json`, `receipt.json`, `capability-input.json`.
- No tracked source changes unless Task 1 tests expose a bounded defect.

- [ ] Step 1: Confirm Hermes UI model is `deepseek-v4-flash` with medium reasoning.
- [ ] Step 2: Run control cohort with `recall_off`, then independent treatment with `recall_on`; both use the same manifest fingerprints and immutable HEAD.
- [ ] Step 3: Capture live session id, completion, token usage, p95 and activation receipt; missing values remain unknown/blocked.
- [ ] Step 4: Run existing `scripts/runner_capability_evidence.py` against the two generated inputs; do not claim ready unless all gates pass.

### Task 3: Strict review and acceptance

**Files:** no additional tracked files.

- [ ] Step 1: Run focused tests, compile, diff check and full relevant sidecar tests.
- [ ] Step 2: Dispatch findings-first Review Agent on Task 1 diff only.
- [ ] Step 3: Re-check Git clean/HEAD and runtime receipts; report ready/blocked explicitly.

## Rollback

Remove ignored runtime receipts and keep production `recall_enabled=false`; no source or canonical rollback is needed.
