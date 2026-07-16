# Agent Operations Streamlit Read-Only Design

狀態：approved for implementation planning
日期：2026-07-16
範圍：Streamlit 頂部第四分頁、Agent Operations read model、手動刷新與治理摘要

## 1. 目的

在目前 Streamlit 應用加入頂部第四個獨立分頁「Agent Operations」，讓使用者在系統內查看 Agent workflow 的使用狀態、目前階段、耗時、findings、驗證與 Hermes 結果。

本階段只展示 Agent Orchestrator Phase 1 已保存的 artifacts，不建立第二套狀態、不執行 Agent、不批准 workflow，也不修改 Git、SQLite、baseline、runtime evidence 或正式服務。

## 2. 已確認的產品選擇

- 入口與「經營分析大盤」、「業務規則配置」、「GMV 排除訂單看板」並列，成為頂部第四分頁。
- 首次進入分頁時建立 snapshot；其後只在使用者按「重新整理」時更新。
- 不使用固定 interval、自動 rerun 或背景 polling。
- 頁面採治理摘要，不展示完整 prompt、stdout、內部推理、敏感路徑或原始 JSON。
- Token usage 只有在 runner artifact 提供正式數據時才顯示；缺少數據時顯示「未提供」，不得估算 Codex Plus 額度。

## 3. 採用架構

採用共用 Read Model Service：

```text
.nbs_agent_runtime/runs/<run-id>/
        -> AgentOperationsService (read-only)
        -> agent-operations-snapshot-v1
        -> Streamlit Agent Operations tab
```

`AgentOperationsService` 是唯一 artifact 讀取邊界。Streamlit 不直接掃描 JSON、不自行推導 workflow 狀態，也不依賴 FastAPI。未來若 FastAPI 或 Vue 需要相同資料，可以重用同一 service，不需改變 artifact contract。

本設計依賴 Agent Orchestrator Phase 1；實作分支必須包含 `backend/agents/workflow_models.py`、`backend/agents/workflow_store.py`、workflow retention policy 與 `.nbs_agent_runtime/runs/` schema。

## 4. 元件與責任

### 4.1 Agent Operations Read Model

新增 `backend/services/agent_operations_service.py`，負責：

- 接受明確的 project root 與 runtime root。
- 列出安全、非 symlink 的 run directories。
- 使用 Phase 1 schema model 驗證 manifest、status、approval 與 events。
- 只讀白名單 stage artifacts，輸出 compact summary。
- 計算階段狀態、時間、耗時、findings、驗證結果、Hermes 結果、artifact bytes 與 retention 狀態。
- 隔離單一損壞 run，並把安全診斷加入 snapshot，不中斷其他 run。

Service 不接受任意 artifact filename，不回傳 runner command、完整 stdout/stderr、絕對敏感路徑、prompt 或未裁剪 evidence。

### 4.2 Streamlit Rendering

新增 `agent_operations_rendering.py`，只接受 compact snapshot 並渲染：

- 執行總覽。
- Workflow 清單與本頁篩選。
- 選定 run 的階段時間線與治理詳情。
- Retention policy 與資料品質診斷。

Rendering module 不讀檔、不解析 schema、不呼叫 Agent CLI，也不修改 workflow。

### 4.3 Page Integration

在 `app_pages.py` 的頂部 tabs 加入第四個 tab：

```text
經營分析大盤 | 業務規則配置 | GMV 排除訂單看板 | Agent Operations
```

`app.py` 維持 thin entrypoint。Agent Operations 不觸發 dashboard facts、AI forecast、export workbook、SQLite reload 或營運分析 filter rerun。

## 5. Snapshot Contract

Service 輸出固定為：

```json
{
  "schemaVersion": "agent-operations-snapshot-v1",
  "generatedAt": "ISO-8601",
  "summary": {
    "runCount": 0,
    "runningCount": 0,
    "awaitingAuthorizationCount": 0,
    "blockedOrFailedCount": 0,
    "latestCompletedAt": null,
    "tokenUsage": null
  },
  "runs": [],
  "retention": {},
  "diagnostics": []
}
```

