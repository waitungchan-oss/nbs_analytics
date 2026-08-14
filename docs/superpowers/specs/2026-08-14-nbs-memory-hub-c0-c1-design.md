# NBS Memory Hub C-0/C-1 Design Spec

## Status

Proposed for review

## Date

2026-08-14

## Scope

Phase C-0/C-1: Memory Contract + read-only Memory Hub Foundation。

本階段將方案 A／B 已驗證的 bounded memory 與 short-term offload 能力，安全地擴展到多個 Agent consumer；不建立 Node.js Gateway、額外 SQLite、migration、管理 UI 或自動 recall。

## 1. Goal

建立一個 provider-neutral、immutable、可重建的 Memory Contract 與 read-only Memory Hub，使不同 Agent 可以查詢同一套經過 provenance、freshness、fingerprint 與 scope 驗證的 memory records，同時維持 NBS 的 canonical-artifact-first authority。

## 2. Source allowlist

第一版只接受以下三類 canonical source：

1. **Governance documents**：spec、implementation plan、ADR、Obsidian evidence 及其已批准的 project governance artifact。
2. **Verified sidecar/offload evidence**：已通過既有 Memory Sidecar／Short-term Offload contract，並可追溯至 completed run 的 evidence。
3. **Approved skill evidence**：已批准且可追溯至 completed run、Review PASS、full verification PASS 與 Hermes PASS 的 reusable skill／SOP evidence。

以下資料在本階段全部拒絕：任意對話歷史、未驗證 Agent output、SQLite rows、Excel／CSV raw rows、完整 logs、prompt、internal reasoning、secrets、credential、未完成或 stale run，以及沒有 source fingerprint 的摘要。

## 3. Non-goals and hard boundaries

- 不修改正式 SQLite、baseline、revenue scope、business rules 或 export schema。
- 不把 Memory Hub 變成 canonical source、approval、dispatch、runtime、Review、Verification、Hermes 或 Git authority。
- 不由 memory 推測 Governance Graph node／edge；Graph 仍只由 canonical artifacts 建立 read-only projection。
- 不建立 Node.js Gateway、長駐外部服務、額外 SQLite 或 migration。
- 不在普通 workflow 開啟 recall；existing Memory Sidecar／Short-term Offload default policy 維持不變。
- 不提供管理 UI、ACL write API、team membership mutation 或 agent registration mutation。
- 不建立 candidate memory。candidate layer 留到 C-0/C-1 穩定後，並且必須與正式 catalog 分離。

## 4. Recommended architecture

採用現有 Python backend／read model 與 immutable file catalog：

```text
Allowlisted canonical sources
        │
        ▼
Memory Catalog Builder (deterministic, read-only source scan)
        │
        ▼
Immutable Memory Catalog / index artifact
        │
        ▼
MemoryHubService (bounded query + scope/ACL/freshness filters)
        │
        ├── Context Agent
        ├── Memory Sidecar adapter
        ├── Short-term Offload consumer
        └── future Wiki / Skill / CodeGraph read models
```

### 4.1 Catalog properties

- Catalog 是 derived artifact，可由同一組 source identity、source bytes 與 policy deterministic rebuild。
- Catalog 不得寫入 canonical source；builder 只讀取 allowlisted roots。
- Catalog 每次輸出都綁定 `catalogFingerprint`、`sourceSetFingerprint`、`policyFingerprint` 與 `builtFromHead`。
- catalog、index、temporary scan files 必須位於獨立的 bounded runtime root，禁止 symlink、path traversal、absolute source leakage 和 cross-root read。
- Catalog 缺失、stale、tampered 或 policy 不相容時，MemoryHubService 回傳 bounded `blocked`／`empty`，不得自動重建或改寫 source。

## 5. Canonical contract

### 5.1 `memory-source-v1`

