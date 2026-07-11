# P0 Upload Single-Writer Contract Design

日期：2026-07-11  
狀態：Brief Ready，等待使用者審閱後才進入 implementation plan  
專案：`/Users/chanwaitung2025/Downloads/nbs_analytics`

## 1. 任務目標

讓 Streamlit 與 FastAPI / Vue 兩個正式上傳入口共用同一套 upload orchestration，並在不同 process 之間保證同一時間只有一個正式 upload transaction。

本任務同時消除 preflight 對全域 `database.DB_FILE` 的暫時改寫，確保兩個入口在以下契約上完全一致：

- governed monthly baseline gate；
- stability history 與 monthly baseline snapshot；
- rollback；
- cache invalidation / rebuild 狀態；
- response status、錯誤與 audit evidence。

## 2. 不可破壞邊界

- 正式口徑固定為：`不含掛賬核銷與TT退款轉團款`。
- `2026-05` frozen baseline 必須維持 `HKD 12,057,968`。
- `2026-01` 至 `2026-06` monthly baseline 定義、精確金額與目前 monitoring / blocking 模式不變。
- 不改報表計算、sheet schema、ranking、Forecast、GMV 排除邏輯或 UI 篩選語義。
- 不重寫 historical validated rows，不修改既有 acceptance history。
- 不以 Vue、Streamlit table、export rounding 或 analysis-layer 補數修復 drift。
- 自動化測試不得向正式 `nbs_marketing_data.db` 寫入。

## 3. Observed State

目前存在兩個可寫入口：

1. Streamlit：`app_pages._render_upload_area()` 使用 `app_workflows.UPLOAD_OPERATION_LOCK`。
2. FastAPI / Vue：`backend.services.upload_action_service.run_vue_upload_action()` 使用另一個 `UPLOAD_OPERATION_LOCK`。

兩個 lock 都是 process-local `threading.Lock`。Streamlit、FastAPI 由不同 PID 運行，因此無法互相排斥。

Preflight 目前使用 `_temporary_database_path()` 暫時覆寫 module-global `database.DB_FILE`。同一 process 的其他 request 或 thread 在這段期間可能取得錯誤 DB target。

兩個入口亦存在 post-write contract 差異：

- Streamlit 使用 governed stability gate，保存 `monthly_baseline`，並重建 Streamlit dashboard cache。
- FastAPI preflight 使用 governed gate，但 post-write gate 仍直接引用 legacy `stability_service.build_phase2c_stability_gate`。
- FastAPI history context 沒有明確傳入 monthly baseline snapshot。
- FastAPI response 目前聲稱「已重建 dashboard cache」，但該路徑沒有執行 Streamlit session cache rebuild。

## 4. 方案比較

### 方案 A：共享 Orchestrator + SQLite Coordination Lock（採用）

新增單一 upload application service。Streamlit 與 FastAPI 只負責輸入轉換和結果展示；所有 preflight、write、gate、rollback、history 與 cache-generation 決策由 orchestrator 執行。

使用獨立 runtime SQLite coordination DB，例如 `.nbs_runtime/upload_coordination.db`。持有一條 connection 並執行 `BEGIN EXCLUSIVE` 作跨 process lease；第二個入口以短 timeout 取得 lock，失敗時回傳 typed busy result。程序異常退出時，SQLite connection 關閉並自動釋放 lock，不需要 stale lock file recovery。

優點：跨平台、無新增依賴、crash-safe、兩入口 parity 最完整。  
代價：需要整理目前兩套 upload workflow，並為 database / gate service 補明確 `db_path` 參數。

### 方案 B：FastAPI 成為唯一 Writer

Streamlit 將檔案轉交 `/api/upload`，所有寫入只在 FastAPI process 發生。

優點：天然 single process writer。  
缺點：Streamlit 正式上傳依賴 API service 與 HTTP multipart；既有進度 UI、session audit 與測試要大幅改寫。現階段風險高於方案 A。

### 方案 C：只增加 lock file

保留兩套 workflow，只在上傳前建立 atomic lock file。

優點：改動最小。  
缺點：無法解決 gate/history/cache contract 分歧；程序崩潰後還要處理 stale lock。不能達成本 Brief 的完整目標。

## 5. Target Architecture

### 5.1 核心元件

`UploadOrchestrator`

