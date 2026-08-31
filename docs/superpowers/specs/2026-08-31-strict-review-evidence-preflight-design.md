# Strict Review Evidence Preflight Design

日期：2026-08-31
狀態：Design approved for self-review
版本：strict-review-evidence-preflight-v1
範圍：NBS Analytics Agent verification pipeline

## 1. 目的

建立 Strict Review 前的 deterministic evidence preflight，於不修改正式業務資料、SQLite、baseline、revenue、business rules 或 application runtime 的前提下，自動檢查並補齊 Review 所需的 verification evidence。

本功能主要解決：

- changed module 缺少 targeted test evidence；
- changed Python surface 缺少 compile/static evidence；
- `verification-v1` schema 不完整；
- brief、HEAD、worktree、diff 與 evidence fingerprint 不一致；
- Context Agent output 與 Review Agent validator 不相容；
- runner capability receipt 過期或 identity 不一致；
- 因 evidence 不完整而重複啟動 Strict Review，浪費模型 Token。

## 2. 設計決策

採用方案 B：Preflight 自動補齊 deterministic evidence，但不自動呼叫 Strict Review runner。

Preflight 是 evidence quality gate，不是第二個 Review Agent，也不是 approval、dispatch 或 production data controller。

## 3. 非目標

本階段不包含：

- 自動修復 production code；
- 自動 commit、merge、push 或 rollback；
- 自動批准 Task 或改變 workflow state；
- 自動執行 Documentation Agent；
- Governance Graph write path；
- Memory Hub provider migration；
- Hermes transport 改成本地 CLI；
- 修改 SQLite、baseline、revenue、business rules、正式 cache 或 export schema；
- 新增 Streamlit approval、dispatch 或 workflow-control UI。

## 4. 架構與資料流

```text
Current Worktree
      |
      v
Evidence Collector
      |
      +-- changed files
      +-- HEAD / brief / worktree fingerprint
      +-- diff / diff check
      +-- source seal
      |
      v
Strict Review Evidence Preflight
      |
      +-- schema validation
      +-- freshness validation
      +-- changed-surface coverage
      +-- targeted pytest
      +-- Python compile/static checks
      +-- runner capability check
      +-- context compatibility
      +-- Governance Graph read-only lineage
      +-- Memory Hub / Sidecar read-only observations
      |
      +-- blocked / invalid / failed
      |
      +-- ready
            |
            v
      verification-v1 artifact
            |
            v
      Strict Review Agent
```

Preflight 只允許寫入 `.nbs_agent_runtime/verification_sessions/<sessionId>/` 或受控 ignored temporary validation artifacts。它不直接呼叫 Review Agent。

## 5. 元件責任

| 元件 | 責任 | 不負責 |
|---|---|---|
| Evidence Collector | 收集 changed files、brief、Git identity、受限 diff 與 fingerprints | 語意判斷、修改 source |
| Preflight Controller | 驗證輸入、建立 coverage plan、執行 deterministic checks、產生 evidence | 呼叫 Review Agent、修改正式資料 |
| Validation Runner | 執行 approved targeted tests、compile、static checks | 修復程式、放寬 gate |
| Governance Graph | 提供 canonical artifact lineage 與 risk projection | approval、dispatch、寫入 workflow state |
| Memory Hub | 提供 bounded context hints | 覆蓋 canonical evidence、改變 verdict |
| Memory Sidecar | 提供 freshness、hint metadata 與 fallback observation | 執行模型、修改 workflow |
| Review Agent | requirement、diff、test、risk review | 修正 evidence、修改程式 |
| Hermes | full system acceptance | 取代 Strict Review preflight |

## 6. CLI contract

建議新增入口：

```bash
.venv/bin/python scripts/strict_review_evidence_preflight.py \
  --session <session-id> \
  --brief <brief-path> \
  --base <base-ref> \
  --head WORKTREE \
  --output <verification-v1-path> \
  --strict
```

CLI 要求：

