---
type: codex-brief
project: nbs_analytics
status: verified
priority: P3-1
created: 2026-07-14
tags: [nbs_analytics, codex, application-service, snapshot, generation, read-model, hermes]
---

# 2026-07-14 P3-1 Unified Application Snapshot Contract

## 任務目標

在既有 Dashboard Facts、Forecast、Data Quality、System Health、Target Governance 與 Decision Service 之上，建立一個薄的 Application Snapshot application service。

它負責在同一次讀取流程內固定資料 generation、公開載入正式 rules、協調各個既有 read model，並輸出可追蹤的 snapshot provenance。第一階段只讓 Decision API 使用，不改 API response contract，也不讓 snapshot service 重新計算任何正式指標。

## Repo

`/Users/chanwaitung2025/Downloads/nbs_analytics`

建立 Brief 時的正式版本：`main@f3dde52`

## 必讀上下文

- [[NBS 系統總覽]]
- [[Revenue Scope Rules]]
- [[Upload Write Path]]
- [[ADR-001 Frozen Baseline 保護]]
- [[ADR-002 Upload Single-Writer Contract]]
- [[驗收基線]]
- [[Hermes 驗收標準]]

Repo context：

- `NBS_ANALYTICS_SYSTEM_MAP.md`
- `docs/superpowers/specs/2026-07-13-dashboard-facts-service-design.md`
- `docs/superpowers/specs/2026-07-13-management-decision-layer-design.md`
- `docs/superpowers/specs/2026-07-14-formal-target-governance-design.md`
- `docs/superpowers/specs/2026-07-14-decision-api-performance-design.md`
- `docs/superpowers/specs/2026-07-14-unified-application-snapshot-contract-design.md`
- `docs/superpowers/plans/2026-07-14-unified-application-snapshot-contract.md`

## Observed State

- `backend/routers/decisions.py` 目前直接載入 generation，並自行組裝 Dashboard Facts、Forecast、Data Quality、System Health、Target Config 與 Decision Overview。
- Decision router 直接引用 `backend.services.dashboard_service._current_rules()`；這是 private helper，不是穩定的跨模組 rules contract。
- Dashboard Facts 與 Data Quality 已使用 generation-aware persistent cache，但 generation consistency 的 retry / conflict 邏輯仍放在 HTTP router。
- Forecast read model 讀取獨立 AI cache；System Health 與 Target Config 亦有各自的 path 與載入方式。現時可以運作，但缺少一個可測試的 application-level orchestration boundary。
- Decision API warm performance 已通過 300ms 門檻；本階段不能因抽象化而明顯倒退。

## 問題判斷

目前主要問題不是計算速度或缺少新資料，而是編排責任位於 transport layer：

1. Router 同時負責 HTTP、generation pinning、rules loading、read-model orchestration 與 retry policy。
2. 新 consumer 若需要相同快照，容易複製同一段組裝邏輯。
3. Rules、generation、cache status 與來源 path 沒有一個統一 provenance contract。
4. 單獨測試 Decision API 時，需要 monkeypatch 多個 router-level dependency，邊界不夠集中。

## 採用方向

建立薄的 Application Snapshot application service，沿用現有 services，不建立第二套計算：

```text
Decision API router
  -> ApplicationSnapshotService
       -> public Rules Provider
       -> load generation token
       -> Dashboard Facts Read Model
       -> Forecast Read Model
       -> Data Quality Read Model
       -> System Health Read Model
       -> Target Config
       -> verify generation token
       -> retry once or raise snapshot conflict
  -> Decision Service
  -> existing typed API response
```

第一階段可以讓 snapshot service 回傳一個明確的 application-level payload：

```text
generation
rules
facts
forecast
quality
health
targets
provenance
```

`Decision Service` 仍負責 targets、alerts 與 decision cards；Snapshot service 不包含管理判斷規則。

## 核心契約

### 1. Generation pinning

