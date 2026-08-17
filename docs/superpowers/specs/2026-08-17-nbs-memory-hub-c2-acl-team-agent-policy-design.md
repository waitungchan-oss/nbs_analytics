# NBS Memory Hub C2：ACL／Team／Agent Policy Design Spec

## Status

Proposed for review

## Date

2026-08-17

## Scope

Phase C2：在 C-0/C-1 Memory Contract 與 read-only Memory Hub Foundation 之上，加入 deployment-owned、immutable 的 Team／Agent policy boundary。

本階段只建立兩個 immutable catalog 與一個共用 read-only policy decision service：

1. `Team Catalog`
2. `Agent Policy Catalog`（包含 Agent identity、Team references、allowed memory kinds／scopes 與 policy rules）
3. `MemoryHubPolicyService`

不建立獨立登入系統、IAM、OAuth、Node.js Gateway、額外 SQLite、migration、dispatch controller 或 ACL mutation API。

## 1. Goal

讓更多 Agent 安全共用 C-0/C-1 Memory Hub，同時能明確回答：

> 這個 Agent 是否可以讀取這筆 Memory？

每次 decision 都必須可追溯到：

- deployment-owned Team Catalog fingerprint
- deployment-owned Agent Policy Catalog fingerprint
- project／agent／team identity
- memory record identity
- policy reason code

Memory Hub 仍是 non-authoritative、read-only projection；canonical artifacts、正式 context、Review、Verification 與 Hermes 仍是真相與治理 gate。

## 2. User value

C2 解決 C-0/C-1 只有基本 scope matching 的限制：

- Context Agent 只讀治理文件與 verified evidence。
- Review Agent 只讀 review 所需的 governance／evidence memory。
- Hermes 或 validation consumer 可限制為驗收 evidence。
- Team scope 不再只依賴 caller 提供的 `teamId`，而是必須對應 immutable Team Catalog。
- 沒有明確 allow rule 時，預設 deny。
- catalog、identity 或 record 無法驗證時，fail closed 為 blocked。

## 3. Non-goals and hard boundaries

- 不管理真實使用者、密碼、session、OAuth、IAM 或 API key。
- 不建立、刪除或修改 Team membership、Agent registration 或 policy rule 的 runtime API。
- 不由 Memory Hub 自動 dispatch Agent、建立 Task、批准 Task 或修改 workflow state。
- 不修改正式 SQLite、baseline、revenue scope、business rules 或 export schema。
- 不修改 Memory Sidecar／Short-term Offload default policy，不自動開啟 recall。
- 不把 decision 當成 approval、Review PASS、Verification PASS 或 Hermes PASS。
- 不建立 Graph node／edge、Graph snapshot 或 Graph write path。
- 不建立 Wiki ingestion、CodeGraph、Skill memory 或 candidate memory；C3 與 candidate layer 另立 spec。
- 不呼叫外部 network/provider；policy decision 必須是 deterministic local read-only evaluation。

## 4. Core architecture

```text
deployment-owned Team Catalog
              +
deployment-owned Agent Policy Catalog
              ↓
      MemoryHubPolicyService
              ↓
      allow / deny / blocked
              ↓
      existing MemoryHubService
```

### 4.1 Two-catalog rule

本階段只有兩個獨立 immutable catalog：

- `Team Catalog`：Team identity、project binding、治理角色與 Agent membership。
- `Agent Policy Catalog`：Agent identity、Agent class、Team references、allowed memory kinds／scopes 與 policy rules。

不另建第三個獨立 Policy Catalog。Policy rules 屬於 `Agent Policy Catalog` 的 canonical immutable payload。

兩個 catalog 必須：

- 由 deployment-owned provider 提供。
- 使用 exact schema。
- 以 canonical JSON 產生 deterministic fingerprint。
- 綁定 project identity。
- 只可 read/load，不能由 MemoryHub query、Streamlit、Agent Operations、Sidecar 或 Offload 重新生成。
- 缺失、stale、tampered、cross-project 或 symlink/path violation 時 fail closed。

### 4.2 Identity meaning

`teamId` 與 `agentId` 只代表治理角色／責任群組，不代表登入帳號或人員身份。

例子：

- `team-finance-governance`
- `agent-context-reader`
- `agent-review-reader`
- `agent-hermes-observer`

