# GMV 排除訂單看板：一鍵合併與版本化報表 Cache 設計

## 1. 文件狀態

- Status: Approved design for spec review
- Date: 2026-08-21
- Scope: GMV 排除訂單看板、退款 ledger upsert、正式淨 GMV active version、版本化報表 cache
- Approved direction: 方案 A：一鍵合併 + 自動建立 active version + 版本化報表 cache
- Out of scope: Governance Graph、Memory Hub、Agent orchestration、新外部服務、新 FastAPI contract、新資料庫

## 2. 背景與問題

目前 GMV 排除訂單看板把一個業務動作拆成多個互相依賴的操作：上傳退款 Excel、載入正式淨 GMV、閱讀 Preview、填寫確認人員、勾選 acknowledgement、建立 active version，以及另外按按鈕生成兩套完整報表。

這與經營分析大盤的「上傳並合併至資料庫」心智模型不一致，也造成兩個實際問題：

1. 使用者上傳後不能直接看到新的正式版本，必須理解 preview／confirmation lifecycle。
2. 報表生成會對總退款與已退款兩個維度各自重跑既有 Dashboard export pipeline；每個維度建立三套 workbook，並以 `openpyxl` 同步序列化大型 worksheet。實測退款套用約 30.7 秒，完整 workbook 生成在第二個 workbook 序列化階段已持續數分鐘，未能在診斷 benchmark 中完成。

正式口徑仍固定為「不含掛賬核銷與TT退款轉團款」。正式淨 GMV 只扣減退款狀態為「已退款」的金額；總退款維度繼續保留作營運比較。

## 3. 設計目標

### 3.1 使用流程

- 退款檔上傳後，使用者只需按一次「上傳並合併退款資料庫」。
- 系統自動執行 Preflight、退款 ledger upsert、狀態更新、active version 建立及報表 cache 建立。
- warning 不要求額外人工確認；只有 blocking error 停止合併。
- 合併完成後自動 rerun，直接展示最新 active version。
- 頁面載入 active version 時不重新掃描整份營收明細。

### 3.2 資料正確性

- 退款資料仍寫入既有 GMV refund ledger，不回寫原營收表。
- 維持 immutable observation／version 邊界及 single-writer coordination。
- 同一 refund identity 的「退款中 → 已退款」必須支援增量狀態更新。
- 主營收資料變更時，舊 active version 不可錯配新營收；重新合併退款檔後才建立新 active version。
- warning、blocking、version、cache 狀態均可追溯至 source file hash、revenue generation token 與 operation id。

### 3.3 性能

- active version page read 不重新執行全量退款調整。
- 同一 version 的重複報表下載不重建 workbook，目標延遲小於 2 秒。
- 首次報表 cache 建立以目前資料量為基準，目標小於 60 秒；此為 performance acceptance target，需以 benchmark 驗證。
- 報表 cache 建立失敗不回滾已成功建立的 active version；必須在 UI 顯示 export pending／failed 狀態及可診斷原因。

## 4. 非目標

- 不修改正式營收 scope、frozen baseline 或月度 baseline registry。
- 不調整旅行團人數／票務數量規則；仍顯示原交易人數／數量，退款只扣減金額。
- 不新增 SQLite database、migration、FastAPI endpoint、background service 或 message queue。
- 不把 warning 靜默隱藏；異常中心仍需展示總退款與已退款兩個維度。
- 不讓 Streamlit Agent Operations 成為 approval、dispatch 或正式狀態寫入入口。

## 5. 使用者流程與 UI contract

### 5.1 初始狀態

GMV tab 開啟時：

- 直接讀取 SQLite schema、唯一 ACTIVE version、metric snapshot、adjustment snapshot 與 export cache manifest。
- 若存在有效 active version，直接展示 version provenance、總退款、已退款、正式淨 GMV、異常中心摘要及可用報表下載。
- 移除「載入正式淨 GMV」按鈕。
- 移除「確認人員」欄位與人工 acknowledgement checkbox。
- 若 active version 的 revenue token 已過期，顯示「主營收已變更，請重新上傳退款檔並合併」；不展示錯配的正式淨 GMV 數值。

### 5.2 上傳與合併

保留退款 Excel／CSV uploader，新增或改名主要 action 為：

`上傳並合併退款資料庫`

按下後以 `st.status` 顯示階段進度：

