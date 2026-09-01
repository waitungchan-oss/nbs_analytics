# NBS Analytics 最新交接說明

更新日期：2026-08-31

專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`

交接基準：`main@64717c62e0e13e777563d9e3c14a95eedda34f8c`

Git 狀態：本地 `main` 與 `origin/main` 對齊；本輪 documentation commit 後 worktree clean

正式收入口徑：`不含掛賬核銷與TT退款轉團款`

2026-05 frozen baseline：`HKD 12,057,968`

---

## 1. 本輪交接結論

NBS Analytics 現在不只是 session-only 分析工具，而是一套以 Streamlit、FastAPI、SQLite、版本化報表 cache 與受控 Agent verification chain 組成的本地企業營運駕駛艙。

下一個對話可以直接在以下專案繼續：

```text
/Users/chanwaitung2025/Downloads/nbs_analytics
```

不要修改：

```text
/Users/chanwaitung2025/Downloads/dashboard-project
```

`dashboard-project` 是另一個展示型旅遊 dashboard；本文件只交接 `nbs_analytics`。

本輪只更新 handoff/spec/plan 文件，不修改程式碼、SQLite、正式業務資料、baseline、業務規則或 runtime。

---

## 2. 開始新對話前必讀

請按以下順序完整閱讀：

1. `AGENTS.md`
2. `NBS_ANALYTICS_SYSTEM_MAP.md`
3. `NBS_ANALYTICS_HANDOFF.md`
4. `Summay/NBS_ANALYTICS_PROJECT_ASSESSMENT.md`
5. `docs/agents/NBS_AGENT_ARCHITECTURE.md`
6. `docs/agents/CODEX_AGENT_DISPATCH.md`
7. `docs/agents/CODEX_CREDIT_SAVING_RUNBOOK.md`
8. `NBS_HERMES_MONITORING.md`

開始實作前另做 live check：

```bash
git status --short --branch
git log -5 --oneline
```

本文件的 commit、測試與 runtime 資訊是交接快照；若與 live evidence 不同，以當前 repo、artifact、SQLite 與 process evidence 為準。

---

## 3. 當前系統狀態

### 3.1 主介面與能力

Streamlit 目前有五個主 tab：

```text
經營分析大盤
業務規則配置
GMV 排除訂單看板
Agent Operations
Memory Hub
```

主要能力包括：

- Excel upload、資料清洗、preflight、SQLite upsert 與熱備份。
- 正式營收 KPI、年度總覽、分社排行榜與產品下鑽。
- Daily / 7-Day / Month-End AI Forecast、WAPE 與 backtest。
- Data Quality、Entity Audit、AI-assisted Cleaning 人工確認流程。
- Forecast Governance、Feature Store、Causal Analytics 只讀診斷。
- 正式報表、診斷報表與版本化 export cache。
- GMV 退款 ledger、雙正式口徑、active version、對帳異常中心與退款扣減報表。
- Agent workflow、Strict Review evidence preflight、full verification 與 Hermes 分層驗證。

Streamlit 已完成模組化：

- `app.py`：thin defensive entrypoint。
- `app_pages.py`：tabs 與頁面編排。
- `app_workflows.py`：upload、cache、quality、forecast、export workflows。
- `app_styles.py`：theme 與 CSS。
- `streamlit_rendering.py`：共用 rendering helpers。

### 3.2 正式營收與雙正式口徑

原正式營收維持不變：

```text
SQLite 清洗後資料
- 排除 收款類型 = 掛賬核銷 對應來源單據號
- 排除 收款方式 = TT 退款轉團款 對應來源單據號
= 正式營收
```

退款扣減不取代原正式營收，而是新增第二套正式口徑：

```text
原正式營收
- 已退款維度的實際可扣減金額
= 正式淨 GMV active version
```

因此目前是「雙正式口徑」：

1. 原正式營收：現有 Dashboard、Forecast 與原正式報表的基礎。
2. 正式淨 GMV：以 active version 固化的已退款扣減視角，供 GMV Dashboard 與退款扣減報表使用。

### 3.3 GMV 退款資料流

現行正式流程是：

```text
上傳退款明細 Excel / CSV
→ Preflight 欄位、重複、狀態、來源單據號與收入範圍檢查
→ blocking：零寫入並停止
→ warning：保留 bounded provenance 並允許繼續
→ 按一次「上傳並合併退款資料庫」
→ upsert 退款 ledger（既有退款狀態可更新，不是 append-only）
→ 建立 immutable batch 與新的 active version
→ 建立總退款／已退款 intermediate facts
→ 建立 version-scoped 報表 cache 與 active pointer／manifest
→ UI 自動顯示 active version、明細、KPI 與下載按鈕
```

頁面刷新後，不需要重新上傳退款 Excel；只要 active version 與 cache 的 revenue generation signature 仍符合當前 SQLite／業務規則，就會直接載入並提供報表下載。

若主營收資料、業務規則或 generation signature 改變，舊 active version 只保留 provenance，UI 會要求重新執行退款 Preflight 與合併，避免把舊退款 cache 套到新收入資料。

### 3.4 退款核心業務規則

- `總退款`：保留所有合資格退款狀態，作營運比較維度。
- `已退款`：只取退款狀態為「已退款」的資料，是正式淨 GMV 的實際扣減維度。
- `TT 退款轉團款`：與正式收入口徑一致，排除於退款扣減；不可因退款表重複扣減。
- 實際扣減金額受原正式收款金額與可扣減範圍約束，超額部分只作稽核，不得產生負收入。
- 退款單狀態可由「退款中／待退款」更新為「已退款」；ledger 必須 upsert 既有退款 identity，而非單純 append。
- 退款只減金額；旅行團人數與票務數量沿用原交易資料，不因退款自動減少。
- 找不到正式 SQLite 來源單據號、落在正式收入排除範圍或 identity conflict 的資料，會進入對帳異常中心並標示原因碼。

### 3.5 報表與效能路徑

目前正式報表與 GMV 報表已採用：

- 共用已計算的 intermediate data，避免三份完整 workbook 重跑整套 aggregation。
- bounded serializer，將 Excel serialization 與 aggregation 解耦。
- version-scoped artifact cache、manifest 與 active pointer swap。
- trusted reference / shadow validation；新高速路徑只有在結果與 reference 等價時才可採用。
- affected-only production rebuild；退款更新時只重算受影響的 `source receipt`，未受影響 rows 直接沿用可信快照。
- total／paid 兩個維度共用必要中間結果，但保留各自正式輸出與稽核資料。
- UI active export 直接讀 cache，下載時不重新掃描完整營收資料。

Cold rebuild 仍會比 warm/cache hit 慢，因為需要建立 reference、equivalence evidence 與 artifacts；不能為追求速度移除結果一致性或 freshness gate。

---

## 4. 主要程式與資料邊界

| 任務 | 主要檔案 |
|---|---|
| Streamlit entrypoint | `app.py` |
| Dashboard、GMV tab、active export UI | `app_pages.py` |
| Upload、cache、forecast、export workflows | `app_workflows.py` |
| Excel 清洗與正式 workbook 結構 | `pipeline.py` |
| SQLite upsert、讀取、備份與修復 | `database.py` |
| 正式規則與欄位常數 | `config.py`、`rules_config.json` |
| Forecast、backtest、WAPE | `forecasting.py` |
| GMV ledger schema／repository | `backend/services/gmv_refund_repository.py` |
| GMV preflight、active version、affected-only orchestration | `backend/services/gmv_refund_service.py` |
| GMV domain models | `backend/services/gmv_refund_models.py` |
| GMV version-scoped cache | `backend/services/gmv_export_cache_service.py` |
| GMV reusable intermediate data | `backend/services/gmv_export_intermediate_service.py` |
| GMV bounded Excel serializer | `backend/services/gmv_export_serializer_service.py` |
| Trusted reference／equivalence validation | `backend/services/gmv_trusted_reference_service.py`、`backend/services/gmv_export_equivalence_service.py` |
| GMV schema migration | `scripts/migrate_gmv_refund_schema.py` |
| GMV benchmark／UI acceptance | `scripts/benchmark_gmv_refund_cache.py`、`scripts/run_gmv_ui_acceptance.py` |
| 本地服務啟停與 acceptance | `scripts/system_manager.py` |
| Agent orchestration CLI | `scripts/agent_workflow.py` |
| Strict Review evidence preflight | `scripts/strict_review_evidence_preflight.py` |
| Hermes post-change check | `scripts/hermes_post_change_check.py` |

### 權威邊界

- SQLite、canonical artifacts、正式規則與 frozen baseline 才是正式業務權威。
- Governance Graph 只可投影 canonical evidence，不得反向改寫政策或業務狀態。
- Memory Hub／Memory Sidecar 只提供 read-only context 或 hints，不得修改 SQLite、baseline、revenue scope、Git、approval、dispatch 或 workflow state。
- Agent Operations 只讀呈現，不是 approval、dispatch、retention 或正式狀態寫入入口。
- Context Agent 與 Review Agent 永遠 read-only。
- Implementation Agent 只能做已批准的一個 Task，不能自行 commit、merge 或擴大 scope。

---

## 5. Agent、Strict Review 與驗證現況

### 5.1 固定開發流程

每個高風險或跨檔 Task 應按以下順序：

```text
read-only inventory／Context bundle
→ 精簡 spec 與 implementation plan
→ 使用者批准單一 Task
→ implementation
→ findings-first Review
→ targeted tests
→ full pytest
→ Hermes
→ 必要時正式 Streamlit/UI acceptance
→ commit／push／PR／merge（須有明確授權）
```

Strict Review、full pytest、Hermes 與 UI acceptance 是四個獨立 gate；其中一項 PASS 不代表其他項也 PASS。

### 5.2 Codex 額度與 runner 規範

依 `docs/agents/CODEX_CREDIT_SAVING_RUNBOOK.md`：

- 編排走本地 `scripts/agent_workflow.py` CLI。
- 不在 Codex 對話內用 subagent 長輪詢代替本地 orchestrator。
- 每個 Task 使用新 bounded session，目標不超過 15 turns。
- 高風險 revenue、baseline、business rules、upload、SQLite、export、架構工作使用批准的高風險 model profile；一般維護採較低成本 profile。
- 不啟用與 Task 無關的 plugins、runner 或外部服務。

### 5.3 最近驗證快照

- 本次 runner identity implementation commit：`64717c62e0e13e777563d9e3c14a95eedda34f8c`。
- Runner identity、Local CLI、capability/cache、verification chain 與 Hermes adapter affected pack：`196 passed`。
- merged `main` full pytest：`2558 passed in 110.91s`；使用受控 elevated environment，因 Codex sandbox 無法執行 macOS `sandbox-exec`。
- 本次變更的 Strict Review：尚未重跑；不沿用歷史 `review_passed` 作為 current PASS。
- 本次 Hermes post-change：尚未形成 PASS；fresh isolated worktree 曾因 services 未啟動及 SQLite 實際讀值 `HKD 0` 而 fail/block。
- 本次 UI acceptance：`not required / not run`，因本次未修改 UI。

上述結果是歷史 evidence，不等於下一輪修改後的驗證結果。任何新實作都必須建立 fresh source-bound evidence，不能沿用舊 PASS 宣稱完成。

相關規格與計畫：

- `docs/superpowers/specs/2026-08-31-strict-review-evidence-preflight-design.md`
- `docs/superpowers/plans/2026-08-31-strict-review-evidence-preflight.md`
- `docs/superpowers/specs/2026-08-28-strict-review-verification-chain-design.md`
- `docs/superpowers/plans/2026-08-28-strict-review-verification-chain.md`

---

## 6. 常用 live verification

### 6.1 Repo 與測試

### Task checkpoint commit contract

每個 approved Task 使用一個 checkpoint commit，subject 格式為
`checkpoint(task-<id>): <imperative summary>`，並以
`task-checkpoint-evidence-v1`、parent HEAD、allowlist、diff fingerprint、Review
fingerprint 與 focused verification 綁定。commit 必須保留
`Final-Acceptance: pending`；這只是可回退的 Task lineage，不代表 final acceptance。

Implementation Agent 不得 commit、push 或 merge；Codex 只在 Review/focused verification
通過後執行明確授權的 Git integration。unrelated dirty changes 必須保留，rollback
預設使用新的 `git revert` commit。Governance Graph、Memory Hub、Memory Sidecar 與
Hermes 只提供 read-only projection/context，不得改變 Git 或 Task state。

```bash
git status --short --branch
git log -5 --oneline
.venv/bin/python -m pytest -q
.venv/bin/python scripts/hermes_post_change_check.py
```

不要把舊 handoff 中的 row count、最大日期或測試數字當成當前真值；需要時直接讀正式 SQLite 與最新 artifacts：

```bash
.venv/bin/python scripts/inspect_sqlite_latest.py
.venv/bin/python scripts/prewarm_ai_cache.py --status
```

### 6.2 本地服務與 UI

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
```

