# NBS Memory Sidecar Default-on Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 eligible development workflow 預設使用 bounded Memory Sidecar recall，並對 protected workflow 維持 recall-off 與 canonical-only fallback。

**Architecture:** 先驗證外部已批准的 `memory-sidecar-default-on-amendment-v1` 與正好三次 live A/B evidence manifest；implementation Task 不建立或批准它們。只有兩份 immutable input、writer-disabled 與 explicit invocation permission 全部成立時，runtime-policy resolver 才能讓 eligible development 的 `auto` 進入 `recall_on`。之後再交給既有 provider-neutral adapter 與 `MemorySidecarService`。Context Agent 維持 canonical-first、non-authoritative hint 分離與 writer-disabled 邊界；CLI `auto` 是預設，`on`／`off` 只提供受控 override。

**Tech Stack:** Python 3、dataclass、JSON policy、既有 `MemorySidecarService`／`MemorySidecarRecallRequest`、Context Agent、pytest、system acceptance、Hermes post-change check。

## Global Constraints

- `writer_enabled` 永遠為 `false`；不新增 approval、dispatch、SQLite、baseline、Graph、Git 或 runtime mutation authority。
- Existing caps remain at most 3 hints, 6000 bytes and 800 ms recall budget.
- Protected classes and markers always resolve to `recall_off`; explicit `off` always wins.
- Canonical context bundle fingerprint excludes memory hints; hints remain `authority=non_authoritative_memory`.
- Provider failure, stale, invalid, conflict or permission failure falls back to canonical-only context without blocking development.
- Current contract remains recall-off until Task 0 verifies an externally approved amendment and binds the exact three-attempt live evidence manifest; Task 0 must never write an approved state.
- Amendment and evidence readiness are trusted only through an external Ed25519 verifier/key bundle outside implementation write paths; self-declared JSON fields, unknown key IDs, unsigned manifests and expired evidence are fail-closed.
- Protected task classification comes only from the trusted workflow descriptor factory; CLI, LLM or brief prose cannot be the sole authority.
- Every Task uses TDD, focused tests, py_compile, git diff check and findings-first review before commit.

---

### Task 0: Amend rollout contract and create trusted task descriptor

**Files:**
- Read-only input: `docs/agents/approved/memory-sidecar-default-on-amendment-v1.json` (must already be externally approved; Task 0 must not create or modify it)
- Read-only input: `.nbs_agent_runtime/live-ab/memory-sidecar-live-ab-evidence-v1.json` (must already contain exactly three bound attempts)
- Modify only if separately approved: `docs/agents/MEMORY_SIDECAR_CONTRACT.md`
- Modify only if separately approved: `docs/superpowers/specs/2026-08-11-nbs-live-hermes-ab-acceptance-design.md`
- Create: `backend/agents/memory_sidecar_task_descriptor.py`
- Create: `tests/test_memory_sidecar_task_descriptor.py`

**Interfaces:**
- Consumes: externally approved amendment with exact id/revision/approver/fingerprint, evidence manifest with exactly three unique attempts, workflow stage/action, approved contract scope and immutable brief fingerprint.
- Trust boundary: verify the amendment and evidence signatures over canonicalized payloads with an externally provisioned `TrustedGovernanceKeyProvider.resolve_public_key(key_id)` dependency and existing controlled Ed25519 verifier; canonicalize as UTF-8 sorted-key compact JSON excluding signature fields. Unavailable key bundle, unknown key ID, invalid signature or canonicalization failure is `blocked_policy` with `providerInvocationAllowed=false`. Bind evidence to current `gitHead`, `policyFingerprint`, `provider`, `model`, `configFingerprint`, and `expiresAt` (maximum 30 days). The Task may read these artifacts but may not create, approve, refresh or rewrite them.
- Produces: `MemorySidecarRuntimeRequest` with trusted `taskClass`, protected markers, `taskFingerprint`, amendment fingerprint/revision, `liveEvidenceManifestFingerprint`, `liveAcceptanceStatus`, and `providerInvocationAllowed` precondition. It never writes approval state.