1. 讀取與 normalize 退款檔。
2. 執行 Preflight。
3. 若 Preflight 有 blocking error，停止並顯示錯誤；不得寫入 refund ledger 或建立 active version。
4. 若只有 warning，繼續執行，並明確顯示「含 warning，已記錄至異常中心」。
5. 取得既有 upload lease／single-writer coordination。
6. 執行 observation upsert 與 current-state 更新。
7. 建立新的 active version，保存上一版本 id、source file hash、revenue generation token、rule version 及 operation id。
8. 建立 version-scoped export cache。
9. 保存 export cache status，完成後 rerun 頁面。

### 5.3 合併後展示

合併成功後頁面直接顯示：

- active version provenance
- 排除前 GMV
- 總退款明細金額與實際扣減金額
- 已退款明細金額與實際扣減金額
- 正式淨 GMV
- 總退款／已退款匹配及異常摘要
- 報表 cache 狀態
- 可下載的總退款及已退款完整報表

異常中心保持 read-only；使用者不需要再按任何「載入」、「確認」或「生成」按鈕。

## 6. Preflight policy

### 6.1 Blocking

Blocking 是資料不能安全寫入或不能形成可信 active version 的條件：

- 缺少必要欄位：退款單號、來源單據號、退款原幣金額、退款狀態。
- 退款單號、來源單據號或退款狀態為空。
- 退款金額無法解析或為負數。
- 沒有任何可用退款列。
- 同一退款單號的 identity 發生衝突，例如來源單據號、金額或其他 immutable identity 與既有 ledger observation 不一致。

Blocking 行為：

- public status 為 blocked／error。
- 不寫入正式 SQLite。
- 不建立新 active version。
- 不更新正式報表 cache。
- 保留 read-only preflight report 供 UI 顯示。

### 6.2 Warning

Warning 是可以計算、但需要留下業務稽核證據的情況：

- 來源單據號在 SQLite 找不到。
- 來源單據號落在正式營收 scope 排除範圍。
- 退款金額超過原收款金額；實際扣減以原收款金額為上限。
- 退款檔包含未定義狀態；「已退款」維度只取精確的「已退款」。

Warning 行為：

- 不要求人工 acknowledgement。
- 允許繼續 upsert 與建立 active version。
- 以 version／operation provenance 保存 warning code、count、amount、examples 摘要。
- 在異常中心提供總退款與已退款維度篩選。

### 6.3 狀態更新

- 相同 refund identity 由「退款中」變成「已退款」：允許建立新 observation、更新 current pointer、建立新 active version。
- 同一 file hash + 同一 revenue generation token 已成功合併：視為 idempotent，回傳既有 version，不重複寫入或重建 cache。
- 同一 file hash + 新 revenue generation token：建立新 active version，讓退款資料與最新主營收 snapshot 對齊。

## 7. Data flow 與元件邊界

```text
Streamlit uploader
  -> GMV merge action
  -> GmvRefundPreflight
  -> existing upload lease / single writer
  -> gmv_refund_observations + gmv_refund_current
  -> gmv_scope_versions ACTIVE promotion
  -> gmv_metric_snapshot + gmv_adjustment_snapshot read model
  -> versioned export cache
  -> Streamlit rerun / read-only display
```

### 7.1 Streamlit layer

責任：

- 檔案選取、merge button、進度與結果展示。
- 不直接組合 active version 的 business state。
- 不在 page render 中重算全量退款調整或同步生成報表。

### 7.2 Refund service layer

責任：

- normalize、Preflight、identity conflict 判定。
- observation upsert、current-state update、version activation。
- idempotency、revenue token freshness、operation provenance。

### 7.3 Read model layer

責任：

- active version metrics 與 adjustment snapshot 的快速讀取。
- 只在建立／更新 version 時計算全量 adjustment。
- 不因 page rerun 重新掃描原營收明細。

### 7.4 Export cache layer

責任：

- cache key 至少包含 `version_id`、`revenue_generation_token`、`rule_version`、export schema version。
- cache payload 包含總退款、已退款兩套完整報表及 audit workbook。
- cache miss 只觸發受控建立流程，不改變 active version。
- cache artifact 保存在既有 ignored runtime cache 範圍，不進 Git，不作 canonical business source。

## 8. Export cache 設計

### 8.1 Cache key

```text
gmv-formal-export-v1:
  version_id:
  revenue_generation_token:
  rule_version:
  official_export_schema:
```

任何一項改變都必須 invalidate 舊 cache。

### 8.2 Cache 內容

每一個 version cache 至少包含：

- 總退款完整報表：全維度、不含掛賬核銷、正式口徑、稽核報表。
- 已退款完整報表：全維度、不含掛賬核銷、正式口徑、稽核報表。
- `version_id`、source file hash、revenue token、建立時間、cache schema version。
- 每個 workbook 的 status、byte size、build duration、error summary。

