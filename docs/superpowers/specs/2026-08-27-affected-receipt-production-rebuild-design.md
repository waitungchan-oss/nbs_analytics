# Affected Receipt Production Rebuild 設計 Spec

## 1. 文件狀態

- 日期：2026-08-27
- 狀態：Draft for user review
- 適用專案：NBS Analytics
- 適用範圍：GMV 排除訂單看板、退款 reconciliation、formal cache 與報表 read model
- 正式收入範圍：不含掛賬核銷與 `TT 退款轉團款`
- Frozen baseline：2026-05，`HKD 12,057,968`
- 本階段：只完成 spec；不直接執行 implementation、migration、正式 rebuild 或 active pointer 變更

## 2. 背景與問題

新的退款明細可能只影響少數來源單據號，但現有 production rebuild 仍會重新 aggregation 全部收入與退款資料。當資料量增長時，這會造成：

1. 上傳合併後等待時間與資料總量成正比，而不是與變更量成正比。
2. `總退款` 與 `已退款` 兩個 dimension 重複處理相同的 unaffected rows。
3. 使用者難以分辨是實際業務變更，還是 cold rebuild 的全量計算造成延遲。

本設計將 production rebuild 拆為「affected receipt 重算」與「unaffected row reuse」，但仍產生完整、獨立、可稽核的新 active version。新版本不得依賴已退休版本才能讀取。

## 3. 設計目標

### 必須達成

- 只對 affected source receipt 執行 reconciliation aggregation。
- unaffected rows 完全跳過 business aggregation；只允許以資料庫內的 set-based copy/reuse 方式納入新 snapshot。
- `總退款`、`已退款` 仍各自計算，不混用 adjusted monetary result。
- 同一退款單號的 status、amount 或退款方式變更必須觸發正確重算；不得只 append。
- 只有 incremental result 與 trusted full rebuild reference 完全等價時，才可 publish READY active version。
- 任一 guard、equivalence、baseline、conservation 或 cache gate 失敗時，保留上一個 READY active pointer；依規則 fallback full rebuild 或 blocking。
- refresh/restart 後仍可直接讀最後一個 READY version，不要求重新上傳退款 Excel。
- 不改變現有正式 scope、日期口徑、數量口徑、baseline 或既有報表 schema。

### 效能目標

以目前 full/cold rebuild 作 baseline，目標為：

- affected receipt 低比例時，rebuild wall-clock 主要隨 affected 數量增長。
- unaffected business aggregation 次數為 0。
- peak RSS 不超過 full rebuild 的 1.5 倍。
- 以 3 次以上 benchmark 計算 median 與 p95，不用單次最快值判定通過。

## 4. 非目標與硬邊界

- 不改變正式 revenue scope；`TT 退款轉團款` 在正式口徑仍排除，不得被退款扣減反向納入。
- 不把 refund detail amount 當成 applied deduction；`實際扣減金額` 仍受正式收款金額、scope 與 over-refund cap 約束。
- 不因退款而改寫旅行團或票務原始人數；數量仍依既有 transaction basis。
- 不新增外部 queue、database、常駐 worker 或服務。
- 不把 Memory Hub、Governance Graph 或 Agent Operations 變成 canonical data、write path 或 publish gate。
- 不以 XLSX binary byte identity 作為 equivalence 唯一標準；以 canonical semantic fingerprint 與 schema/value contract 為準。
- 不刪除或覆蓋上一個 READY version，也不修改既有正式業務資料以湊出性能或 baseline。

## 5. 核心定義

### 5.1 Affected receipt

`affected_source_receipt_nos` 是本次 refund state delta 需要重新計算的來源單據號集合，取下列集合的 union：

- `NEW`：新退款單的來源單據號。
- `STATUS_CHANGED`：退款狀態變更的來源單據號。
- `AMOUNT_CHANGED`：退款原幣金額變更的來源單據號。
- 退款方式變更，尤其進入或離開 `TT 退款轉團款` 的來源單據號。
- 退款單 identity conflict 涉及的舊、新來源單據號；這類情況預設 blocking，不直接進 incremental publish。

### 5.2 Unaffected row

同時符合以下條件的 row 才是 unaffected：

