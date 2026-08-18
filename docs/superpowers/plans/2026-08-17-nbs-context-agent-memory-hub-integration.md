# Context Agent × Memory Hub Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with review checkpoints. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich every Context Agent `collect-only` result with policy-gated Memory Hub hints while preserving canonical collector evidence and fail-closed fallback.

**Architecture:** Keep `EvidenceCollector` as the canonical source. Add a small deployment-owned read-only adapter that builds a bounded `MemoryQuery`, calls the existing `MemoryHubService`, and projects only fresh/verified allowed records into the existing non-authoritative `memoryHints` envelope. Wire the adapter after collection; any blocked, unavailable, stale, malformed, or timeout result leaves canonical context unchanged.

**Tech Stack:** Python 3.11+, existing dataclasses/models, `MemoryHubService`, `MemoryQuery`, `RuntimeIdentity`, pytest, existing Context Agent CLI and Hermes checks.

## Global Constraints

- Formal revenue scope remains `不含掛賬核銷與TT退款轉團款`.
- Frozen 2026-05 baseline remains `HKD 12,057,968`.
- No writes to formal SQLite, baseline registry, catalog sources, catalog manifests, primary runtime, exports, approvals, dispatch, or Git from the adapter.
- `EvidenceCollector` remains the canonical context source; `memoryHints` remains non-authoritative and excluded from `bundleFingerprint`.
- Only `governance`, `evidence`, and `skill` memory kinds are queryable.
- Caller code cannot override provider, catalog paths, policy service, identity, or ACL decisions.
- Missing composition, unavailable policy, stale/unknown source, malformed projection, timeout, and degraded results fail closed to canonical-only context.
- Every task ends with RED/GREEN focused tests, `py_compile`, `git diff --check`, and a findings-first Review before the next task.

---

### Task 1: Context Memory Hub Adapter Contract

**Files:**
- Create: `backend/agents/context_memory_hub_adapter.py`
- Test: `tests/test_context_memory_hub_adapter.py`
- Modify: `docs/briefs/context-agent-memory-hub-task-1-brief.md`

**Interfaces:**
- Consumes: deployment-owned `MemoryHubService`, `MemoryQuery`, `RuntimeIdentity`.
- Produces: `query_context_memory(*, project_root: Path, identity: RuntimeIdentity, query: MemoryQuery) -> dict[str, object]`.

- [ ] **Step 1: Write the failing tests**

Add tests for ready/allow projection, empty result, blocked identity, unavailable policy service, stale source, unknown freshness, malformed record, bounded item/byte limits, and exact `authority: non_authoritative_memory` output.

- [ ] **Step 2: Run RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_context_memory_hub_adapter.py
```

Expected: collection failure because `context_memory_hub_adapter` does not exist.

- [ ] **Step 3: Implement the minimal adapter**

Use only deployment-owned catalog composition. Convert a ready `MemoryQueryResult` into the existing `MemoryHints` model; map `empty`, `blocked`, `timeout`, and `degraded` to bounded diagnostics with an empty hints list. Reject stale/unknown/invalid records before projection and never read artifact bytes.

- [ ] **Step 4: Run GREEN and static checks**

Run the focused test, then:

```bash
.venv/bin/python -m py_compile backend/agents/context_memory_hub_adapter.py tests/test_context_memory_hub_adapter.py
git diff --check
```

- [ ] **Step 5: Record and review**

Write the task report with allowed files and no-write evidence; run findings-first Review on only Task 1 files.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/context_memory_hub_adapter.py tests/test_context_memory_hub_adapter.py docs/briefs/context-agent-memory-hub-task-1-brief.md
git commit -m "feat: add context memory hub adapter"
```

### Task 2: Context Agent Collect-only Wiring

**Files:**
- Modify: `backend/agents/context_agent_service.py`
- Modify: `scripts/context_agent.py`
- Test: `tests/test_context_agent_memory_hub_integration.py`
- Modify: `docs/briefs/context-agent-memory-hub-task-2-brief.md`