### 8.3 失敗隔離

- active version transaction 與 workbook serialization 不得共用長時間 SQLite transaction。
- active version 成功後才建立 export cache。
- export cache 失敗不得 rollback active version。
- UI 顯示 `pending`、`ready` 或 `failed`，並保留可供 Hermes／diagnostics 使用的 stage timing。

## 9. Error handling

- 使用既有 upload lease 防止兩次同時 merge。
- Preflight blocking 在 DB write 前返回。
- DB write／activation 失敗使用既有 backup／rollback contract。
- cache failure 只標記 export status，不偽造下載檔案。
- active version stale 時 fail closed，不展示錯配的淨 GMV。
- 所有錯誤訊息需包含可定位的 stage、code、operation id；不得只顯示「生成失敗」。

## 10. Testing strategy

### 10.1 Unit tests

- blocking schema／empty／invalid amount／identity conflict。
- warning 不阻止 merge。
- status `退款中 → 已退款` 可更新 current state。
- idempotent same file + same revenue token。
- same file + new revenue token 建立新 version。
- cache key 受 version、token、rule、schema 變更影響。
- cache failure 不影響 active version。

### 10.2 Integration tests

- 一鍵 merge 從 uploaded file 到 active version 的完整流程。
- warning batch 寫入 ledger、建立 active version、異常中心可讀。
- blocking batch 不修改正式 SQLite。
- page rerun 直接讀取 active snapshot，不重建全量 adjustment。
- 兩個 dimension 的 export cache 可重用且下載 bytes 穩定。

### 10.3 Performance tests

- 記錄 read、normalize、Preflight、adjustment、activation、cache build 各 stage timing。
- 建立 baseline：目前 adjustment 約 30.7 秒，完整 export pipeline 超過數分鐘。
- acceptance target：重複下載小於 2 秒；首次 cache build 小於 60 秒，或輸出明確 performance exception。

### 10.4 Runtime acceptance

- targeted pytest。
- 完整 pytest。
- Streamlit UI 實際上傳與一鍵 merge 驗收。
- `system_manager.py acceptance`。
- `scripts/hermes_post_change_check.py`。
- SQLite integrity、唯一 ACTIVE version、frozen baseline、正式 revenue scope、Git status。

## 11. Rollout 與 rollback

### Rollout

1. 先加入 service／UI／cache contract 與 tests。
2. 在 read-only／fixture data 上驗證 cache read model。
3. 在實際退款 Excel 上做 Preflight dry-run。
4. 確認 blocking／warning 行為與 stage timings。
5. 才啟用正式一鍵 merge。

### Rollback

- 程式 rollback 使用 Git branch／PR rollback。
- active version rollback 使用既有 version／database rollback contract，不直接刪除 immutable ledger。
- export cache 可失效或重建，不作為 rollback authority。
- 不刪除原始退款 Excel、SQLite backup 或 quarantine 作為 rollback 手段。

## 12. Acceptance criteria

- 使用者只需上傳退款檔並按一次 merge action，即可完成 Preflight、upsert、active version promotion 與 page rerun。
- warning batch 不要求確認人員或 checkbox，仍可建立 active version，異常中心保留完整摘要。
- blocking batch 不寫正式 SQLite、不建立 active version。
- 「退款中 → 已退款」可正常更新，不被誤判為 identity conflict。
- page 載入 active version 不再觸發全量退款 adjustment 重算。
- 總退款及已退款報表均以 version cache 提供，重複下載不重建 workbook。
- cache key 能防止舊版本、舊 revenue token 或舊 export schema 錯配。
- active version 建立成功但 export cache 失敗時，系統保留正式版本並清楚顯示 export failure。
- 完整測試、Streamlit UI acceptance、SQLite integrity、baseline 與 Hermes 均通過。

## 13. Open implementation decisions

以下項目留給 implementation plan 定義，不在本 spec 中偷偷擴大 scope：

- 是否沿用現有 `gmv_metric_snapshot`／`gmv_adjustment_snapshot` 欄位，或只增加 runtime cache manifest。
- export workbook 是否改用現有 writer 的 shared intermediate frames、較快 writer，或 version-scoped prebuild；必須以 benchmark 選擇，不可直接更換正式 export schema。
- automatic merge 的 audit actor 固定值與 UI 顯示文案。
- cache retention 是否沿用既有 runtime retention 設定，及其保留數量。
