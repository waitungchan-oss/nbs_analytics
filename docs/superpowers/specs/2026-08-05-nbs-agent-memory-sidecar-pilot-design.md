# NBS Agent Memory Sidecar Pilot Design Spec

狀態：draft for review  
日期：2026-08-05  
適用範圍：NBS Analytics Agent Pipeline、Context Agent 與已完成 run 的 bounded memory reuse

## 1. 摘要

本 pilot 評估 TencentDB-Agent-Memory 作為獨立、非權威的 Agent Memory Sidecar，目的在減少跨對話重複探索、長任務上下文注入與已驗證工程經驗的重建成本。

Pilot 只提供兩項能力：

1. 在 Context Agent 規劃前，以受限 recall 提供歷史記憶候選提示。
2. 在一個 run 完成必要 gate 後，將已驗證、已去敏、可追溯的工程摘要寫入 sidecar。

Memory Sidecar 不是 canonical artifact、Governance Graph、Review Agent、Hermes、Documentation Controller、SQLite 或 Git 的替代品，也不是 approval、dispatch、runtime、risk decision 或 snapshot 建立入口。

## 2. 現況與問題

NBS 已有 Evidence Collector、Context Agent、Review Agent、Implementation Agent、Documentation Agent、Hermes 與 read-only Governance Graph。canonical artifacts 位於 `.nbs_agent_runtime/runs/<run-id>/`，Graph 是可重建的衍生 projection。

現有流程已透過 compact Evidence Bundle、SHA-256 fingerprint、token cap 與 cache reuse 控制輸入成本，但以下成本仍可能反覆出現：

- 新對話重複閱讀 system map、ADR、handoff 與 agent contracts。
- 相似 task 重複搜尋相同模組、測試與歷史 failure pattern。
- 長時間 inline execution 的 tool output、測試 log 與決策背景在每輪重新注入。
- 已完成 run 的 SOP、殘餘風險與驗證命令未形成可查詢的 bounded memory。

## 3. 目標

- 量化 memory recall 對 Context token、探索時間與返工的實際改善。
- 讓歷史工程經驗可跨對話檢索，同時保留來源、fingerprint、freshness 與 drill-down reference。
- 在 sidecar 不可用、timeout、stale 或內容衝突時，保持 NBS 依 canonical evidence 正常運作。
- 以獨立 schema、allowlist 和 telemetry 控制導入範圍，令 pilot 可 rollback、可替換 provider、可審查。
- 為未來 short-term offload、Skill memory 或 CodeGraph 補充建立明確擴展邊界。

## 4. 非目標與禁止事項

本 pilot 不會：

- 修改正式 SQLite、baseline、revenue scope、business rules 或 export schema。
- 修改 Governance Graph schema、建立新的 Graph node/edge，或由 memory 推測治理關係。
- 讓 memory 內容成為 Review、Verification、Hermes 或 final acceptance evidence。
- 讓 sidecar approve、dispatch、repair、apply、prune、delete、commit、merge、push 或建立 snapshot。
- 自動捕捉所有對話、原始營銷資料、SQLite rows、完整 logs、secrets、prompt 或內部推理。
- 直接接管 NBS Hermes。TencentDB-Agent-Memory 的 Hermes integration 是另一個 Hermes Agent provider，與 NBS 的 read-only Hermes 驗收角色不同。
- 在本 pilot 同時導入完整 Memory Hub、Wiki、CodeGraph、remote embedding 或外部多租戶管理面。

## 5. 方案比較

### 5.1 方案 A：Bounded Memory Sidecar（採用）

以獨立 HTTP Gateway／Python SDK adapter 提供 recall 和 distillation。Context Agent 只接收 bounded `memory-hints-v1`；完成 run 後才寫入 sanitized memory。

優點是最小整合面、可獨立停用、容易做 A/B，且不觸碰現有治理 authority。缺點是第一階段只改善 planning/context，不會立即壓縮所有工具 log。

### 5.2 方案 B：加上 Short-term Offload

將長工具輸出外置為 refs，僅在上下文保留 Mermaid task canvas 與 `node_id`，需要細節時再 drill down。

它可能對長 Inline Execution 有更大 Token 收益，但需要新增 runner hook、恢復策略與更嚴格的資料清理測試，應在方案 A 的 A/B 結果通過後再規劃。

### 5.3 方案 C：完整 Memory Hub／Wiki／CodeGraph