**Interfaces:**
- Consumes: Task 1 `query_context_memory` and existing `build_context_evidence_payload`.
- Produces: Context evidence with optional `memoryHints` while preserving canonical `bundleFingerprint`.

- [ ] **Step 1: Write the failing integration tests**

Cover automatic query on `collect-only`, allow projection, blocked/empty/timeout fallback, canonical fingerprint equality with and without hints, and no change to existing caller output when deployment composition is unavailable.

- [ ] **Step 2: Run RED**

```bash
.venv/bin/python -m pytest -q tests/test_context_agent_memory_hub_integration.py
```

Expected: failure because Context Agent does not invoke the adapter.

- [ ] **Step 3: Wire the adapter after canonical collection**

Keep `EvidenceCollector.collect_context(...)` unchanged. Build a fixed `context-agent` identity and bounded query from the deployment-owned descriptor, call the adapter once, and pass only its validated projection to `build_context_evidence_payload`. Do not make the adapter a collector dependency and do not alter `bundleFingerprint` inputs.

- [ ] **Step 4: Run GREEN and static checks**

```bash
.venv/bin/python -m pytest -q tests/test_context_agent_memory_hub_integration.py tests/test_context_agent_service.py tests/test_context_agent_cli.py
.venv/bin/python -m py_compile backend/agents/context_agent_service.py scripts/context_agent.py tests/test_context_agent_memory_hub_integration.py
git diff --check
```

- [ ] **Step 5: Record and review**

Write the task report and run findings-first Review on the wiring and integration tests. Confirm no SQLite, baseline, catalog, runtime source, or Git writes.

- [ ] **Step 6: Commit**

```bash
git add backend/agents/context_agent_service.py scripts/context_agent.py tests/test_context_agent_memory_hub_integration.py docs/briefs/context-agent-memory-hub-task-2-brief.md
git commit -m "feat: enrich context collection with memory hub hints"
```

### Task 3: Full Verification and Hermes Acceptance

**Files:**
- Modify: `docs/briefs/context-agent-memory-hub-task-3-brief.md`
- Modify: `docs/superpowers/plans/2026-08-17-nbs-context-agent-memory-hub-integration.md` (task checkboxes only)

**Interfaces:**
- Consumes: immutable Task 1 and Task 2 commits plus their Review reports.
- Produces: full verification and Hermes evidence; no product/runtime authority changes.

- [ ] **Step 1: Run affected compile and focused suites**

```bash
.venv/bin/python -m py_compile backend/agents/context_memory_hub_adapter.py backend/agents/context_agent_service.py scripts/context_agent.py
.venv/bin/python -m pytest -q tests/test_context_memory_hub_adapter.py tests/test_context_agent_memory_hub_integration.py tests/test_context_agent_service.py tests/test_context_agent_cli.py tests/test_memory_hub_service.py tests/test_memory_hub_policy_service.py
git diff --check
```

- [ ] **Step 2: Run full verification**

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/system_manager.py acceptance
```

If the primary environment is unavailable, use the existing verification-runtime profile; do not borrow another worktree service or create an empty database.

- [ ] **Step 3: Run Hermes**

```bash
.venv/bin/python scripts/hermes_post_change_check.py --json
```

Hermes must report PASS. Any blocked/degraded result must be reported with its exact phase and classified as environment or program failure.

- [ ] **Step 4: Record final acceptance**

Write the final brief with focused/full counts, Context canonical fingerprint invariance, policy fallback evidence, system acceptance, Hermes status, and proof that baseline/SQLite fingerprints did not change. Do not claim token reduction until a separate real usage comparison exists.

- [ ] **Step 5: Commit documentation only after acceptance**

```bash
git add docs/briefs/context-agent-memory-hub-task-3-brief.md docs/superpowers/plans/2026-08-17-nbs-context-agent-memory-hub-integration.md
git commit -m "test: accept context memory hub integration"
```