- 不在 affected receipt set。
- revenue source fingerprint、active rules fingerprint、pipeline generation token 均與上一 READY version 相同。
- 既有 reconciliation snapshot 完整且可驗證。
- 沒有 pending identity conflict、unknown refund state 或未處理的 source mapping change。

若任何條件不成立，整個 rebuild 不得假定該 row unaffected。

## 6. 目標架構與資料流

```text
current READY version + current refund state + rules/source fingerprints
                              |
                              v
                    classify refund state delta
                              |
                              v
                     Incremental Rebuild Plan
                     - affected receipts
                     - copy candidates
                     - gate/fallback decision
                              |
                 +------------+------------+
                 |                         |
                 v                         v
       recompute affected receipts   set-based copy unaffected rows
       TOTAL_REFUND + REFUNDED       into new version snapshot
                 |                         |
                 +------------+------------+
                              v
                 combine new immutable snapshot
                              |
                              v
             delta metrics or bounded aggregate over new rows
                              |
                              v
       trusted reference/shadow full rebuild semantic comparison
                 |                         |
                 v                         v
           READY + atomic swap       fallback/full rebuild/blocking
```

### 6.1 Version isolation

新 version 必須擁有自己的 reconciliation result、member、adjustment snapshot、metric snapshot 與 manifest identity。`previous_version_id` 只作 provenance，不得作 runtime read dependency。unaffected rows 可以從上一版本以 `INSERT ... SELECT` 或等價 set-based copy 方式搬入，但完成後讀取新版本不應再 join 退休版本。

### 6.2 計算分層

1. Preparation：只做一次必要的 source normalization、scope classification、日期與 stable key 準備。
2. Affected engine：只載入 affected receipts 對應的 revenue rows 與 refund states，計算兩個 refund dimensions。
3. Unaffected reuse：只做 snapshot row copy，不重新執行 business aggregation、mapping 或 refund allocation。
4. Metric layer：優先使用舊 metric 加上 affected delta；若 metric contract 無法安全做 delta，使用 bounded SQL aggregation，但不得回到 per-workbook full aggregation。
5. Validation：以 trusted full rebuild 或 canonical reference 做 semantic compare。

## 7. Data contract

### 7.1 Existing model reuse

沿用既有：

- `RefundStateDelta`
- `classify_refund_state_delta(...)`
- `gmv_reconciliation_results`
- `gmv_reconciliation_members`
- `gmv_adjustment_snapshot`
- `gmv_metric_snapshot`
- version-scoped active pointer 與 export cache manifest

不以新增 schema 作為第一階段前提；若 benchmark 顯示需要 index，另行提出 migration spec。

### 7.2 Proposed planner contract

```python
IncrementalRebuildPlan = {
    "base_version_id": str,
    "affected_source_receipt_nos": list[str],
    "affected_refund_ids": list[str],
    "affected_count": int,
    "unaffected_copy_candidate_count": int,
    "revenue_generation_token": str,
    "rules_fingerprint": str,
    "source_fingerprint": str,
    "decision": "INCREMENTAL_ELIGIBLE" | "FULL_REBUILD_REQUIRED" | "BLOCKED",
    "reason_codes": list[str],
}
```

### 7.3 Proposed result contract

```python
IncrementalRebuildResult = {
    "version_id": str,
    "recomputed_receipts": int,
    "copied_receipts": int,
    "recomputed_rows": int,
    "copied_rows": int,
    "dimensions": ["TOTAL_REFUND", "REFUNDED"],
    "equivalence_status": "PASS" | "FAIL",
    "fallback_used": bool,
    "publish_status": "READY" | "ROLLED_BACK" | "BLOCKED",
}
```

所有 monetary values 必須保留既有 Decimal/rounding contract；所有 receipt、refund identity 與 status 都必須可追溯至 observation/batch provenance。

## 8. Incremental orchestration

### Step 1：建立穩定計畫

- 讀取目前 active refund state、上一個 READY version、rules/source/pipeline fingerprints。
- 以退款單號做 identity diff；status、amount、退款方式變更不得被視為 unchanged。
- 產生 deterministic、去重、排序後的 affected receipt set。

### Step 2：Eligibility gate

Incremental 僅在下列條件全部成立時可執行：