主要 URL：

```text
http://127.0.0.1:8502/
http://127.0.0.1:8502/_stcore/health
http://127.0.0.1:5173/
http://127.0.0.1:8601/docs
```

GMV UI acceptance 至少確認：

- 上傳退款明細後，一鍵合併能建立或更新 active version。
- blocking 時沒有 batch、current、active version 或 cache 寫入。
- warning 可保留 provenance 並繼續。
- `總退款` 與 `已退款` KPI、明細及報表同時存在。
- `TT 退款轉團款` 不被重複扣減。
- 刷新頁面後可直接載入 active version 與下載 cache 報表。
- generation signature mismatch 時 fail closed 並要求重建。
- 報表 cache hit 不重新掃描完整營收資料。
- 新高速路徑輸出與 trusted reference 等價。

---

## 7. 下一輪最實用工作

### 已完成 Task：統一 runner naming 與 identity contract

本 Task 已建立並合併至 `main`，統一 `Local CLI`、`Remote API`、`Local Model` 的 runner naming，避免相同 runner 在 Strict Review、Hermes、cache 與 evidence 中使用不同名稱。

建議目標：

- 定義 canonical `runnerId`、`transport`、`provider`、`model`、`profile` 與 `executionEnvironment`。
- `transport` 明確區分 `local_cli`、`remote_api`、`local_model`，不要把模型名稱當 transport。
- 將 runner identity 綁定 verification-v1、Strict Review report、Hermes evidence 與 cache manifest。
- 舊 artifact 只做兼容讀取；新寫入必須使用 canonical schema。
- identity 缺失或不一致時 fail closed，錯誤訊息指出缺少哪個欄位及修復方式。
- 不修改 SQLite、正式營收、baseline、GMV 業務規則、export schema 或 Dashboard 指標。

