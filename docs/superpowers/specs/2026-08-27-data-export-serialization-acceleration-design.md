# Data Export Shared Intermediate and Bounded Serialization Design

## 1. 文件狀態

- 日期：2026-08-27
- 狀態：Approved design
- 專案：NBS Analytics
- 範圍：經營分析大盤 Data Exports：報表與日誌匯出
- 目標：讓 affected-only 與 shared intermediate 的效益真正反映到完整 Excel 報表等待時間

## 2. 問題定義

目前匯出流程雖然已有 lazy export、shared intermediate、equivalence、manifest 與 fast controller 的基礎，但一般完整報表仍可能在同一個流程中重複建立三份 workbook。現有 intermediate 主要提供 normalized/classified frames 與有限的 amount aggregate；它尚未成為三份 workbook 共用的完整 report facts。現有 parallel controller 也需要進一步把一般 Data Export 的 serializer 與 manifest telemetry 接好，才能量出真正的 serialization 改善。

本設計只優化匯出計算與序列化，不改變任何業務數值、SQLite 資料、Dashboard KPI、AI Forecast、WAPE、baseline 或正式收入規則。

## 3. 設計目標

1. 對同一個 generation、rules fingerprint 與 export schema，只建立一次 shared intermediate。
2. 由同一個 intermediate 產生 `all`、`no_writeoff`、`official` 三種 scope 的完整 report facts。
3. 將 report facts 與 Excel serialization 分離；serializer 不再重新執行 normalization、classification 或 aggregation。
4. 以 bounded worker pool 平行建立三份 workbook，並保留 sequential fallback。
5. 以 semantic equivalence、schema、checksum、baseline 與 generation gate 保證結果一致。
6. 以 versioned manifest 保存每階段 timing、artifact size、worker count、fallback reason 與 equivalence evidence。
7. READY cache 命中與下載只讀 artifact，不重新 aggregation 或 serialization。

## 4. 非目標與硬邊界

- 正式收入範圍固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite schema、原始業務資料、upload、rollback、baseline 或 revenue scope。
- 不修改既有 workbook sheet、欄位、檔名、排序與業務數值 contract。
- 不把 intermediate 寫入 SQLite；只可存在於單次 job 記憶體或 ignored temporary/cache path。
- 不新增外部服務、雲端 storage、資料庫或 agent workflow control。
- Memory Hub 與 local agents 只提供 read-only context/hints，不得成為 export 結果或 rollout gate 的 authority。

## 5. 現況盤點

### 5.1 已完成並應保留

| 模組 | 現況 | 本階段處理 |
|---|---|---|
| `backend/services/export_intermediate_service.py` | 已有 `ExportIntermediateModel`、三個 `ExportScope` 與 source fingerprint | 擴充 facts，不重做 scope contract |
| `backend/services/export_equivalence_service.py` | 已有 XLSX canonicalization 與 bounded mismatch report | 作為唯一 semantic gate |
| `backend/services/export_manifest_service.py` | 已有 versioned manifest、atomic write、checksum、ZIP 基礎 | 補齊 telemetry、package verification 與 active lookup contract |
| `backend/services/export_fast_path_service.py` | 已有 bounded `ProcessPoolExecutor`、equivalence gate 與 fallback | 接上 reusable facts/serializer，保留 legacy reference |
| `backend/services/gmv_export_serializer_service.py` | 已有 bounded serializer、timeout、atomic artifact write 與 parallel contract | 抽出可供一般 Data Export 使用的共用 serializer contract |
| `app_workflows.py` | 已有 legacy builder、fast candidate、manifest lookup 與 lazy export | 改為優先使用 verified READY artifact |
| `app_pages.py` | 已有 lazy export 與下載入口 | 補齊狀態、ZIP 與 fallback 顯示 |

### 5.2 下一階段真正需要實作

1. 將 shared intermediate 擴充為完整、可重用的 report facts preparation。
2. 把三種 scope 的 facts 接到單一 bounded serializer job contract。
3. 將三個 workbook 的 serialization 變成 bounded parallel artifacts，避免 serializer 內重跑 business aggregation。
4. 為 manifest 增加 per-stage telemetry、artifact fingerprints 與 package verification。
5. 讓 Data Export UI 的 download handler 只讀 READY artifacts，並清楚呈現 PREPARING、VERIFYING、READY、FALLBACK、FAILED。
6. 建立固定資料 snapshot 的 production-shaped benchmark，分別量測 aggregation、serialization、equivalence、package 與 cache hit。