## 5. Canonical catalog contracts

### 5.1 `memory-team-catalog-v1`

Team Catalog 的 envelope：

```json
{
  "schemaVersion": "memory-team-catalog-v1",
  "projectId": "nbs_analytics",
  "teams": [
    {
      "teamId": "team-finance-governance",
      "role": "governance_reader",
      "agentIds": ["agent-context-reader", "agent-review-reader"],
      "allowedScopes": ["project", "team"],
      "recordFingerprint": "lowercase-64-hex"
    }
  ],
  "catalogFingerprint": "lowercase-64-hex"
}
```

Rules：

- exact keys only；unknown fields reject。
- `projectId` 必須與 deployment project identity 一致。
- `teamId`、`agentIds`、`role` 與 scope 使用既有 safe identifier rules。
- `agentIds` 必須唯一、排序 deterministic。
- Team 不得引用不存在的 Agent identity only when the corresponding Agent Policy Catalog is loaded；cross-catalog mismatch 為 `blocked`。
- `recordFingerprint` 由 canonical Team fields 重算。
- `catalogFingerprint` 由完整 catalog payload 重算。
- 不保存真實姓名、email、token、credential 或外部身份 provider claim。

### 5.2 `memory-agent-policy-catalog-v1`

Agent Policy Catalog 的 envelope：

```json
{
  "schemaVersion": "memory-agent-policy-catalog-v1",
  "projectId": "nbs_analytics",
  "agents": [
    {
      "agentId": "agent-context-reader",
      "agentClass": "context",
      "teamIds": ["team-finance-governance"],
      "allowedMemoryKinds": ["governance", "evidence"],
      "allowedScopes": ["project", "team"],
      "rules": [
        {
          "memoryKinds": ["governance", "evidence"],
          "scopes": ["project", "team"],
          "decision": "allow",
          "ruleFingerprint": "lowercase-64-hex"
        }
      ],
      "recordFingerprint": "lowercase-64-hex"
    }
  ],
  "defaultDecision": "deny",
  "catalogFingerprint": "lowercase-64-hex"
}
```

Rules：

- exact keys only；unknown fields reject。
- `defaultDecision` 固定為 `deny`，不可配置成 allow。
- Agent identity、Team references、memory kinds、scopes 與 rules 必須 deterministic sorted。
- rule 只允許 `allow` 或 `deny`；不允許 rule 直接產生 `blocked`。
- `blocked` 只表示 identity、catalog、record 或安全驗證失敗。
- Agent Policy Catalog 不得引用不存在的 Team；cross-catalog mismatch 為 `blocked`。
- Agent Policy Catalog 不得修改 C-0/C-1 `MemoryRecord`、source status 或 freshness。

## 6. Decision input and output

### 6.1 Input

沿用 C-0/C-1 的 `RuntimeIdentity`、`MemoryQuery` 與 `MemoryRecord`，不新增登入或 credential fields：

```text
evaluate(identity: RuntimeIdentity,
         query: MemoryQuery,
         record: MemoryRecord) -> MemoryPolicyDecision
```

### 6.2 Output: `memory-policy-decision-v1`

```json
{
  "schemaVersion": "memory-policy-decision-v1",
  "projectId": "nbs_analytics",
  "agentId": "agent-context-reader",
  "teamId": "team-finance-governance",
  "memoryId": "lowercase-64-hex",
  "requestedScope": "team",
  "recordScope": "team",
  "decision": "allow",
  "reason": "same_team",
  "teamCatalogFingerprint": "lowercase-64-hex",
  "agentPolicyCatalogFingerprint": "lowercase-64-hex",
  "recordReturned": true,
  "decisionFingerprint": "lowercase-64-hex"
}
```

Allowed decisions：`allow`、`deny`、`blocked`。

Allowed reason codes：

- allow：`same_project`、`same_agent`、`same_team`、`policy_allow`
- deny：`policy_deny`、`scope_mismatch`、`memory_kind_not_allowed`、`agent_not_in_team`
- blocked：`missing_identity`、`invalid_identity`、`team_catalog_missing`、`agent_policy_catalog_missing`、`catalog_fingerprint_mismatch`、`cross_project_catalog`、`unknown_agent`、`unknown_team`、`record_invalid`、`record_stale`、`source_blocked`