後續若要繼續 runner 相關工作，先以本 commit 的 spec、plan 與 fresh gate evidence 做 read-only inventory，不要直接進行大型 runner 重寫。

其他候選工作：

1. 以真實退款增量批次持續量測 affected-only rebuild、serializer 與 UI 等待時間。
2. 逐步清理剩餘 pandas deprecation／FutureWarning，但必須保持 dtype 與數值等價。
3. Daily WAPE、Normal-Day、Two-Lane 與 Causal Analytics 可繼續實驗；未通過 guardrail 前不得覆蓋正式 forecast。

---

## 8. 新對話可直接貼用的開場 Prompt

```text
請在以下專案繼續協作：

/Users/chanwaitung2025/Downloads/nbs_analytics

請使用繁體中文回覆，保留必要英文技術名詞。本對話只處理我指定的 NBS Analytics 業務功能或受控 tooling Task，不主動擴大到新的 Governance Graph、Memory Hub、Agent orchestration、approval、dispatch、workflow control、資料庫或大型架構重寫。

開始前請完整讀取：
1. AGENTS.md
2. NBS_ANALYTICS_SYSTEM_MAP.md
3. NBS_ANALYTICS_HANDOFF.md
4. Summay/NBS_ANALYTICS_PROJECT_ASSESSMENT.md
5. docs/agents/NBS_AGENT_ARCHITECTURE.md
6. docs/agents/CODEX_AGENT_DISPATCH.md
7. docs/agents/CODEX_CREDIT_SAVING_RUNBOOK.md
8. NBS_HERMES_MONITORING.md

然後執行 read-only live check：
- git status --short --branch
- git log -5 --oneline

正式收入口徑固定為「不含掛賬核銷與TT退款轉團款」，2026-05 frozen baseline 固定為 HKD 12,057,968。GMV 採雙正式口徑：原正式營收不變；正式淨 GMV 只扣減「已退款」，總退款保留作比較維度。退款不減旅行團／票務人數，TT 退款轉團款不得重複扣減。

Strict Review、full pytest、Hermes、UI acceptance 是獨立 gates。任何新改動必須使用 fresh source-bound evidence，不得沿用舊 PASS。Governance Graph、Memory Hub、Memory Sidecar 與 Agent Operations 都是 read-only／non-authoritative，不得回寫正式業務狀態。

目前 runner naming／identity contract 已完成；任何下一個 implementation Task 都應先做 read-only inventory、提出精簡 spec、最小改動、風險、rollback 與驗證方法，並取得明確授權。
```