- [ ] **Step 1: Write failing tests** for unknown/missing class, CLI-only spoofing rejection, brief fingerprint mismatch, protected marker derivation, amendment missing → off, live acceptance not ready → off, and ready prerequisite → eligible descriptor.
- [ ] **Step 1b: Add trust-anchor regression tests** for unsigned/tampered amendment, unknown key ID, invalid signature, three-attempt manifest mismatch, expired evidence and current-identity drift; each must resolve to `recall_off` without provider invocation.
- [ ] **Step 2: Run the descriptor tests and confirm failure.**
- [ ] **Step 3: Implement the trusted factory.** Read and verify the external amendment and exact three-attempt manifest; derive class/markers from workflow stage/action and approved scope; bind to the immutable brief fingerprint; never parse LLM prose as authority and never write an approved state.
- [ ] **Step 4: Run focused tests, py_compile and diff-check. Contract text changes remain a separately approved governance action.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_task_descriptor.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/memory_sidecar_task_descriptor.py
git diff --check
```
- [ ] **Step 5: Stop for findings-first Review; no default-on activation is allowed before this Task passes.**

---

### Task 1: Add immutable default-on runtime policy model

**Files:**
- Create: `backend/agents/memory_sidecar_runtime_policy.py`
- Create: `agent_config/memory_sidecar_runtime_policy.json`
- Create: `tests/test_memory_sidecar_runtime_policy.py`

**Interfaces:**
- Consumes: trusted `MemorySidecarRuntimeRequest` from Task 0, exact JSON policy with `defaultRecallEnabled` and `policyVersion`.
- Produces: `MemorySidecarRuntimeDecision` with `mode`, `status`, `providerInvocationAllowed`, `reason`, `policyFingerprint`, `taskFingerprint`, `writerEnabled=False`, `shadowMode`.

- [ ] **Step 1: Write failing tests** for auto development → `recall_on`, protected class/marker → `recall_off`, explicit off precedence, explicit on rejection for protected tasks, invalid schema, and stable policy fingerprint.
- [ ] **Step 2: Run the new test file and confirm the missing model/config failure.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_runtime_policy.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement exact JSON loading and resolver.** Validate exact keys `schemaVersion`, `policyVersion`, `defaultRecallEnabled`, `eligibleTaskClasses`, `protectedTaskClasses`, `protectedMarkers`, and `contractAmendmentRequired`; reject unknown values and fail closed. Keep policy decisions free of query/prompt/path/secret data.
- [ ] **Step 4: Run focused tests and static checks.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_runtime_policy.py tests/test_memory_sidecar_models.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/memory_sidecar_runtime_policy.py
git diff --check
```

- [ ] **Step 5: Stop for findings-first Review of only Task 1 files.**

### Task 2: Integrate policy resolution at the Context Agent boundary

**Files:**
- Modify: `backend/agents/context_agent_service.py`
- Modify: `scripts/context_agent.py`
- Create: `tests/test_memory_sidecar_default_on_context.py`
- Modify: `tests/test_context_agent_service.py`
- Modify: `tests/test_memory_sidecar_context_integration.py`

**Interfaces:**
- Consumes: Task 1 `MemorySidecarRuntimeDecision`, existing `MemoryHints`, canonical `EvidenceBundle`, and an injected provider-neutral invocation seam.
- Produces: Context payload with unchanged `bundleFingerprint` and optional non-authoritative `memoryHints`; policy decision is telemetry-only and is not added to strict Context payload keys.

- [ ] **Step 1: Write failing tests** for CLI `--recall auto|on|off`, development auto-on, protected task forced-off, explicit off no provider call, provider invocation count with `shadow_mode=True`, canonical fingerprint independence from hints, and rejection of policy telemetry by `context_bundle_from_payload()`/`context_summary_from_evidence_payload()`.
- [ ] **Step 2: Run the focused context tests and confirm failure.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_default_on_context.py tests/test_context_agent_service.py -q -p no:cacheprovider
```

- [ ] **Step 3: Add the resolver call before provider invocation.** Preserve existing `MemorySidecarService` caps and `_memory_hints_payload` validation. Derive a bounded query from `bundle.task.objective`, declare only canonical evidence sourceRefs allowed by the existing denylist, and inject a provider-neutral invoker. In `auto`, call it only when `providerInvocationAllowed=True`; in `off` or protected `on`, do not invoke it even when `shadow_mode=True`. No concrete network provider is added in this Task.
- [ ] **Step 4: Run focused Context Agent tests, including existing memory-hints integration, then py_compile and diff check.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_default_on_context.py tests/test_context_agent_service.py tests/test_memory_sidecar_context_integration.py tests/test_memory_sidecar_service.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/context_agent_service.py scripts/context_agent.py
git diff --check
```

- [ ] **Step 5: Stop for findings-first Review.** Review must verify no protected task can invoke recall and no canonical fingerprint includes hints.

### Task 3: Add bounded policy-decision telemetry and fallback regression coverage

**Files:**
- Create: `backend/agents/memory_sidecar_policy_telemetry.py`
- Modify: `backend/agents/agent_runtime.py`
- Create: `tests/test_memory_sidecar_default_on_telemetry.py`
- Modify: `tests/test_memory_sidecar_telemetry.py`

**Interfaces:**
- Consumes: Task 1 decision and existing provider status (`ready`, `empty`, `timeout`, `degraded`, `stale`, `conflict`).
- Produces: exact `memory-sidecar-policy-decision-v1` telemetry; existing `memory-sidecar-telemetry-v1` remains unchanged.

