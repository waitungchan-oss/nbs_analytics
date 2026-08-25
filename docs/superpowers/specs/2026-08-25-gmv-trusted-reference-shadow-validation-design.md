# GMV Trusted Reference / Shadow Validation Design

## 1. 目標與邊界

本設計在不改變正式營收口徑、退款計算、11 份 export artifacts、active version 或 SQLite contract 的前提下，移除每次 cache rebuild 都完整重建 legacy reference 的成本。

新增兩個 derived cache layer：

- `trusted reference`：針對一組明確 input fingerprint 保存不可變的 semantic reference manifest。
- `shadow validation`：fast candidate 在 private staging 中與 trusted reference 比對；所有 gate 通過後才 atomic swap active cache pointer。

不新增 SQLite table、migration、外部服務或 approval/workflow control；Memory Hub、Governance Graph 與 Agent Operations 不進入 runtime decision path。正式口徑仍為「不含掛賬核銷與TT退款轉團款」，2026-05 frozen baseline 必須維持 `HKD 12,057,968`。

## 2. 現況問題

目前安全流程為：

```text
legacy full build → fast candidate → semantic equivalence → cache publish
```

這能保護結果一致性，但每次新建或重建 cache 都重跑 legacy aggregation。現有 `active.json` 已提供 generation-level pointer swap；本設計在其上增加獨立 trusted-reference layer，不改變 active reader contract。

## 3. 核心決策

### 3.1 Reference identity 不包含 version_id

`version_id` 每次 active version 都可能不同，但相同營收、退款與規則內容應能 reuse reference。`content_fingerprint` 由下列 canonical JSON 的 SHA-256 產生：

```json
{
  "revenueGenerationToken": "gmv-revenue-state-v1:...",
  "refundStateSha256": "...",
  "ruleVersion": "不含掛賬核銷與TT退款轉團款",
  "exportSchemaVersion": "gmv-formal-export-v2",
  "pipelineFingerprint": "pipeline-gmv-fast-v1",
  "serializerVersion": "gmv-export-serializer-v1",
  "artifactContractVersion": "gmv-formal-export-artifacts-v1"
}
```

不包含 `version_id`、actor、timestamp、temporary path 或 build duration。退款狀態、退款金額、revenue token、規則或 export schema 任一改變，都必須產生新 fingerprint。

`content_fingerprint` 是 upstream source/contract identity，不是下載檔案內容的 digest。這是刻意的兩層設計：warm lookup 必須能在 candidate serialization 前完成；11 份 artifact 的輸出語義則由 reference manifest 內的逐 artifact `schemaFingerprint`、`semanticFingerprint`、row/sheet counts 保存，並在 active publication 前以 exact artifact-set comparison 與 shadow gate 驗證。輸出語義不同時即使 source identity 相同，也不得 publish fast candidate。

### 3.2 Reference 只保存 compact semantic manifest

Trusted reference 不是第二份可下載報表。每個 canonical artifact 只保存：artifact key/kind、schema fingerprint、semantic fingerprint、bounded row/sheet counts 與 seed provenance。Reference pin 其 seed generation；若 generation 被 retention 淘汰，reference 變成 invalid，不可繼續作為 trusted oracle。

### 3.3 新 fingerprint 第一次必須 seed

找不到 exact reference 時，fast candidate 先在 private staging 產生，legacy builder 執行一次作為 seed oracle。兩者通過 equivalence、schema、checksum 與 baseline gate 後，才保存 trusted reference 並 publish fast candidate。

因此 warm rebuild、retry、process restart 可真正省去 legacy aggregation；cold seed 的成本要單獨 benchmark，不能宣稱為 warm fast path。舊退款批次的 reference 不得套用到新退款批次。

### 3.4 Shadow validation fail closed

Candidate 在 active pointer swap 前必須通過：

```text
input identity → exact 11-artifact contract → schema fingerprints
→ semantic fingerprints → frozen baseline → source/checksum
→ bounded memory/time → atomic generation publication
```

任何 gate 失敗，candidate 不可被 active reader 看見；舊 pointer 保持不變。只有 legacy fallback 自身 ready 且可從磁碟重新讀取時，才可 publish legacy generation。

## 4. Data contract

### 4.1 Trusted reference manifest

路徑：

```text
<cache_root>/<reference_namespace>/<content_fingerprint>/manifest.json
```

Exact top-level contract：

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

Loader 必須驗證 exact keys、bounded strings、sorted artifact keys、合法 fingerprint 長度、`status == TRUSTED`、source identity、canonical 11 artifacts 與 seed provenance。任一不符即 fail closed。

### 4.2 Export cache manifest additions

`GmvExportCacheManifest` 保留既有欄位，新增：

