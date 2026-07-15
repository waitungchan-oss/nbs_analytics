# Agent Orchestrator Phase 1 Design

狀態：approved for implementation planning  
日期：2026-07-15  
範圍：CLI orchestration、run status、retention、macOS desktop notification

## 1. 目的

建立一個本地、非長駐的 Agent Orchestrator，將既有 Context Agent、Implementation Agent、Review Agent、完整驗證與 Hermes 串成可追蹤流程。Orchestrator 保存精簡 run evidence，並在需要使用者注意時提供 macOS 桌面通知。

第一階段不建立 Streamlit Agent Operations 頁面；該頁面會在第二階段以 read-only 方式讀取本設計產生的 run artifacts。

## 2. 已批准流程

```text
Obsidian Brief
  -> agent_workflow.py run
  -> Context Agent
  -> awaiting_authorization
  -> Codex 根據 Context 完成 plan / Task contract
  -> 使用者明確授權
  -> agent_workflow.py approve --run-id ... --contract ...
  -> Implementation Agent（單一 Task）
  -> Targeted verification evidence
  -> Review Agent
  -> Full verification
  -> Hermes read-only acceptance
  -> completed / changes_required / blocked / failed
```

`run` 永遠不執行 Implementation。只有 `approve` 才能綁定已批准 Task contract 並進入修改階段。

## 3. 採用方案

採用單次 CLI process + filesystem state store：

- 不建立 daemon、queue server、FastAPI endpoint 或 background worker。
- 每個 command 執行完成後 process 結束；狀態由 JSON artifacts 延續。
- 透過 per-run file lock 防止兩個 `approve` 同時執行。
- 透過 atomic replace 寫入 status、approval 與報告。
- 通知是 adapter；通知失敗只記錄 warning，不改變 workflow result。

這個方案延續現有 Agent CLI 與 `.nbs_agent_runtime/` 邊界，也讓第二階段 Streamlit UI 可以只讀同一真相來源。

## 4. CLI

```bash
.venv/bin/python scripts/agent_workflow.py run \
  --brief docs/briefs/<brief>.md \
  [--context-agent-command "<approved-command>"] \
  [--no-notify]

.venv/bin/python scripts/agent_workflow.py approve \
  --run-id <run-id> \
  --contract .nbs_agent_runtime/contracts/<task>.json \
  --implementation-agent-command "<approved-command>" \
  --review-agent-command "<approved-command>" \
  [--no-notify]

.venv/bin/python scripts/agent_workflow.py status --run-id <run-id>
.venv/bin/python scripts/agent_workflow.py list --limit 20
.venv/bin/python scripts/agent_workflow.py prune [--dry-run]
```

Agent command 字串只在目前 process 記憶體中使用，不寫入 manifest、events 或 telemetry。Orchestrator 不自行選擇模型、runner 或外部服務。

Exit codes：

| Code | 意義 |
|---:|---|
| 0 | run 已等待授權、status/list/prune 成功，或完整流程 completed |
| 1 | Review `changes_required` |
| 2 | blocked、授權失效、服務未 ready 或必要 evidence 缺失 |
| 4 | Agent output / context overflow |
| 5 | Orchestrator runtime 或 schema error |

## 5. Run 檔案結構

```text
.nbs_agent_runtime/runs/<run-id>/
  manifest.json
  status.json
  approval.json
  context.json
  implementation.json
  targeted-verification.json
  review.json
  full-verification.json
  hermes.json
  events.jsonl
  .lock
```

- `manifest.json`：建立 run 時一次寫入，包含 schema version、run ID、Brief identity、Git branch/HEAD、dirty-file identity 與建立時間。
- `approval.json`：`approve` 時一次寫入，包含 Task contract path identity、contract fingerprint、approved base SHA、批准時間與授權狀態；不保存 runner command。
- `status.json`：目前狀態的 materialized view。
- `events.jsonl`：append-only 狀態轉換與 warning metadata。
- 各 stage JSON：現有 Agent report 或受限 verification/Hermes 摘要。
- `.lock`：只作 advisory lock，不包含 secrets。

所有路徑都必須 lexical + resolved 保持在 `.nbs_agent_runtime/runs/<run-id>/`。Symlink parent、symlink target、非 regular artifact 一律 fail closed。

## 6. Schema 與狀態機

`status.json` 固定欄位：

```json
{
  "schemaVersion": "agent-workflow-status-v1",
  "runId": "uuid-hex",
  "stage": "authorization",
  "status": "awaiting_authorization",
  "startedAt": "ISO-8601",
  "updatedAt": "ISO-8601",
  "completedAt": null,
  "message": "Context ready; explicit authorization required",
  "errorCode": null,
  "artifactBytes": 0
}
```

