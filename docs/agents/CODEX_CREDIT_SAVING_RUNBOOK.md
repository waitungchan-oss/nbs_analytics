# Codex Credit Saving Runbook

狀態：active
版本：v1
日期：2026-08-28
適用：`nbs_analytics` 日常開發與維護的 Codex 使用規範

---

## 1. 目的

本 runbook 定義如何在維持既有治理（Context/Review/Implementation Agent、Hermes、
人工授權、frozen baseline）的前提下，降低 Codex 額度消耗。

依據 2026-08-28 用量實測（來源：`~/.codex/sessions` rollout 記錄）：

- 3 個月共 **575 個 session**、輸入 token 約 **145.3 億**（96.5% 為 cached）。
- **21 個長 session（>50 turns）吃掉 95% 的輸入 token**；129 個含 `spawn_agent`
  的編排類 session 佔輸入 41.5%、輸出 41%、reasoning 39%。
- 單一怪獸 session（如 2026-07-27）達 **14.8 億輸入 token**：380 個 turn、52 次
  compaction、`spawn_agent ×77`、`wait_agent ×653`。
- Review Agent telemetry：153 次 review 中 80 次 `changes_required`（52% 返工率），
  cache hit 僅 10%。

**結論：成本主要來自「在 Codex 對話內做多 agent 編排」與「超長 session」，**
不是來自 agent pipeline 本身。本 runbook 的目標是把編排移出 Codex 對話，
讓每個 agent 步驟成為有預算上限的一次性呼叫。

預期效果：總額度消耗下降 50–70%（與 `NBS_AGENT_ARCHITECTURE.md` 設計目標
「降低主 Codex 前期探索內容約 50% 至 75%」一致）。

---

## 2. 核心原則（不可協商）

1. **不在 Codex 對話內 spawn/wait agent 做編排**。編排一律走本地
   `scripts/agent_workflow.py` CLI。
2. **每 Task 開新 session**，目標 10–15 個 turn 內完成；不開長對話。
3. **模型分級**：日常維護用便宜模型 + `low` reasoning effort；只有高風險領域
   （revenue、baseline、business rules、upload、SQLite、export）才用最強模型。
4. **本地能做的絕不讓 LLM 做**：context 收集（`--collect-only`）、diff、測試、
   搜尋一律本地執行，0 LLM token。
5. **Read-only 查詢不開 Codex session**：先用 `rg`、python、`system_manager.py`
   等本地工具查。
6. **JSON 是給 Codex 讀的交接單，不是給人看的**：你只負責閘口批准與驗收，
   Codex 負責讀 JSON 並用白話向你彙報。

---

## 3. 標準工作流程（本地 CLI 驅動）

每個 Task 的循環（對應 `docs/agents/NBS_AGENT_ARCHITECTURE.md` 第 13 節）：

```bash
# 0) 前置：確認主工作區乾淨
git status --short --branch
git log -1 --oneline

# 1) 收集 Context（純本地，0 LLM token；跑完停在 awaiting_authorization）
.venv/bin/python scripts/agent_workflow.py run --brief docs/briefs/<brief>.md
#    輸出：{"status": "awaiting_authorization", "stage": "context", "message": "Context ready; ..."}

# 2) Codex 依 JSON 用白話向你彙報 contract 內容，你審核後明確批准
#    approve 必須帶齊 run-id + contract + implementation runner + review runner，
#    禁止 implicit approval（見 AGENTS.md 與 CODEX_AGENT_DISPATCH.md）
.venv/bin/python scripts/agent_workflow.py approve \
  --run-id <run-id> \
  --contract .nbs_agent_runtime/contracts/<task>.json \
  --implementation-agent-command '<approved runner>' \
  --review-agent-command '<approved runner>'

# 3) 監看進度（Codex 讀 JSON、向你彙報；你只看摘要）
.venv/bin/python scripts/agent_workflow.py status --run-id <run-id>

# 4) Review PASS 後：full verification + Hermes（見 NBS_CODEX_WORKER_WORKFLOW.md）
# 5) 完成後：prune 舊 run（--dry-run 先看計畫，零寫入）
.venv/bin/python scripts/agent_workflow.py prune --dry-run
```

### 閱讀 JSON 的重點欄位

| 欄位 | 意義 | 你據此做什麼 |
|---|---|---|
| `status` | `awaiting_authorization` / `completed` / `blocked` | 下一步能否進行 |
| `stage` | `context` / `review` / `hermes` | 卡在哪一關 |
| `message` | 一行中文摘要 | 不用讀細節 |
| exit code | 0=PASS / 1=findings / 2-5=錯誤 | 決定 approve 或退回 |

### 常見 JSON 情境

