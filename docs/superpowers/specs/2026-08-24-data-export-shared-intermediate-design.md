# Data Exports Shared Intermediate + Parallel Package Design

## 1. 文件狀態

- 日期：2026-08-24
- 狀態：Draft for user review
- 適用專案：NBS Analytics
- 目標模組：Streamlit `Data Exports：報表與日誌匯出`
- 正式收入範圍：不含掛賬核銷與 TT 退款轉團款
- 方案：B + C
  - B：Shared Intermediate Model + parallel Excel serialization
  - C：versioned ZIP package + individual artifact downloads

本設計只處理匯出效能與可驗證性，不改變原始業務資料、SQLite schema、正式收入規則、Dashboard KPI、AI Forecast、WAPE 或既有 workbook contract。

## 2. 背景與問題定義

現行 `_compute_export_workbooks()` 依序建立三份完整 workbook：

1. 全維度報表
2. 不含掛賬核銷報表
3. 不含掛賬核銷與 TT 退款轉團款的正式口徑報表

三個 export path 都會重新執行大量 normalization、分類、groupby、交易人數與票務數量統計，再進行 Excel serialization。現有 lazy export 保護了 Dashboard 首屏，但把首次計算成本延後到使用者按下「準備下載報表」時。

現況證據：

- `app_pages.py` 的 Data Export Center 在首次使用時呼叫 `_ensure_export_workbooks()`。
- `app_workflows.py` 的 `_compute_export_workbooks()` 連續呼叫三個 dashboard workbook builder。
- 現有 export cache 可命中後快速載入，但 cache 約 44MB，且以單一 pickle 保存三份 workbook bytes。

## 3. 設計目標

### 3.1 主要目標

- 對相同資料 generation 與 business rules，只做一次 shared aggregation。
- 三個 workbook 的 Excel serialization 可平行執行。
- 提供一個 ZIP package，讓使用者一鍵下載完整報表包。
- 保留三個個別 workbook download。
- 只有在新版與既有輸出通過 semantic equivalence gate 後，才採用高速結果。
- 若高速路徑失敗、不一致或超時，自動回退既有 export path。
- 下載請求只讀取 READY artifacts，不在 Streamlit request 中重新計算。

### 3.2 非目標

- 不修改 SQLite schema 或原始業務資料。
- 不改變正式收入範圍、日期口徑或業務規則。
- 不改變既有 workbook sheet、欄位、檔名與業務數值。
- 不新增外部服務、雲端 object storage 或資料庫。
- 不把 Export Job Manager 擴展為一般 agent orchestration 或 workflow control。
- 不把 Streamlit Agent Operations 變成 job approval 或 dispatch 入口。

## 4. 驗收定義：Semantic Equivalence

高速路徑的「結果一致」定義為業務語義、報表結構與業務規則一致，不要求 `.xlsx` binary bytes 完全相同。

### 4.1 必須一致

#### 數值

- GMV、營收總額與各期間合計。
- 分社、專職、旅行團、郵輪、票務的金額。
- 交易人數與票務數量。
- 各 sheet 的業務 metrics、排名與明細金額。

#### 結構

- sheet 數量與名稱。
- 欄位名稱、順序與型別。
- 每個 sheet 的 row count。
- 每個 sheet 的 key、資料列集合與排序規則。
- 公式、篩選、欄寬或格式若屬既有 contract 的一部分，也必須一致。

#### 業務規則

- 正式收入範圍仍為不含掛賬核銷與 TT 退款轉團款。
- 正式日期仍使用主表 `收款時間`。
- 不得改動 SQLite、Dashboard、Forecast、WAPE 或 baseline。
- 2026-05 frozen baseline 必須維持 HKD 12,057,968。

### 4.2 不要求 binary identity

下列非業務 metadata 可不同：

- XLSX ZIP entry 順序。
- workbook 建立時間與 writer metadata。
- XML 非業務性排序。
- ZIP compression implementation details。

### 4.3 Canonical comparison

equivalence runner 必須把新舊 workbook 讀回成 canonical representation：

```text
workbook
  -> sheet order / names
  -> header and type normalization
  -> stable key sort
  -> Decimal money normalization to 2 decimal places
  -> quantity normalization
  -> canonical JSON / SHA-256
```

