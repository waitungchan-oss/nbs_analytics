# GMV Refund Active-Version Cache Optimization Design

## 1. 文件狀態與範圍

- 日期：2026-08-24
- 狀態：Draft for user review
- 目標流程：GMV 排除訂單看板「上傳並合併退款資料庫」後的 active-version cache rebuild
- 正式收入範圍：不含掛賬核銷與 TT 退款轉團款
- 只處理：退款對帳、總退款／已退款報表、GMV export cache、測試與 benchmark
- 不處理：SQLite schema migration、Dashboard KPI、AI Forecast、WAPE、Governance Graph、Memory Hub、Agent Operations

本設計不改變正式業務數據或既有報表語義，只重組 cache build 的計算與 serialization 路徑。

## 2. 現況證據

目前 `build_gmv_formal_artifacts()` 的流程為：

```text
active version
  -> TOTAL_REFUND adjustment
  -> REFUNDED adjustment
  -> 3 份 audit workbook
  -> total 維度 3 份完整 dashboard workbook
  -> paid 維度 3 份完整 dashboard workbook
  -> 11 個 cache artifacts + manifest
```

在目前正式資料約 35,000 rows 的 read-only profiling：

| 階段 | 耗時 |
|---|---:|
| 兩個退款維度 adjustment | 約 13.4 秒 |
| 三份 audit workbook | 約 1.0 秒 |
| cache artifacts / manifest 寫入 | 約 0.2 秒 |
| 六份完整 dashboard workbook | 約 320 秒 |
| 全流程 | 約 334.6 秒 |

不含 workbook serialization 的六次 dashboard facts aggregation，每次約 1.9–2.6 秒；因此最大瓶頸是每個 dashboard workbook 反覆重跑 pipeline 與 `openpyxl` serialization，而不是 SQLite merge 或 manifest 寫入。

## 3. 設計目標

1. 退款合併的業務結果、active version 語義與現有 11 個 artifacts 保持一致。
2. 同一個 `revenue_generation_token`、rules fingerprint 與 export schema 下，共用可重用的 base preparation。
3. `總退款` 與 `已退款` 各只建立一次三 scope report facts，再分別序列化 3 個 workbook。
4. 6 個 workbook serialization 使用 bounded workers；worker 數量受設定與記憶體上限約束。
5. 新路徑必須先通過 semantic equivalence、schema、checksum 與 baseline gates，才可標為 READY。
6. 新路徑失敗或無性能收益時，自動回到既有 `_compute_gmv_exclusion_workbooks()` legacy path。
7. 重建完成後，重新整理頁面仍能直接讀取 active-version cache，不要求再次上傳退款 Excel。

## 4. 非目標與硬邊界

- 不修改 `nbs_marketing_data.db` 的業務表、退款 ledger schema 或 active-version lifecycle contract。
- 不改變 `總退款` 與 `已退款` 的退款金額、超額扣減、正式口徑排除規則。
- 不改變 `掛賬核銷` 與 `TT 退款轉團款` 的排除語義。
- 不把退款金額直接套用到旅行團人數或票務數量；現有 quantity basis 繼續保留。
- 不以 binary `.xlsx` 相同作為唯一驗收條件；允許 writer metadata / ZIP entry 差異。
- 不新增外部服務、database、queue、background worker service 或 workflow control。
- 不把 generic Data Export cache 與 GMV formal cache 共用同一個 manifest namespace。

## 5. 目標架構

```text
active version + revenue frames + rules snapshot
                    |
                    v
       GMV Base Preparation Cache
       - normalized columns
       - date/category/branch keys
       - revenue-scope masks
       - source row fingerprints
                    |
          +---------+---------+
          v                   v
   TOTAL_REFUND layer   REFUNDED layer
   adjusted amounts     adjusted amounts
          |                   |
          v                   v
   facts: all/no-writeoff/official  facts: all/no-writeoff/official
          |                   |
          +---------+---------+
                    v
       bounded workbook serializers
       total: ex / no_writeoff / official
       paid:  ex / no_writeoff / official
                    |
                    v
       semantic equivalence + artifact checks
                    |
          +---------+---------+
          v                   v
     READY manifest       FALLBACK legacy path
```

### 5.1 Base preparation layer

新增的 derived model 必須帶有：

```python
GmvExportBaseKey = {
    "version_id": str,
    "revenue_generation_token": str,
    "rules_fingerprint": str,
    "export_schema_version": str,
    "pipeline_fingerprint": str,
}
```

Base preparation 可以在 ignored runtime cache 或單次 job temporary directory 保存，但不能寫 SQLite。任何 key 不一致都必須 cache miss。

