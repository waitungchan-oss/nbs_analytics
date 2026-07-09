# NBS 系統總覽

更新日期：2026-07-02  
專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`  
目的：讓後續 Codex 在接手 `nbs_analytics` 前，先建立正確系統地圖，避免把資料口徑、analysis layer、upload layer、Vue frontend 和 Streamlit baseline 混在一起。

---

## 1. 一句話總結

NBS Analytics 是一套本地營銷數據與 AI 預測系統：

```text
Excel 原始資料
→ Python 清洗與 SQLite 入庫
→ 正式營收 analysis frames
→ Streamlit / FastAPI / Vue 顯示與匯出
→ AI Forecast / Backtest / Data Quality / Governance 診斷
```

正式營收基準不是 Excel 原始總額，而是：

```text
不含掛賬核銷與TT退款轉團款
```

這個口徑是所有 Dashboard、API、Vue、Export、Forecast、WAPE、驗收測試的共同底線。

---

## 2. 系統分層

### 2.1 原始資料層

來源是多個 Excel：

- 營收主表：正式收款金額與正式營收日期來源。
- 旅行團副表：補充交易、產品、團隊、銷售點、線路等資訊。
- 其它業務副表：票務、交通、酒店、套票等資料。

最重要規則：

```text
正式收入日期 = 主表「收款時間」
副表「交易時間」只作補充，不可替代正式收入日期
```

### 2.2 清洗與入庫層

主要檔案：

| 檔案 | 職責 |
|---|---|
| `pipeline.py` | Excel 讀取、欄位清洗、主副表匹配、分社/專職/產品分類、workbook sheets 建構 |
| `database.py` | SQLite upsert、熱備份、刪舊寫新、欄位兼容、Frozen Baseline write-time guard |
| `config.py` | 欄位常數、分社規則、專職銷售代表、郵輪部門、rules config |
| `backend/services/upload_preflight_service.py` | Upload preflight response contract 與 `batchSummary` |

SQLite 保存的是清洗後明細，不是最終 KPI，不是正式口徑聚合，也不是 AI 模型。

主要表：

- `tour_data`
- `others_data`
- `temp_ids`：upsert 技術輔助表，不是業務表。

### 2.3 Analysis Layer

Analysis layer 從 SQLite 讀取明細後派生正式口徑。

正式口徑：

```text
排除 收款類型 = 掛賬核銷
排除 收款方式 = TT 退款轉團款
```

注意：Frozen Baseline 後，不能用「同一來源單據號只要後續出現掛賬核銷，就回溯排除歷史正常收入」的舊理解。後續新上傳的排除類收款行本身不計入，但不得 retroactively 改動已驗收月份的正常收款。

主要相關檔案：

| 檔案 | 職責 |
|---|---|
| `backend/services/revenue_scope_service.py` | API / backend 端正式口徑服務 |
| `app_workflows.py` | Streamlit workflow 中的正式 analysis frames、cache、quality、export helper |
| `backend/services/dashboard_service.py` | Dashboard summary read model |
| `tests/test_phase2_precheck_acceptance.py` | 核心 baseline 驗收 |

### 2.4 UI 與 API 層

目前前後端已分離，但 Streamlit 仍是重要 baseline：

| 入口 | 預設 URL | 用途 |
|---|---|---|
| Streamlit | `http://127.0.0.1:8502/` | 正式 baseline UI、上傳、補算 AI、舊有完整操作入口 |
| FastAPI Docs | `http://127.0.0.1:8601/docs` | API contract、health、dashboard、upload、stability、history |
| Vue | `http://127.0.0.1:5173/` | 漸進式前端、狀態透明化、cockpit 與 upload/report 入口 |

Streamlit 已完成 Phase 2R 模組化：

| 檔案 | 職責 |
|---|---|
| `app.py` | thin defensive entrypoint |
| `app_pages.py` | Streamlit tabs、upload UI、dashboard sections、GMV tab |
| `app_workflows.py` | upload/cache/quality/forecast/export workflow helpers |
| `app_styles.py` | CSS 與 theme token |
| `streamlit_rendering.py` | sidebar、section、shared rendering |

後續改 UI，不要再把所有東西塞回 `app.py`。

### 2.5 AI 與診斷層

AI 與診斷都應從正式口徑 analysis frames 出發：

- Daily Forecast
- 7-Day Macro Forecast
- Month-End Macro Forecast
- Daily WAPE
- Macro Backtest
- Forecast Governance
- Feature Store / Lead Signal
- Data Quality Scorecard
- Entity Resolution Audit
- Causal Analytics

這些診斷不應回寫 SQLite，也不應修改正式口徑。

---

## 3. 高風險邊界

### 3.1 不要把 analysis layer 當作修資料入口

如果 baseline drift，先查：

1. upload write path
2. SQLite upsert
3. excluded receipt row guard
4. quarantine DB
5. rollback result
6. acceptance history

不要第一時間改 dashboard summary 或前端顯示去「湊回數字」。

### 3.2 不要讓 Vue 重算正式口徑

Vue 只能展示 API 回傳的正式結果。不可在 Vue 中自行：

- 重算 `不含掛賬核銷與TT退款轉團款`
- 重組 branch ranking
- 重算 specialist revenue
- 重算 Forecast consensus
- 重算 GMV 排除財務結果

### 3.3 不要把 freshness update 當成 drift

新資料上傳後，最大日期、row count、analysisRows、excludedRows 有變化是 freshness update，不一定是 core drift。

Core drift 只應指：

- Frozen baseline month revenue 變了
- 正式口徑定義被破壞
- branch / specialist baseline 不一致
- rollback 後仍不一致

### 3.4 不要跳過驗收

任何改動只要碰到以下任一類，都必須跑驗收：

- upload
- database
- revenue scope
- dashboard summary
- export
- rollback
- baseline tests
- Vue / API contract
- Streamlit modularization

---

## 4. 核心數字錨點

第一個必守 baseline：

```text
月份：2026-05
範圍：2026-05-01 至 2026-05-31
視角：全部分社 + 全部專職銷售組
正式口徑：不含掛賬核銷與TT退款轉團款
分社營收：HKD 6,658,144
專職銷售組營收：HKD 5,399,824
分社 + 專職銷售組總營收：HKD 12,057,968
```

如果任何改動後這個數字不等於 `12,057,968`，不得宣稱驗收通過。

---

## 5. 接手前必讀文件

建議後續 Codex 先讀：

1. `Summay/NBS 系統總覽.md`
2. `Summay/ADR-001-Frozen Baseline 保護.md`
3. `Summay/2026-06-25 Full Snapshot Baseline Drift.md`
4. `Summay/驗收基線.md`
5. `NBS_ANALYTICS_HANDOFF.md`
6. `NBS_ANALYTICS_SYSTEM_MAP.md`
7. `NBS_SQLITE_DATABASE_GUIDE.md`

---

## 6. 最短操作準則

每次修改前先回答：

```text
這次改的是 raw data、write path、analysis layer、API contract、UI，還是 export？
會不會改 SQLite？
會不會改正式口徑？
會不會影響 2026-05 baseline？
是否需要 rollback / quarantine / acceptance history？
驗收命令是什麼？
```

答不清楚，不要動正式資料鏈路。

