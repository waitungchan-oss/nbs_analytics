# NBS Analytics 最新交接說明

更新日期：2026-06-30  
專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`  
正式收入口徑：`不含掛賬核銷與TT退款轉團款`

---

## 1. 交接重點

後續所有 NBS 營銷數據、AI 銷售預測、SQLite、GMV 排除訂單、Streamlit 駕駛艙與報表匯出工作，都應在以下專案中處理：

```text
/Users/chanwaitung2025/Downloads/nbs_analytics
```

不要修改：

```text
/Users/chanwaitung2025/Downloads/dashboard-project
```

`dashboard-project` 是另一個展示型旅遊 dashboard；目前這套 AI / SQLite / NBS 收入分析系統在 `nbs_analytics`。

---

## 2. 目前系統狀態

NBS Analytics 已是一套本地企業營運駕駛艙，包含：

- Streamlit cockpit UI。
- 深色 / 淺色主題切換。
- SQLite 本地持久化。
- Excel 上傳、清洗、upsert、熱備份。
- 上傳日期來源診斷：主表 `收款時間` vs 副表 `交易時間`。
- 正式淨收入口徑：不含 `掛賬核銷` 與 `TT 退款轉團款`。
- Daily / 7-Day / Month-End AI Forecast。
- Daily WAPE 診斷、Normal-Day、Two-Lane Selector 實驗。
- Macro WAPE 回測。
- Data Quality Scorecard：資料品質健康檢查。
- Entity Resolution Audit：單號匹配稽核。
- AI-assisted Data Cleaning：本地智能清洗建議，人工確認後才落規則。
- Forecast Governance：模型健康治理，不只看 WAPE。
- Feature Store / Lead Signal：預測特徵與先行信號庫。
- Causal Analytics：營收變動解釋，v1 是 driver analytics，不宣稱嚴格因果。
- Lazy Export：正式大型 Excel workbook 按需生成。
- 第 3 個 tab：`GMV 排除訂單看板`。
- Windows / VS Code / NVIDIA GPU handoff 文件與腳本。
- Streamlit 已完成 Phase 2R 模組化瘦身：`app.py` 保持 thin defensive entrypoint，頁面編排在 `app_pages.py`，上傳/快取/診斷/export workflow helper 在 `app_workflows.py`，樣式在 `app_styles.py`，共用渲染在 `streamlit_rendering.py`。
- Phase 2I 一鍵啟動器已管理 Streamlit / FastAPI / Vue；同專案既有 Streamlit 佔用 `8502` 時會採納為受管理服務，不再誤報 unmanaged process。
- Upload preflight `batchSummary` 已補齊批次最早/最晚收款時間、金額合計與 `包含 2026-06-15` 等欄位，避免清洗成功後出現錯誤日期提示。

目前主 tabs：

```text
經營分析大盤
業務規則配置
GMV 排除訂單看板
```

---

## 3. 重要業務邊界

### 3.1 正式經營分析

正式經營分析使用 SQLite 明細讀出後的 analysis frames：

```text
SQLite 清洗後資料
- 收款類型 = 掛賬核銷 的來源單據號
- 收款方式 = TT 退款轉團款 的來源單據號
```

這是 dashboard、AI Forecast、WAPE、正式 export 的基礎。

### 3.2 GMV 排除訂單看板

GMV 排除訂單看板是獨立分析視角：

```text
上傳交易號碼清單
→ 匹配 SQLite 來源單據號
→ session-only 扣除匹配訂單
→ 顯示 GMV 排除後 KPI / 明細 / 未匹配
→ 生成 GMV 排除版完整報表
```

它不做：

- 不回寫 SQLite。
- 不改正式經營大盤。
- 不改 AI Forecast / WAPE。
- 不污染正式 export cache。

### 3.3 6/15 日期排查原則

正式收入日期看主表：

```text
主表 收款時間 = 正式營收日期
副表 交易時間 = 交易補充資訊
```

如果副表有 6/15 `交易時間`，但主表沒有 6/15 `收款時間`，正式看板不會新增 6/15 收入。系統目前已加入 preflight / batch summary 提示，避免誤判為 dashboard 或 export 未刷新。

截至 2026-06-30，本機正式 SQLite 已包含 2026-06-15 收款資料；先前「本次清洗後批次未解析到 2026-06-15」是 upload preflight UI contract 欄位不足造成的 false warning，已在 `backend/services/upload_preflight_service.py` 修正。後續若再次看到這類提示，應先展開「查看上傳預演結果」，檢查 `batchSummary` 的最早/最晚收款時間、金額合計與 `包含 2026-06-15`。

### 3.4 只讀診斷與實驗層

以下能力都不回寫 SQLite，不覆蓋正式 Daily Forecast，也不改正式 WAPE：

- `Data Quality Scorecard`
- `Entity Resolution Audit`
- `Forecast Governance`
- `Feature Store / Lead Signal`
- `Causal Analytics`

`AI-assisted Data Cleaning` 也是先建議、後人工確認；套用後只更新 `rules_config.json` 支援的既有規則欄位。

---

## 4. 主要文件

| 文件 | 用途 |
|---|---|
| `NBS_ANALYTICS_SYSTEM_MAP.md` | 系統全景、模組串聯、流程導航 |
| `NBS_SQLITE_DATABASE_GUIDE.md` | SQLite 設計、寫入/讀取、排查方法 |
| `DESIGN.md` | Stitch UI redesign 設計契約 |
| `WINDOWS_VSCODE_GPU_HANDOFF.md` | Windows + VS Code + NVIDIA GPU 遷移與安裝 |
| `NBS_ANALYTICS_HANDOFF.md` | 本交接文件 |

---

## 5. 主要程式入口

| 任務 | 主要檔案 |
|---|---|
| Streamlit app thin entrypoint / defensive bootstrap | `app.py` |
| Streamlit tabs、dashboard page orchestration、GMV tab | `app_pages.py` |
| Upload、cache、quality、forecast、export workflow helpers | `app_workflows.py` |
| Streamlit theme CSS 與視覺 token | `app_styles.py` |
| Sidebar / section / shared rendering helpers | `streamlit_rendering.py` |
| Excel 清洗、主副表匹配、正式 workbook sheets | `pipeline.py` |
| SQLite upsert、熱備份、讀取、修復 | `database.py` |
| 欄位常數、業務規則、rules_config | `config.py` |
| AI Forecast / Backtest / WAPE 診斷 | `forecasting.py` |
| 香港假期與旅遊展特徵 | `business_calendar.py` |
| 圖表樣式 | `visuals.py` |
| Streamlit / FastAPI / Vue 一鍵啟停、健康檢查、驗收 | `scripts/system_manager.py` |
| SQLite 最新狀態檢查 | `scripts/inspect_sqlite_latest.py` |
| AI cache 預熱/狀態 | `scripts/prewarm_ai_cache.py` |
| 日曆驗證 | `scripts/validate_business_calendar.py` |

---

## 6. 當前已完成能力

### 6.1 Dashboard / UI

- 企業營運駕駛艙風格。
- 深色 / 淺色主題切換。
- 左側 sidebar 已拆成 `Navigation` 與 `Control Center`：
  - `Navigation` 使用 submenu 分組與 hash anchor 做頁內跳轉，不刷新整個 Streamlit app。
  - `Control Center` 只保留主題、年份、月份、日期、分社、專職與套用/重設篩選。
  - Menu item 以 badge 標示 `正式`、`只讀`、`稽核`、`人工確認`、`宏觀`、`回測`、`治理`、`診斷`、`解釋型`、`匯出`。
  - Active 狀態由 CSS `:target` / `:has()` 驅動；不要再改回 `?nav=` query 方案，否則點擊 menu 會觸發頁面 rerun。
- KPI cards、排行榜、產品下鑽、AI Forecast、Model Diagnostics、Export。
- Data Quality、Entity Audit、AI Cleaning、Forecast Governance、Feature Store、Causal Analytics 已接入經營分析大盤。
- 三個主 tab，GMV 排除訂單看板獨立於正式經營大盤。
- `app.py` 已不再承擔大段 UI 與 workflow 定義；後續改 Streamlit 畫面優先看 `app_pages.py` / `streamlit_rendering.py`，改上傳與 cache 流程優先看 `app_workflows.py`。

### 6.2 SQLite / 上傳

- `tour_data / others_data` 都已存在。
- 目前 SQLite 最大日期已到 `2026-06-29`，最新收款時間約 `2026-06-29 19:08:12`。
- 2026-06-15 已存在於正式 SQLite；若 UI 顯示「未解析到 2026-06-15」，優先視為 upload preflight 顯示/contract 排查，不要直接判定資料庫缺日。
- 上傳前會檢查主表/副表日期來源。
- upsert 前自動熱備份。
- upsert 摘要會顯示寫入前後 row count、最大日期、覆蓋/追加情況。
- Data Quality / Entity Resolution / Causal Analytics 都從 SQLite 明細派生，不新增 DB table。

### 6.3 AI Forecast

- Daily Forecast：逐日波動預測。
- 7-Day Macro Forecast：未來 7 日總額，不是自然週。
- Month-End Macro Forecast：MTD actual + 本月剩餘預測。
- Daily / Macro 回測與模型健康燈號。
- 上傳後的快速重建預設先跳過 AI / backtest 重算；若需要補算，請在 AI Forecast 區按「補算 AI」按鈕手動觸發完整快取重建。
- Forecast Governance 綜合 Accuracy、Bias、Stability、Sample、Role，不取代 WAPE。
- Feature Store / Lead Signal 會檢查 `NoFutureLeak`，作為下一輪 Daily WAPE 實驗依據。
- Normal-Day Tight Guardrail 只作診斷最佳模型，不覆蓋正式 Daily Forecast。
- Two-Lane Selector 仍是 backtest 實驗。

### 6.4 Export

- 正式三份全維度報表保留原檔名與 sheets。
- AI 三軌 forecast 報表與模型回測報表保留。
- Data Quality、Entity Resolution、AI Cleaning、Forecast Governance、Feature Store、Causal Analytics 都有獨立診斷 workbook。
- Lazy Export 避免首屏同步生成大型 workbook。
- GMV 排除訂單看板新增三份同規格 GMV 排除版報表與匹配稽核報表。

---

## 7. 新對話建議開場

可以貼以下內容給 Codex：

```text
請在 /Users/chanwaitung2025/Downloads/nbs_analytics 專案中繼續協作。

