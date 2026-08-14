# NBS Memory Hub C-0/C-1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立只接受三類 canonical source、可 deterministic rebuild、具 deny-by-default scope filtering 的 read-only Memory Hub，安全承接現有 Memory Sidecar／Short-term Offload 的 verified evidence，而不改變現有 workflow authority 或 recall default。

**Architecture:** 新增 provider-neutral Python contract models、bounded immutable catalog builder／loader 與 MemoryHubService。Catalog 是 canonical artifacts 的衍生 read model；query 只回傳經 fingerprint、freshness 和 scope decision 驗證的 records。Sidecar／Offload 只透過 read-only projection 使用，不引入 Gateway、SQLite 或 migration。

**Tech Stack:** Python 3、`dataclasses`、`pathlib`、現有 `canonical_fingerprint`、pytest、py_compile；不新增 runtime dependency。

## Global Constraints

- 只允許 `governance_document`、`verified_evidence`、`approved_skill` 三種 `sourceKind`。
- Canonical artifacts 是唯一真相來源；Memory Hub、catalog、query result 都是 read-only derived projection。
- 不修改正式 SQLite、baseline、revenue scope、business rules、export schema、Git、Graph authority、approval 或 dispatch。
- 普通 workflow 的 Memory Sidecar／Short-term Offload recall default 維持現狀；不得在本計畫內 default-on。
- 所有 path 必須在明確 allowlisted root 內；拒絕 absolute path、`..`、symlink、cross-root、secret-like path、raw data 和 unbounded content。
- 所有 public envelopes exact-key、bounded、fingerprint-derived；不接受 caller supplied identity/fingerprint。
- 缺失、stale、unknown、tampered、scope mismatch、timeout 或 provider unavailable 必須 fail closed 或 fallback 到既有 canonical context。
- 每個 Task 只修改其 allowlisted files；Implementation Agent 不得 commit、merge、push 或自行進入下一 Task。

---

### Task 1: C-0 Contract Models and Identity

**Files:**
- Create: `backend/agents/memory_hub_models.py`
- Test: `tests/test_memory_hub_models.py`

**Interfaces:**
- Produces `MemorySource`, `MemoryRecord`, `MemoryQuery`, `RuntimeIdentity`, `MemoryACLDecision`, `MemoryQueryResult` and `MemoryHubSchemaError`.
- Each model exposes `from_dict(payload)`, `to_dict()`, and deterministic identity/fingerprint properties where applicable.
- Consumes `backend.agents.evidence_models.canonical_fingerprint` only; no filesystem or provider calls.

- [ ] **Step 1: Write failing schema tests**

Add tests for exact keys, all three allowed source kinds, source/record/query/identity bounds, fingerprint re-derivation, UTF-8 summary cap, and reject arbitrary conversation, SQLite/CSV/Excel/log, secret-like path, absolute path, traversal and unknown fields.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_hub_models.py -q
```

Expected: collection or import failure because `backend.agents.memory_hub_models` does not exist.

- [ ] **Step 3: Implement immutable models**

Use frozen dataclasses and strict `from_dict` validation. Derive `sourceId`, `memoryId`, `sourceFingerprint`, `recordFingerprint`, `queryFingerprint` and `decisionFingerprint` from canonical unsigned fields; do not trust supplied values. Keep `runId`, `gitHead`, owner, scope and freshness explicit.

- [ ] **Step 4: Run focused GREEN verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_hub_models.py -q
.venv/bin/python -m py_compile backend/agents/memory_hub_models.py tests/test_memory_hub_models.py
git diff --check
```

Expected: all focused tests pass and compile/diff checks exit 0.

- [ ] **Step 5: Write the Task 1 report and stop for findings-first review**

Record changed files, RED/GREEN evidence, scope exclusions and residual risks in the approved task report. Do not touch catalog or service files.

### Task 2: Immutable Catalog Builder and Loader

**Files:**
- Create: `backend/agents/memory_hub_catalog.py`
- Test: `tests/test_memory_hub_catalog.py`

**Interfaces:**
- Consumes Task 1 models and a read-only source root.
- Produces `build_catalog(source_root, output_path, policy) -> MemoryCatalog` and `load_catalog(catalog_path, runtime_root, policy) -> MemoryCatalog`.
- `MemoryCatalog` exposes `to_dict()`, `catalog_fingerprint`, `source_set_fingerprint`, `policy_fingerprint`, `built_from_head`, and immutable lookup by `source_id` / `memory_id`.
- Builder accepts only explicit source descriptors; it must not scan arbitrary repository files or auto-discover undocumented roots.

- [ ] **Step 1: Write failing catalog tests**

Cover deterministic rebuild, exact catalog envelope, source hash verification, three-source allowlist, root containment, symlink/intermediate symlink, traversal, denied extensions, missing source, stale source, duplicate identity, tampered catalog, and immutable existing output conflict.

- [ ] **Step 2: Run tests and verify RED**

Run `.venv/bin/python -m pytest tests/test_memory_hub_catalog.py -q` and expect missing-module failure.

- [ ] **Step 3: Implement bounded builder/loader**

Canonicalize and validate roots before reading; reject symlinks and escape paths. Read only explicit relative artifact refs, recompute artifact SHA-256, validate source/record fingerprints, sort records deterministically, and write only to an isolated catalog output root. Loader must never rebuild or mutate a missing/tampered catalog.