Base preparation 只保存可重建的 prepared frames / masks / fingerprints，不保存 active version 狀態、不取代 SQLite，也不保存未授權 raw Excel。

### 5.2 Refund dimension layer

兩個維度的 adjustment 結果仍然獨立：

| dimension | refund source | 用途 |
|---|---|---|
| `total` | `TOTAL_REFUND` | 總退款比較維度 |
| `paid` | `REFUNDED`、狀態為「已退款」 | 正式淨 GMV 扣減維度 |

兩個維度不能互相重用 adjusted monetary values。可以重用 base preparation、scope masks、欄位派生與 aggregation helper。

### 5.3 Report facts 與 workbook serialization

每個 dimension 先生成：

```text
ReportFacts(
  scope_id: all | no_writeoff | official,
  sheets: Mapping[str, DataFrame],
  row_counts: Mapping[str, int],
  schema_fingerprint: str,
  data_fingerprint: str,
)
```

serializer 只負責把一個 `ReportFacts` 寫成既有 workbook，不得重新呼叫完整 dashboard aggregation。

首個可行 writer 順序：

1. 先保留現有 `openpyxl`，拆出 artifact-level jobs。
2. benchmark `openpyxl` bounded parallel 與可用的 `xlsxwriter` / write-only candidate。
3. 只有 semantic equivalence 與記憶體 gate 都通過的 writer 才能成為 fast default；否則保留 legacy writer。

## 6. Data Contract

### 6.1 Build request

```json
{
  "versionId": "string",
  "revenueGenerationToken": "string",
  "ruleVersion": "不含掛賬核銷與TT退款轉團款",
  "rulesFingerprint": "sha256:string",
  "exportSchemaVersion": "string",
  "requestedDimensions": ["total", "paid"],
  "workerCount": 3
}
```

### 6.2 Artifact IDs

既有 artifact filename 必須保留：

```text
total.detail.csv
total.audit.xlsx
total.ex.xlsx
total.ex_no_writeoff.xlsx
total.ex_no_writeoff_refund_transfer.xlsx
paid.detail.csv
paid.audit.xlsx
paid.ex.xlsx
paid.ex_no_writeoff.xlsx
paid.ex_no_writeoff_refund_transfer.xlsx
summaries.json
```

若增加 package artifact，只能作為額外 artifact，不得改名或移除既有下載 artifact。

## 7. Cache Manifest

新增 `gmv-formal-export-v2` manifest；reader 必須向後兼容現有 `gmv-formal-export-v1`。

```json
{
  "schemaVersion": "gmv-formal-export-v2",
  "versionId": "string",
  "triggerBatchId": "string",
  "previousVersionId": "string|null",
  "revenueGenerationToken": "string",
  "ruleVersion": "string",
  "rulesFingerprint": "sha256:string",
  "exportSchemaVersion": "string",
  "pipelineFingerprint": "sha256:string",
  "builderMode": "legacy | fast | fallback",
  "status": "PREPARING | VERIFYING | READY | FALLBACK | FAILED",
  "artifacts": {},
  "performance": {
    "basePreparationMs": 0,
    "adjustmentMs": {},
    "factsMs": {},
    "serializationMs": {},
    "equivalenceMs": 0,
    "cacheWriteMs": 0,
    "totalMs": 0,
    "workerCount": 0,
    "peakRssBytes": null
  },
  "equivalence": {
    "status": "PASS | FAIL | NOT_RUN",
    "referenceBuilder": "legacy",
    "checkedArtifacts": [],
    "mismatchCount": 0,
    "boundedExamples": []
  },
  "fallback": {
    "used": false,
    "reasonCode": null
  }
}
```

Publication order：temporary job directory → artifact checksum/schema verification → equivalence → manifest atomic replace → READY read model。

任何 incomplete、stale、checksum mismatch 或 equivalence 未通過的 manifest 都不得被 `load_active_gmv_read_model()` 視為 READY。

## 8. Equivalence Contract

比較 fast 與 legacy 的每個 dimension × scope workbook：

- Sheet names、數量與順序一致。
- 欄位名稱與順序一致。
- row count 一致。
- key/data row set 一致。
- 金額以 Decimal 2 位小數 canonicalize。
- 人數／票務數量以 numeric canonicalize。
- 日期、空值與中文欄位以穩定 canonical form 比較。
- 排名 tie、零值、空 sheet 必須一致。
- 產出 `schemaFingerprint`、`dataFingerprint`、`rowCounts` 與 bounded mismatch examples。

`.xlsx` binary bytes 不要求相同；business semantic mismatch count 必須為 0。

## 9. Fallback

以下任一情況都 fail closed：

