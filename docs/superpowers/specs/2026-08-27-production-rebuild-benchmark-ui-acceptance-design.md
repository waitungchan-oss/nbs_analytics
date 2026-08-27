# Production Rebuild Benchmark 與 Streamlit/UI Acceptance 設計 Spec

## 1. 文件狀態

- 日期：2026-08-27
- 狀態：Draft for implementation
- 適用專案：NBS Analytics
- 適用範圍：affected-receipt production rebuild、formal export cache、Streamlit GMV 排除訂單看板
- 正式收入範圍：不含掛賬核銷與 `TT 退款轉團款`
- Frozen baseline：2026-05，`HKD 12,057,968`

## 2. 背景與問題定義

現有 `Task 11` benchmark 只測 planner/orchestration contract，能證明 affected set 可建立、unaffected aggregation call 為 0，但尚不能回答以下 production 問題：

1. 真正的 rebuild flow 在資料庫、repository、reconciliation、metrics、cache manifest 與 active pointer 邊界上的總耗時是多少。
2. affected 低比例時是否真的比 full/cold rebuild 快，而不是只在 planner 層快。
3. incremental 與 trusted full rebuild 的兩個退款維度、metrics、報表 artifact 是否完全等價。
4. production-like merge 後，Streamlit 是否能直接讀最後 READY active cache、顯示 bounded status 並下載兩套報表。

本 spec 只補「可證明 production 行為」的 benchmark 與 UI acceptance，不改變正式營收結果、不改 baseline、不新增外部服務，也不把 benchmark 結果直接變成 publish authority。

## 3. 設計目標

### 必須達成

- Benchmark 可在 isolated fixture DB 執行完整 rebuild orchestration：plan、affected recompute、unaffected copy、metrics、equivalence、cache publish/read。
- Benchmark 同時執行 full/cold、incremental/shadow 與 warm-read 對照；至少以 0.1%、1%、10% affected ratio 和 over-guardrail case 驗證。
- 每種 case 至少 3 次 cold run 與 3 次 warm read，輸出 median、p95、peak RSS、stage timings、affected/copied/recomputed rows、fallback rate。
- `unaffectedAggregationCalls` 必須為 0；若 instrumentation 無法證明，benchmark status 必須是 `INCONCLUSIVE`，不可宣稱通過。
- incremental 與 trusted full reference 必須以 canonical semantic fingerprint 比較，不以 XLSX binary bytes 作為唯一標準；equivalence 未達 100% 不可宣稱成功。
- Streamlit/UI acceptance 使用明確隔離的 acceptance DB/cache fixture，驗證上傳、合併、active version、兩個 dimension、下載與 restart/read-existing-ready-cache。
- UI acceptance 只驗證既有 read/write contract，不增加 UI write authority；production DB acceptance 不由 benchmark 自動執行。
- 所有 failure path 都保留上一個 READY pointer，並輸出 bounded fallback reason。

### 不變更

- 正式 scope、日期口徑、數量口徑、退款金額與 `TT 退款轉團款` 排除規則。
- `總退款` 與 `已退款` 的獨立計算。
- SQLite canonical source、既有 cache manifest schema 與 active pointer contract。
- 2026-05 frozen baseline `HKD 12,057,968`。

## 4. 非目標與安全邊界

- Benchmark 不讀取或寫入 production SQLite，不使用正式 runtime cache，不上傳 raw customer data 到 agent 或 Memory Hub。
- 不以 benchmark 自動啟用 `opt_in/default` incremental publish。
- 不新增 queue、worker、外部 database、migration 或常駐服務。
- 不刪除 immutable versions、artifact 或 trusted reference。
- Memory Hub / local agents 只提供 read-only bounded context；不得改變 fixture、結果、active pointer、Git 或 approval state。
- UI acceptance 不等同正式業務驗收；正式 runtime acceptance 必須另有明確 target、backup/rollback 與人工確認。

## 5. Target architecture