請先閱讀：
- NBS_ANALYTICS_SYSTEM_MAP.md
- NBS_SQLITE_DATABASE_GUIDE.md
- NBS_ANALYTICS_HANDOFF.md

目前系統是 Streamlit + SQLite 的 NBS 企業營運駕駛艙。正式收入口徑是「不含掛賬核銷與TT退款轉團款」。

請注意：
1. 不要修改 /Users/chanwaitung2025/Downloads/dashboard-project。
2. SQLite 保存清洗後明細；正式口徑、GMV 排除、AI Forecast 都是 Python 層派生。
3. GMV 排除訂單看板在第 3 個 tab，只是 session-only 派生視角，不回寫 SQLite。
4. Normal-Day / Two-Lane 仍是診斷與實驗，不覆蓋正式 Daily Forecast。
5. Data Quality、Entity Audit、Forecast Governance、Feature Store、Causal Analytics 都是只讀診斷層。
6. AI-assisted Data Cleaning 只做建議，人工確認後才寫入 rules_config.json。
7. 每次修改後都要做 py_compile、business calendar 驗證、必要時 Streamlit smoke test。

請先檢視 app.py、pipeline.py、database.py、forecasting.py，再按我的具體需求規劃或實作。
```

---

## 8. 常用驗證命令

```bash
.venv/bin/python -m py_compile app.py app_pages.py app_workflows.py app_styles.py streamlit_rendering.py forecasting.py pipeline.py database.py business_calendar.py visuals.py backend/services/upload_preflight_service.py scripts/system_manager.py scripts/validate_business_calendar.py scripts/inspect_sqlite_latest.py scripts/prewarm_ai_cache.py
.venv/bin/python scripts/validate_business_calendar.py
.venv/bin/python scripts/inspect_sqlite_latest.py
.venv/bin/python scripts/prewarm_ai_cache.py --status
.venv/bin/python scripts/system_manager.py acceptance
```

Streamlit smoke test：

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
```