比較結果需保存每份報表的：

- `schemaFingerprint`
- `dataFingerprint`
- `rowCounts`
- `metricSummary`
- `mismatchCount`
- bounded mismatch examples

## 5. 目標架構

```text
SQLite / existing read models
        |
        v
Generation + rules fingerprint
        |
        v
Shared Intermediate Model
  - normalized frames
  - classified transactions
  - reusable groupby outputs
  - quantity and amount aggregates
        |
        +-------------------+-------------------+
        v                   v                   v
  Full report inputs   No-writeoff inputs   Official-scope inputs
        |                   |                   |
        +-------------------+-------------------+
                            v
                 Bounded parallel serializers
                   - full.xlsx
                   - no_writeoff.xlsx
                   - official.xlsx
                            |
                            v
                 Semantic Equivalence Gate
                            |
                +-----------+-----------+
                |                       |
             PASS                    FAIL
                |                       |
                v                       v
       Versioned READY manifest   Existing export fallback
                |
       +--------+---------+
       v                  v
 individual XLSX      all-reports.zip
```

### 5.1 Shared Intermediate Model

Shared intermediate data is a derived, generation-scoped object. It may contain:

- normalized column names and dates;
- business type classification;
- branch and salesperson mapping;
- reusable transaction keys;
- shared amount and quantity aggregates;
- source row fingerprints needed for equivalence and audit.

It must not become a new source of truth. It is invalidated by:

- SQLite data generation token change;
- business rules fingerprint change;
- official export schema version change;
- relevant pipeline code version change.

The intermediate model must not be written into SQLite. It may be stored in an ignored local temporary/cache path for the duration of one export job.

### 5.2 Scope-specific report inputs

Each report input is produced from the same intermediate model with an explicit scope descriptor:

```json
{
  "scopeId": "full | no_writeoff | official",
  "excludedReceiptTypes": [],
  "excludedPaymentMethods": [],
  "officialScope": false
}
```

The scope descriptor is part of the artifact fingerprint. A report must never silently reuse an artifact from another scope.

### 5.3 Parallel serialization

Serialization is an artifact-level operation. Each worker receives only:

- one scope-specific report input;
- the immutable export schema contract;
- the target temporary artifact path;
- the expected artifact fingerprint.

Workers must not write SQLite, mutate Streamlit session state, or change shared pandas frames. The controller collects worker results, runs equivalence checks, and publishes the manifest atomically.

The first implementation should use a bounded local worker pool. Worker count must be configurable and capped, with a default of three report workers. If process startup or serialization overhead exceeds the measured benefit, the implementation may use two workers or a sequential serializer while retaining the same artifact contract.

## 6. Data Contract

### 6.1 Export request

```json
{
  "generationToken": "string",
  "rulesFingerprint": "sha256:string",
  "exportSchemaVersion": "official-export-schema-version",
  "requestedArtifacts": [
    "full",
    "no_writeoff",
    "official",
    "package"
  ],
  "requestedAt": "ISO-8601",
  "requestedBy": "string"
}
```

### 6.2 Scope contract

| scopeId | 排除收款類型 | 排除收款方式 | 用途 |
|---|---|---|---|
| `full` | 無 | 無 | 完整分析報表 |
| `no_writeoff` | 掛賬核銷 | 無 | 管理比較報表 |
| `official` | 掛賬核銷 | TT 退款轉團款 | 正式口徑報表 |

### 6.3 Artifact contract

```json
{
  "artifactId": "full.xlsx",
  "scopeId": "full",
  "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "relativePath": "artifacts/full.xlsx",
  "bytes": 0,
  "sha256": "sha256:string",
  "schemaFingerprint": "sha256:string",
  "dataFingerprint": "sha256:string",
  "rowCounts": {},
  "buildDurationMs": 0,
  "status": "READY | FAILED | FALLBACK"
}
```

### 6.4 Package contract

ZIP 必須包含：

- 三份既有 workbook，保留既有檔名。
- `export-manifest.json`。
- `equivalence-report.json`。
- 可選的 bounded `README.txt`，說明 generation token、scope 與建立時間。