```text
isolated fixture generator
        |
        v
fixture SQLite + fixture cache root
        |
        +--> full/cold reference runner ----------------+
        |                                                |
        +--> incremental/shadow runner                   +--> canonical semantic equivalence
        |                                                |        |
        +--> warm active-cache read runner --------------+        v
                                                         benchmark evidence manifest
                                                                  |
                                                                  +--> Streamlit/UI acceptance
                                                                  |       - upload
                                                                  |       - merge
                                                                  |       - active READY
                                                                  |       - two reports download
                                                                  |       - restart read
                                                                  |
                                                                  +--> rollout recommendation only
```

### 5.1 Execution layers

1. **Fixture layer**：建立最小、可重現、無敏感資料的 SQLite fixture，包含正式 scope、TT exclusion、總退款、已退款、status update、amount update、multi-member、over-refund 與 unmatched case。
2. **Rebuild layer**：呼叫既有 service/repository/cache path，不複製另一套 business rule。每個 stage 使用 monotonic clock 記錄 elapsed ms。
3. **Reference layer**：同一份 immutable fixture 先跑 trusted full/cold，再跑 candidate incremental/shadow；兩者用 canonical semantic digest 比較。
4. **Read layer**：從 active pointer 讀兩個 dimension 的 cache artifacts；驗證不需重新上傳 Excel、不重新 aggregation、不依賴 retired version。
5. **Acceptance layer**：在獨立 Streamlit session / acceptance DB 上執行 UI contract probe，保存截斷後的 DOM/status/download evidence，不保存 raw rows。

## 6. Benchmark data contract

### 6.1 Case manifest

```json
{
  "schemaVersion": "gmv-production-rebuild-benchmark-v1",
  "caseId": "ratio-0.010",
  "fixtureId": "synthetic-gmv-v1",
  "affectedRatio": 0.01,
  "receiptCount": 10000,
  "affectedCount": 100,
  "runCount": 3,
  "warmReadCount": 3,
  "formalScope": "不含掛賬核銷與TT退款轉團款",
  "databaseMutated": false
}
```

### 6.2 Run evidence

```json
{
  "mode": "incremental-shadow",
  "decision": "INCREMENTAL_ELIGIBLE",
  "stageMs": {
    "plan": 0.0,
    "affectedRecompute": 0.0,
    "unaffectedCopy": 0.0,
    "metrics": 0.0,
    "equivalence": 0.0,
    "publish": 0.0,
    "warmRead": 0.0
  },
  "affectedRows": 0,
  "copiedRows": 0,
  "recomputedRows": 0,
  "unaffectedAggregationCalls": 0,
  "peakRssBytes": 0,
  "equivalenceStatus": "PASS",
  "fallbackReason": null,
  "activePointerUnchangedOnFailure": true
}
```

禁止將 example 的 `0.0` 當成實際結果；實際 evidence 必須由 runner 產生。每個 report 只允許 bounded counts、digests、timings、reason codes 與 fixture identity。

### 6.3 Summary contract

每個 case 必須輸出：

- full/cold、incremental/shadow、warm-read 的 samples。
- `medianMs`、nearest-rank `p95Ms`、`peakRssBytes`。
- `equivalenceRate`、`fallbackRate`、`unaffectedAggregationCalls`。
- stage timing 的 median/p95。
- `status`: `PASS`、`FAIL` 或 `INCONCLUSIVE`。
- `failureReasons`: bounded stable reason codes。

## 7. Benchmark matrix and gates

| Case | Affected ratio | Purpose | Expected decision |
|---|---:|---|---|
| `ratio-0.001` | 0.1% | low-change production target | incremental eligible |
| `ratio-0.010` | 1% | normal small batch | incremental eligible |
| `ratio-0.100` | 10% | upper useful range | incremental eligible or measured fallback |
| `over-guardrail` | > configured ratio/count | safety boundary | full rebuild required |
| `status-transition` | fixed | `退款中 -> 已退款` | affected and equivalent |
| `tt-method-transition` | fixed | enter/leave TT exclusion | affected and no double deduction |
| `over-refund` | fixed | cap and over-refund evidence | equivalent |
| `multi-member` | fixed | member allocation conservation | equivalent |