每個 `runs[]` item 包含：

- `runId`、`briefName`、`gitBranch`、`gitHeadShort`。
- `status`、`stage`、`message`、`errorCode`。
- `createdAt`、`updatedAt`、`completedAt`、`durationMs`。
- `stages`：Context、Implementation、Targeted Verification、Review、Full Verification、Hermes 的狀態與可用耗時。
- `findings`：總數、最高嚴重度與安全摘要。
- `verification` 與 `hermes`：pass、fail、blocked、not_started 或 unavailable。
- `artifactBytes`、`retentionState`。
- `tokenUsage`：正式 artifact 有值時為整數或分項 object，否則為 `null`。

Snapshot 不包含 `selectedRun`。選定 run 是 Streamlit session view state，避免 service contract 因 UI 選擇而改變。

## 6. 狀態與顯示語意

| 類別 | Workflow 狀態 | 顯示語意 |
|---|---|---|
| success | `completed` | 綠色，已通過 Hermes |
| active | `context_running`、`implementation_running`、`targeted_verification_running`、`review_running`、`full_verification_running`、`hermes_running` | 藍色，執行中 |
| attention | `awaiting_authorization`、`changes_required` | 黃色，需要人工注意 |
| failure | `blocked`、`failed` | 紅色，顯示受限錯誤摘要 |
| neutral | `created` 或 stage artifact 不存在 | 灰色，未開始或未提供 |

頁面不得用缺少 stage artifact 推翻 `status.json` 的正式狀態。若 retention 已 compact stage reports，顯示 `archived_summary`，不誤判成失敗。

## 7. 頁面區塊

### 7.1 執行總覽

顯示 run 總數、執行中、等待授權、阻擋／失敗、最近完成時間與 Token usage。Token 缺失時顯示「未提供」。

### 7.2 Workflow 清單

預設按 `updatedAt` 由新至舊排序。提供狀態、日期與 Brief 關鍵字篩選；篩選只保存在 Agent Operations session state，不影響 dashboard 或產品下鑽。

清單顯示 Run ID 短碼、Brief、branch、狀態、stage、更新時間、耗時、findings、Hermes 與 artifact size。

### 7.3 Run 詳情

選取一個 run 後顯示：

- 階段時間線。
- Review findings 的 severity、rule/code 與受限摘要。
- Targeted/full verification 的命令數、pass/fail 數與結果。
- Hermes overall result 與受限訊息。
- 阻擋原因、error code、artifact size 與 retention state。

不提供原始 JSON 展開、artifact 下載、批准、重跑、停止、prune 或刪除按鈕。

### 7.4 治理與資料品質

顯示 retention policy：保留天數、最新 terminal run 數、stage artifact hard cap、run artifact soft cap 與 command output tail cap。

Diagnostics 只顯示 run 短碼、診斷代碼與安全摘要；不顯示絕對路徑或原始 exception payload。

## 8. 刷新與 Session State

- 首次渲染 Agent Operations tab 時呼叫 service 一次。
- Snapshot 存在 `st.session_state["AGENT_OPERATIONS_SNAPSHOT"]`。
- 「重新整理」只重新呼叫 Agent Operations service，並更新 `AGENT_OPERATIONS_SNAPSHOT` 與選取 run 的有效性。
- Dashboard、AI、export、upload 與 application snapshot cache 不得被清除。
- 不使用 `st.rerun()` 完成定時刷新；若按鈕互動自然造成一次 Streamlit rerun，service 只在 refresh flag 設定時重讀。

## 9. 安全與 Fail-Closed 規則

