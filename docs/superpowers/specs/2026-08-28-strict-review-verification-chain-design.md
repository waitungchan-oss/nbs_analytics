# Strict Review Verification Chain Spec

## 1. 目的

建立一條可重現、可續跑、fail-closed 的驗收鏈路，解決目前 `full pytest`、Hermes、fresh evidence 與 Strict Review 之間的順序衝突、provenance 不一致、runner 啟動失敗，以及舊報告殘留等問題。

本 spec 採用「Verification Session + 兩階段驗收」方案：

```text
Source Seal
  → pre-review verification
  → Strict Review
  → full pytest
  → Hermes
  → Completion Attestation
```

Strict Review 負責程式碼與需求裁決；full pytest 與 Hermes 負責後續整體驗收；最終完成與否由 deterministic Completion Attestation 判定。

## 2. 已確認問題

### 2.1 流程順序衝突

現有治理文件要求 `Review PASS → full verification → Hermes`，但目前 Strict Review 又依賴預先寫入的 full pytest 與 Hermes evidence。這會造成：

- 沒有 full pytest，Strict Review 不敢 PASS。
- 要先跑 full pytest，又無法確認 code review 已通過。
- Hermes evidence 可能屬於另一個 source state。

### 2.2 Freshness 與 TOCTOU

HEAD、brief、worktree 與 verification commands 分開建立，沒有被同一個 immutable session 綁定。測試或 runner 執行期間若 dirty worktree 改變，舊 evidence 仍可能被送入 Review。

### 2.3 Static preflight 不等於 runner 可執行

目前 preflight 可以確認 CLI version、model cache 與 executable，但實際 `codex exec` 仍可能在讀取 stdin 後 exit `1`。因此需要將 static capability 與 live turn capability 分開。

### 2.4 Batch 與報告終態非原子

Review batch 可能部分完成，後續 batch 或 runner 失敗時，新的 terminal report 沒有明確發布，使用者仍會看到上一輪 `context_overflow`。舊報告不得繼續代表新 session。

### 2.5 Hermes profile 混淆

primary runtime 與 isolated verification profile 是不同驗收模式。profile 缺失或過期時應為 `blocked_runner_capability`；不能把 no-profile PASS 與 stale-profile FAIL 合併成一個不明確結果。

## 3. 範圍

### In scope

- Strict Review、full pytest、Hermes、fresh evidence 的執行順序與 provenance contract。
- immutable Verification Session manifest。
- source seal、gate state machine、batch resume 與 deterministic aggregation。
- Codex runner static/live capability、bounded diagnostics 與 recovery。
- atomic terminal artifacts、stale report isolation、completion attestation。
- 本地 Context Agent 與 Memory Hub hint 的 bounded 整合。
- 對應 tests、benchmark、runtime-only evidence 與 operator diagnostics。

### Out of scope

- 不修改正式 SQLite、baseline、revenue scope、business rules 或 export schema。
- 不改動正式營收資料、active export、trusted reference pointer 或 AI Forecast。
- 不新增外部服務、資料庫 migration、認證系統或新的 approval workflow。
- 不把 Memory Hub、Governance Graph 或 Agent Operations 變成 authority。
- 不以 synthetic agent response、手工 PASS 或舊 report 取代真實 runner。
- 不由 Hermes 執行修復、prune、Git 整合或正式資料寫入。

## 4. 設計原則

1. `verification-v1` 維持既有 parser compatibility；session metadata 不塞入其 top-level。
2. Source identity 先封存，再執行所有 gate；任何 source drift 都使本輪失效。
3. Review PASS、full verification PASS、Hermes PASS 是不同狀態，不互相取代。
4. 每個失敗都產生新的 bounded terminal artifact，不沿用舊結果。
5. LLM 只做 code review；fingerprint、evidence、aggregation 與 final attestation 全部 deterministic。
6. Memory Hub 只提供 non-authoritative bounded hints，不能改變 verdict、scope、baseline 或 gate。
7. runner capability 缺失時 fail closed；不得用提高 timeout 或放寬 parser 取得 PASS。

## 5. Architecture

