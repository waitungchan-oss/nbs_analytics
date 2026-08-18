# NBS Memory Hub Agent／Module Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 Memory Hub 以受治理、read-only、fail-closed 方式接入 Agent Operations、Governance Graph、Review、Implementation 與 Documentation workflow，同時保留 Context Agent 唯一 direct-query 入口。

**Architecture:** 共用 `memory-hub-agent-integration-v1` immutable evidence contract；Context Agent 直接查詢，Hermes Sidecar bounded 消費，Short-term Offload 只比較 evidence，其餘模組只接受 observation 或 gated supplementary context。所有 consumer 在 Memory Hub unavailable 時回到 canonical-only workflow。

**Tech Stack:** Python 3.10、dataclasses、canonical JSON SHA-256、pytest、Streamlit read model、NBS Agent workflow artifacts、Governance Graph projection。

**Spec:** `docs/superpowers/specs/2026-08-18-nbs-memory-hub-agent-module-integration-design.md`

## Global Constraints

- canonical artifacts 是唯一正式真相來源；Memory Hub hints 固定 `authority=non_authoritative_memory`。
- Context Agent 是唯一 direct-query 入口；其他 Agent 不得自行查詢 Memory Hub。
- Memory Hub 不得新增 approval、dispatch、workflow control、Graph snapshot refresh、SQLite、baseline、business rules、export schema 或 Git write。
- Query bounds 固定為 `maxItems=3`、`maxBytes=6000`、`timeoutMs=800`。
- 所有 paths 必須 project/runtime-relative；拒絕 symlink、traversal、absolute path 與 divergent immutable output。
- Review PASS、full verification PASS、Hermes PASS 必須分開驗證。
- 每個 implementation Task 只可修改列明檔案，完成後交 findings-first Review。

---

### Task 1: Shared integration evidence contract and provisioning command

**Files:**
- Create: `backend/agents/memory_hub_integration_models.py`
- Create: `scripts/provision_memory_hub_catalog.py`
- Create: `tests/test_memory_hub_integration_models.py`
- Create: `tests/test_memory_hub_provisioning.py`
- Modify: `agent_config/evidence_allowlist.json`

**Interfaces:**
- Produces: `MemoryHubIntegrationEvidence.from_dict(payload)` and `.to_dict()`.
- Produces: `build_memory_hub_integration_evidence(...) -> MemoryHubIntegrationEvidence`.
- Produces CLI: `.venv/bin/python scripts/provision_memory_hub_catalog.py [--check-only]`.
- Consumes existing `memory_hub_catalog_deployment.json`, `MemoryCatalog`, `TeamCatalog`, `AgentPolicyCatalog` contracts.

- [ ] **Step 1: Write RED schema tests**

Add tests proving exact keys, allowed modes/statuses, canonical fingerprint, relative refs, bounded hint count, and rejection of raw memory, absolute paths and unknown fields.

- [ ] **Step 2: Run RED tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_integration_models.py
```

Expected: collection/import failure because `memory_hub_integration_models` does not exist.

- [ ] **Step 3: Implement immutable evidence model**

Define:

```python
@dataclass(frozen=True)
class MemoryHubIntegrationEvidence:
    project_id: str
    consumer_id: str
    integration_mode: str
    status: str
    reason: str
    query_fingerprint: str | None
    hints_fingerprint: str | None
    policy_decision_fingerprints: tuple[str, ...]
    source_refs: tuple[str, ...]
    hint_count: int
    generated_at: str
    evidence_fingerprint: str
```

The builder must canonicalize deterministic ordering and reject `hint_count > 3`.

- [ ] **Step 4: Add provisioning RED tests**

Cover fresh build, `--check-only`, idempotent rerun, missing source, source hash mismatch, symlink runtime, existing divergent artifact, and fixed output paths.

- [ ] **Step 5: Implement controlled provisioning command**

Use only fixed tracked manifest/source paths and fixed `.nbs_agent_runtime/memory-hub/`. Return `memory-hub-provisioning-report-v1` with three catalog fingerprints. Do not accept caller-provided output paths.

- [ ] **Step 6: Verify Task 1**

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_integration_models.py tests/test_memory_hub_provisioning.py tests/test_memory_hub_deployment_provider.py tests/test_memory_hub_policy_service.py
.venv/bin/python -m py_compile backend/agents/memory_hub_integration_models.py scripts/provision_memory_hub_catalog.py
git diff --check
```

- [ ] **Step 7: Findings-first Review and commit**