- 每次 snapshot 最多嘗試兩次。
- 每次嘗試開始時取得 generation token，所有 generation-aware read models 使用同一 token。
- 組裝結束後重新讀取 generation。
- generation 改變時整個 snapshot 重試一次；再次改變時回報明確的 snapshot conflict。
- HTTP 409 轉換由 router 負責，application service 不依賴 FastAPI exception。

### 2. Public Rules Provider

- 將正式 branch mapping、target branches、cruise departments、sales reps 由公開 service/function 提供。
- Snapshot 與 Dashboard consumer 使用相同 rules contract。
- 不再跨模組引用 `_current_rules()`。
- 本階段不修改 `rules_config.json` schema、override 規則或規則 UI。

### 3. Existing read models only

- Snapshot service 只協調現有 read model builder。
- 不直接讀 Pandas DataFrame、不直接查詢正式營收明細、不自行重算 KPI。
- 不複製 Dashboard Facts、Data Quality、Forecast 或 System Health 邏輯。

### 4. Provenance

Snapshot provenance 至少包含：

- generation token；
- DB path；
- rules/config identity 或穩定 fingerprint；
- Facts、Dashboard Read Model、Data Quality 與 Forecast cache status/version；
- snapshot attempt count；
- snapshot consistency status。

API response 只加入既有 schema 可安全承載的欄位；若需要改公開 schema，必須在 design spec 明確列出並加入 contract test。

### 5. Dependency boundary

- Router 只處理 HTTP request/response 與 application error 到 HTTP status 的轉換。
- Snapshot service 不匯入 FastAPI、Streamlit 或 Vue 程式碼。
- Paths 由 application dependency/config 明確提供，測試不可讀寫正式 DB、正式 runtime 或正式 cache。

## 第一階段允許改動範圍

- 新增 Application Snapshot service 與 typed internal contract。
- 新增公開 Rules Provider，並讓既有 `_current_rules()` caller 漸進接入。
- 將 Decision API 的 generation/retry/read-model 組裝移入 application service。
- Decision router 改為薄 adapter。
- Snapshot、Decision API、generation consistency、rules contract 與 performance tests。
- Hermes targeted test pack 與必要的 system-map/Brief 回填。

## 不可破壞

- 正式口徑仍為「不含掛賬核銷與TT退款轉團款」。
- `2026-05` baseline 必須維持 `HKD 12,057,968`。
- `2026-01` 至 `2026-06` 六個 blocking monthly baseline 必須全部 matched。
- 不修改 upload、preflight、single-writer lease、upsert、rollback、history 或 generation advance 邏輯。
- 不修改 branch override、report sheets、金額/人數/交易數量守恆、GMV 或 Forecast 算法。
- Vue 不自行重算 target attainment、alerts 或 decisions。
- 不以 UI、rounding、export 或 cache payload 掩蓋 baseline drift。

## 不屬於本 Brief

- Background job queue、Celery、Redis、worker process。
- Forecast 模型重訓或預測算法優化。
- Export 非同步化。
- JSON/JSONL governance 全面遷移到 SQLite。
- Streamlit 全面改用 API。
- Dashboard API response 重設計。
- Postgres、DuckDB、Polars、Node.js 或其他語言重寫。
- 通用 dependency-injection framework 或大型 repository pattern 重構。

## 必須先寫的測試

- 一次成功 snapshot：所有 generation-aware dependency 收到相同 token。
- generation 中途改變：整個 snapshot 重試一次，不能混合兩代 payload。
- generation 連續改變：application service 回報 typed conflict，router 映射為 HTTP 409。
- Rules Provider 回傳穩定、可注入、不可由 consumer 意外修改的 contract。
- Snapshot service 使用明確 temp DB/runtime/cache paths，測試不接觸正式檔案。
- Decision API response 與 P2-5/P2-6 既有 contract 相容。
- Snapshot service 不直接呼叫正式 revenue calculation/pipeline builder。
- warm Decision API median 仍不高於 300ms。

