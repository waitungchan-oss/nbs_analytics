# NBS Hermes Monitoring Contract

更新日期：2026-07-15
專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`
契約版本：v1.1 read-only monitoring

---

## 1. Purpose

本文件定義 Hermes Agent 如何針對 `nbs_analytics` 做可協調、可分派、可監控的改動後巡檢。

Hermes 的角色是 monitoring coordinator，不是直接修改系統的 executor。預設行為是 read-only inspection：讀取文件、log、runtime 狀態、SQLite 健康資訊、測試結果與 Git 狀態，然後回報風險、證據與下一步。

---

## 2. Project Scope

In scope:

- Streamlit cockpit UI。
- FastAPI backend。
- Vue cockpit。
- SQLite 本地持久化。
- Excel ingest、清洗、upsert、rollback、backup、quarantine。
- Dashboard summary、upload preflight、stability、health、export、forecast、data quality。
- `nbs_analytics` 的 runtime logs、測試、驗收命令與健康歷史。

Out of scope:

- `/Users/chanwaitung2025/Downloads/dashboard-project`。
- 任意非 `nbs_analytics` 專案。
- 未授權的 code rewrite、schema migration、資料庫寫入或歷史資料重算。

Hermes 若發現任務其實屬於 `dashboard-project`，必須標示 out-of-scope，不得混用上下文。

---

## 3. Monitoring Sources

Hermes 可以讀取以下來源：

### Runtime

- `.nbs_runtime/services.json`
- `.nbs_runtime/health_history.jsonl`
- `.nbs_runtime/restore_drill_latest.json`
- `.nbs_runtime/diagnostics/`

### Phase 1 Workflow Artifacts

- `.nbs_agent_runtime/runs/<run-id>/manifest.json`
- `.nbs_agent_runtime/runs/<run-id>/status.json`
- `.nbs_agent_runtime/runs/<run-id>/events.jsonl`
- `.nbs_agent_runtime/runs/<run-id>/approval.json` 與 allowlisted stage JSON artifact
- `agent_config/workflow_retention.json`

Hermes 只可 read-only 報告 workflow artifact、cap warning 與 retention state，不得寫入 artifact、執行 `prune` 或取代 Review / full verification gate。每個 stage artifact 的 hard cap 是 5 MiB；單一 run 的 stage artifact 合計超過 25 MiB 時 Store 會記錄 warning event，但不會由 Hermes 自動 compact。retention 僅可由既有 policy 的 best-effort housekeeping 或明確 `prune --apply` 處理合資格的 completed run；最新 30 個 terminal run 與非 completed run 均受保護。

### Logs

- `.nbs_runtime/logs/api.log`
- `.nbs_runtime/logs/streamlit.log`
- `.nbs_runtime/logs/vue.log`
- `.nbs_runtime/logs/api.log.*`
- `.nbs_runtime/logs/streamlit.log.*`
- `.nbs_runtime/logs/vue.log.*`

### SQLite

- `nbs_marketing_data.db`
- `nbs_marketing_data.db.backup_*`
- `nbs_marketing_data.db.quarantine_*`

### Health / Stability

- `backend/routers/health.py`
- `backend/routers/stability.py`
- `backend/services/system_health_service.py`
- `backend/services/stability_service.py`
- `backend/services/stability_history_service.py`
- `backend/services/restore_drill_service.py`
- `backend/services/backup_retention_service.py`

### Upload / Baseline Risk

- `database.py`
- `pipeline.py`
- `app_workflows.py`
- `backend/services/upload_preflight_service.py`
- `backend/services/upload_rollback_service.py`
- `backend/services/upload_lock_service.py`
- `backend/services/upload_orchestrator_service.py`
- `backend/services/cache_generation_service.py`
- `backend/services/revenue_scope_service.py`

### Operations

- `scripts/system_manager.py`
- `scripts/inspect_sqlite_latest.py`
- `scripts/phase2j_baseline_check.py`
- `PHASE2I_OPERATIONS.md`
- `PHASE2_PRECHECK_ACCEPTANCE.md`
- `NBS_ANALYTICS_HANDOFF.md`
- `NBS_ANALYTICS_SYSTEM_MAP.md`
- `NBS_SQLITE_DATABASE_GUIDE.md`

### Tests

- `tests/test_phase2_precheck_acceptance.py`
- `tests/test_dashboard_service.py`
- `tests/test_dashboard_api.py`
- `tests/test_database_rollback.py`
- `tests/test_stability_history_service.py`
- `tests/test_system_health_service.py`
- `tests/test_restore_drill_service.py`
- `tests/test_upload_preflight_service.py`
- `tests/test_upload_rollback_service.py`
- `tests/test_upload_api.py`
- `tests/test_upload_lock_service.py`
- `tests/test_upload_orchestrator_service.py`
- `tests/test_upload_single_writer_integration.py`

### Single-Writer Evidence

Hermes 必須讀取 `system_health` 的 `uploadCoordination`、`dataGeneration` 與 `uploadEvidence`：

- lease busy 是資訊狀態，不代表 DB integrity failure；
- generation 有 operation ID 但找不到同 operation ID history，必須報 degraded；
- history 有 `cacheError` 或 generation signature 與目前 DB 不符，必須報 degraded；
- Hermes 不得取得或顯示 lease owner 的來源檔案名稱。

---

## 4. Allowed Read-only Commands

Hermes 可要求 Codex 或 worker 執行以下 read-only 或非破壞性命令。

### Runtime Status

```bash
.venv/bin/python scripts/system_manager.py status
.venv/bin/python scripts/system_manager.py monitor
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/system_manager.py drill
.venv/bin/python scripts/system_manager.py diagnose
```

Notes:

- `monitor` 會新增 `.nbs_runtime/health_history.jsonl` 健康歷史，內容不保存訂單或客戶明細。
- `drill` 使用隔離副本驗證，不替換正式 database。
- `diagnose` 輸出診斷包，不包含 SQLite、Excel、upload 檔案或交易明細。

### SQLite Inspection

```bash
.venv/bin/python scripts/inspect_sqlite_latest.py
.venv/bin/python scripts/phase2j_baseline_check.py
```

### Core Acceptance Tests

```bash
.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py -q
.venv/bin/python -m pytest tests/test_dashboard_service.py tests/test_dashboard_api.py -q
.venv/bin/python -m pytest tests/test_database_rollback.py tests/test_stability_history_service.py -q
.venv/bin/python -m pytest tests/test_system_health_service.py tests/test_restore_drill_service.py -q
.venv/bin/python -m pytest tests/test_upload_preflight_service.py tests/test_upload_rollback_service.py tests/test_upload_api.py -q
```

### Log Tail

```bash
tail -n 120 .nbs_runtime/logs/api.log
tail -n 120 .nbs_runtime/logs/streamlit.log
tail -n 120 .nbs_runtime/logs/vue.log
```

### File Change Inspection

```bash
find . -type f -mtime -1 \
  -not -path './.venv/*' \
  -not -path './__pycache__/*' \
  -not -path './.nbs_runtime/logs/*'
