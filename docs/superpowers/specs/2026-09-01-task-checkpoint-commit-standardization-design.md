# Task Checkpoint Commit Standardization Spec

狀態：Draft，供 implementation review

日期：2026-09-01

適用專案：`nbs_analytics`

## 1. 目的

將每個已批准 implementation Task 的完成點標準化為一個可追蹤、可驗證、可回退的 checkpoint commit，讓 Task、實際 diff、Review evidence 與 focused verification 保持一對一關係。

Checkpoint commit 的目的，是保存 Task 邊界與 lineage，不是宣稱整輪工作完成。`Strict Review`、full pytest、Hermes、UI acceptance、baseline 與正式資料驗證仍是獨立 gates。

## 2. 已驗證現況與問題

本次 live inventory：

- branch：`main`
- local `HEAD` 與 `origin/main` 對齊
- latest relevant commit：`ec4a6fc fix: make sandbox preflight local gate non-failing`
- working tree：clean
- 現行 dispatch contract：Implementation Agent 不得 commit、merge 或 push；Codex 負責檢查 diff、Review、完整驗證與 Git integration。
- Governance Graph：只讀 canonical artifacts 的 projection，不是 commit、approval 或 dispatch control input。
- Memory Hub / Memory Sidecar：只提供 bounded、non-authoritative context，不得改變 Git 或 gate 狀態。

目前缺少的是跨 Task 的一致 commit identity、evidence binding、allowlist enforcement 與 rollback 語義。若沒有標準，容易出現：

- 多個 Task 混在一個 commit，難以定位 regression。
- commit message 有描述，但無法綁定 approved Task、Review fingerprint 或 source HEAD。
- 將 checkpoint commit 誤當成 Review/full pytest/Hermes PASS。
- staging 時把既有 unrelated dirty changes 一併提交。
- 以 `reset`、amend 或重新利用舊 PASS 掩蓋新 diff。

## 3. 設計原則

1. **One approved Task → one checkpoint commit**：一個 checkpoint 只涵蓋一個 immutable Task contract。
2. **Codex-owned Git integration**：Implementation Agent 不得執行 `git add`、commit、push、merge、rebase、reset 或 stash。
3. **Evidence before commit**：至少要有該 Task 的 fresh focused verification、findings-first Review PASS 與 allowlist diff check。
4. **Checkpoint 不等於 final acceptance**：checkpoint 只能代表 Task 已保存；不能取代 Strict Review、full pytest、Hermes 或 UI acceptance。
5. **Fresh source-bound evidence**：evidence 必須綁定 parent HEAD、Task contract fingerprint、實際 diff fingerprint 與當次 verification output；不得沿用舊 PASS。
6. **Preserve unrelated work**：既有 dirty changes 不屬於本 Task 時必須保留，不可 reset、stash 或混入 checkpoint。
7. **Read-only governance boundary**：Governance Graph、Memory Hub、Memory Sidecar 只能提供 context / projection；不可批准 Task、建立 commit、修改 Git、正式 SQLite、baseline 或 runtime business state。
8. **最小變更**：第一版只標準化 Task checkpoint identity、pre-commit validation、evidence binding 與 rollback guidance，不新增 workflow database、daemon、scheduler、approval state machine 或自動 commit。

## 4. 方案選擇

### Option A：只規範 commit message

改動最少，但無法可靠驗證 Task allowlist、fresh evidence 或 staged unrelated files；不採用作為完整方案。

### Option B：Deterministic checkpoint validator/controller（採用）

由 Codex 在 Git integration 階段呼叫 bounded local validator：檢查 Task contract、parent HEAD、allowlist、diff fingerprint、Review/verification evidence 與 commit trailers，再由 Codex 執行明確授權的 commit。它保留人類授權與現有 Agent 邊界，且可被測試與 rollback。

### Option C：Git hook 或自動 commit

可提高一致性，但會在未明確授權時改變 Git history，且難以理解 Task contract、Review 或 NBS gate；不採用。

## 5. Checkpoint lifecycle

```text
Approved Task contract
        |
        v
Implementation Agent produces allowlisted diff
        |
        v
Fresh focused verification + findings-first Review PASS
        |
        v
Deterministic checkpoint validation
        |
        v
Codex creates one checkpoint commit
        |
        v
Next approved Task or final integration verification
```

### 5.1 Commit 前必要條件

checkpoint commit 只可在以下條件全部成立後建立：

- Task contract 有 immutable `taskId`、目的、allowed files、forbidden surfaces 與 focused verification commands。
- Implementation diff 只包含該 Task allowlist；若有 unrelated dirty changes，必須明確排除且保留在 working tree。
- targeted/focused verification 已在同一 source HEAD/diff context fresh 執行並通過。
- findings-first Review 已針對同一實際 diff 回報 PASS，或所有 findings 已修復並重新 review。
- `git diff --check` 通過。
- 不包含 `.nbs_agent_runtime` 的暫存 evidence、secrets、credentials、SQLite、baseline、logs 或 generated runtime state，除非 Task contract 明確且另有 protected authorization；本 spec 預設不允許。
- checkpoint validation 確認 parent HEAD 仍是預期 SHA；若 HEAD 漂移，停止並重新產生 evidence。