然後檢查：

```text
http://127.0.0.1:8502/
http://127.0.0.1:5173/
http://127.0.0.1:8601/docs
http://127.0.0.1:8502/_stcore/health
```

UI smoke checklist：

- Sidebar 預設展開，品牌區顯示 `NBS Analytics`。
- 收合按鈕位於品牌區附近，與 side menu 視覺對齊。
- `Navigation` submenu 可點擊，URL hash 變化後能跳到對應區塊。
- 點擊 menu 不應重建 AI cache、不應重新生成 Export、不應改變篩選條件。
- AI 預測 cache 延後重建時，使用者需要明確按下「補算 AI」才會重新計算，不應靠單純刷新頁面完成。
- `Control Center` 的主題、年份、月份、日期、分社、專職與套用/重設仍可正常使用。
- 深色 / 淺色切換後 sidebar、主畫布、cards、tables、charts 不應出現深淺背景混雜。

---

## 9. 下一輪建議工作

1. **GMV 排除訂單看板驗收**
   - 用真實取消/不計算訂單清單測試。
   - 確認三份 GMV 排除版報表 sheets 與正式報表一致。
   - 確認原正式看板與正式 export 不變。

2. **Daily WAPE 降低**
   - 繼續 Normal-Day 與 spike/extreme day 方向。
   - 優先使用 Feature Store / Lead Signal 的 readiness matrix 和 `NoFutureLeak` 結果。
   - 不要未驗證就接入正式 Daily Forecast。
   - 先看 Normal Days 是否穩定壓低，再評估 Two-Lane 是否值得升級。

