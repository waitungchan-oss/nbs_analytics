# Agent Governance Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the repository root to the verified `main` revision and align the Agent architecture truth sources with the completed Context, Review, and Implementation Agent capabilities.

**Architecture:** Preserve Git history and user work by proving the root dirty change is superseded before removing it. Keep documentation changes limited to the two Agent governance truth sources; no application logic, SQLite, baseline, revenue scope, runtime, or service contract changes are allowed.

**Tech Stack:** Git worktrees, Markdown governance documents, pytest contract tests, NBS system manager, Hermes post-change check.

## Global Constraints

- Formal revenue scope remains `不含掛賬核銷與TT退款轉團款`.
- 2026-05 frozen baseline remains `HKD 12,057,968`.
- Do not modify SQLite, upload, rollback, revenue, business rules, exports, runtime evidence, or Agent executable behavior.
- Preserve unrelated user changes unless content comparison proves they are fully superseded by `main`.
- Root services must be restarted from the repository root only after it is checked out on `main`.

---

### Task 1: Restore the repository root to verified main

**Files:**
- Inspect: `backend/agents/agent_runtime.py`
- No tracked file is intentionally modified.

**Interfaces:**
- Consumes: `main@57df42c` and the root worktree diff on `codex/implementation-agent-plan`.
- Produces: a clean repository root checked out on `main` without losing unique user work.

- [ ] **Step 1: Prove the dirty change is superseded**

Run:

```bash
git diff -- backend/agents/agent_runtime.py
git diff --no-index backend/agents/agent_runtime.py .worktrees/implementation-agent/backend/agents/agent_runtime.py
```

Expected: the dirty change only adds the early `resolve_implementation_runtime_path`; `main` contains the hardened form plus the completed sandbox runner.

- [ ] **Step 2: Remove only the superseded dirty hunk**

Use `apply_patch` to delete the 34 uncommitted lines from the old plan branch. Do not reset, stash, or overwrite any other path.

- [ ] **Step 3: Switch the repository root to main**

Run:

```bash
git switch main
git status --short --branch
```

Expected: `## main` with no tracked changes.

### Task 2: Align Agent governance truth sources

**Files:**
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Create: `docs/superpowers/plans/2026-07-15-agent-governance-alignment.md`
- Test: `tests/test_agent_dispatch_contract.py`
- Test: `tests/test_agent_read_only_contract.py`

**Interfaces:**
- Consumes: completed Agent implementation at `57df42c`, existing CLI contracts, and final verification evidence.
- Produces: governance documents that identify Context, Review, and Implementation Agent as active while preserving manual authorization and Hermes boundaries.

- [ ] **Step 1: Update architecture status and evidence**

Change the architecture version/date to the completed Phase 1 state, keep autonomous task selection and Agent UI as non-goals, move Implementation Agent out of the future roadmap, add Orchestrator/notification/UI as later phases, and replace stale Task 5-7 evidence with the final `474 passed`, system acceptance PASS, Hermes PASS, and `57df42c` evidence.

- [ ] **Step 2: Update dispatch status**

Change `implementation_in_progress` to `active`, explicitly state that current dispatch is CLI/Codex orchestrated rather than application-runtime automatic, and retain all sandbox, authorization, Review, full verification, and Hermes gates.

- [ ] **Step 3: Run documentation contract checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent_dispatch_contract.py tests/test_agent_read_only_contract.py -q
git diff --check
```

Expected: all tests pass and `git diff --check` exits 0.

- [ ] **Step 4: Commit the governance alignment**

Run:

```bash
git add docs/agents/NBS_AGENT_ARCHITECTURE.md docs/agents/CODEX_AGENT_DISPATCH.md docs/superpowers/plans/2026-07-15-agent-governance-alignment.md
git commit -m "docs: align active agent governance"
```

### Task 3: Verify and integrate the governance alignment

**Files:**
- Verify only; no new tracked files.

**Interfaces:**
- Consumes: clean governance commit and repository root on `main`.
- Produces: a fast-forwarded `main`, running root services, and fresh acceptance evidence.

- [ ] **Step 1: Run full Python verification**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Fast-forward main**

Merge `codex/agent-governance-alignment` into `main` with `--ff-only` from the clean repository root.

- [ ] **Step 3: Restart and validate root services**

Run:

```bash
.venv/bin/python scripts/system_manager.py stop
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

Expected: all services ready, system acceptance passed, Hermes `overallStatus=pass`, formal scope unchanged, and 2026-05 baseline matched at `HKD 12,057,968`.

- [ ] **Step 4: Confirm final repository and database state**

Run:

```bash
git status --short --branch
git rev-parse main
shasum -a 256 nbs_marketing_data.db
```

Expected: clean `main`; database hash unchanged from the pre-change value.

### Task 4: Make Agent verification portable between root and worktrees

**Files:**
- Modify: `tests/test_validation_runner.py`
- Modify: `tests/test_implementation_agent_cli.py`

**Interfaces:**
- Consumes: the existing `ValidationRunner` repository-root contract and a tracked implementation plan.
- Produces: the same Agent verification behavior when tests run from the repository root or a linked `.worktrees/<name>` checkout.

- [ ] **Step 1: Reproduce the root-only failure**

Run the Hermes targeted pack from the repository root. Expected RED: validation tests expect `PROJECT_ROOT.parent.parent/.venv`, while CLI tests cannot find the ignored Task 6 brief.

- [ ] **Step 2: Use a root/worktree-aware expected repository path**

In `tests/test_validation_runner.py`, define `REPOSITORY_ROOT` as `PROJECT_ROOT` for the root checkout and `PROJECT_ROOT.parent.parent` for linked project worktrees. Derive `REPOSITORY_PYTHON` once and use it in all interpreter assertions.

- [ ] **Step 3: Replace the ignored CLI fixture dependency**

In `tests/test_implementation_agent_cli.py`, derive the same repository root for the Python executable and use tracked plan `docs/superpowers/plans/2026-07-14-implementation-agent.md` for `planPath` and `planFingerprint`.

- [ ] **Step 4: Verify root and worktree execution**

Run:

```bash
.venv/bin/python -m pytest tests/test_validation_runner.py tests/test_implementation_agent_cli.py -q
```

Expected: all tests pass from both the repository root and the linked feature worktree.