ZIP 不得包含原始 SQLite、未授權 secrets、完整 runtime log 或與本次 export 無關的 cache。

## 7. Cache Manifest

建議 manifest schema：`export-manifest-v2`。

```json
{
  "schemaVersion": "export-manifest-v2",
  "jobId": "uuid",
  "generationToken": "string",
  "rulesFingerprint": "sha256:string",
  "exportSchemaVersion": "string",
  "pipelineFingerprint": "sha256:string",
  "status": "PREPARING | VERIFYING | READY | FALLBACK | FAILED",
  "createdAt": "ISO-8601",
  "completedAt": "ISO-8601",
  "artifacts": {
    "full": {},
    "no_writeoff": {},
    "official": {},
    "package": {}
  },
  "equivalence": {
    "status": "PASS | FAIL | NOT_RUN",
    "referencePath": "string",
    "checkedArtifacts": [],
    "mismatchCount": 0,
    "reportPath": "equivalence-report.json"
  },
  "fallback": {
    "used": false,
    "reasonCode": null,
    "details": null
  },
  "retention": {
    "expiresAt": "ISO-8601",
    "sourceGeneration": "string"
  }
}
```

### 7.1 Publication rule

Artifacts are first written under a job-specific temporary directory. The controller publishes them only after:

1. All requested workbook artifacts complete.
2. Each artifact passes checksum and schema validation.
3. Semantic equivalence passes.
4. ZIP package is created and verified.
5. Manifest is written last using atomic replace.

Incomplete jobs must never appear as READY.

## 8. Fallback and failure handling

### 8.1 Automatic fallback conditions

Fallback to the existing export path when any of the following occurs:

- shared intermediate build exception;
- worker timeout or process crash;
- workbook serialization exception;
- schema fingerprint mismatch;
- row count mismatch;
- metric mismatch;
- baseline drift;
- artifact checksum mismatch;
- manifest or package verification failure.

### 8.2 Fallback behavior

- Do not publish the new package as READY.
- Preserve the failed job manifest as `FALLBACK` or `FAILED`.
- Run the existing `_compute_export_workbooks()` path.
- Mark the UI as `高速匯出驗證失敗，已使用相容匯出路徑`。
- Record bounded diagnostics, not raw full datasets.
- Allow retry after the source generation or rules fingerprint changes.

### 8.3 Fail-closed boundaries

The fast path must fail closed for:

- unknown scope ID;
- missing generation token;
- missing rules fingerprint;
- missing artifact contract;
- stale manifest;
- unverified equivalence result.

## 9. UI and user experience

The Data Export Center should expose:

1. `準備下載報表` only when no matching READY manifest exists.
2. Progress states: `Preparing` → `Verifying` → `Ready`.
3. One primary button: `一鍵下載完整報表包 ZIP`.
4. Three secondary buttons for individual XLSX files.
5. Last build time, generation token short form and cache status.
6. Fallback notice when the legacy path was used.
7. No repeated regeneration when the same manifest is READY.

The browser download action must never trigger aggregation or workbook generation.

## 10. Test Matrix

### 10.1 Unit tests

| Area | Test |
|---|---|
| scope | 三種 scope filter 與既有規則一致 |
| intermediate | shared aggregation 對固定 fixture 產生穩定 fingerprint |
| serializer | 每份 workbook 可獨立生成與讀回 |
| manifest | schema、checksum、path confinement、atomic publication |
| package | ZIP 內容、檔名、manifest 與 checksum |
| fallback | worker exception、timeout、schema mismatch 均回退 |
| stale cache | generation/rules/schema 不一致不得命中 |

### 10.2 Equivalence tests

- 舊版與高速版三份 workbook 逐 sheet canonical comparison。
- 空資料、單列、重複單號、跨月份、掛賬核銷、TT 退款轉團款。
- 金額小數、缺失值、日期邊界、中文欄位與特殊字元。
- 排名 tie、零金額與空 sheet。
- 檔案 bytes 不同但 semantic fingerprint 相同時必須 PASS。

### 10.3 Regression and baseline tests

- `tests/test_phase2_precheck_acceptance.py`。
- Dashboard service/API export contract tests。
- Existing export workbook contract tests。
- 2026-05 baseline：HKD 12,057,968。
- 2026-01 至 2026-06 monthly baseline checks。
- Upload、revenue scope、rollback、cache generation tests。

