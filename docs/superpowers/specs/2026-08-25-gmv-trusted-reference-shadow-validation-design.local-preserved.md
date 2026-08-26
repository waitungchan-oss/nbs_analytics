# GMV Trusted Reference / Shadow Validation Design

## 1. 目標與非目標

### 目標

在不改變正式營收口徑、退款計算、11 份 export artifacts、active version 或 SQLite contract 的前提下，移除每次 cache rebuild 都完整重建 legacy reference 的成本。

本設計把已驗證結果拆成兩個角色：

1. `trusted reference`：針對一組明確 input fingerprint 保存的、不可變的 semantic reference manifest。
2. `shadow validation`：fast candidate 在 private staging 中與 trusted reference 比對；只有所有 gate 通過後，才 atomic swap active cache pointer。

### 非目標

- 不新增 SQLite table、migration 或外部服務。
- 不改變正式口徑「不含掛賬核銷與TT退款轉團款」。
- 不調整退款金額、超額退款上限、TT 退款轉團款排除或旅行團／票務數量口徑。
- 不讓 trusted reference 成為 dashboard、forecast、WAPE 或 SQLite 的 canonical source。
- 不把 Memory Hub、Governance Graph 或 Agent Operations 放進 runtime decision path。

## 2. 現況與問題

目前 fast controller 的安全流程是：

```text
legacy full build → fast candidate → semantic equivalence → cache publish
```

這能保護結果一致性，但每次新建或重建 cache 都重跑 legacy aggregation。現有 active cache 的 `active.json` 已經提供 generation-level pointer swap；本設計只在其上增加獨立的 trusted-reference layer，不改變 active reader contract。

## 3. 核心決策

### 3.1 Reference identity 不包含 version_id

`version_id` 會因每次 active version 建立而變動，但相同的營收、退款與規則內容不應因此失去 reference reuse。Reference 的 identity 使用 `content_fingerprint`，不直接使用 cache key：

```json
{
  "schemaVersion": "gmv-trusted-reference-v1",
  "revenueGenerationToken": "gmv-revenue-state-v1:...",
  "refundStateSha256": "...",
  "ruleVersion": "不含掛賬核銷與TT退款轉團款",
  "exportSchemaVersion": "gmv-formal-export-v2",
  "pipelineFingerprint": "pipeline-gmv-fast-v1",
  "serializerVersion": "gmv-export-serializer-v1",
  "artifactContractVersion": "gmv-formal-export-artifacts-v1"
}
```

Canonical JSON 的 SHA-256 即 `content_fingerprint`。它不包含 `version_id`、actor、timestamp、temporary path 或 build duration。

### 3.2 Reference 儲存 semantic facts，不重複保存大型 workbook

Trusted reference 是 compact manifest，不是第二份可下載報表。每個 canonical artifact 保存：

- artifact key 與 kind
- schema fingerprint
- semantic fingerprint
- row/sheet bounded counts
- source generation token 與建立 provenance

Reference 會 pin 其 seed cache generation 的 provenance；若 generation 被 retention 淘汰，reference 轉為 `INVALID_RETENTION`，不可繼續作為 trusted oracle，下一次重建必須重新 seed。

### 3.3 新 fingerprint 的第一次 build 必須 seed

如果找不到 exact trusted reference：

1. fast candidate 先在 private staging 產生。
2. legacy builder 只執行一次作為 seed oracle。
3. 兩者通過 semantic equivalence、schema、checksum、baseline gate 後，建立 trusted reference，再 publish fast candidate。

因此 trusted reference 不會把上一批退款資料誤用於新批次。真正的性能改善保證在 warm rebuild、retry、process restart 或同一 fingerprint 的 cache regeneration；cold seed 的成本會單獨 benchmark，不宣稱它是 fast path。

### 3.4 Shadow validation 必須 fail closed

所有 candidate 在 active pointer swap 前都必須通過：