- stdout 只輸出單一 bounded JSON object；
- diagnostic 詳情只寫入受限制的 stderr 或 runtime artifact；
- output path 必須位於專案 `.nbs_agent_runtime`；
- 不接受 output symlink、absolute path escape 或 runtime root symlink；
- 不保存 secrets、raw business rows、完整 prompt 或完整 logs；
- 不執行 commit、merge、push、reset、rebase、stash 或 service management。

## 7. `strict-review-preflight-v1` contract

```json
{
  "schemaVersion": "strict-review-preflight-v1",
  "status": "ready",
  "sessionId": "verification-session-id",
  "sourceFingerprint": "sha256",
  "bundleFingerprint": "sha256",
  "changedFiles": ["backend/agents/review_agent_service.py"],
  "coverage": {
    "targetedTests": "pass",
    "compileStatic": "pass",
    "diffCheck": "pass",
    "runnerCapability": "pass",
    "contextCompatibility": "pass",
    "governanceLineage": "pass",
    "memoryReadiness": "ready"
  },
  "generatedEvidence": ["targeted-tests", "python-compile", "git-diff-check"],
  "verificationPath": ".nbs_agent_runtime/verification_sessions/<id>/verification-v1.json",
  "diagnostics": [],
  "createdAt": "2026-08-31T00:00:00Z"
}
```

`status` 允許值：

- `ready`：必要 evidence 完整且可送入 Strict Review；
- `blocked`：runner、環境、權限或必要資源不可用；
- `invalid_evidence`：schema、provenance、freshness 或 fingerprint 不一致；
- `verification_failed`：targeted test、compile 或 static check 失敗；
- `degraded`：非權威 Graph 或 Memory enrichment 不可用，但 canonical evidence 完整。

## 8. `verification-v1` contract

既有 `verification-v1` 作為 Strict Review 正式輸入，不破壞既有欄位。Preflight 只補齊 deterministic `commands`：

```json
{
  "schemaVersion": "verification-v1",
  "commands": [
    {
      "label": "targeted-review-agent-tests",
      "argv": [".venv/bin/pytest", "tests/test_review_agent_service.py", "-q"],
      "exitCode": 0,
      "stdoutTail": "58 passed",
      "stderrTail": ""
    }
  ]
}
```

`verification-v1` 的 command item 維持既有 exact schema，不新增破壞性欄位。command fingerprint、source fingerprint、時間與 gate provenance 應由 Preflight artifact 及 Verification Chain gate metadata 保存。不得以手工編輯 output 取代實際 command execution。

## 9. Changed-surface coverage

Preflight 依 changed files 分類：

| Changed surface | Required checks |
|---|---|
| `backend/**/*.py` | Python compile、對應 targeted tests、`git diff --check` |
| `tests/**/*.py` | test file compile、targeted pytest、`git diff --check` |
| `scripts/**/*.py` | Python compile、對應 CLI tests、`git diff --check` |
| `docs/**` | docs/link/format validation |
| mixed surfaces | 合併所有必要 coverage，不重複執行相同 command |

Production module 沒有可辨識的對應 targeted test 時，結果為 `blocked`，不因變更看似細小而自動略過。純 docs 或 generated evidence 變更則依既有 deterministic no-doc policy 判定。

## 10. Fingerprint 與 freshness

source seal 由以下內容組成：