- base preparation、adjustment、facts 或 serializer exception；
- worker timeout / crash；
- writer 不可用；
- schema、row count、metric 或 data fingerprint mismatch；
- active version / revenue token / rules fingerprint stale；
- artifact checksum、path confinement 或 atomic publication 驗證失敗；
- performance benchmark 未達到最低改善門檻且 fast path 沒有明確收益。

Fallback 行為：

1. 不發布 fast READY manifest。
2. 將 job 記為 `FALLBACK`，保留 bounded reason code。
3. 執行既有 legacy builder，確保正式結果仍可產生。
4. UI 顯示「高速 cache 驗證未通過，已使用相容路徑」。
5. 不寫入原始 SQLite，不回滾已確認的 immutable refund batch。

## 10. Test 與 Benchmark Matrix

### 10.1 Contract / unit

- base key fingerprint deterministic；資料、規則、schema 任一改變都 miss。
- total / paid dimension 不會互用 adjusted amount。
- 三個 scope filter 與既有規則一致。
- facts builder 不重新呼叫 legacy workbook builder。
- serializer worker 不修改 shared input frame 或 Streamlit session state。
- manifest v2 write/read、v1 compatibility、checksum、atomic publication。

### 10.2 Equivalence

- fixture：空資料、單列、重複來源單據號、部分退款、超額退款、待退款轉已退款。
- revenue scope：掛賬核銷、TT 退款轉團款、混合收款單號。
- 日期、小數、缺失值、中文欄位、零金額、排名 tie、空 sheet。
- real active version snapshot：total / paid × 3 scopes 逐 sheet semantic comparison。

### 10.3 Integration / UI

- merge 後 11 artifacts 全部 READY。
- 任一 serializer failure 不會產生半成品 READY cache。
- cache hit 不重新掃描完整營收、不重新 serialization。
- reload 後不需再次上傳退款 Excel即可顯示下載按鈕。
- fallback 有明確狀態與 reason code。

### 10.4 Benchmark

新增 read-only benchmark command，必須支援：

```bash
./.venv/bin/python scripts/benchmark_gmv_refund_cache.py \
  --db-path <path> \
  --version-id <id> \
  --cache-dir <temporary-dir> \
  --mode legacy,fast \
  --workers 1,2,3
```

benchmark 必須輸出 JSON：

```json
{
  "mode": "legacy | fast",
  "basePreparationMs": 0,
  "factsMs": {},
  "serializationMs": {},
  "equivalenceMs": 0,
  "totalMs": 0,
  "artifactBytes": 0,
  "peakRssBytes": null,
  "equivalenceStatus": "PASS | FAIL"
}
```

benchmark 只能讀取正式 DB，寫入指定 temporary cache；不可改 SQLite、active version、baseline 或正式 cache。

### 10.5 Performance gates

- cache hit / manifest load：< 1 秒。
- fast path equivalence：0 semantic mismatch。
- fast path total duration：相同 snapshot 下至少比目前 334.6 秒 baseline 改善 40%；若未達標，不切 default。
- peak RSS 必須低於 rollout 設定上限；超過時降低 workers 或 fallback。
- legacy 與 fast 報表結果必須一致，即使 writer bytes 不一致。

## 11. Rollout Strategy

### Phase 0：測試與 benchmark only

建立 fixture、real read-only benchmark、legacy timing baseline；不改正式 build 路徑。

### Phase 1：shadow / opt-in

在 temporary cache 同時生成 fast 與 legacy，執行 equivalence；只記錄結果，不讓 fast output 取代正式 cache。

### Phase 2：fast default with fallback

equivalence、baseline、checksum、memory gate 均通過後，fast 成為 default。任何 failure 自動 legacy fallback。

### Phase 3：cache reuse / retention

重用同 generation + rules + schema + pipeline fingerprint 的 base preparation；清理過期 derived artifacts，不刪除 SQLite、refund ledger、baseline 或 rollback evidence。

## 12. Definition of Done

- 測試與 benchmark 可重現目前 334.6 秒 baseline與各階段 timing。
- fast path 使用 shared base preparation，total / paid 各自只做一次三-scope facts build。
- 6 個 workbook 可 bounded parallel serialization。
- semantic equivalence、schema、checksum、baseline gates 全部通過才發布 READY。
- fallback 可在 fast failure 時產生既有結果。
- active version cache reader 向後兼容 v1，reload 後不需重新上傳退款 Excel。
- targeted tests、full pytest、Review、Hermes 與實際 Streamlit UI acceptance 均通過。
- 沒有 SQLite、正式業務口徑、Dashboard KPI、Forecast 或 WAPE regression。