- 以共享 `begin_upload()` operation context 先取得跨 process upload lease。
- lease 成功後，adapter 才讀取 upload bytes，轉成標準化 named payload。
- 接收已取得 lease 的 operation、標準化 inputs 與明確 `live_db_path`。
- 執行 preflight、正式 upsert、post-write governed gate、rollback、history 與 generation update。
- 回傳同一 `UploadResult` contract。
- 不依賴 Streamlit、FastAPI request object 或 Vue。

`UploadLease`

- 使用 `.nbs_runtime/upload_coordination.db`。
- lease metadata 至少包含 operation ID、entry point、PID、started at、source filenames。
- busy 時不等待長排隊；回傳目前 owner metadata，Streamlit 顯示 warning，FastAPI 回傳 HTTP 409。
- 所有 release 都在 `finally`；process crash 由 SQLite 自動解除 transaction lock。

`Database Target`

- `database.get_db_connection()`、load、backup、upsert、restore 等受影響函數接受 `db_path`。
- `db_path=None` 時才沿用正式預設，保留既有 call-site compatibility。
- Preflight 對 temp DB 的所有操作必須明確傳入 `temp_db_path`。
- 移除 upload path 對 `database.DB_FILE` 的 module-global mutation。

`Governed Gate And History`

- Preflight 與 post-write 都只使用 `build_governed_stability_gate(db_path=...)`。
- rollback 二次驗證使用相同 governed gate。
- accepted / rejected / rollback history 使用同一 writer，保存 `monthlyBaseline`、entry point、operation ID 與 stage timings。
- Streamlit 與 FastAPI 不得自行另寫 stability history。

`Cache Generation Contract`

- 每次 accepted write 或 successful rollback 後，原子更新 `.nbs_runtime/data_generation.json`。
- payload 至少包含 generation、DB signature、operation ID、status、updated at。
- Streamlit session cache 保存已載入 generation；不一致時設定 `PROCESSED_DATA_CACHE=None`、`DB_LOADED_FLAG=False`，再以 `include_ai=False` 重建。
- FastAPI upload response 只回報真實狀態，例如 `cacheState: invalidated` 或 `cacheState: streamlit_rebuilt`；未執行 rebuild 時不得聲稱已重建。
- AI / Export content-addressed cache 與報表內容不在本任務修改。

### 5.2 標準資料流

```text
Streamlit adapter OR FastAPI adapter
  -> acquire UploadLease
  -> only after lease success: read/normalize inputs
  -> preflight(temp_db_path explicit)
  -> governed gate on temp DB
  -> formal hot backup + upsert(live_db_path explicit)
  -> governed gate on live DB
  -> rollback if blocking drift
  -> governed gate after rollback
  -> write one stability history record
  -> advance cache generation
  -> release UploadLease
  -> adapter renders the shared UploadResult
```

## 6. UploadResult Contract

兩個入口至少共用以下欄位：

- `operationId`
- `entryPoint`
- `status`: `success | blocked | busy | error`
- `message`
- `sourceFiles`
- `preflightReport`
- `upsertSummary`
- `stabilityGate`
- `monthlyBaseline`
- `rollbackResult`
- `historyRecordId`
- `historyError`
- `writeCommitted`
- `cacheState`
- `dataGeneration`
- `stageTimings`

FastAPI 將 `busy` 映射為 HTTP 409；Streamlit 顯示 lock owner 與 started at，但不得暴露本機敏感路徑或檔案內容。

## 7. Error Handling

- 任何 preflight drift：不寫正式 DB，回傳 blocked result，不寫 stability history，不進 rollback。
- 正式 upsert 後 blocking drift：使用本次 hot backup rollback，再跑 governed gate。
- rollback verification 失敗：狀態為 error，保留 quarantine、backup 與完整 evidence，不推進 data generation。
- history 寫入失敗：不得把它包裝成完整成功；回傳 `historyError`，Hermes 必須 WARNING / FAIL。
- cache generation 更新失敗：DB 已 accepted 時回傳 degraded success，禁止聲稱 cache ready，並要求下一次 UI load 用 DB signature 強制 refresh。
- lease busy：不讀檔、不做 preflight、不建立 backup、不寫 history。

## 8. 預計改動範圍

允許修改或新增：

