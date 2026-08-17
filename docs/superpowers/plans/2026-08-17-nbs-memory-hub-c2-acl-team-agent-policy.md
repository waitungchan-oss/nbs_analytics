# NBS Memory Hub C2 ACL／Team／Agent Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 C-0/C-1 Memory Hub 之上加入兩個 deployment-owned immutable catalog 與共用 read-only policy decision service，讓 Agent 的 Memory 讀取可驗證、可追溯且 deny-by-default。

**Architecture:** `Team Catalog` 保存 project-bound team identity、治理角色與 agent membership；`Agent Policy Catalog` 保存 agent identity、team references、allowed memory kinds／scopes 與 immutable rules。`MemoryHubPolicyService` 載入並驗證兩個 catalog，再對 C-0/C-1 `RuntimeIdentity`、`MemoryQuery`、`MemoryRecord` 做 deterministic `allow／deny／blocked` decision。既有 `MemoryHubService`、Memory Sidecar、Short-term Offload 與 Streamlit 只消費 read-only decision，不取得任何 mutation 或 dispatch 能力。

**Tech Stack:** Python 3、frozen `dataclasses`、`pathlib`、現有 `canonical_fingerprint`、pytest、py_compile；不新增 runtime dependency、SQLite、Node.js service 或外部 network。

## Global Constraints

- 只有兩個獨立 immutable catalog：`Team Catalog` 與 `Agent Policy Catalog`；policy rules 必須包含在 Agent Policy Catalog，不建立第三個 policy catalog。
- Catalog 只由 deployment-owned provider 提供；query、UI、Sidecar、Offload 與 Agent Operations 不得建立、更新、刪除或重建 catalog。
- C-0/C-1 canonical contracts、canonical artifacts、正式 context、Review、Verification 與 Hermes 仍是真相來源或治理 gate。
- `projectId`、`agentId`、`teamId` 只代表治理角色／責任群組，不是登入帳號、IAM、OAuth、credential 或人員身份。
- 所有 public envelopes exact-key、bounded、fingerprint-derived；不接受 caller supplied identity、membership 或 fingerprint。
- `defaultDecision` 固定為 `deny`；identity、catalog、record、source 或 path 無法驗證時固定 `blocked`，不得猜測或放寬。
- 不修改正式 SQLite、baseline、revenue scope、business rules、export schema、Git、Graph snapshot、approval、dispatch 或 workflow state。
- Memory Sidecar／Short-term Offload default policy、recall flag、writer flag 與 canonical fallback 必須維持不變。
- 不接入外部 network/provider，不新增 dependency；所有 decision 必須在本地 deterministic read-only 完成。
- 每個 Task 只能修改其列出的 allowlist files；每個 Task 完成後必須停在 findings-first Review，Implementation Agent 不得 commit、merge、push 或自行進入下一 Task。

---

### Task 1: Team Catalog immutable contract and loader

**Files:**
- Create: `backend/agents/memory_hub_team_catalog.py`
- Test: `tests/test_memory_hub_team_catalog.py`

**Interfaces:**
- `TeamCatalog`, `TeamRecord`, `MemoryHubTeamCatalogError`
- `TeamCatalog.from_dict(payload, *, expected_project_id) -> TeamCatalog`
- `TeamCatalog.load(path, *, runtime_root, expected_project_id) -> TeamCatalog`
- `TeamCatalog.team(team_id) -> TeamRecord | None`
- `TeamCatalog.catalog_fingerprint -> str`
- `TeamRecord.to_dict() -> dict[str, object]`

- [ ] **Step 1: Write failing contract tests**

Add tests that construct a valid `memory-team-catalog-v1` payload and assert:

```python
catalog = TeamCatalog.from_dict(payload, expected_project_id="nbs_analytics")
assert catalog.team("team-finance-governance").agent_ids == ("agent-context-reader",)
assert len(catalog.catalog_fingerprint) == 64
```

Also add negative cases for unknown keys, malformed IDs, duplicate teams or agents, wrong project, unsorted arrays, caller-supplied fingerprint tampering, missing referenced team, symlink catalog path, path traversal, and catalog output outside `runtime_root`.

