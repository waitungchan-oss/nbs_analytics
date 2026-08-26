# Scope-Aware Cache Generation Signature Design

> Status: implementation-ready design draft
>
> Scope: 修正正式 runtime 將 GMV ledger 寫入誤判為主營收 cache stale 的問題；不修改正式營收口徑、不重寫 SQLite 業務資料、不改 GMV 報表 schema。

## 1. Problem Statement

目前 `.nbs_runtime/data_generation.json` 以整個 `nbs_marketing_data.db` 檔案的 SHA-256 作為 cache generation freshness 判斷。這個判斷在只有主營收資料時成立，但 GMV 正式 ledger 也保存於同一個 SQLite 檔案：

```text
主營收資料：tour_data / others_data
GMV ledger：gmv_refund_* / gmv_scope_* / gmv_*_snapshot
```

GMV active version 建立後，SQLite 檔案會改變；主營收資料沒有改變，但 system health 仍看到：

```text
Cache generation signature does not match current database
```

正式 runtime 的已觀察證據：

- `data_generation.json` generation `37` 在 `09:41:04` 寫入，記錄 DB size `29,130,752`。
- GMV active version 在 `09:51:31` 寫入同一 SQLite。
- 現行 DB size 為 `30,511,104`，full-file SHA 已改變。
- SQLite integrity、formal baseline、GMV cache `equivalenceStatus=PASS`、`shadowStatus=PASS` 均通過。

這表示現行警告是 **signature scope mismatch**，不是已證明的主營收資料損壞。若直接 refresh 全檔案 signature，只會消除現象，不能表達「哪一類資料真正變更」。

## 2. Goals

1. 主 Dashboard、Dashboard API、Forecast、WAPE 與正式 Export 只在 core revenue inputs 改變時失效。
2. GMV ledger 或 GMV active version 寫入不再使主營收 cache 變成 false stale。
3. 保留 full SQLite file signature 作為 diagnostics、incident evidence 與 rollback 輔助資訊。
4. 保持現有 GMV active version、trusted reference、export manifest 與 `gmv-revenue-state-v1` 相容。
5. 舊版 `data_generation.json` 可以安全讀取；未完成 migration 時 fail-closed，不得默認宣稱 cache fresh。
6. 不改變正式收入口徑：`不含掛賬核銷與TT退款轉團款`。
7. 不改變 2026-05 frozen baseline：`HKD 12,057,968`。
8. 不新增 SQLite migration；runtime metadata migration 僅更新 `.nbs_runtime` sidecar。

## 3. Non-Goals

- 不重算或修正正式營收數字。
- 不移動 GMV tables 到另一個 SQLite database。
- 不改變退款金額、退款狀態、TT 退款轉團款排除規則。
- 不改變 Excel export sheet、欄位或檔名 contract。
- 不移除既有 `gmv-revenue-state-v1` token。
- 不新增 Governance Graph、Memory Hub、orchestration 或 approval workflow。
- 不以 cache refresh 掩蓋 baseline drift、SQLite integrity failure 或 rollback failure。

## 4. Decision Summary

採用 **scope-aware dual signature**：

| Signature | 內容 | 用途 | GMV 寫入是否影響 |
|---|---|---|---|
| `coreRevenueSignature` | `tour_data`、`others_data` 經 normalization、正式收入 scope 與 rules contract 後的 deterministic fingerprint | Dashboard / Forecast / WAPE / formal Export cache freshness | 否 |
| `sqliteFileSignature` | 完整 SQLite file size、mtime、SHA-256 | diagnostics、incident evidence、檔案變更追蹤 | 是，但只產生 informational diagnostic |
| `gmvRevenueStateToken` | 現有 `gmv-revenue-state-v1`，由 revenue frames + rule version 產生 | GMV active version、GMV export cache、trusted reference | GMV ledger 寫入不會改變；主營收改變會使 GMV version stale |

核心原則：**只有 `coreRevenueSignature` 是 Dashboard cache invalidation authority；full SQLite SHA 不再單獨觸發 degraded health。**

## 5. Architecture

### 5.1 Components