3. **Causal Analytics 深化**
   - v1 是 driver decomposition，不是嚴格因果。
   - 下一步可加入可選期間、去年同期、旅遊展/假期窗口與大單事件對比。
   - 所有解釋都要保留「可能解釋，不是因果結論」的標籤。

4. **Windows / VS Code / NVIDIA GPU 遷移**
   - 參考 `WINDOWS_VSCODE_GPU_HANDOFF.md`。
   - 使用 `scripts/setup_windows_gpu.ps1` 與 `scripts/verify_windows_gpu.py`。
   - 長訓練任務只保存結果和 log，不需要 Codex 全程監測。

5. **SQLite 健康檢查**
   - 考慮為 `來源單據號` 建 index。
   - 考慮 upsert 後清理 `temp_ids`。
   - 考慮新增 DB audit log。

6. **Phase 2R 後續模組邊界收斂**
   - `app_pages.py` 對 `app_workflows.py` 的星號匯入已收斂成明確依賴清單。
   - 下一步可把 `streamlit_rendering` 的星號匯入也改成明確清單。
   - 再下一步收窄 `app_workflows.py` / `streamlit_rendering.py` 的 `__all__`，讓後續拆分更不容易串錯。

---

## 10. 最重要提醒

任何後續修改前先判斷：

```text
這是正式口徑？
還是 GMV 派生視角？
是否會回寫 SQLite？
是否會影響 AI Forecast / WAPE？
是否會改原正式報表？
這是只讀診斷，還是會落到 rules_config？
```

只要這五個問題說清楚，後續協作就不容易把系統口徑搞混。
