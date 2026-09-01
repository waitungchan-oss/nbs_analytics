# Task Checkpoint Commit Standardization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 deterministic、source-bound 的 Task checkpoint validator，標準化 approved Task 的 commit identity，同時保留 Review、full pytest、Hermes、UI acceptance 與 push/merge 的獨立授權邊界。

**Architecture:** 新增 provider-neutral evidence model、formatter 與 read-only validator。Validator 只檢查 Task contract、live Git parent HEAD、allowlist、staged diff、fresh Review/focused evidence 與 trailers；Codex 在明確授權下執行實際 commit。Implementation Agent、Governance Graph、Memory Hub、Memory Sidecar 與 Hermes 不取得 Git write authority。

**Tech Stack:** Python 3.10、dataclasses、JSON、SHA-256、Git CLI、pytest、existing `.nbs_agent_runtime` evidence boundary、Markdown。

**Spec:** `docs/superpowers/specs/2026-09-01-task-checkpoint-commit-standardization-design.md`

## Global Constraints

- Formal scope 固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite、baseline、revenue、GMV、退款、rollback business rule、export schema 或正式 runtime data。
- Implementation Agent 不得 commit、merge、push、reset、stash、service management 或 dependency install。
- Validator 預設 read-only；不得自動 stage、commit、push、merge、reset、stash 或修改 working tree。
- 一個 approved Task 只允許一個 checkpoint commit；無變更 Task 為 explicit no-op，不產生空 commit。
- Checkpoint 必須標示 `Final-Acceptance: pending`，不代表 Review、full pytest、Hermes、UI 或正式完成。
- Unrelated dirty changes 必須保留，不可混入 allowlist，也不可用 destructive Git command 清除。
- Governance Graph、Memory Hub、Memory Sidecar 僅作 read-only context/projection，不能批准 Task 或改變 Git/gate state。
- 每個 Task 先完成 fresh focused verification、findings-first Review、`py_compile` 與 `git diff --check`，再建立 checkpoint。
- Full pytest、Hermes 與 UI acceptance 是 final independent gates，不被 checkpoint 取代。

## File Map

| File | Responsibility |
|---|---|
| `backend/agents/task_checkpoint_models.py` | Exact evidence model、bounded fields、fingerprint、commit metadata。 |
| `backend/agents/task_checkpoint_validator.py` | Read-only live Git、allowlist、parent HEAD、evidence validator。 |
| `scripts/task_checkpoint.py` | `inspect` / `validate` CLI；不得提供 Git mutation subcommands。 |
| `tests/test_task_checkpoint_models.py` | Schema、fingerprint、formatter、secret/path rejection。 |
| `tests/test_task_checkpoint_validator.py` | Temporary Git repo、allowlist、dirty worktree、freshness、rollback metadata。 |
| `tests/test_task_checkpoint_cli.py` | CLI output、exit code、read-only behavior。 |
| `NBS_ANALYTICS_HANDOFF.md` | Task/checkpoint identity 與 live snapshot 說明。 |
| `NBS_HERMES_MONITORING.md` | Hermes read-only checkpoint evidence boundary。 |
| `docs/agents/CODEX_AGENT_DISPATCH.md` | Codex-owned Git integration 與 final gate separation。 |
| `tests/test_checkpoint_documentation_contract.py` | Documentation authority boundary regression tests。 |

---

### Task 1: Checkpoint evidence 與 commit identity models

**Files:**

- Create: `backend/agents/task_checkpoint_models.py`
- Test: `tests/test_task_checkpoint_models.py`

**Interfaces:**

- `TaskCheckpointEvidence.from_dict(payload: Mapping[str, Any], *, expected_parent_head: str | None = None) -> TaskCheckpointEvidence`
- `TaskCheckpointEvidence.to_dict() -> dict[str, Any]`
- `TaskCheckpointEvidence.recompute_fingerprint() -> str`
- `TaskCheckpointCommitMetadata.subject() -> str`
- `TaskCheckpointCommitMetadata.body() -> str`
- `TaskCheckpointCommitMetadata.trailers() -> dict[str, str]`