Review only Task 1 files. After PASS:

```bash
git add backend/agents/memory_hub_integration_models.py scripts/provision_memory_hub_catalog.py tests/test_memory_hub_integration_models.py tests/test_memory_hub_provisioning.py agent_config/evidence_allowlist.json
git commit -m "feat: add governed memory hub integration evidence"
```

---

### Task 2: Agent Operations observation-only integration

**Files:**
- Modify: `backend/services/agent_operations_service.py`
- Modify: `agent_operations_rendering.py`
- Modify: `app_pages.py`
- Modify: `tests/test_agent_operations_service.py`
- Modify: `tests/test_agent_operations_rendering.py`
- Modify: `tests/test_app_pages_memory_hub.py`

**Interfaces:**
- Consumes: `MemoryHubIntegrationEvidence` and fixed deployment provider.
- Produces snapshot field: `memoryHubIntegration: {status, consumers, diagnostics}`.
- Produces no write, activation, refresh, provision, approval or dispatch callback.

- [ ] **Step 1: Write RED service tests**

Assert `AgentOperationsService.build_snapshot()` projects ready/missing/invalid evidence with bounded counts and fingerprints; absolute paths and malformed artifacts become diagnostics.

- [ ] **Step 2: Run RED service tests**

```bash
.venv/bin/python -m pytest -q tests/test_agent_operations_service.py -k memory_hub
```

Expected: failure because `memoryHubIntegration` is absent.

- [ ] **Step 3: Implement observation read model**

Add a private reader limited to `.nbs_agent_runtime/memory-hub/` and allowlisted integration evidence. It must return `catalog_missing` rather than creating runtime files.

- [ ] **Step 4: Write RED rendering tests**

Assert UI renders catalog readiness, Context/Sidecar/Offload mode, latest status, hint count and diagnostics; assert no buttons/callbacks for provision, activation, dispatch, approval or refresh-write.

- [ ] **Step 5: Implement UI projection**

Extend existing Memory Hub/Agent Operations sections using current Streamlit components and dynamic theme tokens. Keep callbacks read-only.

- [ ] **Step 6: Verify, Review and commit Task 2**

```bash
.venv/bin/python -m pytest -q tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_app_pages_memory_hub.py tests/test_memory_hub_rendering.py
.venv/bin/python -m py_compile backend/services/agent_operations_service.py agent_operations_rendering.py app_pages.py
git diff --check
```

After findings-first PASS:

```bash
git add backend/services/agent_operations_service.py agent_operations_rendering.py app_pages.py tests/test_agent_operations_service.py tests/test_agent_operations_rendering.py tests/test_app_pages_memory_hub.py
git commit -m "feat: expose memory hub integration observations"
```

---

### Task 3: Governance Graph memory evidence lineage

**Files:**
- Create: `backend/agents/governance_graph_memory_integration_models.py`
- Create: `backend/agents/governance_graph_memory_integration_service.py`
- Modify: `backend/agents/governance_graph_query_service.py`
- Create: `tests/test_governance_graph_memory_integration.py`
- Modify: `tests/test_governance_graph_query_service.py`

**Interfaces:**
- Consumes: validated `memory-hub-agent-integration-v1` artifacts only.
- Produces deterministic `memory-hub-lineage-v1` read model links with `evidenceRef`.
- Does not modify `GovernanceGraphSnapshot v1`, canonical workflow node status, or persist/refresh snapshots implicitly.

- [ ] **Step 1: Write RED lineage model tests**

Test exact node types, allowed relations (`derived_from`, `produces`, `verifies`, `documented_by`), deterministic ordering, evidence reference containment and fingerprint validation.

- [ ] **Step 2: Run RED tests**

```bash
.venv/bin/python -m pytest -q tests/test_governance_graph_memory_integration.py
```

Expected: import failure for the new lineage service.

- [ ] **Step 3: Implement read-only lineage service**

Implement:

```python
class GovernanceGraphMemoryIntegrationService:
    def project(self, run_id: str) -> dict[str, object]:
        ...
```

Return exact `memory-hub-lineage-v1` keys: `runId`, `status`, `links`, `evidenceRefs`, `diagnostics`, `lineageFingerprint`. Only project links backed by regular, bounded, fingerprint-valid artifacts. Missing evidence returns no inferred link and a bounded diagnostic.

- [ ] **Step 4: Integrate query read model without changing canonical statuses**

Expose the lineage read model through the existing read-only Graph query path. Do not add an `edges` field to `GovernanceGraphSnapshot v1`; do not change `_overall_status`, `_allowed_next` or descendant invalidation.