```

---

## 5. Forbidden Actions

Hermes 不得自行要求或執行以下操作：

- 修改正式 SQLite database。
- 執行 upload / upsert / rollback apply。
- 刪除 backup、quarantine、runtime diagnostics 或 log。
- 重寫 historical validated rows。
- 用 analysis layer 廣義排除來掩蓋 baseline drift。
- 未授權修改 `database.py`、`pipeline.py`、`app_workflows.py`、`rules_config.json`、tests 或任何 code。
- 未授權改動正式 Excel export schema。
- 未授權啟動 schema migration。
- 未授權重訓或覆蓋正式 AI Forecast / WAPE / cache。
- 將 `dashboard-project` 的規則或資料流混入 `nbs_analytics`。

若需要修復，Hermes 必須先輸出規劃、影響範圍、風險與驗收方式，等待使用者授權。

---

## 6. Change Detection

改動後巡檢時，Hermes 應先判斷 changed surface：

- 是否有 code 檔案變更。
- 是否有 tests 變更。
- 是否有 `rules_config.json` 變更。
- 是否有 SQLite backup / quarantine 新增。
- 是否有 runtime logs 新錯誤。
- 是否有 health history 新紀錄。
- Git branch、diff、recent commits 與 active worktrees。
- 是否有 `.nbs_agent_runtime` artifact hard-cap rejection、soft-cap warning 或 retention skipped reason。

---

## 7. Runtime Log Monitoring

Hermes 應檢查最新 log 尾段，優先搜尋：

- `Traceback`
- `Exception`
- `ERROR`
- `CRITICAL`
- `SQLite`
- `database is locked`
- `IntegrityError`
- `rollback`
- `baseline`
- `stability`
- `upload`
- `preflight`
- `health`
- `port`

Log findings 必須分成：

- fatal errors
- recoverable errors
- warnings
- noisy but non-actionable messages

不得只說「log 看起來正常」；必須附上檢查來源與尾段時間或命令。

---

## 8. Health / Stability Monitoring

優先使用：

```bash
.venv/bin/python scripts/system_manager.py status
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/system_manager.py monitor
```

Hermes 應確認：

- Streamlit: `http://127.0.0.1:8502/`
- Vue: `http://127.0.0.1:5173/`
- FastAPI Docs: `http://127.0.0.1:8601/docs`
- FastAPI Health: `http://127.0.0.1:8601/api/health`