## 6. 目標架構

```text
SQLite read models / existing cache
        |
        v
generation + rules + schema fingerprint
        |
        v
ExportIntermediateModel
  normalized + classified frames
  shared amount/quantity/groupby facts
  stable row identities
        |
        +----------------+----------------+----------------+
        v                v                v
   all facts      no_writeoff facts   official facts
        |                |                |
        +----------------+----------------+
                         v
             bounded serializer jobs
                 (one artifact/job)
                         |
                         v
          XLSX artifacts + ZIP package
                         |
                         v
      schema/checksum/equivalence/baseline gates
                         |
              atomic READY manifest swap
                         |
               UI download reads only
```

### 6.1 Intermediate contract

```python
@dataclass(frozen=True, slots=True)
class ExportIntermediateModel:
    generation_token: str
    rules_fingerprint: str
    schema_version: str
    normalized_tour: pd.DataFrame
    normalized_others: pd.DataFrame
    classified_tour: pd.DataFrame
    classified_others: pd.DataFrame
    shared_aggregates: Mapping[str, pd.DataFrame]
    source_fingerprints: Mapping[str, str]

def build_export_intermediate(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    generation_token: str,
    rules_fingerprint: str,
    schema_version: str,
) -> ExportIntermediateModel
```

`shared_aggregates` 必須至少涵蓋報表需要的 amount、quantity、branch、salesperson、business type、date 與 stable row identity facts。所有 scope input 都是 base model 的 deep copy 或 immutable view，不得改動 base frame。

### 6.2 Scope contract

| scopeId | 規則 |
|---|---|
| `all` | 不排除 |
| `no_writeoff` | 排除 `掛賬核銷` |
| `official` | 排除 `掛賬核銷` 與 `TT 退款轉團款` |

Scope descriptor 必須進入 report facts fingerprint，避免跨口徑誤用 artifact。

### 6.3 Serializer contract

```python
@dataclass(frozen=True, slots=True)
class ExportSerializerJob:
    artifact_id: str
    scope_id: str
    facts: DashboardReportFacts
    target_path: Path
    schema_fingerprint: str
    data_fingerprint: str

def serialize_export_jobs_parallel(
    jobs: Sequence[ExportSerializerJob],
    *,
    max_workers: int = 3,
    timeout_seconds: float | None = None,
) -> tuple[SerializerResult, ...]
```

Worker 只能把 facts 寫成自己的 temporary XLSX，回傳 bounded metadata；不能讀 SQLite、Streamlit session state 或修改 shared facts。controller 收集結果、驗證後才 publish。

## 7. Equivalence 與 correctness gate

新舊輸出不要求 XLSX binary bytes 相同，但必須比較 canonical workbook representation：

- sheet 名稱與順序
- header、欄位順序與型別
- row count、stable key 與排序
- 金額以 Decimal 兩位小數比較
- 交易人數、票務數量與各種分組 totals
- `掛賬核銷` 與 `TT 退款轉團款` 排除規則
- 退款總額、已退款與實際扣減金額
- bounded mismatch examples，最多 20 筆

只有 `schema=PASS`、`equivalence=PASS`、`checksum=PASS`、`baseline=PASS`、generation identity matched 時，manifest 才能成為 READY。

## 8. Manifest 與 package contract

manifest schema 固定為 `export-manifest-v2`，並新增：

```json
{
  "schema": "export-manifest-v2",
  "status": "PREPARING | VERIFYING | READY | FALLBACK | FAILED",
  "generation_token": "string",
  "rules_fingerprint": "sha256",
  "export_schema_version": "string",
  "pipeline_fingerprint": "sha256",
  "artifacts": {},
  "equivalence": {"status": "PASS | FAIL | NOT_RUN", "mismatch_count": 0},
  "telemetry": {
    "intermediate_ms": 0,
    "serialization_ms": {},
    "equivalence_ms": 0,
    "package_ms": 0,
    "total_ms": 0,
    "worker_count": 3,
    "peak_rss_bytes": null
  },
  "fallback": {"used": false, "reason_code": null}
}
```