| 你看到 | 意思 | 動作 |
|---|---|---|
| `"status": "awaiting_authorization"` | Context 就緒，等你批准 | 審 contract → approve |
| `"status": "completed", "stage": "review", "message": "Review pass"` | Review 通過 | 進入 full verification + Hermes |
| exit code 1 / `changes_required` | Review 有 findings | Codex 修完再 review（見 §5 降低返工） |
| `blocked_missing_brief` / `blocked_missing_runner` | 缺輸入或 runner | 補齊再跑，不要繞過 |
| `context_overflow` | 超出 token 預算 | 縮小範圍重跑，不自行擴大讀取 |

---

## 4. 模型分級規則

### 分級對照

| 任務類型 | 模型 | reasoning effort | 例子 |
|---|---|---|---|
| 高風險（鎖定最強模型） | `gpt-5.6-luna` | `medium` | revenue/baseline/business rules、upload、SQLite、export schema、架構設計、refactor |
| 一般開發 | 中階模型 | `low` | 功能開發、測試撰寫、bug fix |
| 維護/低風險（可用便宜模型） | 便宜模型 | `low` | typo、格式、純文件、小測試調整、現況說明 |

### 判斷守則

- 不確定風險等級時，先查 `NBS_CODEX_WORKER_WORKFLOW.md` 的高風險關鍵字清單
  （baseline drift、revenue-scope、掛賬核銷、TT 退款轉團款、rollback 等），
  命中即升級為高風險。
- 簡單任務誤用最強模型的代價 = 輸出/reasoning token 單價數倍差距；
  高風險任務誤用便宜模型的代價 = 返工。兩者權衡以「命中高風險關鍵字」為準。
- 一次 session 內不要混用等級：同一任務從頭到尾用同一模型。

### 設定方式（CLI session 級，不影響其他 session）

- Codex CLI 可依需求選擇模型與 effort（在 session 啟動時指定）；
- 全域預設調整請先備份 `~/.codex/config.toml` 再改：
  `cp ~/.codex/config.toml ~/.codex/config.toml.bak-$(date +%Y%m%d)`

---

## 5. 降低返工（Review 52% 返工率的對策）

1. **Review 前先本地自檢**（零 token）：`python -m compileall`、targeted tests、
   `git diff --check`、dirty worktree 檢查。常見 findings
   （缺測試、dirty worktree、格式）在進 Review 前就攔下。
2. **Task 切小**：單一 implementation Task 限 1–3 個檔案、diff < 500 行。
   參考反例：merge #46 一次 46 檔 / 9,306 行。
3. **TDD**：先寫 failing test → 確認 fail → 實作 → 確認 pass（已列於
   `NBS_CODEX_WORKER_WORKFLOW.md` 開發規則，務必遵守）。
4. **相同 fingerprint 重用**：context/review 的 fingerprint 相同時直接重用結果，
   不重新跑（目前 cache hit 僅 10%，目標 >50%）。

---

## 6. Session 規範（對應「縮短 session」）

| 規則 | 說明 |
|---|---|
| 每 Task 一 session | 不跨 task 延續對話 |
| 目標 ≤ 15 turns | 超過就主動 `/compact`，或接受上下文被壓縮 |
| 禁止長輪詢 | 不用 wait_agent 迴圈等結果；改用 CLI `status` 一次查 |
| 完成即開新 | 不把下一個 task 貼進舊對話 |
| 背景記憶 | 上一 task 結論寫進 `docs/briefs/` 或 run artifact，不靠對話記憶 |

---

## 7. Plugin 清理

現況：`~/.codex/config.toml` 啟用 18 個 plugins
（canva、gmail、spreadsheets、presentations、documents、computer-use、browser、
chrome、hyperframes、remotion 等）。

- 純程式開發只需保留 `github`（視需要 `codex-app-tools`）。
- 關閉方式：編輯 `~/.codex/config.toml`，把對應 `[plugins."xxx"] enabled = true`
  改為 `false`（先備份）。
- 需要桌面版文件/瀏覽功能時再開回來，一行設定。
- 專案內 `.nbs_agent_runtime/codex_home/.tmp/plugins/`（約 80MB）是插件快取，
  與系統無關；清理後下次使用會自動重建。

---

## 8. 驗證與還原

- 本 runbook 全部措施只動 `~/.codex/config.toml`、操作習慣與 `.nbs_agent_runtime/`
  快取，**不碰 Streamlit/FastAPI/SQLite/正式程式碼**，不影響運行中系統。
- 還原方式：還原 `config.toml` 備份即可；session/plugin 快取刪除後會自動重建。
- 禁止事項：本 runbook 不授權刪除 backup、quarantine、logs；
  這些仍受 `NBS_CODEX_WORKER_WORKFLOW.md` 第 9 條與 retention 政策保護。

---

## 9. 監控（持續優化）