```text
                         ┌──────────────────────────────┐
                         │ Source Seal / Session Manifest │
                         │ brief + HEAD + worktree + diff│
                         └──────────────┬───────────────┘
                                        │
             ┌──────────────────────────┼─────────────────────────┐
             ▼                          ▼                         ▼
     Pre-review evidence       Runner capability             Memory hints
     compile/targeted tests    static + cached live probe     read-only only
             │                          │                         │
             └──────────────────────────┼─────────────────────────┘
                                        ▼
                              Strict Review batches
                                        │
                              deterministic aggregation
                                        │ PASS
             ┌──────────────────────────┴─────────────────────────┐
             ▼                                                    ▼
       Full pytest                                           Hermes acceptance
             └──────────────────────────┬─────────────────────────┘
                                        ▼
                           Completion Attestation
```

### 5.1 Components

| Component | Responsibility |
|---|---|
| `VerificationSession` | 建立 immutable source seal、session state 與 gate identity。 |
| `EvidenceWriter` | 從真實命令結果建立 bounded `verification-v1` 與外層 command metadata。 |
| `RunnerCapability` | 執行 static preflight；必要時執行小型 live turn probe。 |
| `ReviewBatchPlanner` | 依檔案與 token budget deterministic 拆分 Review batches。 |
| `ReviewRunner` | 只對每個 batch 執行受控 read-only Strict Review。 |
| `ReviewAggregator` | deterministic 合併 batch verdict、findings 與 coverage。 |
| `FullVerificationRunner` | 在 Review PASS 後執行 full pytest 與 compile evidence。 |
| `HermesAdapter` | 使用明確 primary 或 isolated profile 執行 Hermes read-only acceptance。 |
| `CompletionAttestation` | 驗證所有 gate、fingerprint、freshness 與沒有未歸屬 drift。 |

## 6. Gate state machine

```text
created
  → sealed
  → pre_review_passed
  → review_running
  → review_passed
  → full_verification_passed
  → hermes_passed
  → complete
```

終止或可續跑狀態：

```text
blocked_runner_capability
blocked_runner_transport
review_changes_required
context_overflow
verification_failed
hermes_failed
stale_source
invalid_evidence
```

規則：

- `review_passed` 不等於 `complete`。
- `full_verification_passed` 不會覆蓋 `review_changes_required`。
- `hermes_failed` 不會被 no-profile Hermes 結果自動覆蓋。
- 每個 terminal state 都必須含 session ID、source seal、gate name、原因與 recovery。
- 只有 `complete` 可以被 operator 顯示為整輪完成。

## 7. Data contracts

### 7.1 `verification-session-v1`

```json
{
  "schemaVersion": "verification-session-v1",
  "sessionId": "uuid",
  "status": "sealed",
  "projectId": "nbs_analytics",
  "baseSha": "40-char sha",
  "headSha": "40-char sha",
  "briefPath": "docs/briefs/task.md",
  "briefFingerprint": "sha256",
  "worktreeFingerprint": "sha256",
  "diffFingerprint": "sha256",
  "contractFingerprint": "sha256",
  "policyFingerprint": "sha256",
  "createdAt": "RFC3339",
  "gates": {}
}
```

session manifest 必須 exact schema、canonical JSON fingerprint，並寫入 `.nbs_agent_runtime/verification_sessions/<sessionId>/`。它不包含 SQLite rows、Excel、完整 logs 或 secrets。

### 7.2 Existing `verification-v1`

保持目前格式：

```json
{
  "commands": [
    {
      "label": "full pytest",
      "argv": [".venv/bin/python", "-m", "pytest", "-q"],
      "exitCode": 0,
      "stdoutTail": "2339 passed in ...",
      "stderrTail": ""
    }
  ]
}
```

不新增 top-level metadata。外層 session gate artifact 保存：

- command fingerprint
- session ID
- startedAt / finishedAt
- producer version
- stdout/stderr digest
- source seal
- reuse reason

### 7.3 Gate result

```json
{
  "schemaVersion": "verification-gate-result-v1",
  "sessionId": "uuid",
  "gate": "pre_review|strict_review|full_pytest|hermes|completion",
  "status": "pass|blocked|failed|stale",
  "sourceFingerprint": "sha256",
  "evidenceFingerprint": "sha256",
  "startedAt": "RFC3339",
  "finishedAt": "RFC3339",
  "diagnostics": [],
  "recovery": []
}
```

### 7.4 Runner capability receipt