## 驗收方式

1. affected files `py_compile`。
2. Snapshot、Rules Provider、Decision Service、Decision API targeted pytest。
3. Dashboard Facts、Data Quality cache、target governance regression tests。
4. full pytest。
5. Vue contract/build；確認 response contract 未破壞。
6. `scripts/profile_decision_api.py --warm-limit-ms 300 --runs 5`。
7. `scripts/system_manager.py acceptance`。
8. `scripts/hermes_post_change_check.py --json`。

## 必守驗收結果

- SQLite integrity：`ok`。
- 2026-01 至 2026-06 monthly baseline：全部 `matched`。
- 2026-05 frozen baseline：`HKD 12,057,968 matched`。
- Hermes：`overallStatus: pass`。
- Decision API warm median：`<= 300ms`。
- Decision router 不再包含 generation retry loop，也不再引用 private `_current_rules()`。
- Git 建立明確版本節點，回填後正式 worktree clean。

## 風險與控制

| 風險 | 控制方式 |
|---|---|
| 抽象層只是搬移程式碼，沒有形成清楚契約 | 先以 typed internal payload、dependency injection 及 unit tests 固定邊界。 |
| Snapshot 包含過多業務規則 | Decision judgement 保留在 Decision Service；Snapshot 只提供來源資料與 provenance。 |
| 新 service 重新計算 Facts | 只允許呼叫既有 read model builders；加入負向測試。 |
| generation retry 行為改變 | 保留最多兩次嘗試與 HTTP 409 語義，建立相容性測試。 |
| 額外編排造成性能倒退 | 保留 warm median 300ms performance gate。 |
| 一次接入所有 consumer 擴大風險 | 第一階段只接 Decision API，Dashboard/Streamlit 留待後續 Brief。 |

## 當前最快、最優的下一步

根據本 Brief 撰寫 P3-1 design spec，先固定 internal snapshot contract、Rules Provider 邊界、typed conflict 與第一階段 Decision API 接入方式，再產生 implementation plan。

預期效果：不改任何正式計算，就能把 generation consistency 與多 read-model 編排從 HTTP router 收斂到單一可測試邊界，為後續 Read Model Registry、Background Jobs 與多 consumer 共用奠定基礎。

## 目前狀態

`verified`

P3-1 implementation 已在隔離 worktree 完成並通過驗收。

Implementation commits：

- `e56b6df`：public Business Rules Snapshot。
- `1aa01d1`：generation-consistent Application Snapshot。
- `f47540c`：builder exception propagation regression test。
- `92ae4e9`：Decision API 接入 Application Snapshot。
- `6f66763`：Decision provenance precedence regression test。
- `70d967e`：限制公開 provenance，補 production wiring integration coverage，修正正式 runtime evidence。

最終驗收證據（2026-07-14）：

- affected modules compile：PASS。
- P3-1 focused contract tests：`20 passed`。
- full pytest：`219 passed in 22.60s`。
- Vue contract：PASS；production build：PASS。
- Decision API profile：cold `240.639ms`；warm median `232.346ms`，低於 `300ms` gate。
- service acceptance：`passed`；Streamlit `8502`、API `8601`、Vue `5173` 均由 P3-1 worktree 啟動並 ready。
- Hermes：`overallStatus: pass`；擴充後 targeted pack `97 passed in 15.95s`。
- system monitor：`status: ok`；SQLite integrity：`ok`；Acceptance Record `15`，latest data `2026-07-13`。
- 2026-01 至 2026-06 六個 blocking monthly baseline：全部 `matched`。
- 2026-05 frozen baseline：`HKD 12,057,968 matched`。
- 正式口徑仍為「不含掛賬核銷與TT退款轉團款」；upload、rollback、報表、GMV、Forecast 算法與 target governance schema 均未修改。