- [ ] **Step 1: Write failing tests**

Test exact fields for `task-checkpoint-evidence-v1`, lowercase SHA-256, 40-character Git SHA, safe relative paths, bounded arrays, fingerprint round-trip, subject format `checkpoint(task-03): ...`, and mandatory `NBS-Final-Acceptance: pending`.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/python -m pytest tests/test_task_checkpoint_models.py -q`; expected failure is missing model/formatter.

- [ ] **Step 3: Implement minimal immutable models**

Use frozen dataclasses. Reject unknown fields, absolute paths, symlinks, secrets, prompts, raw logs, raw commands and unbounded diagnostics. Derive the evidence fingerprint from unsigned exact fields using canonical JSON.

- [ ] **Step 4: Verify GREEN**

Run the focused tests, `py_compile backend/agents/task_checkpoint_models.py`, and `git diff --check`; all must pass.

- [ ] **Step 5: Review and checkpoint**

After findings-first Review PASS, Codex stages only Task 1 files and creates `checkpoint(task-01): add checkpoint evidence models` with the required body/trailers. Implementation Agent does not commit.

### Task 2: Read-only checkpoint validator

**Files:**

- Create: `backend/agents/task_checkpoint_validator.py`
- Test: `tests/test_task_checkpoint_validator.py`

**Interfaces:**

- `CheckpointValidationError(ValueError)`
- `CheckpointValidationResult(status: Literal["ready", "blocked", "no_op"], reasons: tuple[str, ...], evidence: TaskCheckpointEvidence | None)`
- `GitCheckpointState(head: str, changed_files: tuple[str, ...], staged_files: tuple[str, ...], status_lines: tuple[str, ...])`
- `inspect_git_state(project_root: Path) -> GitCheckpointState`
- `validate_checkpoint(project_root: Path, task_contract: Mapping[str, Any], verification: Mapping[str, Any], *, expected_parent_head: str) -> CheckpointValidationResult`

- [ ] **Step 1: Write failing tests**

Use temporary Git repositories to test valid allowlisted changes and rejection of parent drift, staged/untracked unrelated files, allowlist violation, missing/failed Review, stale verification, fingerprint mismatch and forbidden paths.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/python -m pytest tests/test_task_checkpoint_validator.py -q`; expected failure is missing validator interfaces.

- [ ] **Step 3: Implement fixed read-only Git inspection**

Use only fixed `git rev-parse HEAD`, `git status --short`, `git diff --name-only`, `git diff --cached --name-only` and bounded staged diff inspection. Never invoke `git add`, `commit`, `push`, `merge`, `reset`, `checkout` or `stash`.

- [ ] **Step 4: Implement evidence and no-op rules**

Require live parent HEAD, Task contract fingerprint, changed-file list, staged diff fingerprint, Review fingerprint and focused verification status to match. Return `no_op` only for an explicit no-change contract; never create an empty commit.

- [ ] **Step 5: Verify, Review and checkpoint**

Run focused tests, `py_compile`, and `git diff --check`. After Review PASS, create `checkpoint(task-02): validate checkpoint boundaries` with fresh metadata.

### Task 3: Bounded validation CLI

**Files:**

- Create: `scripts/task_checkpoint.py`
- Test: `tests/test_task_checkpoint_cli.py`

**Interfaces:**

- `python scripts/task_checkpoint.py inspect --project-root <repo>`
- `python scripts/task_checkpoint.py validate --project-root <repo> --task-contract <json> --verification <json> --expected-parent-head <sha>`
- Output schema: `task-checkpoint-cli-v1`
- Exit `0`: `ready` or `no_op`; exit `2`: blocked validation; exit `3`: invalid input/evidence。

- [ ] **Step 1: Write failing CLI tests**