### 10.4 Integration tests

- full job：三份 XLSX + ZIP + manifest 全部 READY。
- one artifact failure：其他 artifact 不得被誤標 READY。
- retry：只重建失敗 artifact，且最終 manifest 正確。
- cache hit：不重新執行 aggregation 或 serializer。
- generation change：舊 manifest 不可被下載。

### 10.5 UI acceptance

- 首頁載入不生成 workbook。
- 按「準備下載報表」後顯示進度，而非無限 spinner。
- READY 後一鍵下載 ZIP。
- 個別 workbook 可下載。
- 重整頁面後仍可讀取 READY manifest。
- fallback 狀態有明確提示。
- 下載按鈕不觸發重新計算。

## 11. Performance acceptance

基準須在相同 SQLite snapshot、相同 rules fingerprint 與相同 machine 上比較：

- `aggregation_ms`
- `serialization_ms` per artifact
- `equivalence_ms`
- `package_ms`
- total job duration
- peak RSS
- cache hit latency

建議初始 gate：

- cache hit download preparation < 1 秒。
- READY manifest lookup < 250ms。
- fast path total duration 至少比目前串行首次生成改善 40%。
- semantic equivalence 0 mismatch。
- fallback 不得改變報表結果。

若平行化使 peak RSS 超過既定安全上限，降低 worker count，不放寬 equivalence gate。

## 12. Rollout Strategy

### Phase 0：Instrumentation only

- 記錄目前三份 workbook 的 aggregation、serialization、cache load 時間。
- 不改輸出路徑、不改 UI 行為。

### Phase 1：Shadow fast path

- 建立 shared intermediate 與 parallel artifacts。
- 同時產生 legacy 與 fast output。
- 只做 equivalence compare，不讓使用者下載 fast artifact。
- 收集 mismatch、duration、RSS 與 failure rate。

### Phase 2：Opt-in package

- 在 Data Export Center 顯示「使用高速匯出驗證版」測試入口。
- 只有 equivalence PASS 才提供 ZIP。
- legacy path 保留為明確 fallback。

### Phase 3：Default fast path

- fast path 成為預設。
- legacy path 仍保留為 automatic fallback。
- UI 顯示 manifest status、last build duration 與 fallback reason。

### Phase 4：Retention and cleanup

- 只保留最近 N 個 generation artifacts。
- 以 manifest retention policy 清理過期 export artifacts。
- 不刪除 canonical SQLite、baseline、rollback 或正式業務資料。

## 13. Rollback Strategy

Rollback 不需要 database migration。只需：

1. 關閉 fast path feature flag。
2. 保留既有 manifest 與 diagnostics。
3. 恢復 `_ensure_export_workbooks()` 使用 legacy export builder。
4. 驗證既有 export contract 與 baseline。

Fast artifacts 可保留作為 read-only evidence，但不得在 equivalence 未通過時被標為 READY。

## 14. Implementation Boundaries

預計 allowlist：

- `app_pages.py`
- `app_workflows.py`
- `pipeline.py` 或新的 bounded export service module
- `backend/services/` 下的 export manifest / equivalence helper
- `tests/` 下對應 export、performance、equivalence、UI contract tests

禁止在本 feature 中修改：

- SQLite schema 或 migration
- revenue scope constants
- business rules semantics
- baseline values
- forecast/WAPE calculations
- Governance Graph、Memory Hub、Agent Operations 或 dispatch controls

## 15. Definition of Done

- shared intermediate 只建立一次並有穩定 fingerprint。
- 三份報表可由 bounded workers 平行 serialization。
- ZIP package 與個別 XLSX 都能下載。
- manifest READY 只在所有 artifact 與 equivalence gate 通過後產生。
- 任一 mismatch、timeout、checksum 或 baseline failure 都自動 fallback。
- 舊版與高速版 semantic equivalence 0 mismatch。
- 完整 pytest、targeted export tests、baseline checks、Hermes 與 Streamlit UI acceptance 均通過。
- 沒有 SQLite、正式業務數據、Dashboard KPI、Forecast 或 WAPE regression。