- [ ] **Step 5: Verify, Review and commit Task 3**

```bash
.venv/bin/python -m pytest -q tests/test_governance_graph_memory_integration.py tests/test_governance_graph_service.py tests/test_governance_graph_query_service.py
.venv/bin/python -m py_compile backend/agents/governance_graph_memory_integration_models.py backend/agents/governance_graph_memory_integration_service.py backend/agents/governance_graph_service.py
git diff --check
```

After findings-first PASS commit only Task 3 files with message:

```text
feat: add memory evidence lineage to governance graph
```

---

### Task 4: Review Agent gated supplementary memory context

**Files:**
- Modify: `backend/agents/review_agent_service.py`
- Modify: `scripts/review_agent.py`
- Modify: `tests/test_review_agent_service.py`
- Modify: `tests/test_review_agent_cli.py`
- Modify: `docs/agents/REVIEW_AGENT_CONTRACT.md`

**Interfaces:**
- Consumes optional `MemoryHubIntegrationEvidence` plus bounded `MemoryHints` already emitted by Context Agent.
- Produces optional `memoryHubContext` observation in review evidence.
- Review verdict calculation remains independent of memory readiness.

- [ ] **Step 1: Write RED review tests**

Cover ready, missing, stale, fingerprint mismatch, wrong consumer/run and over-cap hints. Assert canonical requirement coverage and verdict are byte-equivalent with and without ignored hints.

- [ ] **Step 2: Run RED tests**

```bash
.venv/bin/python -m pytest -q tests/test_review_agent_service.py tests/test_review_agent_cli.py -k memory
```

Expected: failure because review evidence does not yet expose `memoryHubContext`.

- [ ] **Step 3: Implement gated adapter**

The adapter may add supplementary context only after exact identity/fingerprint checks. Map invalid evidence to `ignored`; never query the provider and never change review verdict inputs.

- [ ] **Step 4: Update Review contract**

Document that Memory Hub is non-authoritative, optional, cannot prove coverage/PASS, and cannot replace actual diff/tests.

- [ ] **Step 5: Verify, Review and commit Task 4**

```bash
.venv/bin/python -m pytest -q tests/test_review_agent_service.py tests/test_review_agent_cli.py tests/test_agent_read_only_contract.py
.venv/bin/python -m py_compile backend/agents/review_agent_service.py scripts/review_agent.py
git diff --check
```

After findings-first PASS commit with message:

```text
feat: gate supplementary memory context in review agent
```

---

### Task 5: Implementation Agent authorized Task-scoped memory context

**Files:**
- Modify: `backend/agents/implementation_models.py`
- Modify: `backend/agents/implementation_guard.py`
- Modify: `backend/agents/implementation_agent_service.py`
- Modify: `scripts/implementation_agent.py`
- Modify: `tests/test_implementation_agent_service.py`
- Modify: `tests/test_implementation_agent_cli.py`
- Modify: `tests/test_implementation_agent_integration.py`
- Modify: `docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md`

**Interfaces:**
- Adds optional contract fields: `memoryContextAllowed: bool` and `expectedMemoryEvidenceFingerprint: str | null`.
- Consumes precomputed bounded hints only; no provider access.
- Produces implementation report observation: `memoryContextStatus` and evidence fingerprint.

- [ ] **Step 1: Write RED contract tests**

Assert exact schema accepts only `(false, null)` or `(true, valid SHA-256)`. Reject implicit true, missing expected fingerprint, unknown fields and mismatched Task/run identity.

- [ ] **Step 2: Run RED tests**

```bash
.venv/bin/python -m pytest -q tests/test_implementation_agent_service.py tests/test_implementation_agent_cli.py -k memory
```

Expected: contract schema failure until new fields are implemented.

- [ ] **Step 3: Implement authorization gate**

Inject sanitized hints into the staged worker input only when both contract fields authorize the exact evidence fingerprint. Do not add files/commands/network permissions or expand allowed risk surfaces.

- [ ] **Step 4: Add sandbox regression tests**

Prove hints cannot modify allowlisted files, commands, environment, network or Task scope; invalid hints are omitted and the canonical Task still runs.

- [ ] **Step 5: Update Implementation contract**

Specify explicit opt-in, exact fingerprint binding, non-authoritative status and no next-Task/commit/merge authority.

- [ ] **Step 6: Verify, Review and commit Task 5**

