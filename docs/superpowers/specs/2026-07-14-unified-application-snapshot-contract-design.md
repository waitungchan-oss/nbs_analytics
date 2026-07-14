# P3-1 Unified Application Snapshot Contract Design

## Goal

建立一個薄的 Application Snapshot application service，將 Decision API 現時位於 router 內的 rules loading、generation pinning、read-model orchestration、retry 與 conflict policy 收斂到單一可測試邊界。

第一階段只接入 `GET /api/decisions/overview`，保持既有 HTTP response、正式口徑、baseline、Forecast、target governance 與 Decision 規則不變。

## Current State

`backend/routers/decisions.py` 現時直接：

1. 從 `dashboard_service._current_rules()` 取得正式 rules；
2. 讀取 generation token；
3. 建立 Dashboard Facts、Forecast、Data Quality、System Health 與 Target Config；
4. 呼叫 Decision Service；
5. 再讀 generation，必要時整段重試一次；
6. generation 再次改變時直接拋出 FastAPI `HTTPException(409)`。

這段流程能正確運作，但 transport layer 同時承擔 application orchestration。其他 consumer 若需要相同快照，容易複製 generation 與 read-model 組裝邏輯；測試亦需要在 router module monkeypatch 多個 service dependency。

## Chosen Architecture

採用兩個小型 service，不導入通用 dependency-injection framework：

1. `business_rules_service.py`：公開、正規化並 fingerprint 影響 Dashboard Facts 的正式 rules。
2. `application_snapshot_service.py`：固定 generation，協調既有 read model，輸出 typed internal snapshot。

```text
Decision router
  -> ApplicationSnapshotService.build()
       -> BusinessRulesService.load()
       -> load generation start token
       -> Dashboard Facts Read Model
       -> Data Quality Read Model
       -> Forecast Read Model
       -> System Health Read Model
       -> Target Config
       -> load generation end token
       -> success / retry once / SnapshotGenerationConflict
  -> build_decision_overview(snapshot fields)
  -> DecisionOverviewResponse
```

這是一個 application boundary，不是新的 data layer。所有正式計算仍由現有 Facts、Quality、Forecast、Health 與 Decision services 負責。

## Components

### Public Business Rules Service

新增公開的 `BusinessRulesSnapshot` internal type，至少包含：

- `branch_mapping`
- `target_branches`
- `cruise_departments`
- `sales_reps`
- `fingerprint`

Rules 在讀取時完成 defensive normalization：mapping 轉為新的 dict，list 轉為 tuple；consumer 取得 Facts builder 參數時再生成新的 dict/list，避免修改 provider 保存的 snapshot。

`fingerprint` 對以上四組 normalized values 做 canonical JSON SHA-256。它只表示會影響 Dashboard Facts 的 rules identity，不代表整份 `rules_config.json` 版本，也不包含 branch override、exclude prefix 或其他不屬於 Facts builder 參數的設定。

為支援隔離測試，`rules.load_business_rules()` 增加 backward-compatible 的 optional path 參數；既有無參數 caller 行為不變。Public provider 接受明確 `config_path`，不依賴 Streamlit session state。

`dashboard_service._current_rules()` 不再作為跨模組介面。為降低第一階段風險，既有 Dashboard code 可先保留相容 wrapper，但 wrapper 必須委派給新的 public provider；Decision router 不再引用 private helper。

### Snapshot Paths

使用 frozen dataclass `SnapshotPaths` 集中：

- `db_path`
- `cache_dir`
- `runtime_dir`
- `rules_config_path`
- `target_config_path`

generation path 固定由 `runtime_dir / "data_generation.json"` 推導。正式 router 使用 project defaults；tests 必須傳入 `tmp_path` 下的所有 paths。

### Snapshot Dependencies

`ApplicationSnapshotService` constructor 接受一組有預設值的 callable dependencies：

- rules loader
- generation loader
- facts builder
- data-quality builder
- forecast builder
- health builder
- target loader

這是局部 constructor injection，只服務 P3-1 單元測試，不建立 container、registry 或全域 service locator。Production caller 不需自行組裝 dependencies。