```json
{
  "schemaVersion": "memory-source-v1",
  "sourceId": "sha256-derived-id",
  "sourceKind": "governance_document|verified_evidence|approved_skill",
  "artifactRef": "relative/allowlisted/path.json",
  "artifactSha256": "lowercase-64-hex",
  "runId": "run-...",
  "gitHead": "40-hex-or-null",
  "scope": "project|agent|team",
  "owner": "governance-role",
  "status": "verified|stale|blocked",
  "freshness": {
    "generatedAt": "ISO-8601 with timezone",
    "expiresAt": "ISO-8601 with timezone",
    "policyVersion": "memory-freshness-v1"
  },
  "sourceFingerprint": "lowercase-64-hex"
}
```

Rules:

- Exact keys only; unknown fields reject。
- `artifactRef` 必須是 catalog root 內的 regular file；禁止 absolute path、`..`、symlink、denied extension、secret-like path 和 cross-root reference。
- `sourceKind=verified_evidence` 或 `approved_skill` 必須綁定 completed run、Review PASS、full verification PASS、Hermes PASS 及 immutable Git／artifact identity。
- `sourceKind=governance_document` 必須綁定 approved project path、document fingerprint 與 source freshness；不接受任意 Markdown copy。
- `sourceFingerprint` 必須由完整 canonical fields 與 artifact hash 重算，不接受 caller supplied fingerprint。
- `status` 不是治理 approval；它只描述 source 是否可被 read model 使用。

### 5.2 `memory-record-v1`

```json
{
  "schemaVersion": "memory-record-v1",
  "memoryId": "sha256-derived-id",
  "memoryKind": "governance|evidence|skill",
  "summary": "bounded UTF-8 non-sensitive summary",
  "sourceRefs": ["source-id"],
  "scope": "project|agent|team",
  "owner": "governance-role",
  "freshness": "fresh|stale|unknown",
  "status": "ready|empty|blocked",
  "recordFingerprint": "lowercase-64-hex"
}
```

Rules:

- `summary` 使用 bounded UTF-8 cap，不保存完整 prompt、tool log、raw rows 或 internal reasoning。
- `sourceRefs` 必須指向同一 catalog 中已驗證的 `memory-source-v1` records。
- `memoryId` 與 `recordFingerprint` 必須由 canonical fields deterministic re-derive。
- 任一 source stale／unknown／blocked、source fingerprint mismatch 或 source 不在 allowlist，record 不得變成 `ready`。
- 相同 source set、policy 與 summary 必須產生相同 record identity。

### 5.3 `memory-query-v1` and `memory-query-result-v1`

Query 只允許 bounded read-only parameters：

```json
{
  "schemaVersion": "memory-query-v1",
  "query": "bounded task-oriented text",
  "consumerId": "agent-safe-id",
  "scope": "project|agent|team",
  "memoryKinds": ["governance", "evidence", "skill"],
  "maxItems": 3,
  "maxBytes": 6000,
  "timeoutMs": 800,
  "queryFingerprint": "lowercase-64-hex"
}
```

Result status 為 `ready|empty|timeout|degraded|blocked`。`ready` 結果每筆必須包含 `memoryId`、summary、sourceRefs、recordFingerprint、freshness 與 scope decision；結果不得包含未經 scope filter 的 record。

## 6. Scope and ACL decision

第一版只建立 read-only、deny-by-default decision：

```json
{
  "schemaVersion": "memory-acl-decision-v1",
  "consumerId": "agent-safe-id",
  "requestedScope": "project|agent|team",
  "recordScope": "project|agent|team",
  "decision": "allow|deny|blocked",
  "reason": "same_project|same_agent|same_team|missing_identity|scope_mismatch|invalid_record",
  "decisionFingerprint": "lowercase-64-hex"
}
```

Scope policy：

- `project`：只有同一 project identity 可讀。
- `agent`：必須 exact match `consumerId`。
- `team`：第一版只有 trusted immutable team claim 可讀；沒有 claim 一律 deny。
- 沒有 identity、identity malformed、record owner 不明或 team claim 不可驗證，一律 deny／blocked。
- 本階段不允許任何 runtime API 改變 owner、team membership、ACL policy 或 agent registration。