Decision fingerprint 必須由完整 canonical decision fields 重算，不接受 caller supplied fingerprint。

## 7. Decision flow

`MemoryHubPolicyService` 必須依以下順序執行，不可跳步或猜測缺失資料：

1. 驗證 `RuntimeIdentity` 的格式與 project binding。
2. 以 deployment-owned provider 載入 Team Catalog 與 Agent Policy Catalog。
3. 驗證兩個 catalog 的 exact schema、path safety、fingerprint 與 freshness。
4. 驗證兩個 catalog 的 `projectId` 與 identity project 一致。
5. 由 Agent Policy Catalog 找到 `agentId`。
6. 驗證 Agent 引用的 Team 存在於 Team Catalog。
7. 驗證 Team membership 與 identity `teamId` 一致；caller 不得自行擴大 membership。
8. 檢查 query memory kind 是否在 Agent allowlist。
9. 檢查 query scope 是否在 Agent allowed scopes。
10. 先套用明確 policy rule；沒有 matching allow rule 時使用 fixed `defaultDecision=deny`。
11. 只有 policy allow 後，才執行 C-0/C-1 record scope、freshness、source status 與 fingerprint checks。
12. 產生 deterministic decision；`deny` 或 `blocked` 時不得回傳 record summary 或 source metadata。

Policy decision 不呼叫外部 provider，不寫入 runtime，不改變 catalog，不建立 Graph snapshot。

## 8. Fail-closed matrix

### 8.1 Identity and catalog failures

| Condition | Result | Data returned |
|---|---|---|
| project／agent identity missing | `blocked` | no record |
| malformed identity | `blocked` | no record |
| Team Catalog missing | `blocked` | no record |
| Agent Policy Catalog missing | `blocked` | no record |
| catalog schema invalid | `blocked` | no record |
| catalog fingerprint mismatch | `blocked` | no record |
| catalog project mismatch | `blocked` | no record |
| symlink、path traversal 或 cross-root catalog | `blocked` | no record |
| unknown agent or team | `blocked` | no record |
| cross-catalog membership mismatch | `blocked` | no record |

### 8.2 Explicit policy denials

| Condition | Result | Data returned |
|---|---|---|
| no matching allow rule | `deny` | no record |
| explicit deny rule | `deny` | no record |
| agent not in requested team | `deny` | no record |
| memory kind not allowed | `deny` | no record |
| requested scope not allowed | `deny` | no record |
| record scope mismatch | `deny` | no record |

### 8.3 Record and source failures

| Condition | Result | Data returned |
|---|---|---|
| record stale／unknown | `blocked` | no record |
| source expired | `blocked` | no record |
| source fingerprint mismatch | `blocked` | no record |
| source status blocked | `blocked` | no record |
| source outside allowlist | `blocked` | no record |

`deny` 與 `blocked` 的語意固定：

- `deny`：規則明確表示不可讀。
- `blocked`：系統無法證明這次讀取安全，因此 fail closed。

## 9. Integration with C-0/C-1

### 9.1 MemoryHubService boundary

`MemoryHubService.query()` 仍是 read-only query facade。C2 以 policy service 作為 query 前置 gate：

```text
RuntimeIdentity + MemoryQuery
        ↓
MemoryHubPolicyService
        ↓ allow only
MemoryHubService.query()
        ↓
MemoryQueryResult
```

若 policy result 是 `deny` 或 `blocked`，`MemoryHubService` 不得回傳任何 MemoryRecord、summary 或 source metadata。

### 9.2 Existing consumers

- Memory Sidecar：policy denied／blocked 時 fallback 到 canonical context，維持既有 recall policy。
- Short-term Offload：policy denied／blocked 時不採用 memory hint，不改變 offload gate。
- Streamlit Memory Hub：只顯示 bounded decision reason、catalog status 與 counts；不提供 catalog 或 policy mutation controls。
- Agent Operations：只讀顯示 policy decision evidence，不成為 dispatch／approval 入口。

## 10. Error and fallback behavior

