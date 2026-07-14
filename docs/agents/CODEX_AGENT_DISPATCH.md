# Codex Agent Dispatch Contract

版本：v1
狀態：implementation_in_progress

## 文件目的

本文件定義 Codex 何時收集 Context、何時要求 Review，以及兩者與 Hermes 的邊界。它是工作流程契約，不會在 NBS Analytics application runtime 內自動執行 Agent，也不會授權任何寫入、修復、commit 或 merge。

## 人類可讀流程

Codex 接到任務後，先判斷是否符合下方 Context 條件。符合時，先執行 `scripts/context_agent.py --collect-only`，將 compact bundle 作為目前任務的證據輸入；Context Agent 只做 read-only evidence summarization。

完成一個 implementation Task，或準備 commit、merge、交給 Hermes 前，Codex 應依 Review 條件收集 diff 與驗證證據，交由 Review Agent 做 findings-first review。Review PASS 只代表可以進入完整驗證與 Hermes 驗收，不代表正式系統已完成。

若環境或使用者明確配置了已批准的 runner，Codex 才可使用 `--agent-command` 將 bundle 交給該 runner。未明確配置時不得自行選擇外部模型或命令。Collector、Context Agent 與 Review Agent 均不得修改 SQLite、baseline、runtime、Git 或程式碼；Hermes 仍負責正式服務、資料庫完整性、baseline、runtime 與整體驗收。

## Machine-readable dispatch rules

```json
{
  "schemaVersion": "codex-agent-dispatch-v1",
  "context": {
    "anyOf": {"changedCodeFilesGte": 2, "requiresImplementationPlan": true, "hasApprovedBrief": true},
    "riskSurfaces": ["upload", "sqlite", "baseline", "rollback", "cache", "api_contract", "export"],
    "skipFor": ["single_line_typo", "markdown_spelling", "read_only_explanation", "valid_fingerprint_cache_hit"]
  },
  "review": {
    "onFileTypes": [".py", ".vue", ".js", ".mjs", ".sql", ".json"],
    "onCrossModuleDiff": true,
    "riskSurfaces": ["revenue", "baseline", "business_rules", "upload", "export"],
    "before": ["commit", "merge", "hermes"],
    "skipFor": ["verified_document_backfill", "git_metadata", "format_only_without_behavior_change"]
  }
}
```

## 執行邊界

- `--collect-only` 不調用 LLM，只產生受白名單限制的 bundle。
- `--agent-command` 只可使用使用者或環境明確批准的 runner。
- Agent 輸出不能取代 Codex 的規劃、使用者授權、完整驗證或 Hermes acceptance。
- Hermes 邊界以 `NBS_HERMES_MONITORING.md` 為準，不與 Review Agent 重複。
