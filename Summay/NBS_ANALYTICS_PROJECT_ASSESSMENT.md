# NBS Analytics 專案全面分析與評估報告

> 評估日期：2026-08-18
> 專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`
> 評估範圍：系統架構、程式碼品質、資料治理、agent 治理機制、測試與驗證現況、安全風險
> 評估方式：靜態盤點 + 四個維度的程式碼深讀 + 實際執行驗證（py_compile / pytest / 業務日曆 / SQLite / Hermes）

---

## 1. 摘要（Executive Summary）

NBS Analytics 是一套**香港中旅（NBS）本地營銷數據與 AI 預測系統**，本質是「企業營運駕駛艙」，資料鏈路為：

```text
Excel 原始資料 → Python 清洗 + SQLite 入庫 → 正式營收 analysis frames
→ Streamlit / FastAPI / Vue 顯示與匯出 → AI Forecast / 回測 / 資料品質 / 治理診斷
```

核心業務口徑固定為「**不含掛賬核銷與TT退款轉團款**」，2026-05 frozen baseline 為 `HKD 12,057,968`。

**總體判斷一句話**：這是「**業務核心工程品質高、但被過度膨脹的自我治理 meta-layer 反噬**」的專案。核心資料治理閉環（single-writer、frozen baseline、rollback、generation 簽章）是高品質的；但 agent 治理層已膨脹到與核心業務幾乎 1:1，且存在明顯的 P0 安全缺口（無認證、任意檔案讀取、pickle RCE），顯示「治理嚴謹」並未覆蓋到最基礎的 HTTP 安全邊界。

---

## 2. 專案定位與核心價值

### 2.1 業務定位

面向香港中旅銷售與營銷部門的**內部企業分析與 AI 決策 dashboard**，服務對象為管理層、營運、銷售與分析人員，用於監控淨收入、產品組合、分社業績、銷售渠道貢獻、AI 預測、資料品質、模型治理與可匯出的稽核報表。

### 2.2 三個主 Tab

| Tab | 內容 |
|---|---|
| 經營分析大盤 | 正式營收、分社/專職、產品下鑽、Data Quality、Entity Audit、AI Cleaning、AI Forecast、Forecast Governance、Feature Store、Causal Analytics、Export |
| 業務規則配置 | 分社代碼、排除前綴、專職銷售代表、郵輪部門、資料庫管理 |
| GMV 排除訂單看板 | session-only 扣除匹配訂單的獨立 GMV 視角 |

### 2.3 核心業務規則

- 正式收入日期 = 主表 `收款時間`；副表 `交易時間` 只作補充，不可替代。
- 正式口徑 = SQLite 清洗後明細 − `收款類型 = 掛賬核銷` 的來源單據號 − `收款方式 = TT 退款轉團款` 的來源單據號。
- SQLite 保存清洗後明細，不保存最終 KPI、正式口徑聚合或 AI 模型。
- GMV 排除是 session-only 派生視角，不回寫 SQLite，不污染正式 export cache。

---

## 3. 系統架構總覽

### 3.1 分層架構

| 層 | 主要模組 | 職責 |
|---|---|---|
| 原始資料層 | Excel 營收主表 / 旅行團副表 / 其它業務副表 | 正式收款與交易補充資訊 |
| 清洗入庫層 | `pipeline.py`、`database.py`、`config.py`、`backend/services/upload_*` | 清洗、主副表匹配、SQLite upsert、熱備份、單寫入協調 |
| Analysis 層 | `backend/services/revenue_scope_service.py`、`app_workflows.py` | 從明細派生正式淨口徑 |
| UI/API 層 | Streamlit（`app.py`/`app_pages.py`/`app_workflows.py`）、FastAPI（8 routers / 40 services）、Vue 3 | 顯示、上傳、匯出 |
| AI/診斷層 | `forecasting.py`、`business_calendar.py`、`visuals.py` | ARIMA/Prophet/LightGBM/Fusion + 診斷 |

### 3.2 三大入口

| 入口 | 預設 URL | 用途 |
|---|---|---|
| Streamlit | `http://127.0.0.1:8502/` | 正式 baseline UI、上傳、補算 AI |
| FastAPI | `http://127.0.0.1:8601/docs` | API contract、health、dashboard、upload、stability |
| Vue | `http://127.0.0.1:5173/` | 漸進式前端、cockpit 與上傳/報表入口 |

