# NBS Memory Hub Agent／Module Integration Design

狀態：ready_for_implementation_plan  
日期：2026-08-18  
範圍：Context Agent、Hermes Memory Sidecar、Short-term Offload、Agent Operations、Governance Graph、Review Agent、Implementation Agent、Documentation Agent

## 1. 目的

本設計把已完成的 Memory Hub、Team Catalog、Agent Policy Catalog、Context Agent adapter、Hermes Memory Sidecar 與 Short-term Offload 能力，整合為一套可追溯、fail-closed、只讀優先的 Agent 記憶供應鏈。

Memory Hub 不成為新的 canonical truth、approval、dispatch、Review verdict、Hermes acceptance、SQLite、baseline、Git 或文件 apply 入口。正式資料與決策仍以 canonical artifacts、實際 diff、測試、Hermes evidence 與 Git commit 為準。

## 2. 核心決策

採用「一個共用 integration evidence contract + 七種明確 integration mode」。各模組不得自行建立另一套 Memory Hub 查詢、權限或 evidence 語意。

| 模組 | integration mode | 權限與責任 |
|---|---|---|
| Context Agent | `direct_query` | 唯一正式 Memory Hub query 入口；輸出 bounded `memoryHints` |
| Hermes Memory Sidecar | `bounded_consumer` | 只消費 Context Agent 產生並驗證的 hints，不直接繞過 policy query |
| Short-term Offload | `evidence_comparator` | 比較 off/on token、latency、provenance，不管理 catalog 或 policy |
| Agent Operations | `observation_only` | 顯示 integration status、fingerprint、hint counts 與 diagnostics；不得 dispatch/write |
| Governance Graph | `derived_lineage` | 從實際 evidence 建立 read-only lineage；不得自行推測或刷新 runtime |
| Review / Implementation Agent | `gated_context` | 只接受已綁定 Task／run 的 supplementary hints；不得直接查詢 Memory Hub |
| Documentation Agent | `approved_evidence_only` | 只消費通過 Review、full verification、Hermes 的 documentation evidence；不得 live query |

## 3. 共用 evidence contract

新增 `memory-hub-agent-integration-v1` immutable envelope。它是 observation evidence，不是 authority。

必要欄位：

- `schemaVersion`
- `projectId`
- `consumerId`
- `integrationMode`
- `status`: `ready | empty | blocked | degraded | ignored`
- `reason`
- `queryFingerprint`（無 query 的 observation mode 可為 `null`）
- `hintsFingerprint`（未返回 hints 時為 `null`）
- `policyDecisionFingerprints`
- `sourceRefs`：只允許 project/runtime-relative references
- `hintCount`
- `generatedAt`
- `evidenceFingerprint`

禁止欄位：raw memory、完整 prompt、API key、credential、SQLite rows、absolute path、未裁剪 log、approval/dispatch command。

所有 consumer 只可接受：

- `authority=non_authoritative_memory`
- fresh、fingerprint-valid、policy-allowed evidence
- bounded limits：`maxItems=3`、`maxBytes=6000`、`timeoutMs=800`
- 與目前 project／consumer／Task／run identity 相符的 evidence

## 4. 資料流

```text
deployment manifest + source documents
    -> provisioned Memory / Team / Agent Policy Catalogs
    -> Context Agent direct query
    -> memoryHints-v1
    -> Hermes Sidecar bounded consumption
    -> Short-term Offload off/on evidence
    -> memory-hub-agent-integration-v1 observation
       -> Agent Operations display
       -> Governance Graph derived lineage
       -> Review/Implementation gated supplementary context
       -> Documentation approved evidence only
```

Context Agent query 失敗時必須返回 canonical-only bundle。後續模組不得自行補查 Memory Hub，也不得把缺失 hints 當作失敗的 canonical evidence。

## 5. 模組邊界

### 5.1 Agent Operations

`AgentOperationsService.build_snapshot()` 可加入 `memoryHubIntegration` read model，內容僅包括 catalog readiness、consumer mode/status、hint count、fingerprints、latest evidence reference 與 bounded diagnostics。

UI 不提供 provision、refresh catalog、activate sidecar、offload switch、approval、dispatch、prune 或任何 runtime write。

### 5.2 Governance Graph

Graph integration 只在實際 artifact 存在時投影以下 evidence lineage：

- Memory Catalog Evidence
- Policy Decision Evidence
- Context Memory Hints
- Sidecar Consumption Evidence
- Short-term Offload Comparison Evidence

目前 `GovernanceGraphSnapshot v1` 沒有 `edges` 欄位；本階段不修改既有 snapshot schema。上述 lineage 以獨立 `memory-hub-lineage-v1` read model 提供給 Graph query／UI，允許關係為 `derived_from`、`produces`、`verifies`、`documented_by`。每條 link 必須保留 evidence reference。缺失、stale、mismatch 應顯示 `missing`、`unknown` 或 `blocked`，不得推測關聯。

Graph snapshot 與 lineage read model 都是衍生、只讀 projection；不得因 Memory Hub unavailable 而改變 canonical workflow node 的正式 status。

### 5.3 Review Agent

Review evidence 可以包含一個 optional `memoryHubContext` observation envelope，但 Review Agent：

