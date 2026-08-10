# NBS Runner Capability Evidence：獨立驗證通道 Design Spec

## Status

- Status: Draft for review
- Date: 2026-08-08
- Scope: Task 0B runner capability evidence before Memory Sidecar Task 5
- Related plan: `docs/superpowers/plans/2026-08-07-nbs-agent-memory-hermes-deepseek-integration.md`
- Related contract: `docs/agents/MEMORY_SIDECAR_CONTRACT.md`

## 1. Goal

在已建立 immutable Git head 後，透過獨立、只讀、可重現的 evidence channel，證明受控 runner 的 live model identity、workspace／scope boundary、recall-on 第二次執行與 token reduction。這個通道只回答「runner capability 是否可被證明」，不直接宣稱 Task 5 A/B acceptance 通過。

## 2. Non-goals

本 Task 不會：

- 修改 production code、canonical artifacts、SQLite、baseline、revenue scope、business rules、export schema、Governance Graph 或 approval／dispatch state。
- 自動啟用 `recall_enabled` 或修改 `writer_enabled=false`、`shadow_mode=true` 的安全預設。
- 將模型名稱、設定檔或 UI 顯示文字單獨當成 live execution proof。
- 將 runner output 當成 Review PASS、Hermes PASS 或 canonical evidence。
- 執行 `scripts/hermes_post_change_check.py`；Hermes post-change acceptance 仍是後續獨立 gate。

## 3. Design alternatives

### A. Manual evidence note

只保存操作者填寫的模型、workspace 和 token 數字。成本最低，但無法可靠驗證 run identity、第二次執行、cache replay 或 fingerprint，一律不足以支援 Task 5。

### B. Bounded runner evidence validator（採用）

由 Hermes desktop 執行兩次受控 run，再由本地 deterministic validator 驗證 bounded JSON evidence。Validator 不呼叫 Hermes、不執行 shell、不寫正式資料，只檢查 immutable head、task／brief fingerprints、live identity、cohort、run sequence、cache/replay metadata、token metrics、provenance 與安全旗標。這能以最小改動補足 Task 0 與 Task 5 之間的證據缺口。

### C. 直接把 capability check 納入 Task 5

讓 Task 5 自己同時驗證 runner、執行 A/B 與判定 acceptance。這會把 runner failure、A/B metric failure 和 integration failure 混在一起，難以重跑、審計與定位，因此不採用。

## 4. Scope and authority boundary

### 4.1 Immutable input

每個 evidence pair 必須綁定：

- immutable 40-character `gitHead`；不得使用 branch name 取代 SHA；
- `projectId`、bounded `workspaceKind`（`repo` 或 `isolated_worktree`）與 workspace fingerprint；
- task brief fingerprint、allowed-files fingerprint、commands fingerprint；
- runner provider identity `hermes` 與 live model identity `deepseek-v4-flash`；
- A/B 共用上述所有欄位，唯一允許差異是 `recallMode`。

### 4.2 Evidence-only output

Evidence schema 固定為 `runner-capability-evidence-v1`，只允許 bounded metadata：

```json
{
  "schemaVersion": "runner-capability-evidence-v1",
  "evidenceId": "sha256",
  "gitHead": "40-char-sha",
  "projectId": "nbs_analytics",
  "workspaceKind": "repo",
  "taskFingerprint": "sha256",
  "briefFingerprint": "sha256",
  "allowedFilesFingerprint": "sha256",
  "commandsFingerprint": "sha256",
  "provider": "hermes",
  "model": "deepseek-v4-flash",
  "control": {"runId": "...", "sequence": 1, "recallMode": "off", "status": "completed"},
  "treatment": {"runId": "...", "sequence": 2, "recallMode": "on", "status": "completed"},
  "comparison": {
    "sameImmutableInputs": true,
    "distinctRunIds": true,
    "cacheReplayDetected": false,
    "tokenReductionRatio": 0.0,
    "alternativeEvidence": false
  },
  "provenance": {"coverage": 1.0, "sensitiveCaptureCount": 0},
  "latency": {"p95Ms": 0},
  "result": "ready"
}
```