| Situation | Hub result | Consumer behavior |
|---|---|---|
| valid catalogs, matching identity and policy | `allow` | bounded Memory query |
| explicit policy mismatch | `deny` | use canonical context |
| missing／tampered identity or catalog | `blocked` | use canonical context and bounded diagnostic |
| stale／invalid record | `blocked` | exclude record; preserve canonical context |
| local provider unavailable | `blocked` | do not rebuild; preserve canonical context |
| query timeout／service unavailable | `timeout`／`degraded` at C-0 query layer | do not block normal workflow |

任何 C2 failure 不得降低 Review、Verification、Hermes、canonical evidence 或正式資料 gate。

## 11. Read-only service API

最小 API：

```python
class MemoryHubPolicyService:
    def evaluate(
        self,
        identity: RuntimeIdentity,
        query: MemoryQuery,
        record: MemoryRecord,
    ) -> MemoryPolicyDecision: ...

    def evaluate_query(
        self,
        identity: RuntimeIdentity,
        query: MemoryQuery,
    ) -> MemoryPolicyQueryDecision: ...
```

禁止 API：

- `add_team`
- `remove_team_member`
- `register_agent`
- `update_policy`
- `approve_memory`
- `dispatch_agent`
- `enable_recall`
- `rebuild_catalog` from runtime/UI/normal workflow

Catalog build／deployment update 只可在受控 offline deployment context 執行，且不屬於 MemoryHubPolicyService。

## 12. Security and privacy constraints

- 不保存 secrets、credentials、prompt、internal reasoning、raw SQLite rows 或完整 logs。
- Decision output 不暴露被拒絕 record 的 summary、artifact path 或 source metadata。
- 所有 catalog path 必須 regular file、root-contained、non-symlink。
- 所有 identity、catalog、rule、decision fingerprint 使用 lowercase SHA-256。
- Catalog provider 不得透過 caller override 任意 root、policy 或 identity source。
- C2 不連線外部服務、不安裝 provider、不啟動 Gateway。

## 13. Testing and acceptance

### Contract tests

- Team Catalog exact schema、safe identifiers、project binding、record fingerprint、catalog fingerprint。
- Agent Policy Catalog exact schema、default deny、rule fingerprint、cross-catalog references。
- Deployment-owned provider 不接受 caller path、mapping、policy 或 verifier override。

### Decision tests

- same project：`allow`。
- same agent：`allow`。
- same team：`allow`。
- missing team claim：`blocked`。
- unknown agent／team：`blocked`。
- explicit policy deny：`deny`。
- no matching allow rule：`deny`。
- memory kind／scope mismatch：`deny`。
- stale／expired／blocked source：`blocked`。
- tampered catalog／record／decision fingerprint：`blocked`。
- cross-project catalog：`blocked`。

### Boundary and regression tests

- Memory Sidecar 與 Short-term Offload default policy 不變。
- Streamlit Memory Hub 不會建立或修改 catalog。
- Agent Operations 不會 dispatch 或 approve。
- C2 failure fallback 維持 canonical context。
- 不寫 SQLite、baseline、Git、Graph snapshot 或 workflow state。
- deterministic ordering、bounded decision size 與 no sensitive metadata leakage。

Required verification：

```bash
.venv/bin/python -m py_compile <affected Python files>
.venv/bin/python -m pytest <affected tests> -q
.venv/bin/python scripts/hermes_post_change_check.py
```

若涉及 Memory Hub UI、Sidecar、Offload 或 Agent Operations integration：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
```

## 14. Implementation decomposition

C2 implementation 另立 implementation plan，建議拆成：

1. Team Catalog immutable model／loader。
2. Agent Policy Catalog immutable model／loader。
3. `MemoryHubPolicyService` deterministic decision engine。
4. C-0/C-1 query integration and fail-closed fallback。
5. Streamlit／Agent Operations read-only decision projection。
6. Contract tests、full verification、Hermes 與 final acceptance。

每個 Task 必須單獨 review；不得將 C3 Wiki ingestion、Node.js Gateway、candidate memory、real IAM 或 dispatch controller 混入 C2。

## 15. C3 handoff

C3 Wiki Knowledge Layer 只能消費已通過 C2 policy decision 的 read-only identity boundary。C3 不得自行建立另一套 ACL；Wiki source、page freshness、source drill-down 與 query 統一經過 `MemoryHubPolicyService`。

只有 C2 Design Spec、Implementation Plan、Review、full verification 與 Hermes acceptance 全部完成後，才進入獨立的 C3 Design Spec。