- 存在完整 READY base version。
- base version 的 revenue token、rules fingerprint、source fingerprint 與目前一致。
- 無 `REFUND_IDENTITY_CONFLICT`、未知 dimension、非法金額或不完整 snapshot。
- affected receipts 可被穩定解析並可取得全部相關 revenue rows。
- affected 比例未超過 bounded guardrail。初始建議以 receipt ratio 與絕對數量雙重限制，實際值由 benchmark 校準。

若只有 affected 比例過大，轉 full rebuild；若資料完整性或 identity 不可信，直接 blocking。

### Step 3：Affected recompute

- 對每個 affected receipt 重新計算 `TOTAL_REFUND`。
- 只對 status 為 `已退款` 的適用資料計算 `REFUNDED`。
- `TT 退款轉團款` 若屬正式收入規則排除範圍，兩個 dimension 都不得將其當作正式可扣減收入；保留 reconciliation reason code。
- `applied_refund_amount <= original_receipt_amount`；超額部分只進 over-refund 欄位，不可使正式淨 GMV 負向超過原收款額。
- 同一 receipt 若有多個收入 member，必須在 receipt scope 內重新分配，不得只改第一筆。

### Step 4：Unchanged reuse

- 從 base version 以 set-based copy 將 unaffected reconciliation/member/adjustment rows 放入新 version。
- copy 過程不可呼叫完整 dataframe aggregation 或重新解析 raw refund Excel。
- 任何 copy candidate 若缺 row、fingerprint 不一致或對應版本不完整，取消 incremental eligibility，進入 full rebuild/blocking。

### Step 5：Metrics 與 artifact gate

- 新 version 的 metrics 必須由 affected delta 與 base metric 合併，或由新 version snapshot 做 bounded aggregation 取得。
- 報表 cache 只接受新 version 的 READY manifest。
- publish 前檢查 semantic equivalence、schema、baseline、conservation、artifact checksum 與 row count。
- 通過後以 transaction/atomic pointer swap 發布；失敗則 rollback 新 version 的未完成狀態，上一 READY pointer 保持不變。

## 9. Fallback、錯誤與併發

### 9.1 Fallback matrix

| 情況 | 行為 | 是否寫 active pointer |
|---|---|---|
| affected set 小且所有 fingerprints 一致 | incremental rebuild | equivalence PASS 後才寫 |
| affected set 超過 guardrail | full rebuild | full gate PASS 後才寫 |
| base snapshot 缺失/損壞 | full rebuild；若無法完成則 blocking | 否 |
| revenue/rules/source fingerprint 不一致 | full rebuild | 否，直到 gate PASS |
| identity conflict、非法金額、未知 status | blocking + audit evidence | 否 |
| incremental/reference 不等價 | 不 publish；可自動 full rebuild | 只有 full gate PASS 才寫 |
| process crash、timeout、memory guard | transaction rollback；保留舊 READY | 否 |

### 9.2 Concurrency

- 同一 revenue token + base version 只允許一個 rebuild lease。
- merge 開始後若 refund state 或 active base version 改變，必須 stale-plan abort，重新建立 plan。
- 不可讓兩個 rebuild 交錯更新同一 active pointer。
- pointer swap 必須在 manifest、metric snapshot、兩個 dimension artifacts 都 READY 後發生。

## 10. Trusted reference / shadow validation

### Semantic fingerprint

比較以下 canonical fields，而非 XLSX byte：

- version dimensions、receipt identity、refund status
- original amount、detail amount、applied amount、over-refund amount
- match status、reason code、scope inclusion
- member allocation 與 row counts
- summary metrics、daily/monthly/branch/product totals
- output sheet schema、欄位順序與 numeric/date semantics

Validation 必須報告 first mismatch 的 version、dimension、receipt、field 與 expected/actual digest；不得把 raw customer data 傳給外部 agent 或 Memory Hub。

### Shadow modes

1. `shadow`：incremental 與 full reference 都計算，不 publish incremental，收集差異與耗時。
2. `opt_in`：明確旗標下可 publish，但仍保留 reference gate。
3. `default`：連續通過 rollout gate 後才成為預設；full rebuild 仍保留為 fallback。

## 11. 測試矩陣與 benchmark

### Unit

- `NEW`、`UNCHANGED`、`STATUS_CHANGED`、`AMOUNT_CHANGED`。
- 退款方式進入/離開 `TT 退款轉團款`。
- identity conflict、空 affected set、重複 receipt、共享 receipt。
- Decimal rounding、over-refund cap、`已退款` dimension filter。