```text
input identity
→ exact 11-artifact contract
→ artifact schema fingerprints
→ semantic fingerprints
→ frozen baseline gate
→ source/checksum gate
→ bounded memory/time gate
→ atomic generation publication
```

任何 gate 失敗：

- candidate generation 不可被 active reader 看見。
- 舊 active pointer 保持不變。
- 若 legacy fallback 成功，legacy generation 才可成為 active。
- 若 legacy fallback 也失敗，回傳 `CACHE_NOT_READY` / `BUILD_BLOCKED`，不能回傳一個看似 ready 的 manifest。

## 4. Data contract

### 4.1 Trusted reference manifest

路徑約定：

```text
<cache_root>/<reference_namespace>/<content_fingerprint>/manifest.json
```

Manifest exact top-level contract：

```json
{
  "schemaVersion": "gmv-trusted-reference-v1",
  "referenceId": "gmv-trusted-reference-v1:<sha256>",
  "contentFingerprint": "<sha256>",
  "status": "TRUSTED",
  "createdAt": "<timestamp>",
  "seedMode": "LEGACY_SEED",
  "source": {
    "revenueGenerationToken": "...",
    "refundStateSha256": "...",
    "ruleVersion": "...",
    "exportSchemaVersion": "gmv-formal-export-v2",
    "pipelineFingerprint": "pipeline-gmv-fast-v1",
    "serializerVersion": "gmv-export-serializer-v1"
  },
  "artifactContract": {
    "version": "gmv-formal-export-artifacts-v1",
    "keys": ["<exact sorted 11 keys>"]
  },
  "artifacts": {
    "<artifact-key>": {
      "kind": "xlsx|csv|json",
      "schemaFingerprint": "<sha256>",
      "semanticFingerprint": "<sha256>",
      "rowCount": 0,
      "sheetCount": 0
    }
  },
  "seedProvenance": {
    "cacheKey": "...",
    "generationPath": "...",
    "manifestSha256": "..."
  }
}
```

Required validation：exact keys、bounded strings、sorted artifact keys、合法 fingerprint 長度、`status == TRUSTED`、source identity 完全匹配、canonical 11 artifact set 完全匹配。

### 4.2 Export cache manifest additions

現有 `GmvExportCacheManifest` 保留所有欄位，新增：

- `content_fingerprint`
- `reference_id`
- `validation_mode`: `TRUSTED_REFERENCE | LEGACY_SEED | LEGACY_FALLBACK`
- `shadow_status`: `PASS | MISS | INVALID | MISMATCH | NOT_RUN`
- `reference_manifest_sha256`

舊 v1/v2 cache 缺少這些欄位時採 defaults，不得升格為 trusted；只能被當作 legacy cache 讀取。

### 4.3 Active pointer contract

現有 `active.json` 的 pointer swap 保持不變。新增欄位只可附加 metadata：

- `contentFingerprint`
- `referenceId`
- `shadowStatus`

Reader 仍以 `generationPath`、`manifestPath`、`manifestSha256` 做完整 integrity validation。Reference pointer 與 active pointer 分離，reference 失效不可直接改變 active version。

## 5. Runtime data flow

```text
退款 merge 完成
   ↓
建立 content_fingerprint
   ↓
lookup trusted reference
   ├─ HIT + VALID
   │    ↓
   │  fast facts/serializer candidate
   │    ↓ shadow validation
   │  PASS → publish new generation → atomic active pointer swap
   │  FAIL → legacy fallback → publish only if legacy ready
   │
   └─ MISS / INVALID
        ↓
      fast candidate private staging
        ↓
      one-time legacy seed oracle
        ↓
      equivalence PASS → persist trusted reference + publish fast candidate
      equivalence FAIL → publish legacy only if ready; mark reference rejected
```

UI 只讀 active cache；不直接讀 reference manifest 來顯示營收，也不在 download path 重建 workbook。

## 6. Fallback、錯誤與併發

