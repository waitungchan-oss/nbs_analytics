# Revenue Pipeline 與正式 Cold Rebuild 優化設計

## 1. 文件狀態

- 日期：2026-08-26
- 狀態：Draft for user review
- 適用專案：NBS Analytics
- 主要模組：`pipeline.py`、GMV formal refund cache、Data Exports、upload orchestration
- 正式收入範圍：不含掛賬核銷與 TT 退款轉團款
- Frozen baseline：2026-05，HKD 12,057,968
- 本 spec 不直接執行 implementation、migration 或正式資料重建

本 spec 匯總兩個互相關聯但可獨立交付的改善方向：

1. Pandas dtype-safe cleanup：移除既有 90 個 `FutureWarning`。
2. Formal cold rebuild optimization：縮短退款合併後建立 active version、正式 cache 與報表的時間。

## 2. 問題背景與目前證據

### 2.1 Pandas warnings

目前 full pytest 為：

```text
2218 passed, 90 warnings
```

90 個 warning 主要是 3 個 `pipeline.py` 位置在多組測試中重複出現：

- `pipeline.py:821)：日期字串與 fallback 欄位的 `fillna`
- `pipeline.py:868)：merge 後對整個 DataFrame 執行 `fillna(0)`
- `pipeline.py:890)：另一條日期 fallback 的 `fillna`

warning 類型都是 pandas 對 object dtype 自動 downcast 的未來行為變更。現階段不代表數值錯誤，但若不明確指定 dtype，未來 pandas 升級可能改變欄位型別、空值處理或匯出結果。

### 2.2 Formal cold rebuild

目前 GMV formal cache 已有：

- generation-scoped cache
- active pointer
- trusted reference／shadow validation
- legacy fallback
- 總退款與已退款兩個 dimension
- versioned artifact manifest

cold miss 時，`build_gmv_formal_artifacts_fast_or_legacy()` 仍可能先執行 legacy seed，再執行 fast gate、baseline validation、artifact serialization 與 manifest publish。現有 profiling 顯示，六份完整 dashboard workbook 的重複 aggregation 與 Excel serialization 是主要瓶頸；SQLite upsert 與 manifest write 不是第一優先瓶頸。

### 2.3 業務風險

效能優化不能以改變結果為代價。特別要保護：

- 正式 revenue scope
- 2026-05 frozen baseline
- `TT 退款轉團款` 排除語義
- 總退款／已退款 dimension 定義
- 退款狀態由「退款中」變成「已退款」的 update/upsert
- 退款明細金額與實際扣減金額的 reconciliation
- active version 與報表 cache 的一致性

## 3. 設計目標

### 3.1 必須達成

1. 將 full pytest 的 90 個 pandas warning 降至 0。
2. warning cleanup 前後，業務結果、欄位型別與輸出 contract 一致。
3. cold rebuild 只執行一次可共享的 normalization、分類與 aggregation。
4. 總退款與已退款維度共用合法的 intermediate data，但不共用錯誤的 adjusted monetary result。
5. Excel serialization 可使用 bounded workers，但必須受 memory 與 worker 上限控制。
6. 新高速路徑只有在 trusted reference／shadow equivalence、schema、baseline 與 artifact checks 全部通過後才能成為 READY。
7. cold rebuild 失敗時，active pointer 保持上一個 READY generation；不得 publish 半成品。
8. 新退款 Excel 的新增、更新、狀態變更都必須被正確納入，不可只 append。
9. refresh/restart 後可直接讀取最後一個 READY active cache，不要求重傳 Excel。
10. 保持 SQLite 為 canonical source；所有 intermediate/cache 都是 derived read model。

### 3.2 效能目標

正式 benchmark 建立後，以目前 legacy cold rebuild 作 baseline。第一階段目標：

- cold rebuild wall-clock 至少降低 50%；若實測瓶頸不在可共享 aggregation，必須明確記錄原因。
- shared aggregation 不因 serializer worker 數增加而重複執行。
- warm cache read 與單一 artifact download 不重新跑 aggregation。
- peak RSS 不超過 legacy cold rebuild 的 1.5 倍。
- equivalence failure、timeout 或 memory guard 觸發時，fallback 必須可完成。
- 不以單次偶然最快時間作為通過條件，至少使用 3 次 cold、3 次 warm sample，報告 median 與 p95。

## 4. 非目標與硬邊界

- 不改變正式 revenue scope、日期口徑、KPI、Forecast、WAPE 或 frozen baseline。
- 不改變 refund ledger 的業務意義或既有 active-version lifecycle。
- 不新增外部服務、queue、database、migration 或常駐 background service。
- 不把 Memory Hub、Governance Graph 或 Agent Operations 變成資料寫入或 cache publish 入口。
- 不把 generic Data Export cache 與 GMV formal cache 共用同一個 namespace。
- 不用無限制 multiprocessing 解決尚未釐清的 aggregation 重複問題。
- 不以 XLSX binary byte identity 作為唯一 equivalence 定義。
- 不刪除上一個可用的 READY cache 來換取新 cache 速度。
- 不為了讓 baseline 通過而修改正式資料、baseline registry 或 dashboard 顯示層。

## 5. 目標架構

```text
正式 SQLite + active refund version + rules snapshot
                         |
                         v
             generation / rules / pipeline fingerprints
                         |
                         v
              Shared Intermediate Preparation
              - normalized columns and dtypes
              - parsed dates and fallback dates
              - branch / salesperson / product classification
              - scope masks
              - source row fingerprints
              - reusable amount / quantity aggregates
                         |
             +-----------+-----------+
             v                       v
       TOTAL_REFUND layer       REFUNDED layer
       總退款 adjusted view      已退款 adjusted view
             |                       |
             v                       v
       scope report facts       scope report facts
       all / no_writeoff /      all / no_writeoff /
       official                 official
             +-----------+-----------+
                         v
              bounded serializers
              - total workbooks
              - paid workbooks
              - detail / audit artifacts
                         |
                         v
          semantic equivalence + baseline + artifact gate
                         |
              +----------+----------+
              v                     v
        READY manifest          legacy fallback
        atomic pointer          previous READY retained