允許狀態：

```text
created
context_running
awaiting_authorization
implementation_running
targeted_verification_running
review_running
changes_required
full_verification_running
hermes_running
completed
blocked
failed
```

只有以下主要轉換合法：

```text
created -> context_running -> awaiting_authorization
awaiting_authorization -> implementation_running
implementation_running -> targeted_verification_running
targeted_verification_running -> review_running
review_running -> changes_required
review_running -> full_verification_running
full_verification_running -> hermes_running
hermes_running -> completed
任何 running state -> blocked | failed
```

Terminal states 為 `completed`、`changes_required`、`blocked`、`failed`。已 terminal 的 run 不可再次 approve。

## 7. 授權與 fingerprint

`run` 保存：

- Brief relative path + SHA-256。
- Git branch、HEAD。
- dirty files 的 relative path + content identity；不保存完整 patch。
- Context bundle fingerprint。

`approve` 必須驗證：

1. run 仍是 `awaiting_authorization`。
2. Brief SHA-256 未變。
3. Git branch、HEAD 與 dirty-file identity 未變。
4. contract `approvedWorktree` 是目前 worktree。
5. contract `approvedBaseSha` 與 run HEAD 一致。
6. plan fingerprint、contract fingerprint 與 schema 合法。
7. Implementation 與 Review runner 均由命令列明確提供且通過既有 executable allowlist。

任一 identity 改變即 `blocked_authorization_stale`，必須建立新 run 或重新收集 Context；Orchestrator 不自動更新授權。

## 8. Stage 執行

### Context

- `run` 呼叫現有 `scripts/context_agent.py`。
- 未提供 runner 時採 `--collect-only`；提供 runner 時產生 Context summary。
- Context stage 成功後必定停在 `awaiting_authorization`。

### Implementation

- `approve` 呼叫現有 `scripts/implementation_agent.py`。
- 只可執行 contract 中一個 Task。
- Orchestrator 不建立或修改 allowed write paths，不代替 Implementation sandbox。

### Targeted verification 與 Review

- 從 Implementation report 取出已執行的 RED/GREEN/validation evidence，正規化成 Review CLI 接受的 `commands` schema。
- 呼叫 `scripts/review_agent.py --strict`。
- Review `changes_required` 立即成為 terminal state，通知使用者並停止。

### Full verification

Review PASS 後執行固定 profile：

1. `.venv/bin/python -m pytest -q`
2. `.venv/bin/python scripts/system_manager.py acceptance`

命令必須使用 exact argv、`shell=False`、timeout 與 stdout/stderr tail cap。Orchestrator 不啟停服務；服務未 ready 時結果為 blocked。

### Hermes

Full verification PASS 後執行：

```bash
.venv/bin/python scripts/hermes_post_change_check.py --skip-monitor --json
```

只有 Hermes `overallStatus=pass` 才可進入 `completed`。

## 9. macOS 桌面通知

預設在 macOS 啟用，使用 exact executable `/usr/bin/osascript`、`shell=False`。其他平台自動降級為 no-op warning，不阻塞流程。

通知節點：

- Context ready / awaiting authorization。
- Implementation completed。
- Review changes required。
- Workflow blocked 或 failed。
- Hermes PASS / completed。
- Hermes FAIL。

通知只包含 `runId` 短碼、stage 與短訊息；不得包含 Brief 全文、runner command、絕對路徑、測試完整輸出、環境值或 secrets。`--no-notify` 只停用桌面通知，不停用 events/status。

## 10. Artifact 大小控制

- 每個 command 只保存 stdout/stderr 最後 12,000 characters。
- 單一 stage JSON 最大 5 MiB；超限先裁剪非必要輸出，再保存 `truncated=true`。
- 單一 run 的非 metadata artifact soft cap 為 25 MiB；超限時 workflow 繼續，但寫入 `artifact_size_warning` event。
- 永不複製 SQLite、Excel、exports、完整 logs、完整 prompt 或 repo source snapshot。
- Status 內保存目前 artifact bytes，供第二階段 UI 顯示。

正常 run 預期約 100 KiB–1 MiB；這是設計估計，正式成效由 telemetry 實測。

## 11. 保留策略

Retention 只管理 `.nbs_agent_runtime/runs/`，不得讀寫 `.nbs_runtime/`、SQLite、Hermes evidence、backups、quarantine 或 exports。

規則：

