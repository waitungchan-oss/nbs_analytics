# Codex Agent Dispatch Contract

版本：v1
狀態：active

## 文件目的

本文件定義 Codex 何時收集 Context、何時要求 Review、何時分派 Implementation Agent，以及三者與 Hermes 的邊界。它是工作流程契約，不會在 NBS Analytics application runtime 內自動執行 Agent。

目前分派由 Codex 依本契約逐步呼叫三個 Agent CLI；Phase 1 已提供 `scripts/agent_workflow.py` 本地 orchestrator CLI 與可選 macOS notification。它只編排既有狀態，不得放寬人工授權、sandbox、Review、完整驗證或 Hermes gate。Streamlit Agent Operations 是現行 read-only view，讀取 `agent-operations-snapshot-v1`，不得成為 dispatch、approval 或 retention 寫入入口。

## 人類可讀流程

Codex 接到任務後，先判斷是否符合下方 Context 條件。符合時，先執行 `scripts/context_agent.py --collect-only`，將 compact bundle 作為目前任務的證據輸入；Context Agent 只做 read-only evidence summarization。

Codex 建立並批准 implementation Task contract 後，只可分派一個 Task。Implementation Agent 不得自行決定下一 Task。Codex 檢查 final implementation report 與實際 diff，交由 Review Agent 做 findings-first review，處理 findings，完成完整驗證，最後呼叫 Hermes。Review PASS 只代表可以進入完整驗證與 Hermes 驗收，不代表正式系統已完成。

### Documentation dispatch

只有在功能變更已通過 Review、full verification 與 Hermes PASS 後，Codex 才可對同一 completed run 呼叫 `agent_workflow.py document`。純 typo、format-only、generated evidence 或 classifier 判定沒有文件影響的測試變更直接 skip，不調用 LLM。按需文件 backfill 必須指定 completed run ID；未提供 approved Documentation runner 時必須停止為 `blocked_missing_runner`，不得由主 Codex LLM 靜默代寫。

Documentation Agent 只讀 `documentation-evidence-v1` 並輸出 `documentation-proposal-v1`。它不 apply；`system map` 與 `ADR` 需要明確 target approval，Brief backfill 也只能由 Codex 依既有授權交給 trusted Controller。任何 sidecar、Operations 或 Hermes check 都不得 auto-apply、批准 targets、改變 Hermes/terminal state、寫入 SQLite、baseline、runtime、Git 或 Obsidian。

### Verified documentation backfill

按需 backfill 必須依 `docs/agents/VERIFIED_DOCUMENTATION_BACKFILL.md` 的單一路徑執行：backfill create -> proposal -> preview -> Review PASS -> `--apply-brief --approve-target system_map` -> Hermes。temporary vault 只可 local-only 使用；未提供 `--approve-target system_map` 時，System Map 必須保持 byte-identical。Review Agent 是 findings-first read-only review，Hermes 是最後的 read-only acceptance gate；兩者不可互相取代，且都不得執行 preview/apply、批准 target、寫 vault 或改變 terminal state。serialized application records 必須只含 vault-relative identity，不得含 vault absolute path。

Phase 1 CLI 的 `run`（或 `start`）只執行 Context collection 並回傳 `awaiting_authorization`；沒有任何 implicit approval。`approve` 必須逐次提供 run ID、approved contract、Implementation runner 和 Review runner，這些 command 不會寫入 run artifact。`status` / `list` 僅讀取 artifact；`run` 後的 best-effort housekeeping 及 `prune --apply` 都依既有 retention policy compact 合資格的已完成 run，`prune --dry-run` 只計畫而不寫入。`--no-notify` 可停用通知；通知失敗只記錄 warning。

若環境或使用者明確配置了已批准的 runner，Codex 才可使用 `--agent-command` 將 bundle 交給該 runner。未明確配置時不得自行選擇外部模型或命令。Collector、Context Agent 與 Review Agent 均不得修改 SQLite、baseline、runtime、Git 或程式碼；Hermes 仍負責正式服務、資料庫完整性、baseline、runtime 與整體驗收。

Agent Operations 只讀 Phase 1 artifacts，不是第二個 source of truth。UI 僅支援「手動重新整理」session-scoped snapshot，且不清除 dashboard caches；不得批准、執行、停止、刪除或 prune workflow。Token usage 僅在 supplied 時顯示，否則顯示 `未提供`。

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
  },
  "implementation": {
    "requiresApprovedPlan": true,
    "requiresExplicitAuthorization": true,
    "requiresIsolatedWorktree": true,
    "requiredBranchPrefix": "codex/",
    "maxTasksPerRun": 1,
    "allowedTaskTypes": ["behavior", "refactor", "test", "documentation", "configuration"],
    "deniedRiskSurfaces": ["upload", "sqlite", "baseline", "rollback", "revenue", "business_rules", "export_schema"],
    "after": ["review_agent", "full_verification", "hermes"],
    "never": ["commit", "merge", "push", "service_management", "dependency_install"]
  },
  "documentation": {
    "triggerAfter": ["review_pass", "full_verification_pass", "hermes_pass"],
    "inputSchema": "documentation-evidence-v1",
    "outputSchema": "documentation-proposal-v1",
    "requiresApprovedRunner": true,
    "missingRunnerStatus": "blocked_missing_runner",
    "noDocumentationDecision": "deterministic_classifier_only",
    "requiresExplicitTargetApproval": ["system_map", "adr"],
    "never": ["auto_apply", "approve_targets", "main_codex_llm_fallback", "hermes_or_terminal_state_change", "sqlite_write", "baseline_change", "runtime_write"]
  }
}
```

## 執行邊界

- `--collect-only` 不調用 LLM，只產生受白名單限制的 bundle。
- `--agent-command` 只可使用使用者或環境明確批准的 runner。
- Agent 輸出不能取代 Codex 的規劃、使用者授權、完整驗證或 Hermes acceptance。
- Implementation Agent 的完整約束見 `docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md`；它只執行已批准的單一 Task，Codex 保留下一 Task 的決定權。
- Production Implementation dispatch 必須經 `scripts/implementation_agent.py` 的 contract-aware macOS staging sandbox：offline coding worker 只讀 disposable tracked-files copy、無 network，完成後由可信任 Controller 原子套用核准檔案。需要 network 的模型 transport 必須與 coding worker 分離；不得直接把可聯網 subprocess、一般 callback 或 service callback 當成 production runner。Sandbox backend 缺失或不支援時必須停止並回 blocked exit `2`。
- Hermes 邊界以 `NBS_HERMES_MONITORING.md` 為準，不與 Review Agent 重複。
- Hermes post-change check 只 read-only 報告 workflow artifact / retention state 並包含 workflow focused tests；它不得執行 prune、改寫 workflow artifact 或取代 Review / final gates。
- Documentation sidecar check 只 read-only 驗證五個 allowlisted artifact 的 schema、status、bounded counts、cap 與 permission；Hermes 不呼叫 documentation runner，不執行 preview/apply/backup/Git/Obsidian write，也不把 Documentation PASS 當成 runtime acceptance。