- 不直接 query Memory Hub。
- 不因 hints 存在而降低 diff、test 或 requirement coverage。
- 不因 hints 缺失而阻止 canonical review。
- 不可把 Memory Hub 內容當成 Review PASS evidence。

invalid/stale/mismatched hints 必須被標記 `ignored`，Review 繼續使用 canonical evidence。

### 5.4 Implementation Agent

Implementation Agent 只可在已批准單一 Task contract 中接收由 Context Agent 提供的 bounded hints。Task contract 必須顯式包含 `memoryContextAllowed=true` 與 expected evidence fingerprint；未提供時固定不注入。

Hints 不可擴大 allowed files、commands、network、risk surface 或 Task scope，也不可觸發下一 Task、commit、merge、SQLite、baseline、business rules 或 export schema 寫入。

### 5.5 Documentation Agent

Documentation Agent 不接 live Memory Hub。只有當同一 completed run 已通過 Review、full verification、Hermes 後，`documentation-evidence-v1` 才可帶入 bounded integration summary，供 proposal 說明「哪些 memory evidence 被使用」。

它仍只輸出 `documentation-proposal-v1`，不得 auto-apply、批准 target 或回填 canonical docs。

## 6. Failure handling

| 情況 | 行為 |
|---|---|
| deployment catalog 缺失 | `blocked/provider_unavailable`；Context canonical-only fallback |
| Team/Agent Policy deny | `blocked/policy_deny`；不返回 hints |
| stale、tampered、identity mismatch | `ignored/invalid_or_stale`；不影響 canonical workflow |
| Sidecar activation unavailable | treatment blocked；control 保持 recall/offload off |
| Offload evidence 不完整 | `blocked_runner_capability` 或 `completion_missing`；不宣稱 reduction |
| Agent Operations/Graph evidence 缺失 | 顯示 `missing/unknown`；不建立虛構 node/edge |
| Review/Implementation optional hints 缺失 | canonical-only execution |
| Documentation gate 未通過 | 不建立 documentation memory summary |

所有 error message 必須 bounded，不得包含 credential、absolute runtime path 或 raw prompt。

## 7. Provisioning 與 lifecycle

`scripts/provision_memory_hub_catalog.py` 是唯一 deployment provisioning command。它只讀固定 tracked manifest/source，僅寫 `.nbs_agent_runtime/memory-hub/`，existing divergent artifact 必須 fail-closed。

本階段不把 provisioning 綁到一般 Terminal 啟動、Streamlit import、Context Agent query 或 application startup。部署程序必須顯式執行 provisioning，再以 `--check-only` 驗證。這避免 read-only consumer 偷偷建立 runtime state。

## 8. Security and governance

- canonical artifacts 永遠優先於 Memory Hub hints。
- Memory Hub 不提供 approval、dispatch、workflow control、snapshot refresh 或 Git write。
- 不新增 Node.js Gateway、外部 database、network recall、vector database 或 migration。
- 所有 runtime paths 固定在 project root 內，拒絕 symlink、traversal、absolute source reference 與 divergent immutable output。
- Context/Review Agent 維持 read-only；Implementation Agent 維持 one-approved-Task contract。
- Hermes 只做 bounded activation/evidence acceptance，不取代 Review。
- 正式口徑與 2026-05 baseline 不得由本功能修改。

## 9. Testing and acceptance

完成條件：

1. Fresh deployment 可用 provisioning command 建立三個 catalogs，重跑 idempotent，tampered existing output fail-closed。
2. Context Agent live query 返回 `ready`、3 筆 bounded hints、`non_authoritative_memory`。
3. Agent Operations 只讀顯示 integration observation，沒有 write/dispatch callbacks。
4. Governance Graph 對存在的 evidence 建立 deterministic nodes/edges；缺失 evidence 不推測。
5. Review Agent 在 hints ready/ignored/missing 三種情況都保持 canonical review requirements。
6. Implementation Agent 只有在 authorized contract + exact fingerprint 下接受 hints，且 scope 不可擴張。
7. Documentation Agent 只接受 gated documentation evidence，不做 live query。
8. Short-term Offload 不因整合而改變既有 off/on semantics 或虛報 token reduction。
9. Targeted tests、full pytest、system acceptance 與 Hermes post-change check 全部通過。

## 10. Non-goals

- 不將 Memory Hub default-on 到所有 Agent。
- 不建立 autonomous memory write/distillation。
- 不新增 approval、dispatch、risk decision 或 automatic Graph snapshot refresh。
- 不建立獨立 Memory Hub website、FastAPI write endpoint、Node.js Gateway、額外 SQLite 或 migration。
- 不把管理層 UI 或 Governance Graph 變成新的正式真相來源。

## 11. Rollout order

1. Shared integration evidence contract + provisioning hardening。
2. Agent Operations observation。
3. Governance Graph derived lineage。
4. Review Agent gated supplementary context。
5. Implementation Agent authorized Task-scoped context。
6. Documentation Agent approved evidence summary。
7. Full acceptance 與 documentation proposal。

每個 Task 都必須獨立 TDD、findings-first Review、targeted verification。除非另有明確連續執行授權，實作時每個 Task 完成後停下等待確認。