- [ ] **Step 2: Run RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_team_catalog.py
```

Expected: import failure because `memory_hub_team_catalog.py` does not exist.

- [ ] **Step 3: Implement strict immutable Team Catalog**

Use frozen dataclasses and exact-key parsing. Sort `teams` and each `agentIds` tuple deterministically before deriving `recordFingerprint` and `catalogFingerprint`. Resolve the catalog path canonically, reject symlinks/intermediate symlinks and require the resolved file to remain inside `runtime_root`. Never write or rebuild from `TeamCatalog.load`.

- [ ] **Step 4: Run GREEN and static checks**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_team_catalog.py
.venv/bin/python -m py_compile backend/agents/memory_hub_team_catalog.py tests/test_memory_hub_team_catalog.py
git diff --check
```

Expected: all tests pass; compile and diff checks exit 0.

- [ ] **Step 5: Write Task 1 report and request strict Review**

Record schema coverage, path-safety evidence, deterministic fingerprint evidence, and explicit confirmation that no Team membership mutation or catalog rebuild was added.

### Task 2: Agent Policy Catalog immutable contract and loader

**Files:**
- Create: `backend/agents/memory_hub_agent_policy_catalog.py`
- Test: `tests/test_memory_hub_agent_policy_catalog.py`

**Interfaces:**
- `AgentPolicyCatalog`, `AgentPolicyRecord`, `AgentPolicyRule`, `MemoryHubAgentPolicyCatalogError`
- `AgentPolicyCatalog.from_dict(payload, *, expected_project_id, team_catalog) -> AgentPolicyCatalog`
- `AgentPolicyCatalog.load(path, *, runtime_root, expected_project_id, team_catalog) -> AgentPolicyCatalog`
- `AgentPolicyCatalog.agent(agent_id) -> AgentPolicyRecord | None`
- `AgentPolicyRecord.allows(memory_kind, scope) -> bool`
- `AgentPolicyCatalog.catalog_fingerprint -> str`

- [ ] **Step 1: Write failing policy tests**

Cover valid `memory-agent-policy-catalog-v1`, fixed `defaultDecision="deny"`, deterministic rule ordering, allowed memory kinds/scopes, exact Team references, rule fingerprint re-derivation, duplicate agent/rule rejection, unknown keys, wrong project, missing Team reference, explicit deny, and attempted `defaultDecision="allow"`.

Use this required assertion:

```python
agent = catalog.agent("agent-context-reader")
assert agent.allows("governance", "project") is True
assert agent.allows("skill", "project") is False
```

- [ ] **Step 2: Run RED**

Run `.venv/bin/python -m pytest -q tests/test_memory_hub_agent_policy_catalog.py` and expect missing-module collection failure.

- [ ] **Step 3: Implement policy catalog**

Use frozen dataclasses, exact-key parsing and canonical fingerprints. Require every `teamId` to resolve through Task 1 `TeamCatalog`; reject cross-project references and rules that allow memory kinds/scopes outside the agent allowlists. Keep `defaultDecision` hard-coded to `deny` during parsing and loading.