- [ ] **Step 1: Write failing tests** for recall-on ready, protected recall-off, provider timeout/degraded fallback, exact policy-decision envelope fields, malformed/symlink telemetry blocking, and no secret/raw query persistence.
- [ ] **Step 2: Run the focused telemetry tests and confirm failure.**
- [ ] **Step 3: Implement the separate exact `memory-sidecar-policy-decision-v1` envelope with keys `schemaVersion`, `runId`, `taskFingerprint`, `policyFingerprint`, `mode`, `status`, `providerInvocationAllowed`, `reason`, `fallback`, `latencyMs`, and `writerEnabled`; enforce safe identifier caps, allowlisted reasons, `latencyMs=0..800`, symlink/path checks and no-raw-data validation. Write only `.nbs_agent_runtime/telemetry/memory_sidecar_policy.jsonl`; do not change `memory-sidecar-telemetry-v1`, write canonical artifacts or alter writer behavior.**
- [ ] **Step 3b: Enforce the literal telemetry contract:** no extra keys; exact schema version; `runId` regex `^[A-Za-z0-9_.-]{1,128}$`; lowercase 64-hex SHA-256 task/policy fingerprints; modes `recall_on|recall_off`; statuses `allowed|protected|blocked_policy|disabled|fallback`; reasons `amendment_missing|evidence_missing|identity_mismatch|protected_task|explicit_off|provider_ready|provider_empty|provider_timeout|provider_degraded|invalid_hint|stale_hint`; strict booleans and integer `latencyMs` in `0..800`.
- [ ] **Step 4: Run focused telemetry tests, py_compile and diff check.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_default_on_telemetry.py tests/test_memory_sidecar_telemetry.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/memory_sidecar_policy_telemetry.py backend/agents/agent_runtime.py
git diff --check
```

- [ ] **Step 5: Stop for findings-first Review.**

### Task 4: CLI/workflow acceptance and final verification

**Files:**
- Modify: `backend/agents/workflow_orchestrator.py`
- Modify: `scripts/agent_workflow.py`
- Create: `tests/test_memory_sidecar_default_on_workflow.py`
- Modify: `tests/test_workflow_orchestrator_start.py`
- Modify: `tests/test_agent_workflow_cli.py`
- Modify: `tests/test_memory_sidecar_task_descriptor.py`

**Interfaces:**
- Consumes: Task 0 trusted descriptor, Task 1–3 policy decision/telemetry, CLI `--recall` override, existing Context Agent output.
- Produces: workflow run metadata that records policy mode/reason without changing approval, Review, Verification, Hermes or documentation gates.

- [ ] **Step 1: Write failing tests** for default auto mode, explicit off, protected task auto-off, deterministic bounded metadata, and unchanged approval/dispatch arguments.
- [ ] **Step 2: Run focused workflow tests and confirm failure.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_default_on_workflow.py tests/test_workflow_orchestrator_start.py tests/test_agent_workflow_cli.py -q -p no:cacheprovider
```
- [ ] **Step 3: Wire the explicit `--recall` option through workflow start to Context Agent only.** Reject unknown values; CLI cannot supply task class/markers; never persist credentials or raw query. Keep writer off and ordinary workflow fallback canonical-only.
- [ ] **Step 4: Run complete targeted suites and final checks.**

```bash
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m pytest tests/test_memory_sidecar_task_descriptor.py tests/test_memory_sidecar_runtime_policy.py tests/test_memory_sidecar_default_on_context.py tests/test_memory_sidecar_default_on_telemetry.py tests/test_memory_sidecar_default_on_workflow.py tests/test_memory_sidecar_service.py tests/test_memory_sidecar_telemetry.py tests/test_context_agent_service.py tests/test_memory_sidecar_context_integration.py tests/test_workflow_orchestrator_start.py tests/test_agent_workflow_cli.py -q -p no:cacheprovider
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python -m py_compile backend/agents/memory_sidecar_task_descriptor.py backend/agents/memory_sidecar_runtime_policy.py backend/agents/context_agent_service.py backend/agents/memory_sidecar_policy_telemetry.py backend/agents/agent_runtime.py backend/agents/workflow_orchestrator.py scripts/context_agent.py scripts/agent_workflow.py
git diff --check
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/system_manager.py acceptance
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python scripts/hermes_post_change_check.py
```

- [ ] **Step 5: Confirm contract amendment, trusted descriptor, ordinary development auto-on, protected workflow off, full verification PASS and Hermes PASS.** Any provider/network limitation remains canonical-only and is reported as fallback, not as a false token-savings claim.

## Rollback

Set `defaultRecallEnabled=false` or pass `--recall off`; remove only ignored telemetry if required. No canonical artifact, SQLite, baseline or Git rollback is needed.