- [ ] **Step 4: Run focused GREEN verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_hub_catalog.py -q
.venv/bin/python -m py_compile backend/agents/memory_hub_catalog.py tests/test_memory_hub_catalog.py
git diff --check
```

- [ ] **Step 5: Write report and stop for Review Agent**

Report exact source roots, catalog output policy, deterministic fingerprint evidence and any blocked cases. No service or adapter changes.

### Task 3: Read-only MemoryHubService

**Files:**
- Create: `backend/agents/memory_hub_service.py`
- Test: `tests/test_memory_hub_service.py`

**Interfaces:**
- Consumes `MemoryCatalog`, `MemoryQuery`, `RuntimeIdentity`, and `MemoryACLDecision` from Tasks 1–2.
- Produces `MemoryHubService.query(query, identity) -> MemoryQueryResult` and `MemoryHubService.resolve_source(source_id, identity) -> SourceResolution`.
- No provider, shell, database, Git, Graph, approval or write dependency.

- [ ] **Step 1: Write failing service tests**

Cover deterministic ordering, maxItems=3, maxBytes=6000, timeout budget, project/agent/team allow/deny/blocked decisions, stale filtering, missing catalog, malformed identity, source drill-down bounds, cross-scope rejection, and no write/side-effect behavior.

- [ ] **Step 2: Run tests and verify RED**

Run `.venv/bin/python -m pytest tests/test_memory_hub_service.py -q`; expect missing-module failure.

- [ ] **Step 3: Implement fail-closed read service**

Filter records by validated scope and fresh status, derive ACL decisions, sort by stable `(memoryKind, memoryId)`, enforce serialized byte/item caps, and map missing/tampered/timeout conditions to `blocked`, `empty`, `timeout` or `degraded` without rebuilding the catalog.

- [ ] **Step 4: Run focused GREEN verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_hub_service.py -q
.venv/bin/python -m py_compile backend/agents/memory_hub_service.py tests/test_memory_hub_service.py
git diff --check
```

- [ ] **Step 5: Write report and stop for Review Agent**

Include evidence that Service failures preserve canonical fallback and that no SQLite, Graph or runtime write occurred.

### Task 4: Sidecar／Offload Read-only Projection

**Files:**
- Create: `backend/agents/memory_hub_projection.py`
- Test: `tests/test_memory_hub_projection.py`

**Interfaces:**
- Consumes `MemoryQueryResult`, existing `MemoryHints`, and existing bounded Short-term Offload projection.
- Produces a labeled non-authoritative projection with source IDs/fingerprints and no new write path.
- Existing default flags and canonical context fingerprint must remain byte-for-byte compatible when the projection is absent, empty, timeout, stale or blocked.

- [ ] **Step 1: Write failing integration tests**

Prove ready results are labeled `non_authoritative_memory`, source fingerprints remain attached, stale/blocked results are not injected, existing canonical bundle fingerprint is unchanged without projection, and offload/sidecar defaults remain unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run `.venv/bin/python -m pytest tests/test_memory_hub_projection.py -q`; expect missing projection import or behavior failure.

- [ ] **Step 3: Implement projection adapter**

Convert only validated ready records to existing bounded hint shapes. Keep canonical evidence separate, do not call catalog builder, do not enable recall, and return existing fallback statuses for all non-ready results.

- [ ] **Step 4: Run focused regression verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_hub_projection.py tests/test_memory_sidecar_context_integration.py tests/test_short_term_offload_projection.py -q
.venv/bin/python -m py_compile backend/agents/memory_hub_projection.py
git diff --check
```

- [ ] **Step 5: Write report and stop for Review Agent**

Document unchanged default policy and proof that projection cannot become canonical evidence or Graph input.

### Task 5: Consolidated Contract Verification and Acceptance

**Files:**
- Modify only if needed: affected C-0/C-1 source/test files from Tasks 1–4
- Test: `tests/test_memory_hub_models.py`, `tests/test_memory_hub_catalog.py`, `tests/test_memory_hub_service.py`, `tests/test_memory_hub_projection.py`
- Report: `.superpowers/sdd/2026-08-14-nbs-memory-hub-c0-c1/task-5-report.md`

**Interfaces:**
- Consumes reviewed Task 1–4 artifacts and actual diffs.
- Produces consolidated verification evidence only; it does not add features or relax gates.

- [ ] **Step 1: Run all C-0/C-1 focused suites**

Run:

```bash
.venv/bin/python -m pytest tests/test_memory_hub_models.py tests/test_memory_hub_catalog.py tests/test_memory_hub_service.py tests/test_memory_hub_projection.py tests/test_memory_sidecar_models.py tests/test_memory_sidecar_context_integration.py tests/test_short_term_offload_projection.py -q
```

- [ ] **Step 2: Run compile, full regression and system acceptance**

Run:

```bash
.venv/bin/python -m py_compile backend/agents/memory_hub_models.py backend/agents/memory_hub_catalog.py backend/agents/memory_hub_service.py backend/agents/memory_hub_projection.py
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
```

- [ ] **Step 3: Run Hermes read-only acceptance**

Run `.venv/bin/python scripts/hermes_post_change_check.py`. If Hermes is degraded, blocked or times out, record the exact stage and do not declare C-0/C-1 accepted.

- [ ] **Step 4: Verify boundary and token claims**

Confirm no SQLite/baseline/business-rule/export/Git writes, no default recall change, no Graph node/edge creation, and no token-reduction claim based solely on catalog presence. Any future token claim must use separately traceable real off/on evidence.

- [ ] **Step 5: Final findings-first review and acceptance report**

Submit the consolidated diff, focused/full test output, system acceptance, Hermes result and residual risks. Stop before any candidate layer, Wiki, CodeGraph, Gateway or migration work.