導入團隊資產、Skill、Wiki、CodeGraph、ACL 與管理面。這可補充程式 symbol/call graph，但會引入 Node.js Gateway、額外資料庫、版本 migration、管理 plane 與治理重疊，超出 pilot 的風險和 scope。

## 6. 採用架構

```text
Task objective
   │
   ├──> Context Agent / Evidence Collector ──> canonical context summary
   │
   └──> Memory Adapter ──> bounded memory-hints-v1
                              │
                              ▼
                    Context Agent receives labeled hints

Completed run evidence ──> Sanitizer + fingerprint validator
                               │
                               ▼
                         Memory Sidecar store
```

### 6.1 元件責任

| 元件 | 責任 | 不負責 |
|---|---|---|
| `MemoryAdapter` | timeout、auth、request/response schema、bounded recall、provider abstraction | 判定正式 evidence、修改 canonical artifacts |
| `MemorySanitizer` | 從 completed run 產生 allowlisted memory candidate、去除敏感內容、保留 source refs | 重新解釋 baseline、風險或業務數字 |
| `MemoryCandidate` | 保存摘要、來源 run、commit、fingerprint、freshness、confidence | 取代 Graph node、Review 或 Hermes artifact |
| `Context Agent` | 將 memory hints 標示為非權威背景，與 canonical bundle 分開 | 把 hint 當成 verification evidence |
| `Governance Graph` | 繼續只投影 canonical artifacts | 讀取、批准或自動建立 memory 關係 |
| `Hermes` | 驗證 sidecar integration 的 bounded status、permission、freshness 與 failure fallback | 執行 memory extraction 或寫入 sidecar |

### 6.2 Provider boundary

第一個 adapter 以 TencentDB-Agent-Memory v2 的 HTTP Gateway／Python SDK 為目標，但必須透過 NBS 自己的 `MemoryAdapter` interface 使用。版本、endpoint、API key、data directory 和 extraction provider 都由受控設定提供，不使用 floating `latest`。

測試必須先使用 local fake adapter；未通過 contract、security 與 A/B gate 前，不要求開啟真實 LLM extraction 或 remote embedding。

## 7. Canonical memory data contract

### 7.1 `memory-candidate-v1`

每一筆可寫入 sidecar 的候選記憶至少包含：

```json
{
  "schemaVersion": "memory-candidate-v1",
  "memoryId": "sha256-derived-id",
  "kind": "decision|sop|failure_pattern|verification_pattern|preference",
  "summary": "bounded sanitized summary",
  "sourceRefs": [
    {
      "runId": "run-...",
      "artifactPath": ".nbs_agent_runtime/runs/run-.../verification.json",
      "artifactSha256": "lowercase-sha256",
      "commit": "40-hex-or-null"
    }
  ],
  "sourceStatus": "completed",
  "freshness": {
    "generatedAt": "2026-08-05T00:00:00Z",
    "expiresAt": "2026-11-05T00:00:00Z",
    "policyVersion": "memory-freshness-v1"
  },
  "confidence": "high|medium",
  "memoryFingerprint": "lowercase-sha256"
}
```

規則：

- `sourceRefs` 必須指向已存在、位於允許 run root 內的 regular file；禁止 absolute path、symlink、path traversal、raw rows 和 secret。
- `summary` 使用 bounded UTF-8 byte cap；不得包含完整 log、完整 diff、prompt 或內部推理。
- 只有具備必要 documentation/no-doc outcome 的 `completed` run、Review PASS、full verification PASS 和 Hermes PASS 可產生寫入候選。
- 記憶 fingerprint 必須涵蓋 schema、summary、source identity、source hashes、provider policy 和 freshness policy。
- source artifact 改變、run identity 不符、超過 TTL、schema 不符或 ref 不可讀時，候選標記 `stale`／`invalid`，不得注入 Context。

### 7.2 `memory-hints-v1`

Recall 回傳給 Context Agent 的資料必須與 canonical evidence 分離：

```json
{
  "schemaVersion": "memory-hints-v1",
  "queryFingerprint": "lowercase-sha256",
  "status": "ready|empty|timeout|degraded",
  "hints": [
    {
      "memoryId": "sha256-derived-id",
      "summary": "bounded hint",
      "sourceRefs": ["run-.../verification.json"],
      "freshness": "fresh|stale|unknown",
      "confidence": "high|medium"
    }
  ],
  "limits": {
    "maxItems": 3,
    "maxBytes": 6000,
    "timeoutMs": 800
  },
  "hintsFingerprint": "lowercase-sha256"
}
```