### 3.3 資料生命週期

```text
Excel 上傳 → Streamlit/FastAPI adapter → shared cross-process lease
→ preflight temp DB + 日期來源診斷 → governed blocking gate
→ live DB hot backup + upsert → post-write governed gate
→ matched：寫 history + 推進 generation；drift：rollback + 二次 gate
→ release lease
```

### 3.4 Agent 治理子系統（疊加在業務系統之上）

`backend/agents/` 約 40 個模組 + `scripts/` 35 個腳本，構成一套 Codex agent 治理流水線：

```text
Brief → Context Agent (collect-only) → Codex 規劃 → 使用者授權
→ Implementation Agent (單一 Task) → Review Agent (findings-first)
→ Full Verification → Hermes (read-only acceptance)
→ Documentation Agent (proposal-only)
```

---

## 4. 量化規模盤點

| 維度 | 數字 |
|---|---|
| Git commits / tracked files | 499 commits / 603 檔 |
| 核心業務程式（app/pipeline/forecasting/database + backend services） | ≈ 19,130 行 |
| `backend/agents/`（agent 治理層） | **≈ 19,332 行** |
| 測試（189 檔） | 31,235 行 |
| 文件（161 檔 Markdown） | 34,484 行 |
| frontend（Vue 3） | 3,788 行 |
| scripts | 5,525 行 |
| 頂層模組（app_*/forecasting/pipeline 等） | 14,024 行 |
| agent runtime | 169 個 runs、230 份 reports、117 個 locks |
| git worktrees | **21 個，共佔 9.2 GB 磁碟** |
| SQLite + 備份 | 主 DB 20.9 MB + 約 60 份 `.backup_*` |
| 資料 generation | 31（最新 2026-08-18，資料到 2026-08-17） |

**關鍵觀察**：`backend/agents/`（19,332 行）與核心業務（~19,130 行）幾乎 **1:1**。其中 governance_graph（4,486 行）+ 三套記憶機制 memory_hub / memory_sidecar / short_term_offload（5,147 行）佔 agents 逾半，而真正幹活的 4 個 agent service（context/review/implementation/documentation）僅 2,318 行。agent 相關測試 25,618 行 > agent 實作 19,332 行，呈「測試倒掛」。

---

## 5. 優點（值得肯定）

1. **資料治理紀律確實高**。核心資料鏈路具備 cross-process single-writer lease、上傳前 preflight temp-DB、寫入前熱備份、`integrity_check`、governed blocking gate、drift 自動 rollback、stability history、DB signature + generation token 的完整閉環。正式口徑在**入庫（`database.py` L26-27/L234-266）、報表（`pipeline.py` L1142-1195）、UI（`app.py` L104-107）三層一致**落地。這是真正財務級資料系統該有的嚴謹度。

2. **AI 預測層分離清楚**。正式 tracks（ARIMA/Prophet/LightGBM/Fusion）與診斷/實驗（Normal-Day Tight Guardrail、Two-Lane Selector、Feature Store）明確分離，診斷不回寫正式 forecast，避免「實驗污染正式」。

3. **可測試性意識強**。大量 service 採無狀態 module-function + Callable 依賴注入（如 `upload_orchestrator_service.py`、`receipt_exclusion_governance_service.py`），型別標注普遍到位（`from __future__ import annotations`、`str | None`、`dataclass(frozen=True)`）。

4. **模組化有在推進**。`app.py` 已瘦身成 125 行 defensive entrypoint；上傳路徑的手動 DI 注入做得好。

5. **SQLite 還原防護良好**。使用官方 `backup()` API 熱備份、`PRAGMA integrity_check` 驗證、restore 前先 quarantine + 暫存檔 + `os.replace`，delete+append 在單一 `commit` 內具原子性。

---

## 6. 問題與風險（依嚴重度）

### 🔴 P0 — 安全與正確性