Assert stable bounded JSON, unchanged `git status`/HEAD, no raw diff/path/secret output, and rejection of unsupported `commit`, `push`, `merge`, `reset`, `stash` and `approve` actions.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/python -m pytest tests/test_task_checkpoint_cli.py -q`; expected failure is missing CLI.

- [ ] **Step 3: Implement inspect/validate only**

Parse bounded JSON input, call the Task 2 validator, and emit status, reasons and fingerprints only. Keep the CLI non-mutating; Codex performs any explicit Git commit outside this CLI.

- [ ] **Step 4: Verify, Review and checkpoint**

Run focused tests, `scripts/task_checkpoint.py --help`, `py_compile`, and `git diff --check`. After Review PASS, create `checkpoint(task-03): add checkpoint validation cli`.

### Task 4: Documentation and governance boundary reconciliation

**Files:**

- Modify: `NBS_ANALYTICS_HANDOFF.md`
- Modify: `NBS_HERMES_MONITORING.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Create: `tests/test_checkpoint_documentation_contract.py`

**Interfaces:**

- Documentation references `task-checkpoint-evidence-v1` and `NBS-Checkpoint-Version: 1`。
- Hermes reports checkpoint evidence read-only and separately from final acceptance。
- Implementation Agent remains `never: commit, merge, push`。

- [ ] **Step 1: Write failing documentation tests**

Assert the three documents define one-Task/one-checkpoint identity, fresh evidence, `Final-Acceptance: pending`, rollback via `git revert`, separate push/PR/merge authorization, and Graph/Memory/Hermes non-authority.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/python -m pytest tests/test_checkpoint_documentation_contract.py -q`; expected failure is missing contract text.

- [ ] **Step 3: Update documentation only**

Add the contract without changing formal business rules, SQLite, baseline, runtime state, Graph projection behavior or Memory provider behavior.

- [ ] **Step 4: Verify, Review and checkpoint**

Run focused tests, documentation completeness scan, `git diff --check`, then create `checkpoint(task-04): document checkpoint commit contract` after Review PASS.

### Task 5: Final independent verification and handoff

**Files:**

- Read: Task 1–4 diffs, checkpoint metadata, Review evidence and fresh runtime artifacts。
- No production code changes expected。

- [ ] **Step 1: Validate checkpoint lineage**

For every checkpoint, verify Task ID uniqueness, subject/trailers, parent SHA, allowed/changed files, diff fingerprint, Review fingerprint and focused verification freshness.

- [ ] **Step 2: Run independent gates**

Run `.venv/bin/python -m pytest -q`, `.venv/bin/python scripts/hermes_post_change_check.py --json`, and `.venv/bin/python scripts/system_manager.py acceptance`. Run UI acceptance separately where applicable. Report sandbox capability blocks/skips separately; never convert a required capability failure into PASS.

- [ ] **Step 3: Confirm business guardrails**

Run `.venv/bin/python scripts/phase2j_baseline_check.py` and `.venv/bin/python scripts/monthly_baseline_check.py`; expect formal scope `不含掛賬核銷與TT退款轉團款` and 2026-05 baseline `HKD 12,057,968`.

- [ ] **Step 4: Findings-first final review**

Review Agent consumes the final actual diff and fresh evidence. Findings require a new approved repair Task/checkpoint; do not amend history to hide evidence.

- [ ] **Step 5: Explicit integration handoff**

Only after explicit user authorization may Codex push, open a PR or merge. Without it, preserve the verified branch and report the exact next command without executing it.

## Plan self-review

- Spec coverage: purpose, identity, evidence, allowlist, dirty worktree, failure, rollback, authority boundaries and independent gates map to Tasks 1–5。
- No new Governance Graph, Memory Hub, Memory Sidecar, database, orchestration, approval state machine, hook, daemon or automatic Git mutation。
- All later interfaces are defined before use; every implementation Task has TDD RED/GREEN, focused verification, Review and checkpoint steps。