```

### 5.1 分層責任

| Layer | 責任 | 不可做的事 |
|---|---|---|
| Canonical source | SQLite、退款 ledger、active version | 不由 cache 反向修改 |
| Preparation | normalization、分類、共用 keys、scope masks | 不決定 active version |
| Refund dimension | 總退款／已退款各自 monetary adjustment | 不混用兩個 dimension 的扣減值 |
| Report facts | 將 intermediate 轉成 sheet-ready frames | 不重新讀 raw Excel |
| Serializer | 將 facts 寫成既有 XLSX/CSV | 不重新 aggregation |
| Equivalence | 比較 legacy 與 fast 語義結果 | 不修改輸出 |
| Manifest/pointer | 原子發布 READY generation | 不指向未完成 artifact |
| UI/read model | 讀 READY cache、顯示狀態與下載 | 不在 download request 重建全量資料 |

## 6. Pandas dtype-safe cleanup contract

### 6.1 日期欄位

日期處理必須拆成明確步驟，不使用依賴 object downcast 的整欄 `fillna`：

```python
parsed = pd.to_datetime(source, errors="coerce").dt.strftime("%Y-%m-%d")
fallback = frame["統一日期"].astype("string")
frame["日期"] = parsed.astype("string").fillna(fallback)
```

實作時需依現有欄位實際 dtype 調整，但結果 contract 必須明確：

- 有效交易時間優先。
- 交易時間無效時使用 `統一日期`。
- 兩者皆無效時保留 pandas missing value，而不是任意轉成字串 `"nan"`。
- 日期輸出格式維持 `YYYY-MM-DD`。

### 6.2 Merge 後數值欄位

不得對整個 DataFrame 使用 `fillna(0)`。必須只對預期 numeric output columns 補零：

```python
for column in ("交易人數", "郵輪交易人數"):
    result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0)
```

非數值欄位的 missing semantics 必須保留，避免 silent conversion。

### 6.3 dtype acceptance

每個 warning site 必須有 regression test，至少驗證：

- 日期欄位 dtype 與內容。
- 金額欄位為 numeric。
- 交易人數與票務數量為 numeric。
- 空 DataFrame 的欄位與 dtype。
- 缺少交易時間、缺少統一日期、全空值。
- 既有 Excel／CSV export 的 schema 與值未變。

## 7. Shared Intermediate Model

### 7.1 Build key

```python
GmvPreparationKey = {
    "version_id": str,
    "revenue_generation_token": str,
    "refund_state_sha256": str,
    "rules_fingerprint": str,
    "export_schema_version": str,
    "pipeline_fingerprint": str,
}
```

任何 key 不一致都必須 cache miss。Preparation 不得跨越不同正式口徑、rules 或 pipeline fingerprint 重用。

### 7.2 Preparation 內容

可包含：

- normalized columns 與明確 dtype
- parsed date columns
- branch、salesperson、product category keys
- revenue scope masks
- source row identity / fingerprints
- reusable amount aggregates
- reusable quantity aggregates
- report facts 所需的 stable sort keys

不得包含：

- 未確認的 raw Excel 暫存檔
- active pointer 狀態
- 可被當成 canonical source 的完整 business ledger
- 未經 scope 標記的混合 monetary values

### 7.3 Dimension isolation

```text
shared preparation
       +----------------------+
       |                      |