1. **API 完全無認證/授權**。`POST /api/upload`、`PUT /api/decisions/targets` 直接寫 DB/config，`backend/main.py` 只有 CORS（且 `allow_origins` 硬編碼 localhost 5173/5174）。任何能連到 8601 的人都能寫入正式資料。
2. **任意檔案讀取**：`backend/routers/health.py` 的 `verification_profile` query 參數直接 `Path()` + 讀檔，未限制在 project_root 內。
3. **pickle 反序列化 RCE**：`backend/services/dashboard_facts_service.py:93` 用 `pickle.loads()` 反序列化 `.nbs_runtime_cache` 檔案，若該目錄可被寫入即有 RCE 風險。
4. **例外細節外洩**：`upload.py:40`、`upload_orchestrator_service.py:157/195/202` 以 `f"{type(exc).__name__}: {exc}"` 直接回傳 500。
5. **SQLite 無並行鎖**：靠 application 層 DELETE-IN 去重（`temp_ids` 是全庫共用暫存表），DB 本身沒有 UNIQUE 約束、`busy_timeout` 或 WAL。cross-process lease 緩解了多寫入，但非 DB 層保證；多瀏覽器 session 併發會互相覆蓋 `temp_ids`。

### 🟠 P1 — 架構耦合與重複

1. **`config.py` import `streamlit`**（L5），而 `database.py`→`config`、services→`database`，導致 FastAPI 啟動就拖進整個 Streamlit 與 pipeline，backend 不再「純 HTTP 服務層」。
2. **dashboard 重複實作**：`_apply_filters`（`dashboard_service.py`）≈ `_filtered`（`dashboard_analytics_service.py`）、兩份 `_ranking*`、兩份 `_money_text`，且存在兩個**同名異義**的私有 `_current_rules`（4-tuple vs 3-tuple）。
3. **無快取，每 request 重跑 pipeline**：context/summary/analytics 各自獨立 `load_all_data_from_db + build_revenue_scope_frames + build_dashboard_data`，只有 `/facts` 有 pickle cache。
4. **巨型模組仍舊存在**：
   - `app_pages.py`（2593 行）從 `app_workflows` 匯入約 70 個名稱（含私有底線函式），還有 `from streamlit_rendering import *`（L124）。
   - `forecasting.py`（3408 行）有 252 行的 `run_ai_backtest_report` 與大量硬編碼魔術數字（0.72/0.35/0.52/0.28…）。
   - Streamlit 層最長函式：`_render_ai_and_exports`（276 行）、`_render_backtest_report`（257 行）、`_render_upload_area_legacy`（238 行）。
   - 版本失效區塊在 `app.py` L79-102 與 `app_workflows.py` L88-111 **幾乎逐字重複**。
   - 測試 `test_app_module_boundaries.py` L104-116 甚至明文化「私有 helper 洩漏到 app_pages namespace」——直接違反 thin+modular 宣稱。
5. **口徑字串散落**：「掛賬核銷 / TT 退款轉團款」在 `app_pages` 檔名與文案散落 20+ 處、無單一常數來源；`REVENUE_SCOPE` 常數在 `app.py` 內即重複定義兩次。
6. **靜默吞錯**：`forecasting.py` L594 與 cache loader L934-935 用 `except Exception` 靜默回 `None`，把預測/快取失敗偽裝成「無結果」。
7. **效能**：12–15 秒 rerun 主因是 rolling backtest 重訓 ~30 cutoff × 2 策略；加上 `importlib.reload(forecasting)` 被執行兩次（`app.py` L36 + `app_workflows.py` L45），冷啟動重複載入 statsmodels/prophet/lightgbm。

### 🟡 P2 — 過度工程（最重要的架構性觀察）

這是本專案最突出的問題。**agent 治理層已膨脹到與核心業務幾乎 1:1**：