```json
{
  "schemaVersion": "runner-capability-v1",
  "status": "static_ready|turn_ready|blocked_runner_capability|blocked_runner_transport",
  "executable": "/absolute/path/to/codex",
  "cliVersion": "codex-cli 0.150.1",
  "model": "gpt-5.4",
  "cacheFingerprint": "sha256",
  "environmentFingerprint": "sha256",
  "diagnostics": [],
  "expiresByFingerprint": "sha256"
}
```

此 receipt 只在 executable、CLI、model、cache、runner configuration 與 execution environment fingerprint 都相同時重用；不能只依賴時間 TTL。

### 7.5 Completion attestation

```json
{
  "schemaVersion": "completion-attestation-v1",
  "sessionId": "uuid",
  "status": "complete|blocked",
  "requiredGates": {
    "strictReview": "pass",
    "fullPytest": "pass",
    "hermes": "pass"
  },
  "sourceFingerprint": "sha256",
  "artifactFingerprints": {},
  "diagnostics": [],
  "generatedAt": "RFC3339"
}
```

## 8. Freshness and provenance

Source seal 必須在 pre-review 前建立，並在每個 gate 開始與完成時重新驗證：

```text
current HEAD == sealed headSha
current brief SHA == sealed briefFingerprint
current filtered worktree SHA == sealed worktreeFingerprint
current diff SHA == sealed diffFingerprint
```

規則：

- 只允許明確列入 preserved process-only paths 的 runtime/process artifacts 被排除。
- `.nbs_agent_runtime`、`.superpowers`、`docs/superpowers` 的排除規則必須在 manifest 中保存並 fingerprint。
- 任何非 allowlisted dirty file 變更使 session `stale_source`。
- Full pytest、Hermes 寫入 ignored runtime logs 不會改變 source seal；若命令改動 tracked 或未豁免 dirty path，必須停止。
- 不得在 Review 失敗後事後補寫 provenance 使其看似新鮮。

## 9. Strict Review contract

Strict Review 只消費：

- approved brief / task contract
- current source-sealed diff
- ready Context summary
- pre-review targeted verification-v1
- runner capability receipt
- optional Memory Hub observation

Strict Review 不要求 full pytest 或 Hermes 作為自身 PASS 前置條件；它只需確認 changed-surface targeted tests、compile/static evidence 與 requirement coverage。full pytest 與 Hermes 是 Review PASS 後的 final acceptance gates。

`review-report-v1` 維持既有 schema。每個 batch 的 `reviewFingerprint` 必須等於該 batch request 的 fingerprint；aggregator 以 full session fingerprint 產生最終外層結果，不能由 LLM 產生 aggregator verdict。

## 10. Runner capability and failure handling

### Static preflight

檢查 executable、absolute path、allowlist、CLI version、model cache schema、model availability 與 command/model consistency。

### Live turn probe

只在 static identity 改變或 receipt 缺失時執行。probe 使用固定、短、read-only prompt，要求輸出最小合法 JSON；不讀取業務資料、不修改 runtime state、不重跑 Review。

結果：

- static fail：`blocked_runner_capability`
- static pass / live probe transport fail：`blocked_runner_transport`
- live probe schema fail：`blocked_runner_transport`
- live probe pass：`turn_ready`

Plugin icon path warnings 若不影響 exit code 與合法 output，只記錄為 bounded warning；若伴隨 exit `1`，必須保留 exit code 與 stderr digest，不能誤判為 PASS。

## 11. Batch resume and aggregation

每個 batch 以 `(sessionId, batchId, batchFingerprint)` 作為 immutable identity：

- 已完成且 fingerprint 相同的 batch 可重用。
- fingerprint 不同時不得重用舊結果。
- runner 失敗只使該 batch blocked，不污染已完成 batch。
- aggregator 必須確認所有 changed files 已覆蓋。
- critical/high/medium/low findings 均不得被摘要刪除。
- aggregator 不呼叫 LLM，不重新解讀 patch。
- 所有 batch PASS 且無 findings 才能輸出 Strict Review PASS。

## 12. Memory Hub boundary

Memory Hub 只提供 Context Agent 已驗證、bounded、fresh 的 observation：

