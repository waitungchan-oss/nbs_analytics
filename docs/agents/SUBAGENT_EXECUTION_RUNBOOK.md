# Subagent Execution Runbook

版本：v1
狀態：active

## 目的

本文件約束 Implementation Agent plan 的 Task 5 至 Task 8 執行方式，避免 subagent 已完成程式與測試後，仍長時間停留在 report、commit、Hermes 或舊 artifact 整理階段。

本流程只優化 Codex / subagent 協作，不改正式營收口徑、SQLite、baseline、upload、rollback、export schema 或 Hermes 權限。

## 已確認根因

1. `.superpowers/sdd/task-1-report.md` 與 `.superpowers/sdd/progress.md` 在 branch base 已屬於先前 P0 Upload Single-Writer 任務。重用固定檔名造成舊報告被保留、ledger 被覆蓋及額外 review 噪音。
2. implementation worktree 沒有 `.venv/bin/python`；正確 interpreter 位於主 repo `/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python`。相對命令會先失敗，再觸發不必要的環境診斷。
3. per-Task prompt 同時要求實作、廣泛 regression、report、兩次 commit、Hermes 或完整 acceptance。實作與 focused tests 已完成後，agent 仍會繼續擴張驗證範圍，形成收尾長尾。
4. completed agent 未立即 `close_agent`，曾直接觸發 `agent thread limit reached`，阻擋下一個 implementer 或 reviewer。
5. 多次短 `wait_agent` 疊加只能顯示 `timed_out`，不能證明 agent 卡死，也增加 controller 噪音。真正的 stuck 判定應基於檔案、Git commit 或 report mtime 是否持續推進。

## Task Artifact Namespace

Task 5 起只使用以下 ignored runtime 路徑：

```text
.superpowers/sdd/implementation-agent/
  task-5-brief.md
  task-5-report.md
  task-5-review.md
  task-6-brief.md
  ...
  progress.md
```

不得再寫入或覆蓋下列先前任務 artifact：

```text
.superpowers/sdd/progress.md
.superpowers/sdd/task-1-report.md
.superpowers/sdd/task-2-report.md
.superpowers/sdd/task-3-report.md
.superpowers/sdd/task-4-report.md
```

Runtime brief、report、review 與 progress 不 commit。正式設計與驗收證據只回填至 `docs/agents/`、implementation plan 或 system map。

## Optimized Per-Task Protocol

### 1. Implementer

Implementer 只負責：

- 讀取一份 unique Task brief。
- 先跑 brief 指定 RED。
- 修改 Task 白名單檔案。
- 跑明確列出的 focused GREEN 與最小 regression。
- 建立一個 source/tests commit。
- 回覆 `status`、commit SHA、精簡測試結果與 concerns。

Implementer 不負責：

- Hermes、system acceptance、full pytest、服務啟停。
- report commit、review 文件、ledger 更新。
- 自行擴張「必要測試」或開始下一個 Task。

### 2. Controller

Codex controller 在 implementer 回報後：

- 檢查 `git show --stat` 與 worktree status。
- fresh rerun Task focused tests。
- 將 deterministic evidence 寫入 ignored `task-N-report.md`。
- 以 dispatch 前 base SHA 產生 review package。
- implementer 一旦 completed 立即 `close_agent`。

### 3. Reviewer

Reviewer 只讀 Task brief、runtime report 與 review package：

- findings-first 檢查 spec 與 code quality。
- 只跑一組明確 targeted tests，不重跑 implementer 的所有命令。
- 寫入 ignored `task-N-review.md`。
- 回覆 verdict 後立即關閉。

Critical / Important finding 由一個 fix agent 一次處理。Fix agent 只修改 finding 涵蓋檔案、跑 covering tests、建立一個 fix commit；report 與 ledger 仍由 controller 負責。

## Wait And Stuck Policy

```text
dispatch
  -> wait once, at most 60 seconds
  -> inspect Git status, commit log, report mtime
  -> if state changed: continue waiting without interruption
  -> if no state change for 120 seconds: send one concise status request
  -> if no state change for 180 seconds after request: close and reassign a bounded finisher
```

不得因單次 `wait_agent timed_out` 就判定 stuck。不得同時保留多個 unresolved wait cells。

## Exact Command Boundary

Per-Task Python 命令一律使用：

```text
/Users/chanwaitung2025/Downloads/nbs_analytics/.venv/bin/python
```

Task 5 至 Task 7 只跑 plan 列出的 focused tests、必要 py_compile 與 `git diff --check`。Full pytest、system acceptance、Hermes 與 frozen baseline 驗證集中在 Task 8，避免每個 Task 重複支付相同時間與 Token。

## Completion Gate

Task 只有在下列條件成立才記為 complete：

- source/tests commit 存在，且只包含 Task 白名單。
- controller fresh focused tests PASS。
- reviewer `No Critical or Important findings`。
- fix finding 時已 re-review PASS。
- worktree clean，Git index 未被 subagent 修改。
- unique runtime ledger 已更新。

以上 gate 不取代 Task 8 的 full verification 與 Hermes final acceptance。