- `backend/agents/` 19,332 行 ≈ 核心業務 ~19,130 行；governance_graph（4,486）+ 三套記憶機制（5,147）佔 agents 逾半，真正幹活的 4 個 agent service 僅 2,318 行。
- **三套記憶機制功能重疊**：`memory_hub`（1,985 行）/ `memory_sidecar`（2,448 行）/ `short_term_offload`（714 行）各自重複實作 fingerprint、bounded-cap、deny-pattern、ACL，卻大多只餵 read-only Streamlit 顯示，只有 Sidecar 以 non-authoritative hint 微弱接入正式 pipeline。
- **Governance Graph D1–E4**（24 檔 4,486 行）是 read-only 衍生 read model，**不驅動任何決策**，只供顯示。
- **測試倒掛**：agent 相關測試 25,618 行 > agent 實作 19,332 行，且集中在 schema/fingerprint 邊界，而非 business rules / baseline。真正承載資料正確性的 `pipeline.py` / `database.py` / `forecasting.py` **直接單元測試稀少**。
- 多個 service（`governance_telemetry_service`、`restore_drill_service`、`verification_runtime_snapshot`、965 行的 `agent_operations_service`）**不被任何 FastAPI router 使用**，只被 Streamlit/scripts/tests 呼叫，放在 `services/` 造成職責模糊。
- **文件治理滯後**：文件記錄的驗收數字（329/474/350 passed）已落後於實際測試套件（2000+ tests），`SYSTEM_MAP.md` 記載的資料狀態（6,676/22,571 行、2026-07-13）也已落後於現況（7,390/27,855 行、2026-08-17）。

**判斷**：呈現典型的「為治理而治理 / yak-shaving」螺旋 —— 為了治理 agent 而不斷建立更複雜的治理工具，而治理工具本身又需要更多治理。核心業務（本質上單人使用的本地 dashboard）並不需要 40 個 agent 模組 + 21 個 worktree + 169 次 agent run 來支撐。

**實際執行依賴的分層**（誠實拆解哪些真會跑 LLM）：
- 真正發網路：`--agent-command`（codex/claude runner）、Documentation 的 `codex exec`、`hermes_turn_receipt`/`hermes_live_ab` 用 openai SDK 打 `api.deepseek.com`（`deepseek-v4-flash`）。
- 純本地確定性可跑：`--collect-only`、`governance_graph build/validate/status`、`memory_hub provision`、`short_term_offload store`、`hermes_post_change_check`。
- 證據/檢視層（不驅動決策）：`memory_sidecar_provider_adapter`（本身不發網路）、`hermes_live_ab`/`runner_capability_evidence`（多被 preflight 擋 blocked）、Governance Graph D2–E4。

### 🟣 P3 — 維運

- 路徑硬編碼在 source-tree 相對路徑（`rules.py:9-11`、`monthly_baseline_service.py:16-17`），`health.py:32-33` 用 CWD 相對 `Path(".nbs_runtime_cache")`，換工作目錄即錯。
- `main.py` 無 lifespan handler、無全域例外處理、無 request logging/rate-limit。
- CI 只有 1 個 workflow（Hermes Governance Graph），只跑 6 個測試檔，未跑完整套件。
- `.worktrees/` 佔 9.2 GB，約 60 份 DB 備份累積，缺清理策略。

---

## 7. 驗證現況（實測結果）

### 7.1 健康檢查（全數通過）

| 檢查項 | 結果 |
|---|---|
| py_compile（核心檔案） | ✅ 通過 |
| `validate_business_calendar.py` | ✅ 2024/25/26 假日 17/17/17，expo 11/12/8 |
| `inspect_sqlite_latest.py`（integrity） | ✅ ok；tour_data 7,390 列、others_data 27,855 列、max_date 2026-08-17 |
| 2026-05 baseline = `HKD 12,057,968` | ✅ matched |
| monthly-baseline | ✅ blocking 且 matched |
| system-monitor（sqliteIntegrity） | ✅ ok |

### 7.2 完整測試套件（修復前 vs 修復後）

| 狀態 | 結果 |
|---|---|
| 修復前 | **28 failed / 1977 passed**（27 環境性缺 `rg` + 1 邏輯性 notifier） |
| 修復後 | **2004 passed / 1 failed**（flaky 並行 lease 測試，單獨執行通過） |

### 7.3 Hermes 整體判定

`overallStatus` 仍為 `fail`，唯一殘留原因是**運行中的 Vue 服務** `service_identity_unavailable`（`ownerMatch/identityMatch=false`）——這是服務在無 verification profile identity 下啟動的 runtime 環境問題，非程式缺陷。其餘 required 檢查全過：targeted-tests（788 passed）、implementation-agent-integration-tests、core-tests、phase2-baseline、monthly-baseline、system-monitor。