ZIP package 必須包含三份既有檔名的 workbook、`export-manifest.json` 與 `equivalence-report.json`，不得包含 SQLite、原始 Excel、customer/payment raw data、secrets 或完整 runtime logs。

## 9. Fallback 與 rollback

以下任一條件都 fail closed：intermediate exception、serializer exception、timeout、worker crash、schema mismatch、row/metric mismatch、checksum mismatch、package verification failure、stale generation/rules/schema 或 baseline failure。

失敗時：

1. 不發布新的 READY manifest。
2. 保持上一個 READY active pointer 不變。
3. 使用 `_compute_export_workbooks()` legacy reference path。
4. UI 顯示 bounded reason code。
5. 記錄 timing 與 failure，不記錄完整業務明細。

Rollback 只需關閉 fast export mode 或移除不符合 gate 的 manifest，不需要 SQLite migration。

## 10. UI contract

Data Export Center 顯示：

- `PREPARING`：建立 intermediate
- `VERIFYING`：serialization/equivalence/checksum
- `READY`：一鍵 ZIP 與三份個別 XLSX
- `FALLBACK`：已使用相容 legacy path
- `FAILED`：沒有可下載 artifact，保留上一個 READY cache

下載按鈕只讀 verified artifact；重整頁面只做 manifest lookup，不重新 aggregation 或 serialization。

## 11. Test matrix

### Unit

- shared intermediate 只建立一次
- scope filters 與 formal revenue rules 一致
- facts fingerprint 穩定
- serializer worker count 有上限
- serializer timeout/cancellation
- source/shared frames immutable
- artifact path confinement

### Equivalence

- 三份 legacy/fast workbook 逐 sheet canonical comparison
- 空資料、單列、重複單號、跨月份、零金額、小數、缺失值
- 掛賬核銷、TT 退款轉團款、退款狀態與退款金額
- tie ranking、空 sheet、中文欄位與特殊字元
- binary bytes 不同但 semantic fingerprint 相同時 PASS

### Cache/failure

- 缺 artifact、checksum mismatch、package mismatch
- serializer exception、timeout、worker crash
- active pointer 在所有失敗情況保持不變
- stale generation/rules/schema 不命中
- cache hit 不呼叫 aggregation/serializer

### UI/benchmark

- 首屏不生成 workbook
- READY 顯示 ZIP 與三份 XLSX
- refresh 仍讀取同一 READY version
- download 不重新計算
- forced mismatch 顯示 fallback
- 固定 snapshot 下量測三個 affected ratio 與 full export

## 12. Performance acceptance

同一 SQLite snapshot、同一 rules fingerprint、同一機器下，分別保存：

- `intermediate_ms`
- `serialization_ms` per artifact
- `equivalence_ms`
- `package_ms`
- `total_ms`
- `peak_rss_bytes`
- READY lookup 與 cache hit latency

初始 acceptance target：

- READY lookup < 250ms
- cache hit preparation < 1s
- 三份 serialization 相對目前串行 legacy serialization 改善至少 40%
- semantic mismatch = 0
- fallback 結果與 legacy 完全一致

若 worker pool 使 peak RSS 超過安全上限，降低 worker count；不得放寬 correctness gate。

## 13. Rollout

1. **Instrumentation**：只記錄 legacy 分段 timing。
2. **Shadow**：建立 fast artifact 並做 equivalence，但使用者仍下載 legacy。
3. **Opt-in**：只有 gate 全 PASS 才提供 fast ZIP/XLSX。
4. **Default**：fast 成為預設，legacy 保留自動 fallback。
5. **Retention**：只清理過期 export artifact，不清理 SQLite、baseline、rollback 或正式業務資料。

## 14. Definition of Done

- 三個 scope 共用同一 intermediate，沒有重複 normalization/classification/aggregation。
- 三份 workbook 由 bounded serializer jobs 產生，並可平行化或安全 sequential fallback。
- manifest、ZIP、checksum、equivalence、baseline 與 generation identity 全部可驗證。
- READY active pointer 只在所有 gate 通過後更新。
- 任一失敗回退 legacy 且不破壞上一個 READY cache。
- full pytest、strict FutureWarning、benchmark、Streamlit HTTP acceptance 與 Hermes 均通過。
- SQLite、正式業務資料、Dashboard KPI、AI Forecast、WAPE、baseline 與 export schema 無 regression。