| Component | Responsibility |
|---|---|
| `backend/services/revenue_generation_service.py` | 建立 core revenue semantic signature；集中 canonicalization，避免 Dashboard 與 GMV 各自實作不同 fingerprint。 |
| `backend/services/cache_generation_service.py` | 讀寫 generation sidecar；保留 v1 compatibility，產出 core cache token 與 file-signature diagnostics。 |
| `backend/services/upload_orchestrator_service.py` | 主營收 accepted write / rollback 後更新 core signature；history write 不會改變 core signature。 |
| `backend/services/system_health_service.py` | 分別回報 core freshness 與 full DB file change；只有無法證明 core freshness 才進入 degraded。 |
| `backend/services/application_snapshot_service.py` | 以 core cache token 固定 request-scoped snapshot；避免 GMV ledger write 造成不必要重算。 |
| `backend/services/gmv_refund_service.py` | 保持 GMV active version 與 `gmv-revenue-state-v1` contract；改用共用 canonical helper，但不改 token 格式。 |
| `backend/services/gmv_export_cache_service.py` | 保持 version + GMV revenue token + rule version + schema version 的 manifest key。 |
| `scripts/migrate_cache_generation_v2.py` | 提供 dry-run 與明確 apply 的 runtime metadata migration；不修改 SQLite 業務表。 |

### 5.2 Data Flow

#### 主營收 upload

```text
Excel
  -> preflight
  -> SQLite upsert tour_data / others_data
  -> governed stability gate
  -> accepted / rollback
  -> build coreRevenueSignature
  -> atomic data_generation.json v2 write
  -> dashboard cacheToken = generation:coreRevenueToken
  -> dashboard / forecast / export read models
```

#### GMV refund merge

```text
退款 Excel
  -> immutable refund batch/current
  -> gmv_scope_versions ACTIVE
  -> gmv-revenue-state-v1 驗證
  -> GMV export cache active pointer swap
  -> SQLite file signature 改變
  -> coreRevenueSignature 不變
  -> Dashboard cache 不 invalidates
```

#### 主營收改變後重新開啟 GMV

```text
主營收 coreRevenueSignature 改變
  -> Dashboard cacheToken 改變
  -> 舊 GMV active version 狀態 = STALE_REVENUE_GENERATION
  -> trusted/export cache 不可直接下載
  -> UI 要求重新跑退款 Preflight / 建立新 active version
```

## 6. Data Contract

### 6.1 `data_generation.json` v2

Path:

```text
.nbs_runtime/data_generation.json
```

Canonical payload:

```json
{
  "schemaVersion": "nbs-data-generation-v2",
  "generation": 37,
  "operationId": "63a3a2587bce4fa2a644c44066a0be0c",
  "status": "accepted",
  "updatedAt": "2026-08-26T09:41:04+08:00",
  "signatureScope": "CORE_REVENUE",
  "coreRevenueSignature": {
    "schemaVersion": "nbs-core-revenue-signature-v1",
    "scopeLabel": "不含掛賬核銷與TT退款轉團款",
    "scopeContractVersion": "revenue-scope-v1",
    "sourceTables": ["tour_data", "others_data"],
    "rowCounts": {
      "tour_data": 0,
      "others_data": 0
    },
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "token": "nbs-core-revenue-v1:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "dbSignature": {
    "sizeBytes": 0,
    "modifiedNs": 0,
    "sha256": "1111111111111111111111111111111111111111111111111111111111111111"
  }
}
```

Contract rules:

- `schemaVersion` 是 required exact string。
- `generation`、`operationId`、`status`、`updatedAt` 保留原 contract semantics。
- `signatureScope` 必須是 `CORE_REVENUE`；不接受未定義 scope 作為 cache authority。
- `coreRevenueSignature.sha256` 是 core semantic payload digest，不是 SQLite file SHA。
- `dbSignature` 保留完整 SQLite file observation，但不直接決定 `cacheToken`。
- 對外 read result 產生以下 derived fields，不寫回 payload：

```json
{
  "currentCoreRevenueSignature": {},
  "currentDbSignature": {},
  "signatureMatched": true,
  "fileSignatureMatched": false,
  "cacheToken": "37:nbs-core-revenue-v1:0000000000000000000000000000000000000000000000000000000000000000",
  "legacyMode": false,
  "migrationRequired": false
}
```

`signatureMatched` 只表示 core revenue signature matched。`fileSignatureMatched=false` 在 core matched 時是 diagnostics，不是 cache stale。

### 6.2 Core revenue canonicalization

`build_core_revenue_signature(db_path, rule_version=REVENUE_SCOPE_LABEL)` 必須：

