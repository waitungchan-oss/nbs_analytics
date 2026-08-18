# NBS Context Agent × Memory Hub Integration Design Spec

**Status:** approved direction, implementation pending
**Date:** 2026-08-17
**Scope:** read-only Context Agent enrichment using the existing C2 Memory Hub

## 1. Goal

讓 Context Agent 在每次 `collect-only` 時，自動以受控 Runtime Identity 查詢
Memory Hub，取得已批准的 governance、verified evidence、approved skill 提示，
同時保留 EvidenceCollector 作為 canonical context evidence 的唯一真相來源。

## 2. Non-goals and boundaries

- 不修改 canonical artifacts、正式 SQLite、baseline、Monthly Baseline Governance、revenue scope 或 export schema。
- 不新增 approval、dispatch、runtime control、catalog mutation 或 Memory Hub writer。
- 不讓 Memory Hub 內容覆蓋、重寫或重新 fingerprint canonical collector evidence。
- 不把 Memory Hub 變成 Context Agent 的必要依賴；provider unavailable 時仍輸出原本 context。
- 不自動啟用新的 candidate memory、Wiki、CodeGraph 或外部 memory service。
- 不把 credentials、SQLite、CSV、Excel、logs 或 secrets 當作 Memory Hub source。

## 3. Current state

Context Agent 目前由 `EvidenceCollector` 建立 `context-evidence-v1`，並可附加
既有 `memoryHints` envelope，但尚未實際呼叫 C2 `MemoryHubService`。C2 已提供兩個
immutable catalogs 與一個 shared read-only policy decision service，並採 deny／blocked
default。

## 4. Proposed architecture

```text
Brief
  │
  ├─ EvidenceCollector (canonical evidence)
  │
  └─ Context Memory Enrichment Adapter
        │ RuntimeIdentity + bounded MemoryQuery
        ▼
      MemoryHubService.query (read-only policy gate)
        │
        ├─ ready  → non-authoritative memoryHints
        ├─ empty  → ignored/empty diagnostic
        ├─ blocked → blocked diagnostic, no hints
        └─ timeout/degraded → fallback, no hints
```

### 4.1 Query contract

每次 `collect-only` 以 deployment-owned identity 建立 bounded query：

- `project_id`: `nbs_analytics`
- `consumer_id`: `context-agent`
- `scope`: 由受控 descriptor 固定為 `project` 或明確允許的 `team`
- `memory_kinds`: 僅 `governance`、`evidence`、`skill`
- `max_items`／`max_bytes`／`timeout_ms`: 使用 Context Agent 的 bounded limits
- query fingerprint: 由既有 `MemoryQuery.from_parts` 產生

不得由 caller 任意傳入 catalog path、policy service、provider、ACL override 或
identity 欄位。無法取得 deployment-owned composition 時，視為 blocked／fallback。

### 4.2 Evidence and fingerprint boundary

`build_context_evidence_payload` 的 canonical unsigned payload 維持不變。
`bundleFingerprint` 只對 collector evidence、documents、symbols、tests、recent changes
計算；`memoryHints` 在 fingerprint 之後以 `authority: non_authoritative_memory` 附加。

因此同一份 collector evidence 在 Memory Hub ready、empty 或 unavailable 時，
其 `bundleFingerprint` 必須相同。

### 4.3 Hint projection

只有 Memory Hub query result 為 `ready` 且每筆 record 與 source 均 fresh／verified、
policy decision 為 allow 時，才可投影為現有 `MemoryHints` schema。

投影內容只包含 bounded summary、memory kind、memory id、source references、
query fingerprint 與 provenance metadata；不得讀取或內嵌禁止的 artifact bytes。

若 record／source stale、unknown、blocked、invalid 或 fingerprint 不匹配，整批 hints
fail closed，不混入部分未可信內容。

## 5. Failure and fallback behavior

| Condition | Context output | Reason |
|---|---|---|
| Memory Hub ready + policy allow | canonical evidence + `memoryHints.status=ready` | enriched |
| no matching record | canonical evidence unchanged | `empty` |
| policy deny／blocked identity | canonical evidence unchanged | `blocked` |
| catalog／policy service unavailable | canonical evidence unchanged | `provider_unavailable` |
| timeout／degraded | canonical evidence unchanged | bounded fallback |
| stale／unknown／invalid hint | canonical evidence unchanged | `invalid_or_stale` |
| malformed projection | canonical evidence unchanged | `invalid` |

Fallback 不得令 `collect-only` 變成 PASS-by-fabrication；report 必須保留可驗證的
memory status／reason，但不得宣稱已使用有效 memory。

## 6. Task boundaries and interfaces

### Context memory enrichment adapter

新增小型 read-only adapter（建議位置：`backend/agents/context_memory_hub_adapter.py`）：

```python
def query_context_memory(
    *, project_root: Path, identity: RuntimeIdentity, query: MemoryQuery
) -> dict[str, object]:
    """Return a bounded memoryHints-compatible projection or a fail-closed diagnostic."""
```

Adapter 只能依賴 deployment-owned catalog provider 與 `MemoryHubService`，不得自行
載入任意 JSON、改變 catalog 或呼叫外部 network。

### Context Agent integration

`scripts/context_agent.py` 在 `collect_context` 完成後呼叫 adapter，將結果傳給
`build_context_evidence_payload(..., memory_hints=...)`；原本無 Memory Hub 的呼叫方式
保持相容。

## 7. Tests and acceptance

必須新增／補充：

- ready／allow 會產生 bounded `memoryHints`。
- blocked、empty、timeout、degraded 都保留 canonical context。
- stale／unknown／invalid record 不得進入 hints。
- canonical `bundleFingerprint` 在有無 hints 時相同。
- identity、query、source provenance fingerprint mismatch fail closed。
- adapter 不寫 SQLite、baseline、catalog、runtime source 或 Git。
- 既有 Context Agent focused tests、C2 Memory Hub tests、full pytest 維持通過。

驗證順序：affected `py_compile` → focused pytest → findings-first Review → full pytest
→ `scripts/system_manager.py acceptance` → Hermes profile acceptance。

## 8. Rollout and rollback

第一階段只在 Context Agent 的 `collect-only` enrichment path 啟用；正常 collector
失敗行為不變。移除 adapter wiring 即可 rollback，不需要 migration、SQLite rollback
或 catalog regeneration。任何 missing composition／policy failure 都維持 canonical-only
fallback。