Required gates:

1. Full/cold and candidate both complete successfully, or candidate explicitly records fallback.
2. Candidate semantic equivalence is `PASS` for every published candidate; overall rate is `1.0` for a passing case.
3. `unaffectedAggregationCalls == 0`; missing instrumentation is `INCONCLUSIVE`.
4. Peak RSS is no more than `1.5 * full/cold peak RSS`.
5. Candidate latency improvement is reported only when median and p95 are both measured; no single-run claim.
6. Over-guardrail does not publish incremental and keeps previous READY pointer.

## 8. Streamlit/UI acceptance contract

### Setup

- Use a temporary acceptance DB copied from a synthetic fixture and a temporary cache root under an approved test directory.
- Start Streamlit through HTTP, never `file://`.
- Pin the app to the acceptance DB/cache paths through existing test/runtime configuration; do not change production defaults.

### Acceptance sequence

1. Open GMV 排除訂單看板 and verify current active READY state is readable without upload.
2. Upload a synthetic refund Excel containing both `總退款` and `已退款` relevant cases.
3. Click existing 「上傳並合併退款資料庫」 once.
4. Verify bounded progress/status shows merge, active version and cache completion; no blocking error.
5. Verify active version changes only after cache artifacts are READY.
6. Verify both `總退款` and `已退款` summary/detail artifacts are available for download.
7. Refresh/restart the Streamlit session without uploading again; verify the same active version and reports remain readable.
8. Inject an equivalence/checksum failure in isolated cache; verify previous READY pointer and reports remain available.

### UI evidence

Store only:

- URL/route, status text, active version id, manifest digest, artifact names and byte sizes.
- download response metadata and semantic digest.
- bounded error code, if any.

Do not store full Excel contents, customer names, payment details or raw refund rows.

## 9. Failure and rollback behavior

- Fixture or benchmark setup failure: `FAIL`, no production side effect.
- Candidate equivalence failure: `FAIL`; candidate pointer is not active; previous READY remains.
- Missing stage instrumentation: `INCONCLUSIVE`; no rollout recommendation.
- Cache checksum/manifest failure: `FAIL`; load path returns `CACHE_INVALID`, previous READY remains.
- UI timeout: `FAIL`; collect HTTP/status evidence, do not retry against production automatically.
- Runtime process failure: acceptance DB/cache is disposable; production flow is not changed.

## 10. Rollout strategy

1. **Local fixture gate**：all matrix cases pass, full pytest and compileall pass。
2. **Shadow evidence**：在 isolated production-like fixture 完成至少 3 cold + 3 warm samples per case，equivalence 100%，unaffected calls 0。
3. **Candidate UI gate**：Streamlit acceptance sequence pass；refresh/read-existing-ready-cache pass。
4. **Formal runtime observation**：只讀/受控執行既有 formal cache read contract，記錄 latency/RSS，不自動 publish。
5. **Opt-in decision**：只有 benchmark + UI + Review + Hermes evidence 全部存在，才可由後續明確授權啟用 incremental opt-in；default rollout 另開 change。

## 11. Acceptance criteria

- 有 production-like complete rebuild benchmark，而非只測 planner。
- 0.1%、1%、10%、over-guardrail matrix 有可重現 JSON evidence。
- full/cold、incremental/shadow、warm-read 均有至少 3 samples 的 median/p95。
- semantic equivalence 100% PASS，且兩個 refund dimensions、TT exclusion、status update、multi-member、over-refund 均涵蓋。
- `unaffectedAggregationCalls == 0`，或明確標記 `INCONCLUSIVE` 並不進 rollout。
- Streamlit upload/merge/active READY/download/refresh acceptance 全部通過。
- failure injection 證明上一 READY pointer 不變。
- full pytest、Hermes、Review、benchmark、UI evidence 分開保存與報告。