- Runtime root、runs root、run directory 與 artifact 必須保持在 project root 內。
- 拒絕 symlink root、symlink run、symlink artifact、非 regular file 與 path traversal。
- 只讀 Phase 1 白名單 artifact；未知 filename 不讀取。
- 單一 artifact 不得超過 `stageArtifactMaxBytes`；超限 run 隔離並記錄 bounded diagnostic。
- JSON 必須是 object 並符合已知 schema；未知 schema 不做寬鬆解析。
- `events.jsonl` 採 bounded line count 與 bounded bytes，只取治理所需事件。
- UI 不直接顯示 exception、絕對路徑、runner argv、environment、secret、完整 stdout/stderr 或 prompt。
- Service 與 rendering 不得寫入 `.nbs_agent_runtime`，不得呼叫 retention apply。

## 10. 錯誤處理

- Runtime 尚不存在：回傳有效空 snapshot，UI 顯示「尚無 Agent 執行紀錄」。
- 單一 run 壞 JSON、缺 manifest/status 或 schema 不明：跳過該 run，加入 bounded diagnostic。
- Optional stage artifact 缺失：stage 顯示 `not_started` 或 `unavailable`，不視為整個 snapshot 失敗。
- Retention compact：優先讀 `archive-summary.json` 的治理摘要，標記 `archived_summary`。
- Retention config 壞檔：runs 仍可顯示，policy 顯示 unavailable 並加入 diagnostic。
- Service 發生未預期錯誤：rendering 顯示局部錯誤區塊，不使其他 Streamlit tabs 白屏。

## 11. 測試與驗收

### Service tests

- 正常 completed、running、awaiting authorization、changes required、blocked 與 failed run。
- 階段時間、duration、findings、verification、Hermes 與 artifact bytes 聚合。
- Token telemetry 有值與缺失。
- 空 runtime、缺 optional artifact、archive summary。
- 壞 JSON、未知 schema、oversize artifact、symlink root/run/artifact、非 regular file 與 traversal 防護。
- 單一壞 run 不影響其他合法 run。

### Rendering tests

- 第四分頁存在且前三個 tab 順序不變。
- 總覽、清單、詳情、retention 與 diagnostics 正確渲染。
- 狀態／日期／Brief 篩選只作用於 Agent Operations。
- 手動刷新更新 snapshot；一般 rerun 重用 snapshot。
- 空 runtime 與 service failure 不白屏。
- 刷新不清除 dashboard、AI、export 或 upload session cache。

### 正式驗收

1. Agent Operations focused tests。
2. Agent Orchestrator regression pack。
3. Streamlit compile 與 dashboard targeted tests。
4. Full pytest。
5. `scripts/system_manager.py acceptance`。
6. `scripts/hermes_post_change_check.py --skip-monitor --json`。
7. 2026-05 baseline 必須保持 `HKD 12,057,968`。
8. 正式 SQLite SHA-256 前後一致。

## 12. 明確非目標

- 頁面內批准、重跑、停止、刪除或 prune workflow。
- 自動刷新、daemon、queue、background worker 或新排程。
- 原始 JSON、prompt、stdout、evidence bundle 或 artifact 下載。
- 顯示內部推理內容。
- 估算 Codex Plus 每週額度或缺失的 Token usage。
- 新增 FastAPI endpoint、Vue 頁面或另一套 workflow database。
- 修改 Agent Orchestrator 狀態機、Hermes、Git、SQLite、baseline、正式口徑、營收規則或報表計算。

## 13. 完成定義

- Streamlit 頂部可見第四個「Agent Operations」分頁。
- 使用者能以治理摘要查看 Agent 使用狀態、階段、耗時、findings、驗證與 Hermes 結果。
- 只有手動刷新會重讀 artifacts，且不影響營運 dashboard cache。
- Read Model Service 對壞檔與惡意路徑 fail closed，同時保留其他合法 run 的可用性。
- Token usage 只呈現真實 telemetry，缺失時顯示「未提供」。
- 不新增任何寫入、執行、批准、Git、SQLite、baseline 或服務管理能力。
- 全部正式驗收通過，baseline 與 DB hash 保持不變。