### Repository/integration

- 新 version copy 後不依賴 base version 讀取。
- affected rows 被替換，unaffected rows semantic fingerprint 不變。
- status update 是 upsert，不會 duplicate refund。
- failed validation 不改 active pointer、不留下半成品 READY manifest。
- concurrent stale plan 與 crash injection 可 rollback。

### Equivalence

至少覆蓋：

- 0.1%、1%、10%、超過 guardrail 的 affected receipts。
- 總退款與已退款各自有變更。
- 正式 scope 排除、SQLite not found、超額退款、multi-member receipt。
- incremental 與 full rebuild 的 row-level、metric-level、artifact-level semantic fingerprint 完全相同。

### Performance benchmark

每個資料規模至少 3 次 cold run、3 次 warm read，記錄：

- plan time、affected recompute time、copy time、metric time、validation time、publish time
- total wall-clock、peak RSS、affected/copied/recomputed rows
- full rebuild 對照、median、p95、fallback rate

通過條件是 unaffected aggregation 次數為 0、結果 equivalence 100% PASS，並達到已核准的 latency/RSS guardrail；若未達成，維持 shadow 或 full rebuild。

## 12. Rollout strategy

### Phase 0：instrumentation

先加入 plan、stage timing、affected/copy counts、reason codes 與 equivalence evidence，不改 production default。

### Phase 1：shadow

以正式資料 read-only 執行 incremental candidate 與 trusted full reference 比較；連續多批次 PASS 且無 unexplained mismatch 後，才進入 opt-in。

### Phase 2：opt-in

在受控 runtime 啟用 incremental publish；保留一鍵關閉旗標與 full rebuild fallback，監控 p95、RSS、fallback 與 pointer correctness。

### Phase 3：default with circuit breaker

將 incremental 設為預設，但任何 equivalence、baseline、conservation、memory 或 latency guard 觸發即回到 full rebuild/上一 READY version。禁止在沒有 evidence 的情況下永久移除 legacy path。

### Rollback

- 優先停用 incremental flag。
- active pointer 只切回上一個已驗證 READY version；不刪除 immutable evidence。
- 若是資料問題，修正 input/state 後重新建立 plan；不直接手改 snapshot。

## 13. Observability 與 audit

每次 rebuild 至少記錄：

- base/new version ID、trigger batch ID、generation/rules/source fingerprints
- affected receipt/refund counts、copied/recomputed row counts
- decision、fallback reason、各 stage timing、RSS guard result
- equivalence/reference digest、baseline/conservation result、publish result

日誌只保留必要 identity/digest 與 bounded examples；Memory Hub 只可保存設計 hint 或 read-only context，不可寫入業務狀態。

## 14. Implementation scope（供下一份 plan 使用）

預計依序拆成：

1. planner contract 與 eligibility/fallback gate。
2. repository set-based copy 與 new-version isolation。
3. affected receipt recompute engine，含兩個 refund dimensions 與 TT scope exclusion。
4. delta metrics、manifest/pointer atomic publish。
5. trusted reference/shadow equivalence 與 mismatch evidence。
6. concurrency、rollback、observability 與 feature flag。
7. unit/integration/equivalence/benchmark/full pytest/Hermes/UI acceptance。

Implementation 前須另建 plan，逐 task 驗證；不得在本 spec 階段直接修改 production SQLite、baseline、active pointer 或正式 cache。

## 15. Acceptance criteria

- affected receipt 重新計算，unaffected rows 的 business aggregation 呼叫數為 0。
- incremental 與 trusted full rebuild 在所有 canonical semantic fields 100% 等價。
- `TT 退款轉團款` 不會被正式 GMV 退款扣減重複計算。
- `總退款` 與 `已退款` 報表仍各自正確存在。
- status/amount/退款方式更新可被正確反映，無 duplicate 或遺漏。
- validation 或 runtime failure 不會污染上一個 READY active version。
- benchmark 證明低 affected ratio 下改善，且不超過記憶體/latency guardrail。
- full pytest、Hermes、正式 Streamlit/UI acceptance 均有獨立 evidence 後，才可升級 rollout phase。
