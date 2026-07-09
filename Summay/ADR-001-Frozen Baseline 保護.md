# ADR-001: Frozen Baseline 保護

狀態：Accepted  
日期：2026-07-02  
範圍：`nbs_analytics` 正式營收口徑、upload write path、baseline drift 防護  

---

## 1. 背景

Phase 2 已鎖定核心 baseline：

```text
2026-05
全部分社 + 全部專職銷售組
正式口徑：不含掛賬核銷與TT退款轉團款
總營收：HKD 12,057,968
```

早期正式口徑曾以 `來源單據號` 整單排除：

```text
只要同一來源單據號存在：
- 收款類型 = 掛賬核銷
或
- 收款方式 = TT 退款轉團款

則整個來源單據號被排除
```

這個規則在單次完整資料中看似合理，但在 full snapshot upload / 後續月份追加時會出現高風險問題：後續新上傳的排除類收款行，可能 retroactively 影響已驗收月份的正常收款。

---

## 2. 問題

若某個 `來源單據號` 在 2026-05 有正常旅費收款，並已納入 baseline；之後 2026-06 同一 `來源單據號` 新增一筆 `掛賬核銷` 或 `TT 退款轉團款`，舊的整單排除邏輯會讓 2026-05 已驗收正常收入被回溯排除。

這會造成：

- 2026-05 baseline drift
- 上傳被 rollback
- 使用者看到「核心口徑漂移」
- 但真正的業務意圖並不是改寫 5 月歷史收入

這類問題不應靠改 dashboard 或 Vue 顯示層解決。

---

## 3. 決策

採用 Frozen Baseline 保護：

```text
已驗收月份的正常收款不得因後續新上傳的排除類收款行而被回溯排除。
```

具體規則：

1. `收款類型 = 掛賬核銷` 的新收款行本身不計入正式營收。
2. `收款方式 = TT 退款轉團款` 的新收款行本身不計入正式營收。
3. 後續新出現的排除類收款行，不得 retroactively 排除同一 `來源單據號` 下已驗收的正常收款行。
4. Full snapshot upload 時，已存在於 SQLite 的歷史排除類收款行可保留，避免重放完整快照時破壞既有 DB 形態。
5. 新排除類收款行是否跳過，以 `收款單號` 是否已存在於 SQLite 作為 write-time guard 的判斷基礎。

---

## 4. 實作位置

主要保護位置在 write path：

| 檔案 | 職責 |
|---|---|
| `database.py` | upsert 時查詢既有 `收款單號`，跳過新的 excluded receipt rows，保留既有 excluded receipt rows |
| `backend/services/revenue_scope_service.py` | 維持正式 revenue scope read model |
| `tests/test_database_rollback.py` | 驗證新 excluded receipt rows 不會刪掉歷史正常收入 |
| `tests/test_dashboard_service.py` | 驗證 revenue scope 不會 retroactively 排除非 writeoff receipts |

這個 ADR 的重點是：

```text
保護應優先落在 write path / service contract / tests，
不要只在 UI 或 export layer 做補丁。
```

---

## 5. 不採用的方案

### 5.1 在 dashboard 顯示層補回數字

拒絕原因：

- 會讓 API、Export、Vue、Forecast 與 Streamlit 數字不一致。
- 無法修復 SQLite 與 acceptance history。
- 容易掩蓋真正的 upload write path 問題。

### 5.2 每次 drift 後手動改 baseline

拒絕原因：

- baseline 是驗收錨點，不是事後調參。
- 只有在業務正式批准口徑變更時，才可修改 baseline。

### 5.3 仍以來源單據號整單回溯排除

拒絕原因：

- 會讓後續月份資料回頭污染已驗收月份。
- 與 Frozen Baseline 的穩定性目標衝突。

---

## 6. 正確與錯誤示例

### 正確

```text
5 月正常收款 HKD 15,000 已驗收
6 月同來源單據號新增掛賬核銷行

結果：
- 5 月正常收款保留
- 6 月掛賬核銷行不計入正式營收
- 2026-05 baseline 不漂移
```

### 錯誤

```text
5 月正常收款 HKD 15,000 已驗收
6 月同來源單據號新增掛賬核銷行

結果：
- 整個來源單據號被排除
- 5 月正常收款被移除
- 2026-05 baseline 從 12,057,968 漂移
```

---

## 7. 驗收要求

任何改動若涉及 upload、database、revenue scope、rollback、dashboard summary，至少跑：

```bash
.venv/bin/python -m pytest tests/test_database_rollback.py -q
.venv/bin/python -m pytest tests/test_dashboard_service.py tests/test_dashboard_api.py -q
.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py -q
.venv/bin/python scripts/system_manager.py acceptance
```

若改動較大，跑完整測試：

```bash
.venv/bin/python -m pytest -q
```

---

## 8. 後續 Codex 注意事項

看到 baseline drift 時，不要急著改：

- Vue
- Streamlit table
- chart
- export format
- display rounding

先查：

1. 新上傳是否包含 excluded receipt rows。
2. excluded receipt rows 是否是新的 `收款單號`。
3. SQLite 是否回滾到上次 accepted state。
4. quarantine DB 是否保存。
5. `stability_gate_history` 是否記錄 drift / rollback。
6. Phase 2 baseline 是否仍等於 `12,057,968`。