1. 非 terminal run 永不自動清理。
2. 所有 90 天內 run 完整保留。
3. 最近 30 個 terminal run 永遠完整保留，即使超過 90 天。
4. 超過 90 天且不在最近 30 個的 `completed` run，可刪除 stage reports，只保留 `manifest.json`、`status.json`、`approval.json` 與 compact `archive-summary.json`。
5. `blocked`、`failed`、`changes_required` 或 contract 含任何 risk surface 的 run，metadata 永久保留；超過 90 天後只可裁剪 stage reports，不可刪除 manifest、status、approval、events summary 與 error code。
6. `.lock` 顯示仍被持有、status 非 terminal、schema 不明或 artifact 路徑異常時跳過並記錄 warning。
7. `prune --dry-run` 必須列出候選、預計釋放 bytes 與保留理由，不做寫入。
8. 自動 housekeeping 只在 `run` 開始前與 terminal state 寫入後執行；任何清理失敗不改變 workflow result。

第一階段不使用 gzip，讓第二階段 Streamlit 可以直接讀 JSON；compact summary 取代壓縮檔。

## 12. Telemetry

每次 run 聚合：

- 各 stage durationMs。
- Context、Implementation、Review estimated input/output tokens。
- Context/Review cache hit。
- Implementation changedFiles、diffLines、repair loops。
- targeted/full verification 結果。
- Hermes result。
- artifact bytes 與 retention outcome。

Telemetry 不保存 prompt、runner command、原始資料或 secrets。首批 3–5 個真實任務只用於建立基線，不宣稱已達成 50%–75% Token 降幅。

## 13. 錯誤處理與恢復

- Agent CLI 非零 exit：保存精簡 report，轉成 `changes_required`、`blocked` 或 `failed`。
- invalid JSON/schema：`failed_invalid_stage_output`。
- per-run lock 已持有：exit 2，不改 status。
- status/event 寫入採 private temp file + fsync + atomic replace；events append 先 lock。
- process 中斷後，running state 保留。Phase 1 提供 `status` 顯示 stale running，不自動 resume 或 rollback。
- 通知失敗：event warning，workflow 繼續。
- Full verification 或 Hermes 失敗：保留現場，不 commit、不 merge、不 rollback。

## 14. 安全與治理邊界

- Orchestrator 不修改正式 SQLite、baseline、revenue scope、business rules 或 exports。
- Orchestrator 不執行 upload、rollback、promotion、service start/stop、dependency install。
- Orchestrator 不執行 Git add、commit、merge、push、reset、checkout 或 stash。
- Context/Review Agent 保持 read-only。
- Implementation Agent 仍受 approved Task、worktree、sandbox、write allowlist 與 diff limits 約束。
- Review PASS 仍不等於正式完成；Full verification 與 Hermes 必須通過。
- 所有 runner 必須由使用者或環境明確提供；不得自動選擇外部模型。

## 15. 模組邊界

```text
backend/agents/workflow_models.py
  schemas, statuses, transitions, fingerprints

backend/agents/workflow_store.py
  safe paths, atomic JSON, events, locks, artifact accounting

backend/agents/workflow_retention.py
  retention decisions, dry-run, compaction

backend/agents/workflow_notifications.py
  notifier protocol, macOS adapter, no-op adapter

backend/agents/workflow_orchestrator.py
  stage sequencing and existing CLI adapters

scripts/agent_workflow.py
  argparse, JSON output, exit codes
```

Orchestrator 核心不得放入 `app.py`、Streamlit pages、FastAPI routers、Hermes script 或現有三個 Agent service。

## 16. 驗收

至少驗證：

- legal/illegal state transitions。
- atomic status/event writes 與 symlink/path escape rejection。
- `run` 必定停在 `awaiting_authorization`。
- stale Brief/HEAD/dirty identity/contract 阻擋 approve。
- concurrent approve lock。
- Agent command 不落盤、notification 不洩漏敏感內容。
- macOS notification success/failure/no-op。
- Review changes required、full verification fail、Hermes fail/pass 狀態。
- retention 90 天、最近 30、active 保留、高風險 metadata 永久保留、dry-run。
- artifact output cap 與 size warning。
- Context/Review/Implementation 既有 contract tests。
- Full pytest、system acceptance、Hermes、正式口徑及 2026-05 baseline 保持通過。

## 17. 明確非目標

- Streamlit Agent Operations UI。
- 常駐 daemon、scheduler、queue 或 remote API。
- 自動重試、resume、rollback、commit、merge 或 push。
- 多 Task 自動分拆或自行批准下一 Task。
- 自動啟停服務或安裝 dependency。
- 向量資料庫、完整 prompt archive 或原始資料保存。