### Application Snapshot

`ApplicationSnapshot` 使用 frozen dataclass 表達 internal contract：

- `generation_token`
- `rules`
- `facts`
- `forecast`
- `quality`
- `health`
- `targets`
- `provenance`

各 read-model payload 保持既有 dict，避免深複製大型 payload 或引入新的公開 schema。Dataclass 固定欄位邊界，但不宣稱巢狀 dict 完全 immutable；consumer 應把它視為 request-scoped read-only value。

## Generation Consistency

每次 `build()` 最多兩次 attempt：

```text
for attempt in [1, 2]:
  rules = load rules snapshot
  start = load generation(db_path, runtime generation path)
  build all read models using start cache token where supported
  end = load generation(db_path, runtime generation path)
  if end token == start token:
      return ApplicationSnapshot
raise SnapshotGenerationConflict
```

Rules 在每次 attempt 重新讀取，確保 retry 不沿用前一次 attempt 的 rules。Facts builder 使用同一次 rules snapshot 與 start token；Data Quality builder使用同一 start token。

現有 Forecast read model 由獨立 AI cache 讀取，並未保存 DB generation token；System Health 亦會自行觀察 runtime generation。因此 P3-1 的 consistency 定義是：

- `coreGenerationConsistent = true`：本次 Facts / Data Quality 建立期間 DB generation 沒有改變。
- Forecast 只回報 cache path、version、modified time 與 status。
- 不新增或偽造 `forecastGenerationMatched`。

未來如要讓 Forecast 綁定 generation，需另開 Brief 修改 AI cache contract。

## Conflict Handling

新增 framework-independent exception：

```text
SnapshotGenerationConflict
  attempts
  observed_tokens
```

Application service 不匯入 FastAPI。Decision router 捕捉此 exception，映射為既有 HTTP 409 與相容的 retry message。其他非 conflict exception 保持原本失敗語義，不回傳舊 snapshot，也不吞掉 read-model builder 錯誤。

## Provenance Contract

Snapshot provenance 使用 internal dict，至少包含：

- `generationToken`
- `coreGenerationConsistent`
- `snapshotAttemptCount`
- `dbPath`
- `rulesFingerprint`
- `factsCacheStatus`
- `readModelCacheStatus`
- `dataQualityCacheStatus`
- `forecastStatus`
- `forecastCache`
- `systemHealthStatus`

Decision Service 現有 provenance 保持對外 contract；P3-1 只把 `rulesFingerprint`、`snapshotAttemptCount` 與 `coreGenerationConsistent` 合併進既有 `provenance` dict。既有 key 不刪除、不改名，Pydantic response model亦不需改欄位結構。

## Router Boundary

重構後 Decision router 只負責：

1. 以正式 paths 建立/取得 Application Snapshot service；
2. 呼叫 `build()`；
3. 將 snapshot fields 交給現有 `build_decision_overview()`；
4. 合併 snapshot provenance；
5. 將 `SnapshotGenerationConflict` 映射為 HTTP 409。

Router 不再：

- import `_current_rules()`；
- 直接載入 generation；
- 直接呼叫 Facts、Quality、Forecast、Health 或 Target loaders；
- 保存 generation retry loop。

## Cache And Performance

P3-1 不新增 snapshot-level persistent cache。它沿用：

- Dashboard Facts persistent cache；
- Dashboard Read Model JSON cache；
- Data Quality generation cache；
- 現有 Forecast AI cache。

增加 application object 或 dataclass 不得觸發額外 SQLite/Pandas 計算。正常 warm request 仍應只有既有 generation start/end checks與 cache hits；`scripts/profile_decision_api.py --warm-limit-ms 300 --runs 5` 繼續作 blocking performance gate。

## Alternatives Rejected

### 1. 只把 router 程式搬到單一 function

改動最少，但 rules、paths、dependencies 與 typed conflict 仍沒有明確 contract，測試仍高度依賴 module monkeypatch。不能滿足後續多 consumer 共用需求。

### 2. 建立全域 DI container / repository framework

