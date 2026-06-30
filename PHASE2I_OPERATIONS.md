# Phase 2I 本地系統操作指南

更新日期：2026-06-30

## 一鍵啟動

### macOS

雙擊：

```text
啟動NBS系統_mac.command
```

### Windows

雙擊：

```text
啟動NBS系統_windows.bat
```

啟動器會依序檢查依賴、port 與必要檔案，然後啟動：

- Streamlit: `http://127.0.0.1:8502/`
- Vue Cockpit: `http://127.0.0.1:5173/`
- FastAPI Docs: `http://127.0.0.1:8601/docs`
- FastAPI Health: `http://127.0.0.1:8601/api/health`

只有三個服務全部通過 HTTP readiness 後，啟動器才會顯示 Ready。

若預設 port 已被同一個 `nbs_analytics` 專案服務佔用，Phase 2I manager 會採納該服務並寫入 `.nbs_runtime/services.json`，避免把正常運行中的 Streamlit 誤判為 unmanaged process。若 port 被其它專案或未知程序佔用，啟動器仍會阻擋並提示先清理該 port。

## 一鍵停止

- macOS：雙擊 `停止NBS系統_mac.command`
- Windows：雙擊 `停止NBS系統_windows.bat`

停止器只會終止由 Phase 2I manager 記錄或採納的 PID，不會清理未知的 port 使用者。

## 命令列

```bash
.venv/bin/python scripts/system_manager.py start
.venv/bin/python scripts/system_manager.py status
.venv/bin/python scripts/system_manager.py stop
```

不自動開瀏覽器：

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
```

## Runtime 資料

```text
.nbs_runtime/
├── services.json
└── logs/
    ├── streamlit.log
    ├── api.log
    └── vue.log
```

每個服務保留最多五代 log。啟動失敗時，先查看對應服務 log。

常見啟動排查：

- `streamlit port 8502 is occupied by an unmanaged process`：若是同專案 Streamlit，更新後的 manager 會採納；若仍出現，代表佔用者不是本專案服務，請先停止該程序。
- API Docs 無法打開：先跑 `scripts/system_manager.py status`，確認 FastAPI 是否在 `8601` 且 `/api/health` ready。
- Vue 無法打開：先確認 Vite 是否在 `5173`，再檢查 `.nbs_runtime/logs/vue.log`。

## 健康狀態

`/api/health` 會回報：

- SQLite integrity
- 最新 Acceptance Gate 與 Rollback
- backup/quarantine 數量及容量
- runtime cache 檔案數與容量
- `ok`、`degraded` 或 `critical`

若 AI cache 先被標示為 deferred，表示上傳後已先保留營運 dashboard 的快速回應；需要完整 AI / backtest 快取時，仍可回到 Streamlit 的 AI Forecast 區手動補算。這個狀態不影響啟動器的 Ready 判定。

## Phase 2J 營運監控

建立一筆健康歷史：

```bash
.venv/bin/python scripts/system_manager.py monitor
```

紀錄保存在：

```text
.nbs_runtime/health_history.jsonl
```

每筆只保存 SQLite integrity、Acceptance/Rollback 摘要、儲存容量及三個
HTTP endpoint 狀態，不保存訂單或客戶明細。Vue 的 API Status 區會顯示最近
20 筆監控結果。

## Backup 保留政策

只預覽、不刪除：

```bash
.venv/bin/python scripts/system_manager.py retention
```

正式套用：

```bash
.venv/bin/python scripts/system_manager.py retention --apply
```

保留規則：

- 最近 7 天全部保留；
- 最近 4 週每週保留最新一份；
- 最近 6 個月每月保留最新一份；
- Acceptance history 引用的 backup 保留；
- quarantine 永不自動刪除；
- retained backup 超過 3 GB 時，Health 顯示容量警戒。

## Restore Drill

用最新 backup 建立隔離副本，執行 SQLite integrity 與 Phase 2 正式口徑驗收：

```bash
.venv/bin/python scripts/system_manager.py drill
```

這個命令不替換正式 database。報告位於：

```text
.nbs_runtime/restore_drill_latest.json
```

## 一鍵診斷包

```bash
.venv/bin/python scripts/system_manager.py diagnose
```

輸出位於 `.nbs_runtime/diagnostics/`，包含健康狀態、服務 PID、log 尾段、
retention 與 restore drill 報告；不包含 SQLite、Excel、upload 檔案或交易明細。

## HTTP 驗收

```bash
.venv/bin/python scripts/system_manager.py acceptance
```

只有 Streamlit、FastAPI 與 Vue 三個 endpoint 全部 ready 才會回傳
`"status": "passed"`。

目前標準驗收 URL：

```text
Streamlit: http://127.0.0.1:8502/
Vue:       http://127.0.0.1:5173/
API Docs:  http://127.0.0.1:8601/docs
Health:    http://127.0.0.1:8601/api/health
```
