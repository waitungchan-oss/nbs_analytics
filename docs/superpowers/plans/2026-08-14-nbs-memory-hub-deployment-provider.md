# NBS Memory Hub Deployment-owned Catalog Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 將 Memory Hub Streamlit tab 接到固定 allowlist 的 deployment-owned immutable catalog provider。

**Architecture:** 新增純 read-only provider composition，讀固定 manifest、重建 C-0/C-1 policy descriptors，再呼叫既有 `load_catalog()`。缺失回傳 `None`，任何 identity/path/schema/hash 不一致都 fail-closed；provider 不建立 catalog。

**Tech Stack:** Python 3、dataclasses、pathlib、既有 Memory Hub catalog loader、pytest、Streamlit。

## Global Constraints

- 只讀 `agent_config/memory_hub_catalog_deployment.json`；不掃描 repository。
- Source root 固定 `docs/memory_hub_sources`，runtime root 固定 `.nbs_agent_runtime/memory-hub`。
- 不呼叫 `build_catalog()`，不寫 catalog、SQLite、baseline、runtime、Git 或外部服務。
- 缺失 manifest／catalog 回傳 `None`；tamper、unknown key、symlink、traversal、hash mismatch 回傳 invalid。
- Memory Hub 仍是 non-authoritative projection；recall、writer、approval、dispatch defaults 不變。

---

### Task 1: Deployment provider composition

**Files:**
- Create: `backend/agents/memory_hub_deployment_provider.py`
- Modify: `app_pages.py`
- Test: `tests/test_memory_hub_deployment_provider.py`, `tests/test_app_pages_memory_hub.py`

**Interfaces:**
- `deployment_owned_catalog_provider(project_root: Path) -> CatalogProvider`
- Provider returns `MemoryCatalog | None`; it reads only the fixed manifest and catalog paths.

- [x] **Step 1: Write failing tests**

Test missing manifest returns `None`, valid fixture loads, tampered manifest/catalog raises a bounded catalog error, and app passes a callable provider instead of a builder or path scanner.

- [x] **Step 2: Run RED**

```bash
.venv/bin/python -m pytest tests/test_memory_hub_deployment_provider.py tests/test_app_pages_memory_hub.py -q
```

Expected: missing provider module import or callable assertion failure.

- [x] **Step 3: Implement minimal provider**

Validate exact manifest keys and canonical fingerprint; parse `MemorySource`/`MemoryRecord`; construct `CatalogBuildPolicy`; call only `load_catalog()` under fixed roots. Return `None` only for missing manifest/catalog; map malformed input through `MemoryHubCatalogError`.

- [x] **Step 4: GREEN verification**

```bash
.venv/bin/python -m pytest tests/test_memory_hub_deployment_provider.py tests/test_app_pages_memory_hub.py tests/test_memory_hub_ui_service.py -q
.venv/bin/python -m py_compile backend/agents/memory_hub_deployment_provider.py app_pages.py
git diff --check
```

- [x] **Step 5: Strict Review and commit**

Review must verify no builder call, no arbitrary path input, no writes, and exact C-0/C-1 loader delegation.

### Task 2: Full acceptance

**Files:**
- Modify only plan status after verification.
- Runtime evidence only under `.nbs_agent_runtime/`.

- [x] **Step 1:** Run focused Memory Hub, Streamlit, Graph and Agent Operations suites.
- [x] **Step 2:** Run full pytest, system acceptance and Hermes read-only check.
- [x] **Step 3:** Confirm missing deployment artifact still renders `catalog_missing` and no default recall/write policy changed.