Raw prompt、raw model output、credentials、runner command、absolute paths、full logs、customer data 和原始 hints 不得進入 schema。

## 5. Two-run protocol

1. **Control run（sequence=1）**：同一 immutable head、brief、scope、commands，`recallMode=off`。
2. **Treatment run（sequence=2）**：重建獨立 runner session，所有 immutable inputs 完全相同，唯一差異是 `recallMode=on`；writer 仍 disabled。
3. `runId`、run fingerprint 與 completion evidence 必須不同；`cacheReplayDetected=true`、缺少 completion 或無法證明 recall-on 實際生效時，結果為 `blocked_runner_capability`。
4. Validator 必須拒絕 branch-only identity、縮寫 SHA、不同 task／brief／scope／commands、重用同一 run ID 或把設定檔 model name 冒充 live identity。

## 6. Acceptance states and metrics

### 6.1 `ready`

只有以下條件全部成立才可輸出 `ready`：

- live provider/model identity 明確為 `hermes`／`deepseek-v4-flash`；
- workspace、project、immutable head、task／brief／scope／commands 全部一致；
- control 完成且 treatment 是獨立第二次 recall-on run；
- `cacheReplayDetected=false`；
- token usage 可由 runner evidence 驗證，`tokenReductionRatio >= 0.20`，或 evidence 明確標示並支持 approved alternative evidence；
- provenance coverage = `1.0`、sensitive capture = `0`；
- p95 latency `<= 800 ms`；
- writer disabled、baseline/formal scope unchanged、Review/Hermes no-regression flags 均為 true。

### 6.2 `blocked_runner_capability`

用於能力不可證明的情況，包括 live identity 缺失、workspace／scope 不明、treatment 未完成、recall-on 未生效、cache replay 無法排除、token usage 缺失或 evidence fingerprint 不一致。此狀態不得轉換成 acceptance PASS。

### 6.3 `acceptance_rejected`

Runner capability 已被真實證明，但 metrics 未達門檻，例如 token reduction < 20%、coverage 不足、p95 超過 800 ms 或 sensitive capture 非零。此狀態仍保持 recall-off，不自動 rollout。

## 7. Implementation boundary

第一版只新增：

- deterministic evidence model／validator；
- bounded CLI，用於讀取兩份 runner result、產生 `runner-capability-evidence-v1`；
- schema、fingerprint、sequence、cache replay、metric threshold 與 negative-path tests；
- Task 5 消費此 evidence 的 contract wording／fixture（如需要）。

CLI 不得啟動 Hermes、選擇模型、修改 UI、執行 Git write、呼叫 network 或改變 recall flag。Hermes desktop 由操作者或既有受控流程執行，validator 只消費其 bounded result。

## 8. Testing and acceptance

- schema round-trip、canonical fingerprint、bounded field、raw-content rejection。
- 同 head／不同 cohort 可通過；不同 head、brief、scope、commands、provider/model、sequence、run ID、cache replay 必須 fail closed。
- 缺 token usage、recall-on 未生效、第二次 run 缺失、sensitive capture、coverage < 1、p95 > 800、reduction < 20% 的狀態分類正確。
- CLI 只讀取 allowlisted evidence paths，拒絕 symlink、絕對路徑、過大 JSON 和未知欄位。
- focused pytest、py_compile、git diff check；不執行 Hermes post-change check。

## 9. Relationship to Task 5

Task 5 只能消費 `result=ready` 的 capability evidence，並在自己的 `memory-sidecar-ab-acceptance-v1` 中重新綁定 immutable inputs 與 A/B metrics。若本 Task 輸出 `blocked_runner_capability` 或 `acceptance_rejected`，Task 5 不得重用舊 evidence、猜測 token reduction 或開啟 recall。

## 10. Rollback and retention

Rollback 只需丟棄本次 bounded evidence 並維持 `recall_enabled=false`。Evidence 可保留於 `.nbs_agent_runtime/runs/<run-id>/` 的 ignored runtime path；不得寫入正式 SQLite、canonical artifacts、Obsidian 或 Governance Graph snapshot。