- `content_fingerprint`
- `reference_id`
- `validation_mode`: `TRUSTED_REFERENCE | LEGACY_SEED | LEGACY_FALLBACK`
- `shadow_status`: `PASS | MISS | INVALID | MISMATCH | NOT_RUN`
- `reference_manifest_sha256`

舊 v1/v2 manifest 缺少欄位時採 defaults，但不得升格為 trusted。

### 4.3 Active pointer

現有 `active.json` 的 `generationPath`、`manifestPath`、`manifestSha256` integrity contract 不變；只附加 `contentFingerprint`、`referenceId`、`shadowStatus` metadata。Reference pointer 與 active pointer 分離，reference invalid 不直接改變 active version。

## 5. Runtime data flow

```text
退款 merge 完成 → content_fingerprint → trusted lookup
  ├─ HIT + VALID → fast candidate → shadow PASS → publish → atomic active swap
  └─ MISS/INVALID → private fast candidate + one-time legacy seed
       ├─ equivalence PASS → persist trusted reference + publish fast candidate
       └─ mismatch → publish legacy only if ready; otherwise preserve old pointer
```

UI 只讀 verified active cache，不直接以 reference manifest 顯示營收，也不在 download path 重建 workbook。

## 6. Fallback、錯誤與併發

| 情境 | 行為 | active pointer |
|---|---|---|
| Reference hit、candidate PASS | publish fast generation | swap |
| Reference miss | one-time legacy seed | seed PASS 才 swap |
| Reference checksum/schema invalid | mark invalid，重新 seed | 保留舊 pointer |
| Candidate semantic mismatch | mark mismatch，legacy fallback | legacy ready 才 swap |
| Serializer timeout/memory gate | cleanup candidate staging | 保留舊 pointer |
| Pointer swap failure | generation 留作 orphan，記錄 error | 保留舊 pointer |
| Concurrent same-fingerprint seed | unique staging；first valid wins | 不覆寫 trusted reference |
| Retention 刪除 seed generation | reference invalid | 不影響 active pointer |

同一 cache key 不得以 failed manifest 覆蓋 ready generation；每次 build 使用 unique generation path，active pointer 是唯一 publication boundary。

## 7. Observability 與 UI

Manifest、benchmark JSON 與 Streamlit status 顯示 `validation_mode`、`shadow_status`、`reference_id`、content fingerprint 前 12 碼、lookup/candidate/validation/publish timings 與 fallback reason code。不得向使用者暴露 stack trace 或敏感路徑。

## 8. 測試矩陣

### Unit

- fingerprint deterministic；version_id/timestamp 不影響結果。
- refund state、revenue token、rule、schema 變更會產生不同 fingerprint。
- manifest exact schema、sorted keys、checksum、path confinement。
- reference hit、miss、invalid、retention invalid。
- semantic PASS、schema mismatch、row/sheet mismatch、artifact missing。

### Integration

- warm hit 產生 11 artifacts 並 atomic swap。
- cold miss 只 seed 一次，reference 與 active provenance 一致。
- mismatch fallback 不覆蓋舊 pointer。
- timeout、memory gate、pointer failure 後舊 cache 可讀。
- 相同 content fingerprint 跨 version_id 可 reuse；不同退款 state 不可 reuse。
- SQLite、baseline、active-version lifecycle 不變。

### Performance and acceptance

- cold seed 與 legacy baseline 分開量測。
- warm path 相對 334.6s baseline 改善至少 40%。
- validation memory bounded，不產生第二份完整 workbook set。
- benchmark status 必須取自實際 manifest，不得 hard-code PASS。
- targeted GMV/export、full pytest、Hermes 與 Streamlit cold/warm/reload/download/fallback acceptance 全部記錄。

## 9. Rollout

1. `off`：只收集 fingerprint，不使用 reference。
2. `shadow`：建立/驗證 reference，但 active 仍使用 legacy；用於 benchmark/acceptance。
3. `trusted_warm`：reference hit 使用 fast；miss 一次 legacy seed；所有 gate fail closed。
4. `default`：只有 warm benchmark ≥40%、full pytest、Hermes、UI acceptance 全 PASS 才啟用。

Rollback 只需把 mode 改回 `off`，不需 migration、不刪 active generation、不改 SQLite。

## 10. Acceptance criteria

- trusted reference 與 active cache schema exact、可驗證、可淘汰。
- 新退款 fingerprint 不會誤用舊 reference。
- warm path 不重新執行完整 legacy aggregation。
- candidate mismatch 不污染 active pointer。
- legacy fallback 仍建立完整 11 artifacts。
- 2026-05 baseline `HKD 12,057,968` 維持 matched。
- 所有結果一致時才可啟用 default trusted warm mode。