```bash
.venv/bin/python -m pytest -q tests/test_implementation_agent_service.py tests/test_implementation_agent_cli.py tests/test_implementation_agent_integration.py tests/test_agent_dispatch_contract.py
.venv/bin/python -m py_compile backend/agents/implementation_models.py backend/agents/implementation_guard.py backend/agents/implementation_agent_service.py scripts/implementation_agent.py
git diff --check
```

After findings-first PASS commit with message:

```text
feat: authorize task scoped memory context
```

---

### Task 6: Documentation approved-evidence integration

**Files:**
- Modify: `backend/agents/documentation_evidence.py`
- Modify: `backend/agents/documentation_models.py`
- Modify: `backend/agents/documentation_workflow.py`
- Modify: `tests/test_documentation_agent_cli.py`
- Modify: `tests/test_documentation_workflow.py`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`

**Interfaces:**
- Consumes only completed-run `memory-hub-agent-integration-v1` observation after Review/full verification/Hermes PASS.
- Adds bounded `memoryHubSummary` to `documentation-evidence-v1`.
- Documentation runner receives no provider, catalog path or live query callback.

- [ ] **Step 1: Write RED documentation tests**

Assert no summary before all three gates, exact run/head binding, bounded source refs, no raw hints, and `blocked_missing_runner` behavior unchanged.

- [ ] **Step 2: Run RED tests**

```bash
.venv/bin/python -m pytest -q tests/test_documentation_workflow.py tests/test_documentation_agent_cli.py -k memory
```

Expected: failure because `memoryHubSummary` is not projected.

- [ ] **Step 3: Implement approved evidence projection**

Project only consumer/mode/status/count/fingerprints/source refs. Do not include memory summaries, raw prompt or catalog contents.

- [ ] **Step 4: Update architecture and dispatch contracts**

Record the seven integration modes and reaffirm Documentation Agent proposal-only/no-live-query boundary.

- [ ] **Step 5: Verify, Review and commit Task 6**

```bash
.venv/bin/python -m pytest -q tests/test_documentation_workflow.py tests/test_documentation_agent_cli.py tests/test_documentation_agent_docs.py tests/test_agent_dispatch_contract.py
.venv/bin/python -m py_compile backend/agents/documentation_evidence.py backend/agents/documentation_models.py backend/agents/documentation_workflow.py
git diff --check
```

After findings-first PASS commit with message:

```text
feat: document approved memory integration evidence
```

---

### Task 7: Cross-module final acceptance

**Files:**
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `tests/test_hermes_post_change_check.py`
- Modify: `docs/superpowers/plans/2026-08-18-nbs-memory-hub-agent-module-integration.md` (completion reconciliation only)

**Interfaces:**
- Consumes all Task 1–6 artifacts and tests.
- Produces no new runtime authority; Hermes remains read-only.

- [ ] **Step 1: Add RED Hermes boundary tests**

Assert Hermes only validates schemas/fingerprints/bounds and never provisions catalogs, queries provider, activates sidecar, toggles offload, approves, dispatches or writes runtime.

- [ ] **Step 2: Implement bounded Hermes inspection**

Add a read-only `memory-hub-agent-integration-report` step that inspects allowlisted artifacts and returns pass/blocked/degraded without network/provider invocation.

- [ ] **Step 3: Run targeted verification**

```bash
.venv/bin/python -m pytest -q \
  tests/test_memory_hub_integration_models.py \
  tests/test_memory_hub_provisioning.py \
  tests/test_agent_operations_service.py \
  tests/test_agent_operations_rendering.py \
  tests/test_governance_graph_memory_integration.py \
  tests/test_review_agent_service.py \
  tests/test_implementation_agent_service.py \
  tests/test_documentation_workflow.py \
  tests/test_hermes_post_change_check.py
```

- [ ] **Step 4: Run full verification**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Do not claim completion if full pytest, system acceptance or Hermes fails. Report exact failed stage and whether it is code or environment.

- [ ] **Step 5: Final findings-first Review**

Review the immutable combined HEAD range, actual full verification output and Hermes evidence. Resolve all findings before documentation dispatch.

- [ ] **Step 6: Reconcile plan and commit**

Mark only evidence-backed tasks complete. Commit final acceptance changes with message:

```text
test: accept governed memory hub agent integrations
```

- [ ] **Step 7: Documentation proposal gate**

Only after Review PASS, full verification PASS and Hermes PASS, invoke the approved Documentation runner. If unavailable, record `blocked_missing_runner`; do not auto-apply or silently backfill.