```text
sourceFingerprint
|- base SHA
|- HEAD SHA
|- brief fingerprint
|- worktree fingerprint
|- diff fingerprint
|- contract fingerprint
`- policy fingerprint
```

規則：

- source fingerprint 改變時，舊 preflight 不可重用；
- verification 與 Graph observation 若與 source seal 不一致，結果為 `invalid_evidence`；
- Context `contextFingerprint` 保持 Context evidence bundle 自己的 identity，不要求等於 source fingerprint；但 Context artifact 必須在 provenance metadata 中明確綁定相同的 session/source seal；
- changed files 增減後必須重新計算 coverage；
- 只更新 timestamp 不會形成新的有效 evidence；
- command cache 只有在 source、command、policy 與 runner identity 都未改變時才可重用；
- 所有 output artifact 使用 atomic write。Preflight 的 source fingerprint、verification gate 的 source fingerprint、Graph observation 的 source fingerprint 必須相等；Context fingerprint 則維持獨立但可追溯的 evidence identity。

## 11. Governance Graph integration

Graph 只提供 read-only observation：

```json
{
  "schemaVersion": "governance-graph-preflight-observation-v1",
  "status": "ready",
  "sourceFingerprint": "sha256",
  "canonicalArtifacts": ["brief", "task-contract", "verification-v1"],
  "riskSurfaces": ["runner", "review", "evidence"],
  "authority": "read_only_projection",
  "diagnostics": []
}
```

Graph 可檢查 canonical lineage、task contract、source identity 與 risk surface，但不得批准 Task、改變 review verdict、觸發 runner 或寫入 workflow state。

Graph unavailable 時，若 canonical evidence 完整，Preflight 仍可回傳 `degraded`；Graph fingerprint mismatch 則回傳 `invalid_evidence`。

## 12. Memory Hub / Memory Sidecar integration

Memory 只提供 bounded、non-authoritative observation：

```json
{
  "schemaVersion": "memory-preflight-observation-v1",
  "status": "ready",
  "authority": "non_authoritative_memory",
  "hintCount": 2,
  "sourceFingerprints": ["sha256"],
  "usedFor": ["recommended-targeted-tests", "runner-recovery-hints"],
  "writes": 0,
  "invocations": 0
}
```

規則：

- Memory hints 不可成為 pytest、compile、fingerprint 或 Review proof；
- stale／degraded hints 只標記 observation，不放寬 canonical gate；
- Memory Hub unavailable 時，canonical evidence 完整即可繼續；
- Sidecar 不啟動 Gateway、不呼叫 provider、不執行 distillation、prune 或 apply；
- hints 不得包含 secrets、raw logs、SQLite rows 或完整 prompt。

## 13. 執行階段

### Phase A：Input validation

檢查 session、brief、source seal、worktree、dirty paths、runtime output path 與 schema。失敗回傳 `invalid_evidence` 或 `blocked`。

### Phase B：Coverage planning

由 changed files 建立 deterministic coverage plan，不使用 LLM。

### Phase C：Validation

透過既有 Validation Runner 執行 approved `pytest_targeted`、`py_compile`、`git diff --check` 與 repo 已存在的 static verification。

### Phase D：Read-only enrichment

讀取 Graph lineage 與 Memory observations；這些資料不改變核心 verdict。

### Phase E：Artifact generation

所有 required checks 通過時，原子產生 `strict-review-preflight-v1` 與 `verification-v1`。任何必要 check 失敗都不輸出 `ready`。

## 14. Fallback matrix

| 情況 | 結果 | 可進 Strict Review |
|---|---|---|
| required checks 全部通過 | `ready` | 可以 |
| targeted pytest 失敗 | `verification_failed` | 不可以 |
| compile/static 失敗 | `verification_failed` | 不可以 |
| fingerprint 不一致 | `invalid_evidence` | 不可以 |
| runner capability 不可用 | `blocked` | 不可以 |
| local validation runner 無法啟動 | `blocked` | 不可以 |
| Governance Graph unavailable | `degraded` | 可以，canonical evidence 完整時 |
| Memory Hub unavailable | `degraded` | 可以，canonical evidence 完整時 |
| Memory Sidecar stale | `degraded` | 可以，canonical evidence 完整時 |
| Context schema 不相容 | `invalid_evidence` | 不可以 |

`degraded` 只適用於非權威 enrichment；不得用於掩蓋 canonical evidence 失敗。

## 15. Strict Review integration

建議由 Codex 或上層 workflow 明確串接：

```text
Preflight
  |- ready -> explicitly invoke Strict Review
  |- blocked -> stop
  |- invalid_evidence -> stop
  `- verification_failed -> stop
```