1. Read-only 載入 `tour_data` 與 `others_data`。
2. 使用現有 runtime column normalization。
3. 使用現有 `build_revenue_scope_frames()` 套用正式 scope。
4. 對 raw/formal frames 使用 stable column order、null normalization、timestamp normalization、string normalization 與 sorted canonical rows。
5. Payload 至少包含 raw/formal 四個 frame digest、row counts、scope label、scope contract version。
6. 以 canonical JSON + SHA-256 產出 `nbs-core-revenue-v1:<digest>`。
7. 不讀取或納入 `gmv_*` tables、stability history、upload coordination、cache manifest 或 runtime logs。

既有 GMV `revenue_state_token()` 保持 `gmv-revenue-state-v1:<digest>` 格式；共用 canonical helper 時，必須以 contract prefix 區分兩種 token，避免 cache key collision。

### 6.3 Cache key rules

| Consumer | Required key material |
|---|---|
| Dashboard facts/read model | `dashboard-facts-v1` + `coreRevenueToken` + rules fingerprint |
| Forecast/backtest cache | existing service version + `coreRevenueToken` + model/rules fingerprint |
| GMV active read model | active `version_id` + `gmvRevenueStateToken` + rule version |
| GMV export cache | existing `version_id` + `gmvRevenueStateToken` + rule version + export schema version |
| Full DB diagnostics | `sqliteFileSignature`，不得作 business cache key |

## 7. Compatibility and Fallback

### 7.1 Existing v1 metadata

讀到沒有 `schemaVersion` 或沒有 `coreRevenueSignature` 的 payload 時：

- `legacyMode=true`。
- `migrationRequired=true`。
- 以既有 full DB token 作保守 fallback，避免把可能 stale 的 cache 當成 fresh。
- 不自動寫入或修改 `.nbs_runtime/data_generation.json`。
- health 回報 `degraded`，原因必須是 `cache generation metadata migration required`，而不是偽裝成正常。

### 7.2 v2 core signature matched / file signature changed

- `signatureMatched=true`、`fileSignatureMatched=false`。
- Dashboard、Forecast、Export 可正常使用 core cache。
- system health status 不因 file mismatch 單獨降級。
- diagnostics 保存 `fileSignatureChanged=true` 與最近 observed signature。

### 7.3 v2 core signature mismatch

- 不發布或下載需要 trusted fresh cache 的 read model/export。
- 清除 request-scoped derived cache，使用 current DB 建立新的 candidate。
- 若 rebuild 成功，才以 atomic manifest/pointer swap 發布。
- 若 rebuild 失敗，保留上一個 immutable artifact，UI 顯示 `refresh_required`，不得回傳假成功。

### 7.4 Core signature unavailable

- health status 至少為 `degraded`。
- 不執行 silent repair。
- retain last known metadata、operation ID、full DB observation 與 error code。
- 只有明確 migration/rebuild command 可更新 runtime metadata。

## 8. Health and Hermes Contract

`system_health_service` 必須分開回報：

```json
{
  "dataGeneration": {
    "generation": 37,
    "signatureScope": "CORE_REVENUE",
    "signatureMatched": true,
    "fileSignatureMatched": false,
    "legacyMode": false,
    "migrationRequired": false,
    "cacheToken": "37:nbs-core-revenue-v1:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "diagnostics": {
    "sqliteFileChangedSinceGeneration": true,
    "sqliteFileChangeClassification": "NON_CORE_TABLE_WRITE"
  }
}
```

Hermes severity rules:

- core signature unavailable、core mismatch、generation operation evidence missing、cacheError、SQLite integrity failure：`degraded` 或 `critical`，依既有 contract 判斷。
- full file signature mismatch 且 core signature matched：`PASS`，但 report 必須列出 informational diagnostic。
- legacy metadata：`WARNING` 或 `degraded`，在 migration 完成前不得作 production-ready green claim。
- baseline drift、rollback failure、SQLite integrity failure 的優先級高於 cache signature diagnostics，仍依既有 P0/P1 gate 處理。

## 9. Testing Matrix

### Unit tests

| Case | Expected |
|---|---|
| 相同 core frames、只新增 GMV table row | core digest/token 相同 |
| 修改 `tour_data` 金額 | core digest/token 改變 |
| 修改 `others_data` | core digest/token 改變 |
| 修改 `rules_config` scope-relevant rule | core digest/token 改變 |
| 修改 stability history only | core digest/token 不變 |
| v1 generation payload | legacy fallback + migrationRequired |
| v2 core matched + full file changed | `signatureMatched=true`、health 不 degraded |
| v2 core mismatch | `signatureMatched=false`、cache refresh required |
| atomic write interrupted / malformed JSON | old valid payload preserved、fail-closed |
| unsafe or unknown schemaVersion | bounded validation failure，不 silently accept |