---

## 9. 最重要提醒

每次需求先回答：

```text
這是原正式營收，還是正式淨 GMV？
退款維度是總退款，還是已退款？
是否會寫入 SQLite／退款 ledger／active version？
是否會改 frozen baseline、正式業務規則或 export schema？
cache／manifest／generation signature 是否仍 fresh？
是否會影響 Forecast、WAPE、人數或票務數量？
這是 read-only evidence，還是正式狀態寫入？
本輪 Review、full pytest、Hermes、UI acceptance 各自狀態是什麼？

## 2026-09 Release Gate Standardization

本輪已建立 release gate implementation checkpoints；正式 release readiness 必須由同一 commit 的 fresh evidence 決定，不能沿用本 handoff 或歷史 CI 結果。

- `Full pytest release gate`：包含 qualified runner 的 sandbox capability；sandbox blocked 不得降級成 skip 或 PASS。
- `Hermes release gate`：只接受 fresh `overallStatus=pass`，並維持 Graph、Memory Hub、Memory Sidecar 與 Agent Operations 的 read-only／non-authoritative 邊界。
- `UI acceptance release gate`：只接受 HTTP/HTTPS、temporary fixture、same commit/source fingerprint 的 bounded acceptance；禁止 `file://` 與正式資料路徑。
- `Release gate aggregate`：只讀三份 evidence；任一 gate 為 `FAIL`、`BLOCKED`、`MISSING`、stale 或 identity mismatch 即 fail closed。
- PR merge 與 release tag 必須分別重新執行三個 gate；aggregate PASS 不取代 Strict Review，也不改變正式 scope「不含掛賬核銷與 TT 退款轉團款」或 `2026-05` baseline `HKD 12,057,968`。

目前 checkpoint 的 `Final-Acceptance: pending` 必須保留；full pytest、Hermes、UI acceptance 與 Strict Review 的 fresh 結果完成前，不宣稱 release-ready。
```

只要這些邊界在 spec、implementation、review 與驗收中保持一致，就不容易把正式口徑、退款扣減、cache 或 Agent evidence 混在一起。