可統一所有 services，但對本地單體系統過度抽象，改動面會擴至 upload、forecast、exports 與 Streamlit，不符合 P3-1 最小範圍。

### 3. 建立 snapshot-level persistent cache

可能進一步加快讀取，但會新增另一層 invalidation、schema version、checksum 與 corruption handling。現有 read-model cache 已達 300ms 目標，現階段沒有足夠收益。

### 4. 同時接入 Dashboard API 與 Streamlit

最終重用較完整，但會把第一階段擴大到 filters、DataFrame session cache 與 UI rerun 行為。P3-1 先以 Decision API 驗證 application boundary。

## Failure Handling

- Rules config 缺失或損壞：沿用 `load_business_rules` 的 default fallback，provenance 仍提供 fallback rules fingerprint。
- Facts/Data Quality cache 損壞：沿用既有 miss/rebuild 行為。
- Forecast cache 缺失：回傳既有 `not_ready` payload，由 Decision Service 產生 warning。
- Target config 缺失：維持 `not_configured`。
- System Health degraded：Snapshot 仍可成功，由 Decision Service 產生管理預警。
- Generation 連續改變：不回傳混代 payload，回 HTTP 409。
- 任一非既有容錯範圍內的 builder exception：request 失敗，不使用 stale fallback。

## Test Design

### Business Rules Service

- 正常 config 產生 normalized values 與穩定 fingerprint。
- 相同語義、不同 mapping key order 產生相同 fingerprint。
- 呼叫端修改轉出的 dict/list 不會影響 provider snapshot。
- explicit rules path 不會讀取正式 `rules_config.json`。
- existing no-argument `load_business_rules()` 行為相容。

### Application Snapshot Service

- 第一 attempt 成功時，Facts 與 Data Quality 收到同一 generation token。
- Facts 收到同一次 rules snapshot 的完整參數。
- generation 改變一次時，所有 read models 整段重建，第二 attempt 回傳新 token。
- generation 連續改變時，拋出 typed conflict 並保存 observed tokens。
- provenance 正確反映 attempt、rules fingerprint 與各 cache status。
- explicit temp paths 傳到 generation、Facts、Quality、Forecast、Health 與 Targets dependencies。
- application module 不匯入 FastAPI、Streamlit、Pandas、pipeline 或 database detail loader。

### Decision API Contract

- Router 只 mock Application Snapshot service 與 Decision Service 即可完成 typed response test。
- Snapshot conflict 維持 HTTP 409。
- response 保留原有 provenance keys，並新增 snapshot metadata。
- OpenAPI `DecisionOverviewResponse` reference 不變。

### Regression And Acceptance

- Dashboard Facts、Data Quality cache、Decision Service、Target Governance tests。
- full pytest。
- Vue contract verification與 production build。
- Decision API warm median `<= 300ms`。
- system acceptance。
- Hermes `overallStatus: pass`。
- 2026-01 至 2026-06 monthly baseline 全部 matched；2026-05 維持 `HKD 12,057,968`。

## Scope Boundary

P3-1 不修改：

- 正式口徑「不含掛賬核銷與TT退款轉團款」；
- upload、preflight、single-writer lease、upsert、rollback、history、generation advance；
- monthly baseline registry 或 blocking mode；
- branch reassignment override；
- report sheets、金額/人數/交易數量守恆；
- Forecast 模型、AI cache payload 或 WAPE；
- Target config schema、approval workflow 或 Decision 規則；
- Vue calculation 或 Streamlit data flow；
- JSON/JSONL storage migration、background jobs、database engine 或程式語言。

## Implementation Sequence

1. 以 TDD 建立 public Business Rules Service 與 backward-compatible explicit path。
2. 以 TDD 建立 Snapshot paths、dependencies、typed payload 與 conflict。
3. 將 Decision router 接入 Snapshot service並保留 HTTP contract。
4. 加入 provenance與 performance regression tests。
5. 執行 targeted、full、Vue、profile、acceptance、Hermes 與 baseline 驗收。
6. 回填 Brief、system map 與 Obsidian，建立 Git 版本節點。