Full pytest、Hermes、UI acceptance 與 final integration review 可在多個 Task checkpoint 之後執行；它們不必每個 checkpoint 重跑，但 final completion 前必須獨立通過。

### 5.2 Commit 後語義

建立 checkpoint 後：

- commit SHA 是該 Task 的 immutable source identity。
- 下一個 Task 必須以該 SHA 作為 parent/source，不得直接沿用舊 branch 或舊 evidence。
- 若後續 full pytest/Hermes fail，checkpoint 保留；狀態標記為 integration blocked，不可 amend 來掩蓋失敗。
- push、PR、merge 仍需另外取得明確授權；checkpoint commit 不代表已推送或已合併。

## 6. Commit identity contract

### 6.1 Subject

固定格式：

```text
checkpoint(task-<taskId>): <imperative summary>
```

規則：

- `<taskId>` 必須來自 approved Task contract，不得臨時創造或重用另一 Task 的 ID。
- summary 使用 imperative English，建議不超過 72 characters。
- 不使用 `WIP`、`misc`、`update`、`fix tests` 等無法識別 Task scope 的 subject。

範例：

```text
checkpoint(task-03): integrate sandbox capability gate
```

### 6.2 Commit body

body 使用固定欄位，內容只放 bounded metadata，不放 secrets、raw logs、完整 prompt、absolute local paths 或內部推理：

```text
Task-ID: task-03
Task-Contract: <sha256>
Scope: integrate sandbox capability gate
Allowed-Files: tests/conftest.py, tests/test_sandbox_pytest_integration.py
Parent-HEAD: <40-hex-sha>
Diff-Fingerprint: <sha256>
Review-Fingerprint: <sha256>
Focused-Verification: focused_sandbox_tests=PASS
Checkpoint-Status: task-saved
Final-Acceptance: pending
Rollback: git revert <commit-sha>
```

`Final-Acceptance: pending` 是必要語義，避免 checkpoint commit 被誤讀為正式完成。

### 6.3 Git trailers

為機器解析，body 結尾使用 Git trailers：

```text
NBS-Checkpoint-Version: 1
NBS-Task-ID: task-03
NBS-Task-Contract: <sha256>
NBS-Parent-HEAD: <40-hex-sha>
NBS-Diff-Fingerprint: <sha256>
NBS-Review-Fingerprint: <sha256>
NBS-Focused-Verification: pass
NBS-Final-Acceptance: pending
```

所有 fingerprint 必須是 lowercase SHA-256。Trailer 不得包含 token、password、API key、credential、absolute path 或 raw command output。

## 7. Checkpoint evidence envelope

Validator 使用的 bounded machine-readable evidence 建議為 `task-checkpoint-evidence-v1`：

```json
{
  "schemaVersion": "task-checkpoint-evidence-v1",
  "taskId": "task-03",
  "taskContractFingerprint": "lowercase-sha256",
  "parentHead": "40-hex-sha",
  "allowedFiles": ["tests/conftest.py"],
  "changedFiles": ["tests/conftest.py"],
  "diffFingerprint": "lowercase-sha256",
  "reviewFingerprint": "lowercase-sha256",
  "focusedVerification": {
    "status": "pass",
    "commandIds": ["sandbox-focused-tests"],
    "evidenceFingerprint": "lowercase-sha256"
  },
  "gitDiffCheck": "pass",
  "generatedAt": "ISO-8601",
  "evidenceFingerprint": "lowercase-sha256"
}
```

Validation rules：

- exact schema、bounded arrays、regular safe relative paths、lowercase fingerprints。
- `changedFiles` 必須是 `allowedFiles` 的 subset；staged diff 與 evidence 必須一致。
- `parentHead` 必須等於 commit 建立前 live `HEAD`。
- `focusedVerification.status` 非 `pass`、Review 缺失、diff mismatch、stale timestamp 或 fingerprint mismatch 時，validator 必須拒絕 commit。
- evidence artifact 只放在 non-authoritative runtime/review input storage，不進正式 SQLite、不改 baseline、不作為 Git commit 的 source of truth。

## 8. Boundary 與責任