- 不放入 canonical source seal。
- 不可改變 approved scope、forbidden paths、baseline 或 formal revenue scope。
- malformed、stale、consumer mismatch 或 fingerprint mismatch 時標記 `ignored`。
- Review / Hermes 不直接 query Memory Hub。
- Memory Hub 失效不阻塞 canonical Review，除非 approved task 明確要求其 evidence；本 task 不要求。

## 13. Error and fallback matrix

| Failure | Terminal state | Must not do | Recovery |
|---|---|---|---|
| Brief mismatch | `invalid_evidence` | 不使用另一份 brief | 重新建立 approved session |
| Stale HEAD/worktree | `stale_source` | 不重用任何舊 gate | 重新 seal |
| Cache schema invalid | `blocked_runner_capability` | 不猜 metadata、不合成 PASS | 修復/替換 cache 後重跑 preflight |
| Live runner exit 1 | `blocked_runner_transport` | 不重試無上限、不發布舊 PASS | 檢查 transport/session/config |
| Review payload over budget | `context_overflow` | 不放寬 strict gate | 重新 deterministic split |
| One batch fails | `blocked_runner_transport` | 不丟棄其他 batch、不中止 provenance | 只重跑 failed batch |
| Full pytest fails | `verification_failed` | 不覆蓋 Review PASS | 修復後建立新 session |
| Hermes fails | `hermes_failed` | 不用 no-profile 結果覆蓋 profile failure | 明確選定 profile 後重跑 |
| Old report exists | 新 session terminal artifact | 不將舊 report 當 current | 以 session ID 選取最新結果 |

## 14. Testing matrix

### Unit / contract

- exact schema、canonical fingerprint、unknown fields rejection。
- source seal stable/changed、filtered dirty path、preserved process artifact。
- `verification-v1` compatibility 與 bounded tails。
- runner static ready / missing executable / incompatible cache / model mismatch。
- live probe pass / timeout / nonzero exit / invalid JSON。
- gate state transition legality與 terminal state payload。
- batch fingerprint reuse、failed-batch resume、coverage completeness。
- deterministic aggregation preserve findings。
- Memory Hub ignored fallback。

### Integration

- clean source session：targeted → Review → full pytest → Hermes → complete。
- full pytest failure：Review remains pass but completion blocked。
- Hermes failure：completion blocked，且 profile identity清楚。
- source drift between gates：session stale，拒絕後續 gate。
- runner failure：沒有新的 PASS report，舊 report 不被選取。
- duplicated invocation：同一 fingerprint 只允許一個 active writer，結果可重用。

### Acceptance

- current `main@de790b0` plus current dirty worktree 可建立新 session。
- 本輪 full pytest、Hermes 與 Strict Review 各有獨立 evidence。
- SQLite、baseline、formal revenue scope、active export 與 trusted reference byte-identical。
- no-profile Hermes 與 isolated-profile Hermes 不混合。
- final attestation 只有在三個 required gates PASS 時輸出 `complete`。

## 15. Rollout strategy

### Phase 0：shadow session

只建立 session、source seal、gate artifacts 與 diagnostics，不改既有 Review verdict。驗證 artifact schema 與 freshness。

### Phase 1：Review chain adoption

Strict Review 改採 pre-review targeted evidence；full pytest 與 Hermes 移到 Review PASS 後。保留既有 CLI compatibility，提供 `--session` 與 `--resume`。

### Phase 2：resume and capability cache

啟用 batch-level reuse、live probe receipt 與 deterministic completion attestation。連續兩輪完整成功後才作為預設 operator path。

### Rollback

- 停用 session controller，回到既有 `blocked_runtime` 行為。
- 保留 session artifacts、cache backup 與歷史報告。
- 不回滾 SQLite、baseline、formal export 或業務資料。

## 16. 成功定義

本 spec 完成後，系統能清楚回答：

1. 這份 Strict Review 看的是哪一個 brief、HEAD、diff 與 worktree？
2. full pytest 與 Hermes 是否針對同一個 source seal？
3. runner 是靜態可用，還是真正完成過 live turn？
4. 失敗的是 Review、full pytest、Hermes，還是 runner transport？
5. 使用者看到的是否是本輪最新 terminal artifact？
6. 是否只有 final attestation PASS 才能宣稱整輪完成？

如果任何問題無法由 bounded artifacts 回答，結果必須是 `blocked` 或 `unknown`，不能推測為 PASS。