- 每週統計一次用量，找出 top 消耗 session 與返工熱點：
  - 來源：`~/.codex/sessions/2026/*/*/rollout-*.jsonl` 的 `token_count` event
    （`total_token_usage` 欄位：input / cached_input / output / reasoning）。
  - 工具：`scripts/codex_usage_report.py`（已實作，read-only）：
    ```bash
    .venv/bin/python scripts/codex_usage_report.py                 # 最近 8 週（人類可讀）
    .venv/bin/python scripts/codex_usage_report.py --format json   # machine-readable
    .venv/bin/python scripts/codex_usage_report.py --since 2026-05-29  # 全範圍
    ```
  - 報表含：每週 session 數、輸入/輸出/reasoning token、快取比例、
    含 agent 活動 session 數、**各專案用量分列**（nbs_analytics 主目錄/worktree/
    sandbox 分開、dashboard-project、其他）、Top session、
    7 日 rate limit 用量百分比、專案 Agent Pipeline 返工率與 cache hit 率
    （若有 telemetry）。
- 觀察指標：每週 session 數、輸入/輸出/reasoning token、cache 比例、
  review 返工率、cache hit 率、rate limit 用量是否接近 100%。
- 每兩週對照一次：若 session 數下降但返工率上升，檢討模型分級是否過度降級。

### 每週自動報表（launchd）

已設定 launchd job `com.nbsanalytics.codex-usage-report`（每週一 08:00 執行）：

- 執行：`~/Library/Scripts/codex_usage_weekly.sh`
  （用 `/usr/bin/python3` 跑 `~/Library/Scripts/codex_usage_report.py`，為專案
  `scripts/codex_usage_report.py` 的 hard link，內容自動同步）。
- 輸出：`~/Library/Logs/nbs-codex-usage/codex_usage_weekly_<日期>.md` 與 `.json`，
  透過桌面捷徑 `~/Desktop/report` 可直接看到。
- 為何不直接寫桌面/專案：macOS TCC 禁止 launchd 背景任務存取
  `~/Downloads`、`~/Desktop` 等受保護資料夾（實測 exit 126）；故 script 安裝在
  `~/Library/Scripts`、輸出在 `~/Library/Logs`，桌面以 symlink 呈現。
- ⚠️ **hard link 維護**：若修改 `scripts/codex_usage_report.py`（編輯工具會重建
  檔案、斷開 hard link），必須重新建立：
  ```bash
  rm ~/Library/Scripts/codex_usage_report.py
  ln ~/Downloads/nbs_analytics/scripts/codex_usage_report.py ~/Library/Scripts/codex_usage_report.py
  ```
- 已知限制：launchd 版報表**不含**「專案 Agent Pipeline」段落（telemetry 在
  Downloads 內讀不到）；要看返工率/cache hit 請在專案內手動執行。
- 手動跑一次：`launchctl kickstart -k gui/$(id -u)/com.nbsanalytics.codex-usage-report`
- 停用：`launchctl bootout gui/$(id -u)/com.nbsanalytics.codex-usage-report`
- log：`~/Library/Logs/nbs-codex-usage-report.log`（stdout）與 `.err.log`。

---

## 10. 與既有治理文件的關係

- 本 runbook **不放寬** `NBS_AGENT_ARCHITECTURE.md`、`CODEX_AGENT_DISPATCH.md`、
  `CONTEXT_AGENT_CONTRACT.md`、`REVIEW_AGENT_CONTRACT.md`、
  `IMPLEMENTATION_AGENT_CONTRACT.md` 的權限、資料保護、Token 或 Hermes 邊界。
- 只改變「編排在哪裡執行」（Codex 對話 → 本地 CLI）與「模型/effort 選擇」。
- Agent pipeline 的 Context/Review Agent 仍 read-only；Implementation Agent 仍
  只可執行已批准且明確授權的一個 Task；Hermes 仍是正式系統 final acceptance。

## 11. 落地紀錄

| 日期 | 項目 | 內容 | 還原方式 |
|---|---|---|---|
| 2026-08-28 | AGENTS.md | 新增「Codex 額度使用固定規範」四條（§2 原則）並指向本 runbook，每個 session 自動載入 | 編輯 AGENTS.md 移除該節 |
| 2026-08-28 | `~/.codex/config.toml` | 全域 `model_reasoning_effort` 由 `medium` 改為 `low`（高風險任務 session 內切回 medium）；關閉 13 個內容類 plugins（canva、documents、spreadsheets、presentations、gmail、hyperframes、remotion、build-web-data-visualization、pdf、template-creator、visualize、sites、record-and-replay），保留 github、codex-app-tools、computer-use、chrome、browser | 還原備份 `~/.codex/config.toml.bak-20260828` |
| 2026-08-28 | 本 runbook | 建立（v1） | — |

下次 Codex 啟動即生效；不影響正在運行的 Streamlit/FastAPI 系統。