| 元件 | 可以做什麼 | 不可以做什麼 |
|---|---|---|
| Implementation Agent | 在 approved Task allowlist 內修改 code/test，輸出 final implementation report | commit、merge、push、approval、下一 Task、SQLite/baseline/business rule |
| Codex Git integration | 驗證 evidence、確認 staged allowlist、在明確授權下建立 checkpoint commit | 把 checkpoint 當 final acceptance；自動 push/merge |
| Review Agent | read-only findings-first review 實際 diff | 修改 code、commit、批准 Task、取代 Hermes |
| Governance Graph | 由 canonical artifacts 產生 read-only projection/lineage | approve、dispatch、commit、改 workflow/Git state |
| Memory Hub / Sidecar | 提供 bounded context hints，失敗時 fallback 到 canonical evidence | 覆蓋 canonical evidence、批准、改 Git、寫 runtime/business state |
| Hermes | read-only runtime、baseline、service、acceptance monitoring | commit、push、merge、修改正式資料或將 pending gate 變成 PASS |

## 9. 失敗與 rollback

### 9.1 Commit 前失敗

若出現以下任一情況，不建立 checkpoint：

- Task contract、Review 或 focused evidence missing/stale。
- staged file 超出 allowlist。
- parent HEAD 漂移。
- `git diff --check` failure。
- secret、credential、SQLite、baseline、runtime artifact 或 forbidden surface 混入。

Codex 應保留未授權的既有 dirty changes，輸出 blocked reason，不能用 reset/checkout/stash 清理現場。

### 9.2 Commit 後 integration failure

若 checkpoint 後 full pytest、Hermes、UI acceptance 或 baseline gate 失敗：

- 保留原 checkpoint SHA 與 failure evidence。
- 將 downstream integration status 標為 `blocked` 或 `protected_incident`，依 risk surface 判定。
- 修復必須建立新的 `checkpoint(task-<same-or-repair-task>): ...` commit；不得修改歷史來消除 failure evidence。
- rollback 預設使用 `git revert <checkpoint-sha>`，並建立新的可追蹤 rollback commit；不得未授權 `reset --hard`。

## 10. Risk 與 non-goals

In scope：

- Task checkpoint commit subject/body/trailer identity。
- allowlist/staged diff/source HEAD/evidence fingerprint binding。
- Codex-owned deterministic validation 與 rollback guidance。
- 與現有 Review、full verification、Hermes、Governance Graph projection 的 evidence lineage 對接。

Out of scope：

- 自動 commit、auto-push、auto-merge、approval、dispatch 或 workflow control。
- 新增 Governance Graph、Memory Hub、Memory Sidecar、agent orchestration 或 workflow database。
- 修改正式 SQLite、revenue scope、GMV、退款規則、baseline `HKD 12,057,968`、export schema 或 business rules。
- 將所有 full pytest/Hermes/UI acceptance 重跑到每個小 Task。
- 用 Git hook、hidden daemon 或 background scheduler 改變 developer workflow。

主要風險與緩解：

| 風險 | 緩解 |
|---|---|
| commit 過度碎片化 | 一個 approved Task 一個 checkpoint；純無行為文件變更可依 contract 判定 no-op |
| 把 checkpoint 當 PASS | 強制 `Checkpoint-Status: task-saved` 與 `Final-Acceptance: pending` |
| unrelated dirty changes 被提交 | staged diff 與 allowlist exact/subset validation |
| 舊 evidence 被重用 | 綁定 parent HEAD、diff fingerprint、generatedAt 與 Review fingerprint |
| 自動 Git mutation | validator 只驗證；commit 仍由 Codex 在明確授權下執行 |
| Graph/Memory 提供錯誤建議 | canonical artifacts 優先；Graph/Memory 永遠 non-authoritative、read-only |

## 11. Acceptance criteria

Implementation 完成後，至少必須能證明：

1. valid Task contract 可產生符合 subject/body/trailer 的 checkpoint metadata。
2. 不同 Task、錯誤 parent HEAD、stale evidence、diff mismatch、allowlist violation 都會被拒絕。
3. unrelated dirty files 不會被 staging 或 checkpoint commit 混入。
4. missing/failed Review 或 focused verification 不會建立 checkpoint。
5. checkpoint metadata 明確保留 `Final-Acceptance: pending`。
6. rollback guidance 使用可追蹤的 revert path，不要求 destructive reset。
7. Governance Graph、Memory Hub、Memory Sidecar 的 read-only/non-authoritative boundary 維持不變。
8. 既有正式收入口徑「不含掛賬核銷與TT退款轉團款」與 2026-05 frozen baseline `HKD 12,057,968` byte/semantic unchanged。
9. checkpoint contract focused tests、full pytest、Hermes、Strict Review 與 UI acceptance 狀態分開報告。

## 12. Proposed minimal implementation surface

第一個 implementation plan 只應包含：

- 一個 provider-neutral checkpoint evidence/validator module。
- 一個 bounded local CLI 或既有 Git integration helper；只驗證，不暗中 commit。
- checkpoint subject/body/trailer formatter 與 exact schema tests。
- allowlist、parent HEAD、diff fingerprint、fresh evidence 與 rollback tests。
- Review/verification/Hermes 文件中的 contract reference，必要時只做 documentation reconciliation。

不應在第一版新增 workflow state machine、Graph node type、Memory provider、UI、database table 或自動化 scheduler。