- [ ] **Step 4: Run GREEN and static checks**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_agent_policy_catalog.py tests/test_memory_hub_team_catalog.py
.venv/bin/python -m py_compile backend/agents/memory_hub_agent_policy_catalog.py tests/test_memory_hub_agent_policy_catalog.py
git diff --check
```

- [ ] **Step 5: Write Task 2 report and request strict Review**

Report that no standalone third Policy Catalog was created; all rules remain inside the immutable Agent Policy Catalog.

### Task 3: MemoryHubPolicyService decision engine

**Files:**
- Create: `backend/agents/memory_hub_policy_service.py`
- Test: `tests/test_memory_hub_policy_service.py`

**Interfaces:**
- `MemoryPolicyDecision` with `to_dict()` and `decision_fingerprint`.
- `MemoryPolicyQueryDecision` with `status`, `decisions`, and `to_dict()`.
- `MemoryHubPolicyService(team_catalog, agent_policy_catalog, *, project_id)`.
- `evaluate(identity, query, record) -> MemoryPolicyDecision`.
- `evaluate_query(identity, query, records) -> MemoryPolicyQueryDecision`.

- [ ] **Step 1: Write failing decision tests**

Add one test for each required outcome:

```python
assert service.evaluate(identity, project_query, project_record).decision == "allow"
assert service.evaluate(identity, skill_query, skill_record).decision == "deny"
assert service.evaluate(identity_without_team, team_query, team_record).decision == "blocked"
```

Also cover same project, same agent, same team, explicit policy deny, no matching allow rule, unknown agent/team, cross-project catalogs, stale source, expired source, blocked source, tampered record fingerprint, catalog fingerprint mismatch, and no metadata leakage on deny/blocked.

- [ ] **Step 2: Run RED**

Run `.venv/bin/python -m pytest -q tests/test_memory_hub_policy_service.py` and expect missing-module failure.

- [ ] **Step 3: Implement fixed-order fail-closed flow**

Implement this exact order:

```text
validate identity
→ validate both catalog identities
→ resolve agent
→ resolve team membership
→ check memory kind and scope allowlists
→ apply explicit rule or fixed deny default
→ validate record freshness/source/fingerprint
→ emit decision fingerprint
```

Return `deny` only for an explicit policy or scope mismatch. Return `blocked` for any identity, catalog, cross-catalog, record, source or path verification failure. When decision is not `allow`, omit record summary, source refs and artifact metadata.

- [ ] **Step 4: Run GREEN and static checks**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_policy_service.py tests/test_memory_hub_agent_policy_catalog.py tests/test_memory_hub_team_catalog.py
.venv/bin/python -m py_compile backend/agents/memory_hub_policy_service.py tests/test_memory_hub_policy_service.py
git diff --check
```

- [ ] **Step 5: Write Task 3 report and request strict Review**

Include a decision matrix excerpt and confirm that the service has no filesystem writes, network calls, SQLite access, Git calls, Graph calls or dispatch hooks.

### Task 4: C-0/C-1 query integration and fallback preservation

**Files:**
- Modify: `backend/agents/memory_hub_service.py`
- Test: `tests/test_memory_hub_service.py`
- Test: `tests/test_memory_hub_policy_service.py`

**Interfaces:**
- Preserve existing `MemoryHubService(catalog, project_id=...)` behavior when no policy service is supplied.
- Add an optional deployment-owned policy gate without changing `query(query, identity)` or `resolve_source(source_id, identity)` signatures.
- Policy `allow` may reach existing C-0/C-1 query filtering; `deny`/`blocked` must return bounded empty/blocked results with no record metadata.

- [ ] **Step 1: Add failing integration tests**

Add tests proving policy denial returns no records, policy blocked returns no records, policy allow preserves existing deterministic query output, and constructing `MemoryHubService` without a policy gate keeps all existing C-0/C-1 tests unchanged.

- [ ] **Step 2: Run RED**

Run `.venv/bin/python -m pytest -q tests/test_memory_hub_service.py tests/test_memory_hub_policy_service.py` and capture the expected missing integration behavior.

- [ ] **Step 3: Implement optional policy gate**

Inject only a typed `MemoryHubPolicyService` from a deployment-owned composition root. Do not accept arbitrary caller policy callbacks, paths, catalog payloads or verifier mappings. Preserve existing query limits, ordering, freshness checks and canonical fallback when the gate is absent or blocked.