| 情境 | 行為 | active pointer |
|---|---|---|
| Reference hit、candidate PASS | publish fast generation | swap |
| Reference miss | one-time legacy seed | 只在 seed PASS 後 swap |
| Reference checksum/schema invalid | quarantine reference metadata，重新 seed | 保留舊 pointer |
| Candidate semantic mismatch | mark `MISMATCH`，執行 legacy fallback | legacy ready 才 swap |
| Serializer timeout/memory gate | candidate staging cleanup | 保留舊 pointer |
| Pointer swap 失敗 | generation 保留為 orphan，記錄 error | 保留舊 pointer |
| Concurrent same fingerprint seed | unique staging；first valid reference wins，後續 re-read | 不覆寫既有 trusted reference |
| Retention 刪除 seed generation | reference 變 invalid | 不影響現有 active pointer |

不允許在同一 cache key 下寫入 failed manifest 覆蓋已 ready generation；每次 build 必須使用 unique generation path，active pointer 是唯一 publication boundary。

## 7. Observability 與 UI

Cache manifest、benchmark JSON 與 Streamlit status 顯示：

- `validation_mode`
- `shadow_status`
- `reference_id`
- `content_fingerprint` 前 12 碼
- `reference lookup / candidate / validation / publish` elapsed milliseconds
- fallback reason code

使用者可看到「trusted reference 命中，shadow validation PASS」或「首次 seed／fallback」，但不顯示內部 stack trace 或敏感路徑。

## 8. 測試矩陣

### Unit

- content fingerprint deterministic；version_id/timestamp 不影響 fingerprint。
- refund state、revenue token、rule、schema 任一變更都產生不同 fingerprint。
- trusted manifest exact schema、sorted keys、checksum、path confinement。
- reference hit、miss、invalid、retention invalid。
- semantic PASS、schema mismatch、row/sheet mismatch、artifact missing。

### Integration

- warm trusted hit 產生 11 artifacts 並 atomic swap。
- cold miss 只 seed 一次，reference 與 active manifest provenance 一致。
- mismatch fallback 不覆蓋舊 active pointer。
- serializer timeout、memory gate、pointer swap failure 後舊 cache 可讀。
- same content fingerprint 跨不同 version_id 可 reuse；不同退款 state 不可 reuse。
- SQLite、baseline、active version lifecycle 完全不變。

### Performance

- cold seed 與現行 legacy baseline 分開量測。
- warm trusted path 必須相對 334.6s baseline 改善至少 40%。
- validation memory bounded；不因讀取 reference manifest 產生第二份完整 workbook。
- benchmark output 必須取自實際 manifest，不得 hard-code PASS。

### Acceptance

- targeted GMV/export suite。
- full pytest。
- Hermes：SQLite integrity、formal scope、baseline、cache readiness、pointer/reference provenance。
- Streamlit：首次 seed、warm rebuild、reload、download、mismatch fallback。

## 9. Rollout strategy

1. `off`：保留目前 legacy/fast equivalence 行為，只收集 content fingerprint，不使用 reference。
2. `shadow`：建立與驗證 reference，但 active 仍使用 legacy；只在 benchmark/acceptance 打開。
3. `trusted_warm`：reference hit 使用 fast candidate；miss 走一次 legacy seed；所有 gate fail closed。
4. `default`：只有 warm benchmark ≥40%、full pytest、Hermes、UI acceptance 全部 PASS 才啟用。

Rollback 只需把 mode 改回 `off`，不需 migration，不刪除 active generation，不改 SQLite。

## 10. Acceptance criteria

- trusted reference 與 active cache schema exact、可驗證、可淘汰。
- 新退款 fingerprint 不會誤用舊 reference。
- warm path 不重新執行完整 legacy aggregation。
- candidate mismatch 不會污染 active pointer。
- legacy fallback 仍可建立完整 11 artifacts。
- formal baseline `2026-05 = HKD 12,057,968` 維持 matched。
- 所有結果一致時才可啟用 default trusted warm mode。