Context Agent 必須將其標示為 `non_authoritative_memory`。`timeout`、`degraded`、`empty`、fingerprint mismatch 或任何 schema error 均等價於沒有 memory hints，不能阻塞 canonical collection。

## 8. 允許與禁止的資料來源

### 8.1 允許寫入 memory

- 已完成 run 的 task objective、決策摘要與 bounded rationale。
- 已通過的驗證命令 ID、測試 pattern 和一般化 failure pattern。
- changed surface 的檔案路徑與 artifact identity，不包含完整 patch。
- 已批准的協作偏好與 reusable SOP。
- run ID、Git commit、artifact SHA-256、schema version、freshness 與 confidence。

### 8.2 必須拒絕

- SQLite、Excel、CSV、原始營銷資料 rows、客戶資料與付款資料。
- API key、token、cookie、credential、private key、`.env` 和 auth home。
- 完整 prompt、完整 tool log、內部推理、完整 diff、未裁剪 export。
- 未完成、被阻塞、stale、protected incident 或未通過 Hermes 的 run。
- 正式 baseline 數值、正式 revenue scope 或任何 business-rule interpretation。

## 9. Data flow 與 authority

1. Codex 提供 task objective、repository HEAD 和 allowlisted query。
2. `MemoryAdapter` 以 bounded query 請求 sidecar；最多 3 筆、最多 6,000 bytes、800 ms timeout。
3. Evidence Collector 仍獨立收集 canonical brief、spec、plan、diff、tests、contracts 和 current artifacts。
4. Context Agent 只可把 memory hints 當作探索提示；所有正式判斷必須重新由 canonical evidence 驗證。
5. completed run 通過所有 gate 後，`MemorySanitizer` 產生 candidate，交由受控 writer 寫入 sidecar。
6. Governance Graph 不讀取 sidecar 來生成 node/edge；Graph 仍只由 canonical projection build 產生。
7. Hermes 只讀取 sidecar integration status、bounded telemetry、schema/freshness/fallback evidence。

## 10. Security、privacy 與運行邊界

- Sidecar 預設只綁 `127.0.0.1`，即使 loopback 也使用 API key；非 loopback 綁定必須拒絕，除非已配置 Bearer auth、明確 CORS allowlist 和 isolation headers。
- sidecar data directory 與 NBS formal SQLite、`.nbs_agent_runtime/`、Obsidian vault 分離。
- 不允許 memory provider 取得 Git write、SQLite write、baseline write、workflow command、runner command 或 shell execution 能力。
- 所有 request、response、sourceRefs、query 和 memory summaries 需要 schema validation、size cap、fingerprint validation 和 redaction。
- 使用者可用 feature flag 停用 recall 或 writer；停用後 workflow 行為與目前 canonical pipeline 相同。
- retention 只適用 sidecar memory 的明確 TTL；不得以 memory retention policy 刪除 NBS canonical artifacts、runtime evidence、backup 或 quarantine。

## 11. Freshness、衝突與失敗政策

| 情況 | sidecar 狀態 | NBS 行為 |
|---|---|---|
| provider 可用、fingerprint 正確 | `ready` | 提供 bounded non-authoritative hints |
| 沒有匹配記憶 | `empty` | 只使用 canonical collector |
| timeout／Gateway down | `timeout`／`degraded` | 立即跳過，記錄 telemetry，不重試阻塞 workflow |
| source artifact 改變 | `stale` | 不注入，要求重新由 canonical evidence 驗證 |
| schema／fingerprint／path error | `invalid` | fail closed，保留診斷證據 |
| hint 與 canonical evidence 衝突 | `conflict` | canonical evidence wins，hint 不得用於正式判定 |
| sanitizer 發現敏感資料 | `blocked_sensitive` | 不寫入，保留 bounded finding，不暴露原文 |

## 12. Token 與品質 A/B 驗證

以 10 個相似風險、非 R2 的工程 task 建立 recall off/on 對照，每個 task 使用相同 brief、HEAD、allowed files 和驗證命令。

### 12.1 必量化指標