Health 狀態：

- `ok`
- `degraded`
- `critical`

若出現 `critical`，不得宣稱巡檢通過。

---

## 9. SQLite / Baseline Monitoring

正式收入口徑固定為：

```text
不含掛賬核銷與TT退款轉團款
```

核心 baseline：

```text
月份：2026-05
範圍：2026-05-01 至 2026-05-31
視角：全部分社 + 全部專職銷售組
正式口徑：不含掛賬核銷與TT退款轉團款
分社營收：HKD 6,658,144
專職銷售組營收：HKD 5,399,824
分社 + 專職銷售組總營收：HKD 12,057,968
```

如果 `2026-05` 同口徑合計不等於 `HKD 12,057,968`，Hermes 必須標示 FAIL。

Baseline drift 排查原則：

- 先分辨 new excluded rows 與 historical excluded rows。
- 先檢查 write path，不要先改 analysis layer。
- 先看 backup、quarantine、stability history 與 regression tests。
- 保護 frozen baseline。
- 不做 retroactive history rewrite。

高風險關鍵字：

- `掛賬核銷`
- `TT 退款轉團款`
- `收款單號`
- `來源單據號`
- `full snapshot upload`
- `baseline drift`
- `revenue-scope`
- `rollback`
- `stability_gate_history`

---

## 10. Test Suites

Hermes 應依任務類型選擇測試。

### Dashboard / API Contract

```bash
.venv/bin/python -m pytest tests/test_dashboard_service.py tests/test_dashboard_api.py -q
```

### Phase 2 Baseline

```bash
.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py -q
```

### Upload / Rollback / Baseline Risk

```bash
.venv/bin/python -m pytest tests/test_database_rollback.py tests/test_upload_preflight_service.py tests/test_upload_rollback_service.py tests/test_upload_api.py -q
```

### Stability / Health / Restore

```bash
.venv/bin/python -m pytest tests/test_stability_history_service.py tests/test_system_health_service.py tests/test_restore_drill_service.py -q
```

### Full Targeted Monitoring Pack

```bash
.venv/bin/python -m pytest \
  tests/test_phase2_precheck_acceptance.py \
  tests/test_dashboard_service.py \
  tests/test_dashboard_api.py \
  tests/test_database_rollback.py \
  tests/test_stability_history_service.py \
  tests/test_system_health_service.py \
  tests/test_restore_drill_service.py \
  tests/test_upload_preflight_service.py \
  tests/test_upload_rollback_service.py \
  tests/test_upload_api.py \
  -q
```

---

## 11. Git / Diff / Worktree Monitoring

`/Users/chanwaitung2025/Downloads/nbs_analytics` 是 Git repository。Hermes 可使用：

```bash
git status --short --branch
git diff --stat
git diff --name-only
git log -5 --oneline
git worktree list
```

