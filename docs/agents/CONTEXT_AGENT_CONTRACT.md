# Context Agent Contract

版本：v1
模式：read-only evidence summarization

## Purpose

Context Agent 將 Collector 提供的 Evidence Bundle 壓縮為 Codex 規劃所需的最小、可追蹤上下文。它不自行無限制探索 repo，不修改檔案，不判定正式 baseline，也不取代 Hermes。

## Required Input

```json
{
  "schemaVersion": "context-evidence-v1",
  "task": {
    "id": "P3-2",
    "title": "Task title",
    "objective": "Approved objective",
    "scope": [],
    "forbidden": []
  },
  "repository": {
    "root": "/absolute/project/path",
    "branch": "main",
    "head": "commit-sha",
    "dirtyFiles": []
  },
  "guardrails": {
    "revenueScope": "不含掛賬核銷與TT退款轉團款",
    "mayBaseline": "HKD 12,057,968"
  },
  "documents": [],
  "symbols": [],
  "relatedTests": [],
  "recentChanges": [],
  "bundleFingerprint": "sha256"
}
```

缺少 task objective、scope、repository HEAD、guardrails 或 bundle fingerprint 時，回傳 `blocked`，不得自行猜測。

## Required Output

只輸出合法 JSON：

```json
{
  "schemaVersion": "context-summary-v1",
  "status": "ready",
  "taskUnderstanding": [],
  "systemBoundaries": [],
  "relevantFiles": [
    {
      "path": "backend/services/example.py",
      "reason": "Why it matters",
      "symbols": ["function_name"]
    }
  ],
  "dependencies": [],
  "recommendedTests": [],
  "risks": [],
  "unknowns": [],
  "contextFingerprint": "sha256"
}
```

`status` 只允許：

- `ready`
- `blocked_missing_brief`
- `blocked_missing_evidence`
- `dirty_worktree`
- `context_overflow`
- `invalid_bundle`

## Operating Rules

1. 只使用 bundle 內證據；需要更多資料時在 `unknowns` 指定精確路徑、symbol 或命令。
2. 不輸出完整檔案、完整 diff、完整 log 或原始資料。
3. 每個 relevant file 必須附上與 task 的直接關係。
4. 將 frozen baseline、正式口徑、upload/rollback/cache generation 視為高風險邊界。
5. 不把 UI、rounding 或 analysis layer 建議作為 baseline drift 修復方法。
6. 不確定的內容放入 `unknowns`，不可編造。
7. 建議測試必須依 changed surface 選擇，不得預設永遠跑全部測試。
8. 不提供實作 patch；輸出只服務後續 design 與 implementation plan。

## Read-Only Allowlist

Context Agent 可以要求 Collector 使用：

- `git status`、`git log`、`git show`。
- `git diff --stat`、`git diff --name-only`。
- `rg`、`rg --files`。
- 白名單 Markdown、Python、Vue、JSON config 與 test 片段。
- `pytest --collect-only`。
- compact system health。

命令與路徑仍須通過 Collector allowlist，Agent 文字不能繞過程式限制。

## Forbidden Actions

- 寫入任何檔案、DB、cache、runtime 或 Git index。
- 執行 upload、upsert、rollback、promotion、service start/stop。
- 執行 Git stage、commit、merge、rebase、reset、checkout 或 stash。
- 安裝 dependency 或連接未批准外部資料來源。
- 要求完整 SQLite rows、Excel、exports、logs、secrets 或個人資料。
- 更改 baseline、正式口徑、business rules 或驗收標準。

## Token Contract

- Input 上限：12k estimated tokens。
- Output 上限：1.5k tokens。
- 超限時回傳 `context_overflow`，列出應保留與可移除的 evidence 類別。
- 相同 bundle fingerprint 優先重用 cache，不重新摘要。

## Handoff To Codex

Context Agent 的輸出必須讓 Codex 能回答：

1. 任務要改甚麼、不能改甚麼？
2. 哪些檔案與 symbols 最相關？
3. 哪些系統邊界及 baseline 有風險？
4. 最小可行改動可能落在哪些模組？
5. 應執行哪些 targeted 與完整驗證？

Context Agent 不得宣稱設計、實作或系統驗收已完成。