- Context／主對話 estimated input tokens。
- Evidence Bundle token 壓縮率與 cache hit rate。
- 從 task 開始到可執行 plan 的時間。
- 重複探索命令數量。
- Review findings 數量、漏測數量與 Hermes 結果。
- memory recall latency、timeout rate、empty rate、stale/conflict rate。
- memory capture 的敏感資料拒絕數量。

### 12.2 Pilot acceptance gates

- Recall on 的 Context／主對話輸入至少下降 20%，或提出有證據的替代收益。
- canonical evidence coverage 維持 100%。
- Review findings、targeted test coverage 和 Hermes PASS 不得因 memory 下降。
- stale/conflict hint 不得進入正式 decision/evidence path。
- sidecar timeout 或關閉時，canonical pipeline 成功率與目前 baseline 相同。
- 每次 recall 不超過 3 筆與 6,000 bytes；p95 latency 不超過 800 ms。
- 受禁止資料 capture 為 0；所有 memory candidate 都能 drill down 到 sourceRefs。
- 不新增 Graph authority、approval、dispatch、SQLite 或 baseline 寫入。

若 Token 下降但 evidence coverage、Review 或 Hermes 任何一項退化，pilot 判定失敗，不進入正式 rollout。

## 13. 測試與驗證範圍

Implementation plan 必須至少包含：

- `MemoryCandidate`、`MemoryHints`、fingerprint 和 sourceRef schema tests。
- allowlist、redaction、byte cap、path traversal、symlink、secret detection tests。
- fake adapter 的 ready／empty／timeout／degraded／stale／conflict 行為 tests。
- Context Agent integration tests，證明 memory hints 與 canonical evidence 分離。
- 完成 gate 前不得寫入 candidate 的 negative tests。
- provider 不可用時的 fallback regression tests。
- telemetry schema、A/B aggregation 和 bounded output tests。
- targeted pytest、py_compile、system acceptance 與 Hermes post-change check。

真實 TencentDB Gateway、Node.js runtime、LLM extraction、remote embedding 和 migration 測試需放在獨立 integration task，不可混入第一個 contract task。

## 14. Rollout、rollback 與完成語義

### 14.1 Rollout stages

1. Contract-only：只建立 schema、fake adapter 和 sanitizer tests。
2. Shadow recall：收集 query/latency/命中率，不注入 Context。
3. Bounded injection：對 allowlisted、非 R2 task 注入最多 3 筆 hints。
4. Post-run distillation：只對 completed run 寫入 sidecar。
5. A/B acceptance：依第 12 節 gate 判定是否繼續。

### 14.2 Rollback

任何 stage 都可透過 feature flag 關閉 recall、writer 或整個 sidecar；關閉後不需刪除 canonical artifacts，也不需改回 Graph snapshot。sidecar memory 可保留作診斷，但不得被 workflow 使用。

### 14.3 Completion semantics

Pilot `completed` 必須同時具備：schema tests PASS、security tests PASS、fallback tests PASS、A/B evidence、Review PASS、full verification PASS、Hermes PASS，以及沒有 canonical boundary regression。單純 Gateway 啟動或 Token 下降不代表 pilot 完成。

## 15. Implementation boundary

第一份 Implementation Plan 只能涵蓋：

- memory contract models / validators。
- provider-neutral adapter interface 與 fake adapter。
- sanitizer、sourceRefs、fingerprint、freshness 和 bounded hints。
- Context Agent 的 read-only、non-authoritative integration。
- telemetry 與 A/B fixture。

下列項目必須另立 task 或後續 spec：

- 真實 TencentDB Gateway 安裝、Node.js runtime 和版本 migration。
- Short-term Mermaid offload。
- Memory Hub、Wiki、CodeGraph 或 Skill memory。
- Streamlit memory UI、FastAPI endpoint 或外部通知。
- 任何 Governance Graph schema、canonical mapping、approval 或 workflow control 變更。

## 16. 設計決策摘要

- 採用獨立 sidecar，不將 memory 混入 canonical artifacts。
- 採用 provider-neutral adapter，避免 NBS 綁定單一供應商。
- recall 只給 bounded non-authoritative hints；canonical evidence 永遠優先。
- completed run 才能 distill；未完成或 protected run fail closed。
- 先 fake／shadow／A-B，再考慮真實 Gateway。
- 不在本 pilot 修改 Governance Graph、Hermes authority、正式 SQLite、baseline 或 business rules。