---

## 8. 總體評價

**核心系統是扎實且可信的**：資料清洗、正式口徑、frozen baseline、single-writer、rollback、generation 簽章的治理閉環，在本地財務報表系統中是難得的高紀律工程。AI 預測與診斷分離、read-only 診斷層不污染正式資料，這些設計都是對的。

**但專案已被一個比例失衡的 meta-agent-governance 層拖累**：治理程式碼 ≈ 核心程式碼、測試 > 實作、文件 34K 行、21 個 worktree、169 次 agent run，而這一切是為了一個本質上單人使用的本地 dashboard。這既是維運負擔，也是認知負擔，且 P0 安全缺口顯示「治理嚴謹」並未覆蓋到最基礎的 HTTP 安全邊界。

---

## 9. 建議優先序

1. **先堵 P0 安全**：API 加認證（至少 upload/targets PUT）；`verification_profile` 限制在 project_root 內並 whitelist 檔名；`pickle.loads` 改 JSON + signature 驗證或移出可寫目錄；統一 exception handler 不外洩內部型別。
2. **收斂 agent 治理**：三套記憶機制合併為一套 read-only catalog；凍結 `governance_graph` D2–E4 的繼續擴張；把測試重心移回 business rules / baseline / upload。
3. **清理 P1 重複與耦合**：抽出共用 filter/ranking/reconciliation；把 `streamlit` 依賴從 `config.py` 剝離（FastAPI 不 import Streamlit）；消除 `_current_rules` 同名異義、版本失效區塊重複與 20+ 處散落口徑字串（收斂為單一常數模組）。
4. **維運**：路徑改由環境變數注入；補 `main.py` lifespan + 統一錯誤 middleware；讓 CI 至少跑完整測試套件；清理 9.2 GB worktree 與約 60 份 DB 備份。

---

## 10. 附錄：本次已執行的修復（2026-08-18）

在完成評估後，依評估結論執行了兩項**零業務風險**修復：

### 修復 1：macOS 通知器路徑遮蔽 bug

- 檔案：`backend/agents/workflow_notifications.py`
- 根因：`_sanitize()` 的環境變數替換迴圈把單一字元 `"/"` 也當成「敏感值」替換，破壞後續 `_ABSOLUTE_PATH` 的路徑遮蔽，導致檔名 `secret.txt` 外洩。
- 修復：新增 `_MIN_ENV_VALUE_LENGTH = 4` 門檻，環境變數值長度 < 4 的不再視為敏感值替換。
- 驗證：`test_workflow_notifications.py` 由 1 failed → **7 passed**。

### 修復 2：evidence_collector 對 ripgrep 的硬依賴

- 檔案：`backend/agents/evidence_collector.py`
- 根因：`_run()` 執行 `subprocess.run(["rg", ...])` 時，系統缺 `rg` 會拋 `FileNotFoundError` 一路崩潰，導致 27 個測試失敗。
- 修復：`_run()` 捕獲 `FileNotFoundError`；新增 `_rg_fallback_command()`，改用確定性的 Python 固定字串搜尋，維持 `rg --files-with-matches --fixed-strings` 語意與 `rg-query-*` evidence contract。ripgrep 從此成為可選加速器。
- 驗證：`test_evidence_collector.py` 3 failed → **22 passed**；受影響 7 個 agent 測試檔 105 passed；完整套件由 28 failed → **2004 passed / 1 flaky**。

### 殘留（未修，與本次修復無關）

1. **flaky 測試**：`test_upload_single_writer_integration.py::test_two_processes_produce_exactly_one_formal_write` 在完整套件滿載下偶發失敗（並行 lease 時序競態），單獨執行通過。
2. **Vue runtime identity**：運行中的 Vue 服務缺 verification profile identity，導致 Hermes `system-acceptance` 未過。這是服務啟動方式問題，非程式缺陷。

---

*本文檔為分析與評估報告，僅作現況記錄與決策參考，不修改任何正式資料、baseline 或業務口徑。*
