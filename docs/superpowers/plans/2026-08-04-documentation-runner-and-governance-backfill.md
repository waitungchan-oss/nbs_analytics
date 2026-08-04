# Documentation Runner and Governance Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Enable the constrained non-interactive Documentation runner and use it to produce a fingerprint-bound `documentation-proposal-v1`, then apply only approved Markdown and Obsidian targets.

**Architecture:** The runner invokes local `codex exec --json` with read-only sandbox flags and stdin-only evidence. The trusted Documentation Agent validates the draft, target classifier, schema, and fingerprints; the Controller performs preview and explicit-target controlled apply. Repository reconciliation remains limited to evidence-backed governance documents.

**Tech Stack:** Python 3.10, existing Documentation Agent service, local Codex CLI, pytest, Markdown, Obsidian local vault.

## Global Constraints

- Preserve revenue scope `不含掛賬核銷與TT退款轉團款`.
- Preserve 2026-05 baseline `HKD 12,057,968`.
- Documentation runner is read-only and allowlisted to `codex`.
- Documentation Agent emits proposals only; Controller owns apply.
- System Map and ADR require explicit target approval; no automatic ADR apply.
- Never write SQLite, runtime terminal state, baseline, Git history, or approval state.

### Task 1: Harden non-interactive Codex runner

**Files:**
- Modify: `backend/agents/documentation_codex_runner.py`
- Test: `tests/test_documentation_codex_runner.py`

**Interfaces:** runner command must include `--json`; stdout remains validated as `documentation-draft-v1`.

- [ ] Add a regression test asserting the subprocess command includes `--json`, `--sandbox read-only`, `--ephemeral`, and `--ignore-user-config`.
- [ ] Change the fixed command to include `--json` and keep stdin-only evidence and output caps.
- [ ] Run focused runner tests and compile the module.

### Task 2: Re-run verified backfill and create proposal

**Files:**
- Runtime only: `.nbs_agent_runtime/runs/<run-id>/...`
- Local only: `.nbs_agent_runtime/documentation.local.json`

- [ ] Re-run fixed verification on the exact clean `main` HEAD.
- [ ] Run `documentation_agent.py --run-id <run-id> --agent-command codex --obsidian-vault <vault>`.
- [ ] Validate `documentation-proposal-v1`, evidence fingerprint, target identities, and preview without changing target bytes.
- [ ] Run findings-first Review over evidence/proposal/preview.

### Task 3: Controlled apply and repository reconciliation

**Files:**
- Modify: `NBS_ANALYTICS_HANDOFF.md`
- Modify: `NBS_ANALYTICS_SYSTEM_MAP.md`
- Modify: `Summay/NBS 系統總覽.md`
- Modify: `Summay/驗收基線.md`
- Obsidian: approved relative targets under `70_Codex_Briefs` and `10_System`

- [ ] Apply Brief and explicitly approve System Map only after proposal and Review PASS.
- [ ] Update the four tracked documents with current merged HEAD, E-4 Management Summary, Governance Graph scope, and verified acceptance evidence.
- [ ] Preserve historical specs/plans and do not rewrite business rules or baseline values.
- [ ] Confirm application records contain only vault-relative identities and after-write hashes.

### Task 4: Final verification

- [ ] Run documentation focused tests, `git diff --check`, and Markdown target checks.
- [ ] Run full relevant pytest pack, `scripts/system_manager.py acceptance`, and `scripts/hermes_post_change_check.py`.
- [ ] Report any pre-existing baseline/runtime failures separately and do not claim completion if Hermes fails.