Preflight 不直接呼叫 Review runner，避免 implicit dispatch、gate 責任混淆與無效模型 Token 消耗。

## 16. Security boundary

Preflight 禁止：

- tracked source、SQLite、baseline、正式 cache、revenue、business rules、export schema 寫入；
- commit、merge、push、reset、rebase、stash；
- 啟停正式服務或安裝 dependency；
- 傳送 raw business data、secrets、完整 logs 或完整 prompt；
- 由 Graph、Memory Hub、Sidecar 觸發 approval、dispatch 或 runner。

## 17. Idempotency 與效能

同一 source fingerprint、command fingerprint、policy fingerprint 與 runner identity 下，可重用仍新鮮的 command evidence；source 或 policy 改變時必須重跑。

效能目標：

- cache hit 不重跑 unaffected checks；
- 不傳送完整 diff 或完整 log；
- Preflight 不使用 LLM Token；
- 不增加 Streamlit page load、正式 SQLite query 或 dashboard/export latency；
- 降低因 evidence 缺口造成的 Strict Review 重跑次數。

## 18. Test matrix

### Unit

- preflight schema 欄位完整性；
- verification schema 與 fingerprint；
- changed-surface mapping；
- command cache reuse；
- output path、symlink、absolute path safety；
- targeted test、compile、diff check pass/fail；
- timeout、output cap、allowlist 與 runner spawn failure。

### Integration

- `ready` 可進 Strict Review；
- failed/invalid/blocked 不可進 Strict Review；
- source drift 強制重新 preflight；
- Context evidence 可轉成 Review-compatible summary；
- Graph degraded 不覆蓋 canonical evidence；
- Memory unavailable 不放寬 gate；
- failed session 不可被覆寫重用。

### End-to-end

```text
fresh session
  -> collector
  -> preflight ready
  -> Strict Review PASS
  -> full pytest PASS
  -> Hermes PASS
  -> completion attestation complete
```

## 19. Acceptance criteria

1. 不需人工編輯 `verification-v1` 即可補齊必要 evidence。
2. changed Python surface 會自動有 compile/static evidence。
3. changed production module 會自動驗證 targeted test coverage。
4. evidence 不完整時不會啟動 Strict Review runner。
5. Review、full pytest、Hermes 綁定同一 source fingerprint。
6. Preflight 不修改 SQLite、baseline、正式 cache 或 business data。
7. Governance Graph、Memory Hub、Memory Sidecar 只提供 read-only observation。
8. 既有 Streamlit、API、dashboard 與 export 行為不變。
9. cache hit 可重用 unaffected evidence，且不降低 freshness 或 provenance 保證。

## 20. Rollout 與 rollback

### Phase 0：Shadow mode

執行 Preflight 但不阻擋現有 Strict Review，收集 false positive、漏判與 latency。

### Phase 1：Advisory mode

顯示 evidence coverage warning，但仍允許人工明確繼續送 Review。

### Phase 2：Fail-closed mode

只有 `ready` 才能進 Strict Review；其他狀態停止。

### Phase 3：Default mode

所有 Strict Review 預設先執行 Preflight，並保留 bounded cache reuse 與 diagnostic no-cache mode。不得新增未經治理批准的 bypass。

如需 rollback，只停用 enforcement、保留既有 artifacts 與 verification chain，回到 Shadow mode；不得回退 SQLite、baseline、正式 cache 或業務資料。

## 21. Implementation boundary

下一階段 implementation 應集中於：

- Preflight controller 與 CLI；
- `strict-review-preflight-v1` model/validator；
- changed-surface coverage planner；
- verification-v1 deterministic evidence writer；
- command cache freshness；
- existing Verification Chain integration；
- unit、integration、end-to-end tests。

不得在同一 implementation plan 內混入 Hermes local CLI migration、正式業務資料變更或 Governance Graph write feature。