### Integration tests

- Primary upload accepted 後 generation v2、history operation ID、core token 三者一致。
- GMV merge 寫入同一 SQLite 後 system health 不因 file mismatch 降級。
- GMV active version 與 export manifest 的 `gmv-revenue-state-v1` 維持一致。
- 主營收改變後，舊 GMV active version 被標示 `STALE_REVENUE_GENERATION`。
- Application Snapshot 在 generation token 變更時 retry；GMV-only file change 不觸發 retry。
- Fast export / legacy fallback 的 artifact equivalence 仍為 `PASS`。

### Full acceptance

- focused cache/generation/health/GMV test pack 全部通過。
- full pytest 全部通過，不能以 warning 或 skipped 取代失敗測試。
- `scripts/review_agent.py` findings-first review PASS。
- `scripts/hermes_post_change_check.py` formal runtime overall PASS。
- 2026-05 baseline 維持 `HKD 12,057,968`。
- 正式 DB integrity PASS。
- Streamlit、FastAPI、Vue service identity 與 HTTP acceptance PASS。
- 正式 GMV cache 的 trusted reference、artifact equivalence、shadow validation PASS。

## 10. Performance and Observability

實作前先建立 formal runtime baseline：

- `/api/health` latency。
- `/api/facts` latency。
- first Dashboard load time。
- GMV active read-model load time。
- cache generation signature calculation time。

Acceptance target：

- GMV-only SQLite write 不得造成 Dashboard facts cache cold rebuild。
- `/api/health` 與 `/api/facts` p95 不得比修復前 baseline 增加超過 10%。
- core signature 計算須記錄 bounded stage timing；不得把完整 business rows 寫入 logs 或 runtime artifacts。
- cache state 必須可區分 `CORE_MATCHED`、`CORE_MISMATCH`、`LEGACY_FALLBACK`、`CORE_UNAVAILABLE`。

## 11. Rollout Strategy

### Phase 0: Observe

- 只讀計算 v2 core signature。
- 同時輸出 legacy full-file result 與 v2 result。
- 不改 cache key、不改 health severity、不寫 runtime。
- 比對正式 DB、GMV active version、dashboard facts 結果。

### Phase 1: Dual-read

- loader 支援 v1/v2。
- v2 result 只作 diagnostics。
- focused tests、benchmark、Review PASS 後才進入 runtime migration。

### Phase 2: Runtime metadata migration

- 使用 `scripts/migrate_cache_generation_v2.py --dry-run` 產生 bounded report。
- 核對 core token、frozen baseline、active GMV version、manifest equivalence。
- 使用明確 `--apply` 原子更新 `.nbs_runtime/data_generation.json`；不改 SQLite。
- migration 失敗時保留舊 sidecar，回報 blocked，不刪除任何 cache。

### Phase 3: Core token authority

- Dashboard/Application Snapshot/Forecast cacheToken 改用 core token。
- system health 僅對 core mismatch/unavailable 降級。
- GMV cache 維持既有 version + GMV token contract。

### Phase 4: Stabilize and retire legacy path

- 連續兩個正式 upload cycle 與一個 GMV merge cycle 通過 Hermes。
- legacy metadata 只保留 read compatibility；不再作為新寫入格式。
- legacy fallback 的移除需另立 change scope，不在本次實作自動移除。

## 12. Rollback Strategy

- code rollback：恢復 v1 loader 與既有 full-file cache token；不恢復或刪除 SQLite 業務資料。
- runtime rollback：以 migration 前 `.nbs_runtime/data_generation.json` 備份原子還原。
- cache rollback：GMV export cache 使用既有 immutable active pointer；不刪除 generation artifacts。
- 若 core signature 與 baseline 不一致，停止 rollout，保留 DB、backup、quarantine 與 diagnostics，先走既有 baseline/rollback 流程。

## 13. Acceptance Criteria

本 spec 完成的最低判定：

1. GMV-only SQLite write 不再讓 system health 產生「cache generation signature 不一致」的 degraded false positive。
2. Core revenue write 仍能被偵測並使 Dashboard cache 失效。
3. 舊版 generation metadata 不會被 silent overwrite。
4. GMV active version、trusted reference、fast export、legacy export 與 artifact equivalence 結果不變。
5. 正式口徑與 frozen baseline 不變。
6. full pytest、Review、Hermes 與正式 runtime acceptance 全部有可追溯 evidence。