- [ ] **Step 4: Run GREEN and regression checks**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_service.py tests/test_memory_hub_policy_service.py tests/test_memory_sidecar_context_integration.py tests/test_short_term_offload_projection.py
.venv/bin/python -m py_compile backend/agents/memory_hub_service.py tests/test_memory_hub_service.py
git diff --check
```

- [ ] **Step 5: Write Task 4 report and request strict Review**

Report unchanged Sidecar／Offload defaults, no catalog rebuild path, and canonical fallback evidence.

### Task 5: Read-only Memory Hub and Agent Operations decision projection

**Files:**
- Modify: `backend/agents/memory_hub_ui_service.py`
- Modify: `memory_hub_rendering.py`
- Modify: `agent_operations_rendering.py`
- Modify: `app_pages.py`
- Test: `tests/test_memory_hub_ui_service.py`
- Test: `tests/test_memory_hub_rendering.py`
- Test: `tests/test_agent_operations_rendering.py`
- Test: `tests/test_app_pages_memory_hub.py`

**Interfaces:**
- Existing read-only UI services receive a typed policy decision projection, not raw catalog payloads.
- UI displays `allow`, `deny`, `blocked`, catalog fingerprints and bounded reason codes only.
- No UI control may write catalogs, change membership, change policy, dispatch agents or toggle recall.

- [ ] **Step 1: Write failing UI projection tests**

Cover allow/deny/blocked rendering, no raw artifact path or summary leakage on denied decisions, catalog missing behavior, policy fingerprint display, and no mutation controls. Assert Agent Operations remains a read-only display.

- [ ] **Step 2: Run RED**

Run the four listed UI test files and capture the expected missing projection or rendering behavior.

- [ ] **Step 3: Implement bounded UI projection**

Add only display adapters and injected typed callbacks. Keep deployment-owned provider composition in `app_pages.py`; do not let Streamlit construct catalogs or policy objects. Render blocked/deny as non-ready states and preserve the existing `catalog_missing` message when deployment artifacts are absent.

- [ ] **Step 4: Run GREEN and UI regression checks**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_ui_service.py tests/test_memory_hub_rendering.py tests/test_agent_operations_rendering.py tests/test_app_pages_memory_hub.py
.venv/bin/python -m py_compile backend/agents/memory_hub_ui_service.py memory_hub_rendering.py agent_operations_rendering.py app_pages.py
git diff --check
```

- [ ] **Step 5: Write Task 5 report and request strict Review**

Include evidence that UI and Agent Operations are observation-only and do not become ACL mutation, approval, dispatch or recall-control entrances.

### Task 6: Full C2 integration, acceptance and Hermes evidence

**Files:**
- Modify only files explicitly identified by prior Review findings.
- Test: `tests/test_memory_hub_team_catalog.py`
- Test: `tests/test_memory_hub_agent_policy_catalog.py`
- Test: `tests/test_memory_hub_policy_service.py`
- Test: `tests/test_memory_hub_service.py`
- Test: `tests/test_memory_hub_ui_service.py`
- Test: `tests/test_memory_hub_rendering.py`
- Test: `tests/test_agent_operations_rendering.py`
- Test: `tests/test_app_pages_memory_hub.py`

**Interfaces:**
- No new public interface; this Task only closes verified findings and records acceptance evidence.

- [ ] **Step 1: Reconcile all Task 1–5 reports and Review findings**

Confirm every finding is either fixed in its owning Task or explicitly recorded as a bounded residual risk; do not mix unrelated dirty files.

- [ ] **Step 2: Run complete C2 regression**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_memory_hub_*.py tests/test_agent_operations_rendering.py tests/test_app_pages_memory_hub.py tests/test_memory_sidecar_context_integration.py tests/test_short_term_offload_projection.py
.venv/bin/python -m py_compile backend/agents/memory_hub_team_catalog.py backend/agents/memory_hub_agent_policy_catalog.py backend/agents/memory_hub_policy_service.py backend/agents/memory_hub_service.py backend/agents/memory_hub_ui_service.py memory_hub_rendering.py agent_operations_rendering.py app_pages.py
git diff --check
```

- [ ] **Step 3: Run full verification and system acceptance**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
```

Expected: full suite passes, Streamlit/API/Vue are ready, SQLite integrity is unchanged, and the fixed baseline remains matched.

- [ ] **Step 4: Run Hermes post-change check**

Run `.venv/bin/python scripts/hermes_post_change_check.py` and require `Overall status: PASS`. Confirm Hermes reports C2 as read-only, with no catalog writes, no SQLite writes, no Graph writes, no dispatch and no recall-default mutation.

- [ ] **Step 5: Perform final acceptance review**

Run a fresh strict Review over the consolidated diff and all verification evidence. Only after Review PASS, full verification PASS and Hermes PASS may Codex commit, push or merge the C2 change.

## Verification and handoff rules

- Review PASS is not final acceptance; full pytest, system acceptance and Hermes are separate gates.
- If Team Catalog or Agent Policy Catalog artifacts are absent in the deployment, UI must show `catalog_missing`／`blocked`; tests must not generate production catalogs as a side effect.
- C3 Wiki Knowledge Layer starts only after C2 final acceptance and must consume `MemoryHubPolicyService`; C3 may not create a second ACL implementation.