- `backend/services/upload_orchestrator_service.py`
- `backend/services/upload_lock_service.py`
- `backend/services/upload_action_service.py`
- `backend/services/upload_preflight_service.py`
- `backend/services/stability_history_service.py`
- `backend/services/monthly_baseline_service.py`
- `backend/services/stability_service.py`
- `backend/services/dashboard_service.py`
- `backend/services/dashboard_analytics_service.py`
- `database.py`
- `app_pages.py`
- `app_workflows.py`
- `backend/routers/upload.py`
- `backend/schemas/` 內 upload response schema
- `scripts/system_manager.py` 的 lock / generation health evidence
- 對應 tests 與 Hermes check

原則上不修改：

- `pipeline.py` 的正式清洗、Entity Resolution 與報表計算。
- `forecasting.py`。
- `visuals.py`。
- `frontend/src/App.vue` 的 dashboard 計算；若 response contract 欄位需展示，只做最小 mapping。
- `data/monthly_revenue_baselines.json` 的 baseline 值與 mode。
- 任何正式 workbook schema。

若 implementation plan 發現必須越出上述範圍，先停止並重新取得授權。

## 9. TDD 與驗收矩陣

### 9.1 必須先失敗的測試

- 兩個不同 process 同時 acquire lease，只有一個成功。
- lease holder process 異常結束後，新 process 可再次取得 lease。
- preflight 使用 temp DB 時，另一個 thread / request 仍只讀 live DB。
- Streamlit 與 FastAPI adapter 對相同 inputs 產生相同 gate、monthly baseline、rollback、history 與 cache contract。
- FastAPI post-write 使用 governed gate，不再引用 legacy-only gate。
- FastAPI 未重建 Streamlit session cache時，不得回報「已重建」。
- busy path 不建立 backup、不執行 preflight、不寫 history。
- accepted upload 只寫一筆 history，並保存 operation ID、entry point、monthly baseline 與 timings。
- rollback 後 generation 指向 restored accepted DB，而不是 drifted DB。

### 9.2 必跑驗收

```bash
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py database.py backend/services/upload_orchestrator_service.py backend/services/upload_lock_service.py backend/services/upload_action_service.py backend/services/upload_preflight_service.py backend/services/stability_history_service.py backend/services/monthly_baseline_service.py scripts/system_manager.py

.venv/bin/python -m pytest tests/test_upload_lock_service.py tests/test_upload_orchestrator_service.py tests/test_upload_preflight_service.py tests/test_upload_api.py tests/test_upload_rollback_service.py tests/test_stability_history_service.py tests/test_monthly_baseline_service.py -q

.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py tests/test_dashboard_service.py tests/test_dashboard_api.py tests/test_database_rollback.py -q

.venv/bin/python -m pytest -q

.venv/bin/python scripts/upload_profiling_dry_run.py --rows 25 --include-drift-diagnosis
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py --json
```

### 9.3 必守結果

- 正式口徑 unchanged。
- `2026-05`：`HKD 12,057,968 matched`。
- `2026-01` 至 `2026-06` current monthly checks 全部 matched。
- SQLite integrity `ok`。
- 虛擬 dry-run `liveDbUnchanged: true`。
- Hermes `overallStatus: pass`。
- 正式 Git worktree 在完成回填與 commit 後 clean。

## 10. 不屬於本任務

- Streamlit rerun 12–15 秒固定耗時優化。
- 全表 repair migration / active-page routing。
- Cache retention 或 storage cleanup。
- API dashboard snapshot cache。
- Background upload queue、取消、重試或多 worker job system。
- DuckDB、Polars、Postgres 或其他語言重寫。

## 11. 第二 Brief Gate

只有在本 P0 完成以下條件後，才建立 `Streamlit Rerun Hot Path` Brief：

1. P0 implementation 已獲使用者授權並完成。
2. Full pytest、baseline、system acceptance、Hermes 全部 PASS。
3. Obsidian 已回填實際 changed files、測試、風險與 commit。
4. Git 已建立清楚版本節點且 worktree clean。

第二 Brief 的目標才是移除每次 rerun 的 no-op repair scan、hidden tab DB reload 與約 12–15 秒固定等待；不得與本 P0 同批修改。

## 12. Implementation Gate

本文件只完成 Brief / design。未取得使用者對本書面 spec 的明確批准前：

- 不修改 production code；
- 不新增 runtime lock DB；
- 不執行正式 upload / upsert / rollback apply；
- 不進入第二 Brief。