## 7. MemoryHubService boundary

Service 只提供以下 read-only operations：

- `build_catalog(source_root, policy) -> catalog artifact`：只在受控 offline build／test context 使用；不由 UI、Hermes 或普通 workflow 自動呼叫。
- `load_catalog(catalog_path) -> immutable catalog`：驗證 schema、fingerprint、root、freshness 與 source set。
- `query(query: MemoryQuery, identity: RuntimeIdentity) -> MemoryQueryResult`：bounded query、ACL filter、freshness filter、deterministic ordering。
- `resolve_source(source_id, identity) -> SourceResolution`：只回傳 bounded metadata 或 bounded drill-down，拒絕跨 scope 和 raw data。

Service 禁止：write source、approve record、dispatch agent、改變 recall default、寫 SQLite／Git、建立 Graph snapshot、呼叫外部 provider、執行 shell。

## 8. Failure and fallback policy

| Condition | Hub status | Consumer behavior |
|---|---|---|
| Valid catalog and matching scope | `ready` | 提供最多 3 筆、6,000 bytes、800 ms 內 hints |
| No matching record | `empty` | 使用既有 canonical context |
| Catalog missing or stale | `blocked` | 不自行建立 catalog，使用 canonical context |
| Fingerprint／schema mismatch | `blocked` | fail closed，保留 bounded diagnostic |
| ACL identity missing／scope mismatch | `blocked` | 不回傳 record |
| Query timeout／service unavailable | `timeout`／`degraded` | 不阻塞 workflow，不改變既有結果 |
| Candidate source or unverified output | `blocked` | 不進正式 catalog、不進 recall |

Memory Hub 任一失敗不得降低 Review、Verification、Hermes 或 canonical evidence gate。

## 9. Candidate memory follow-up boundary

C-0/C-1 穩定後才可設計 candidate layer：

- Candidate catalog 與 verified catalog 使用不同 schema、root、fingerprint namespace 和 query status。
- Candidate 只可由 read-only observation／proposal 產生，不能被正式 query default 使用。
- Candidate 必須經人工批准、source re-validation、Review／verification／Hermes gate 後，才能轉成 approved skill 或 verified evidence。
- Candidate 不得覆寫或刪除 verified record。

## 10. Testing and acceptance

最少測試：

- exact schema、canonical fingerprint、deterministic catalog rebuild。
- 三類 source allowlist 與所有 denied source regression。
- stale／unknown／tampered／missing source fail-closed。
- path traversal、absolute path、symlink、cross-root、oversize、secret-like path regression。
- project／agent／team scope ACL allow／deny／blocked。
- query maxItems、maxBytes、timeout、deterministic ordering。
- catalog unavailable 時，existing Memory Sidecar／Offload／Context fallback 維持相同結果。
- source drill-down 只能讀 bounded metadata/content slice，不能讀 raw SQLite 或任意檔案。
- 不會由 Memory Hub 建立 Graph node／edge、approval 或 snapshot。

Required verification：

```bash
.venv/bin/python -m py_compile <affected Python files>
.venv/bin/python -m pytest <affected tests> -q
.venv/bin/python scripts/hermes_post_change_check.py
```

若跨 Agent Operations／Memory Sidecar／Offload integration，另需：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/system_manager.py acceptance
```

## 11. Implementation decomposition

此 spec 只涵蓋下列獨立 implementation tasks：

1. C0 contract models：source、record、query、ACL decision、fingerprint。
2. C1 immutable catalog builder／loader：allowlist、root safety、deterministic rebuild。
3. C1 read-only MemoryHubService：query、scope／freshness filtering、drill-down、fallback。
4. Existing Sidecar／Offload adapter projection：只讀整合，不改 default policy。
5. Contract tests、targeted review、full verification 與 Hermes acceptance。

Node.js Gateway、extra SQLite、migration、Wiki ingestion、CodeGraph、candidate memory、管理 UI 及 team／agent mutation 全部另立 phase，不得混入本 implementation plan。