TOTAL_REFUND adjustment   REFUNDED adjustment
       |                      |
total report facts        paid report facts
```

兩個 dimension 可以共享：

- source normalization
- transaction identity
- date/category mapping
- scope masks
- aggregation framework

兩個 dimension 不可以直接共享：

- adjusted amount
- applied refund amount
- over-refund amount
- dimension summary

## 8. Incremental refund rebuild

### 8.1 Identity

refund row identity 以穩定的退款單號為主；來源單據號、退款狀態、退款原幣金額與退款方式是可變業務欄位。

同一退款單號再次出現時：

- 不 append duplicate。
- 執行 upsert。
- 記錄 `NEW`、`UNCHANGED`、`STATUS_CHANGED`、`AMOUNT_CHANGED`、`REFUND_IDENTITY_CONFLICT`。
- 若同一退款單號對應不同來源單據號，視為 blocking 或明確人工處理，不可靜默覆蓋。

### 8.2 狀態變更

「退款中」變為「已退款」時：

1. 更新 immutable refund batch 的最新狀態 view。
2. 保留前一狀態的 audit evidence。
3. 只重算受影響來源單據號。
4. 重新產生總退款與已退款 dimension summary。
5. active version 以新的 refund state fingerprint 建立。
6. 通過 gate 後才 atomic swap active pointer。

### 8.3 Scope rule

退款方式為 `TT 退款轉團款` 的來源單據號必須套用既有正式 revenue scope exclusion：

- 不應再扣減正式 GMV。
- 應在異常／對帳 evidence 中保留排除原因。
- 總退款與已退款兩個維度都必須遵守此規則。
- 超額退款計算不能把已被正式 scope 排除的原收款金額納入可扣減基礎。

## 9. Cache manifest 與 atomic publish

沿用現有 GMV formal export manifest contract，擴充 performance 與 validation metadata；不另立互斥 namespace。

必要欄位：

```json
{
  "schemaVersion": "gmv-formal-export-v2",
  "versionId": "string",
  "revenueGenerationToken": "string",
  "refundStateSha256": "sha256:string",
  "ruleVersion": "string",
  "rulesFingerprint": "sha256:string",
  "exportSchemaVersion": "string",
  "pipelineFingerprint": "sha256:string",
  "builderMode": "legacy | fast | fallback",
  "status": "preparing | verifying | ready | fallback | failed",
  "validationMode": "legacy | trusted_warm | shadow",
  "shadowStatus": "pass | fail | not_run",
  "equivalenceStatus": "pass | fail | not_run",
  "active": false,
  "artifacts": {},
  "performance": {
    "basePreparationMs": 0,
    "dimensionMs": {},
    "factsMs": {},
    "serializationMs": {},
    "equivalenceMs": 0,
    "manifestWriteMs": 0,
    "totalMs": 0,
    "workerCount": 1,
    "peakRssBytes": null
  },
  "fallback": {
    "used": false,
    "reasonCode": null
  }
}
```

發布順序：

1. 在 version-specific temporary generation 建立 artifacts。
2. 寫入 manifest，狀態為 `preparing` 或 `verifying`。
3. 執行 schema、checksum、equivalence、baseline 與 business gates。
4. 只有全部通過才寫 `ready` manifest。
5. 最後 atomic swap `active.json`。
6. 舊 active generation 仍保留，直到新 generation `ready` 且 pointer swap 成功。

Cache reader 必須：

- 只接受 manifest 與 artifact checksum 一致的 `ready` generation。
- 遇到 v1 legacy cache 時可 read-compatible，但不可標記為 fast `ready`。
- pointer、manifest、generation 不一致時 fail closed 到上一個 READY 或 legacy fallback。
- 不因單一非核心 DB file signature 變更而錯誤 invalidation；使用 scope-aware core revenue signature。

## 10. Bounded serializer

### 10.1 執行模型

第一階段先建立 artifact job list：

```text
total.detail
paid.detail
total.workbooks
paid.workbooks
summaries
audit
```

每個 job 只接收已完成的 ReportFacts 或 serialized bytes，不得再次執行 dashboard aggregation。

### 10.2 Worker policy

- 預設 worker count：1 或 2。
- 上限：3。
- worker count 由 benchmark 與 memory guard 決定。
- 若 peak RSS、timeout、worker exception 或 equivalence failure，立即回退 legacy。
- 不對 SQLite connection、Streamlit session state 或 mutable global DataFrame 做跨 worker 共享。

### 10.3 serializer acceptance

新 serializer 需要通過：

- sheet names
- columns and order
- row counts
- canonical data fingerprints
- numeric totals
- detail/audit consistency
- existing download filenames
- output artifact checksum
- memory upper bound

## 11. Fallback 與失敗處理

| Failure | 行為 | active pointer |
|---|---|---|
| preparation failure | 記錄 reason，執行 legacy | 保留舊 READY |
| dimension calculation failure | 不發布新 cache | 保留舊 READY |
| serializer timeout | 取消 fast jobs，legacy rebuild | 只接受 legacy READY |
| semantic mismatch | fast 結果 quarantine | 保留舊 READY |
| baseline mismatch | cache 不可 READY | 保留舊 READY |
| manifest write failure | 不更新 pointer | 保留舊 READY |
| pointer swap failure | 新 generation orphaned | 保留舊 READY |
| DB core signature changed during build | 丟棄 build，重新以新 signature 開始 | 保留舊 READY |
| refund identity conflict | blocking，停止 active version 建立 | 不寫新 active |

所有 fallback 都必須在 manifest、stability history 與 UI status 顯示可診斷 reason code，不可只顯示 generic error。

## 12. Benchmark Design

### 12.1 Benchmark modes

| Mode | 說明 |
|---|---|
| legacy-cold | 既有完整 cold rebuild |
| fast-cold | shared intermediate + fast serializer |
| trusted-warm | 已存在 trusted reference 的 rebuild |
| new-refund-version | 新 refund state fingerprint |
| status-change | 退款中 → 已退款 |
| amount-change | 同退款單號金額更新 |
| cache-missing | manifest/artifact 不存在 |
| cache-corrupt | checksum 或 manifest 不一致 |
| revenue-change | core revenue signature 變更 |
| non-core-db-change | 只改非核心 metadata，確認不誤 invalidation |

### 12.2 Metrics

每次 benchmark 必須記錄：

- total wall-clock
- preflight time
- SQLite upsert/reload time
- preparation time
- dimension adjustment time
- report facts time
- serializer time by artifact
- equivalence time
- baseline gate time
- manifest/pointer write time
- cache hit/miss
- worker count
- peak RSS
- artifact count and bytes
- fallback reason
- equivalence status

輸出 JSON 僅寫入 isolated benchmark directory，不得寫入正式 runtime cache 或正式 SQLite。

### 12.3 Comparison policy

至少每個 mode 執行 3 次，報告：

- median
- p95
- min/max
- peak memory
- equivalence pass rate

只有在數值一致且 latency/memory guard 達標時，fast path 才可進入 rollout。

## 13. Testing Matrix

### 13.1 Unit tests

- 三個 pandas warning site 的 dtype-safe 行為。
- canonical frame fingerprint 的 row/column order invariance。
- preparation key invalidation。
- total/paid dimension isolation。
- refund upsert 與 status change。
- TT 退款轉團款 scope exclusion。
- excess refund cap。
- manifest schema、checksum、pointer swap。
- legacy v1 read compatibility。
- fallback reason code。

### 13.2 Integration tests

- new refund batch → preflight → SQLite upsert → active version → cache READY。
- status change only → no duplicate refund row。
- core revenue change → cache invalidated。
- non-core table change → core signature remains matched。
- serializer failure → previous READY remains downloadable。
- failed pointer swap → no partial active pointer。
- refresh Streamlit → no re-upload needed。
- total／paid report download bytes can be read and semantically compared。

### 13.3 Performance tests

- legacy-cold vs fast-cold。
- one worker vs two workers vs three workers。
- shared aggregation on/off。
- cold vs trusted warm。
- full rebuild vs incremental affected-order rebuild。
- memory guard under production-sized fixture。
- repeated download is read-only and does not rebuild。

### 13.4 Full acceptance

每個 rollout checkpoint 必須提供：

- targeted pytest result
- full pytest result
- warning count
- Hermes result
- formal baseline result
- cache manifest and pointer evidence
- actual Streamlit UI acceptance
- unchanged formal scope evidence

## 14. Observability

每次 upload/cache build 都要能回答：

- 哪個 generation 被建立？
- 使用 legacy、fast 還是 fallback？
- 哪一個 dimension 最慢？
- aggregation 是否重複執行？
- serializer 是否並行？
- equivalence 是否通過？
- active pointer 是否成功切換？
- 是否保留上一個 READY generation？
- 退款明細金額與實際扣減金額差異為何？
- 有多少筆因 TT 退款轉團款被正式口徑排除？

UI 只需顯示 bounded summary：

- status
- generation/version
- build duration
- cache hit/miss
- validation mode
- shadow/equivalence status
- fallback reason
- total／已退款 artifact availability

詳細 rows 與 raw evidence 留在 immutable batch/history/cache manifest，不塞入首頁。

## 15. Rollout Strategy

### Phase 0：Evidence baseline

- 只增加 profiling 與 benchmark，不改結果。
- 建立 legacy-cold baseline。
- 保存目前 90 warning inventory。
- 驗證正式 baseline 與服務 identity。

### Phase 1：Pandas cleanup

- 以 TDD 修正 3 個 warning site。
- 逐段比較 dtype、row count、numeric totals 與 export schema。
- full pytest warning count 必須為 0。
- 不與 cold rebuild fast path 同批啟用。

### Phase 2：Shared intermediate shadow

- fast preparation 只在 isolated cache 產生。
- legacy path 仍是正式輸出。
- 執行 semantic equivalence 與 benchmark。
- mismatch 一律保留 evidence，不切 active pointer。

### Phase 3：Bounded serializer shadow

- serializer worker count 從 1 開始，再 benchmark 2/3。
- fast artifact 與 legacy artifact 同時建立。
- 只有 equivalence PASS 才記錄 performance candidate。
- 任何 failure 自動使用 legacy。

### Phase 4：Fast path limited rollout

- 先對測試資料與指定正式 batch 啟用。
- active pointer 只接受 READY fast generation。
- 監控 p95、peak RSS、fallback rate、equivalence rate。
- 若連續出現 mismatch、memory guard 或 timeout，退回 legacy default。

### Phase 5：Incremental rebuild

- 先支援 status change 與 amount change。
- 以 immutable batch + audit evidence 驗證。
- full rebuild 保留作為 reconciliation／repair path。
- 未通過完整 acceptance 前，不移除 full rebuild。

### Rollback

rollback 只切回上一個 READY cache／legacy builder，不回寫或刪除正式 SQLite。若業務資料本身需要 rollback，沿用既有 governed upload rollback contract，不由本優化 spec 新增第二套流程。

## 16. Acceptance Criteria

本 spec 完成後，必須全部符合：

- [ ] full pytest 從 90 warnings 降至 0，且測試仍全部通過。
- [ ] warning cleanup 前後，正式報表數值、schema 與 dtype contract 一致。
- [ ] fast cold rebuild 與 legacy cold rebuild semantic equivalence PASS。
- [ ] total／paid 不重複執行 shared aggregation。
- [ ] bounded serializer 的 worker count 受控，peak RSS 在 guard 內。
- [ ] 新退款狀態可 update/upsert，不產生 duplicate。
- [ ] TT 退款轉團款不會被納入正式 GMV 扣減。
- [ ] cold failure 不會破壞上一個 READY active cache。
- [ ] active pointer 只指向完整且 checksum verified 的 READY generation。
- [ ] refresh/restart 後可直接讀取可下載報表。
- [ ] 2026-05 baseline 仍為 HKD 12,057,968。
- [ ] formal revenue scope 仍為不含掛賬核銷與 TT 退款轉團款。
- [ ] Hermes overall status 為 `pass`。
- [ ] 不修改正式 SQLite schema、baseline registry 或外部服務。

## 17. 後續 implementation 拆分

本 spec 不直接等同一個 implementation task。建議拆成以下獨立 plans，每個 plan 都要有自己的 Review、full pytest 與 Hermes gate：

1. Pandas dtype-safe cleanup。
2. Cold rebuild profiling 與 benchmark harness。
3. Shared intermediate model。
4. Bounded serializer 與 artifact equivalence。
5. Incremental refund rebuild。
6. Background-style rebuild state machine 與 active pointer integration。
7. Cache retention、observability 與 rollout controls。

在開始 implementation plan 前，應先確認 Phase 0 的實測 baseline，避免用假設的 bottleneck 排定 worker 或 incremental 設計。