Git monitoring 回報需包含：

- current branch
- dirty files
- untracked files
- diff summary
- recent commits
- active worktrees

Hermes 不得自行 commit、reset、checkout、stash、merge 或 rebase，除非使用者明確授權。

---

## 12. Alert Levels

### PASS

條件：

- HTTP acceptance passed。
- `/api/health` 為 `ok` 或符合預期的 `degraded` 並有原因。
- logs 無新 fatal error。
- SQLite integrity 無異常。
- `2026-05` baseline 未漂移。
- 相關核心測試通過。

### WARNING

條件：

- logs 有非致命 exception 或 warning。
- AI cache deferred，但不影響 dashboard ready。
- backup 容量接近警戒。
- 某服務由同專案既有 PID 採納。
- Git 監控不可用。
- 某些非核心測試未執行，但核心 baseline 已通過。

### FAIL

條件：

- `/api/health` 為 `critical`。
- HTTP acceptance failed。
- SQLite integrity failed。
- `2026-05` baseline 不等於 `HKD 12,057,968`。
- upload / rollback / stability 相關 regression tests failed。
- 出現新的 baseline drift 且原因未釐清。
- 正式 database、backup 或 quarantine 有未授權破壞性變更跡象。

---

## 13. Hermes Report Format

Hermes 每次巡檢需使用以下格式：

```text
Overall status: PASS / WARNING / FAIL

Observed state:
- Runtime:
- Logs:
- Health:
- SQLite:
- Baseline:
- Tests:
- Git:

Evidence:
- Commands run:
- Files inspected:
- Key outputs:

Risks:
- ...

Recommended next action:
- ...
```

若有 blocking issue，必須明確寫：

```text
Blocked because:
Next required user decision:
```

---

## 14. Post-change Inspection Prompt

可直接貼給 Hermes：

```text
Hermes，請對 nbs_analytics 執行改動後巡檢。

專案路徑：
/Users/chanwaitung2025/Downloads/nbs_analytics

請先閱讀：
- NBS_HERMES_MONITORING.md
- NBS_ANALYTICS_HANDOFF.md
- NBS_ANALYTICS_SYSTEM_MAP.md
- PHASE2I_OPERATIONS.md
- PHASE2_PRECHECK_ACCEPTANCE.md

請不要修改任何檔案。請只做 read-only inspection。

請檢查：
1. 最近修改的檔案
2. .nbs_runtime/logs/api.log、streamlit.log、vue.log 的最新錯誤
3. system_manager status / acceptance / monitor 結果
4. SQLite 是否存在 baseline drift 風險
5. 2026-05 正式口徑 baseline 是否仍為 HKD 12,057,968
6. 與 upload、rollback、stability、dashboard API、system health 有關的測試是否通過
7. 如果目前不是 Git repo，請明確標示 Git branch / diff / worktree 監控不可用

回報格式：
- Overall status: PASS / WARNING / FAIL
- Observed state
- Evidence
- Risks
- Recommended next action

特別規則：
- 不要修改檔案
- 不要重寫歷史資料
- revenue-scope / full snapshot upload / baseline drift 問題，優先保護 frozen baseline
- 不要只看 UI，必須提供可驗證證據
```

---

## 15. Future Automation Plan

v1 只定義 read-only monitoring contract。

後續可逐步擴展：

1. 建立 `.hermes/runs/`，保存每次巡檢 summary。
2. 讓 Codex 每次任務完成後輸出 machine-readable run summary。
3. 將 `system_manager.py monitor` 納入定期巡檢。
4. repo 化後加入 Git branch / diff / worktree monitoring。
5. 建立 fail-fast alert：baseline drift、health critical、acceptance failed 時自動提示停止部署或停止後續修改。
6. 建立最小 dashboard：讀取 `.nbs_runtime/health_history.jsonl` 與 `.hermes/runs/` 顯示近期巡檢狀態。

任何 automation 都必須維持 read-only first 原則。涉及修復、寫入、刪除、rollback apply 或 schema migration 時，必須先取得使用者授權。
