# 2026-06-25 Full Snapshot Baseline Drift

日期：2026-06-25  
類型：Baseline drift incident / full snapshot upload / revenue scope regression  
狀態：已修復，需作為後續高風險案例保留  

---

## 1. 事件摘要

在處理 2026-06-25 左右的 full snapshot upload 時，系統出現核心口徑 drift。Upload / acceptance gate 判定正式 SQLite 在重建後不符合 Phase 2 baseline，因此拒絕該批次並觸發 rollback。

核心 baseline：

```text
2026-05
全部分社 + 全部專職銷售組
正式口徑：不含掛賬核銷與TT退款轉團款
總營收：HKD 12,057,968
```

漂移原因不是 Vue、Streamlit UI 或 export rounding，而是 full snapshot 中新增的 excluded receipt row 觸發了舊的整單排除規則，導致已驗收 5 月正常收款被回溯排除。

---

## 2. 觀察到的問題

典型表象：

```text
本次上傳因核心口徑 Drift 已被拒絕；
異常資料庫已隔離；
正式 SQLite 已回滾；
回滾後核心口徑 matched。
```

這代表：

- 上傳流程有偵測到 drift。
- rollback 機制有生效。
- drifted database 應被 quarantine 保存。
- 正式 SQLite 不應被 drifted 批次污染。

這是系統保護機制成功攔截，而不是單純「上傳失敗」。

---

## 3. 根因

舊規則以 `來源單據號` 做整單排除：

```text
同一來源單據號只要任一行是：
- 掛賬核銷
- TT 退款轉團款

則該來源單據號全部收入被排除
```

Full snapshot 帶來的風險：

1. 5 月某來源單據號已有正常旅費收入，並已通過 baseline。
2. 6 月 full snapshot 中，同一來源單據號新增一筆 `掛賬核銷` 或 `TT 退款轉團款`。
3. 舊 analysis scope 將整個來源單據號排除。
4. 5 月已驗收正常收入被回溯移除。
5. `2026-05` baseline drift。

具體案例記錄中曾涉及：

```text
來源單據號：225JIA6515114503
受影響專職：JIA 江嘉韵
歷史正常旅費：約 HKD 15,000
```

---

## 4. 正確修復方向

修復不是改顯示層，而是改資料寫入與 scope guard：

```text
新的 excluded receipt row 本身不計入正式收入，
但不能 retroactively 排除同來源單據號下已驗收的正常 receipt。
```

落點：

| 層 | 正確做法 |
|---|---|
| Upload write path | 在 `database.upsert_to_db` 過濾新的 excluded receipt rows |
| Existing DB replay | 已存在的 historical excluded receipt rows 保留 |
| Analysis service | 不讓後續 excluded row 回溯污染 frozen baseline |
| Acceptance gate | 若 baseline 仍 drift，拒絕批次並 rollback |
| Quarantine | 保存 drifted DB 作診斷，不覆蓋正式 DB |

---

## 5. 錯誤修復方向

後續 Codex 不應做以下修法：

- 在 Vue 把總額硬改成 `12,057,968`。
- 在 Streamlit table 對某個人或分社加回差額。
- 在 export workbook 補一列調整數。
- 修改 Phase 2 baseline 以配合漂移後結果。
- 只改 `dashboard_service.py`，不處理 write path。
- 把所有 `掛賬核銷` / `TT 退款轉團款` 歷史行直接從 DB 刪掉。

這些都會造成 analysis layer 誤修或報表/API/UI 不一致。

---

## 6. 事故後已形成的防護

### 6.1 Phase 2G Stability History

每次 upload acceptance 應留下記錄，包括：

- core validation status
- baseline expected / actual
- freshness update
- rollback status
- quarantine path
- post-rollback verification

### 6.2 Phase 2H Auto Rollback

若 core drift：

```text
detect drift
→ quarantine drifted DB
→ restore backup
→ rebuild cache
→ verify baseline again
→ persist rejected history
```

Freshness update 不應觸發 rollback。

### 6.3 Frozen Baseline Revenue Scope

後續新 excluded receipt rows：

- 本身不計正式收入。
- 不回溯改動已驗收月份的正常收款。
- 以 `收款單號` 判斷是否是新 excluded row。

---

## 7. 後續排查 Runbook

若再次看到 drift：

1. 先確認是否是 core drift 還是 freshness update。
2. 查看 `stability_gate_history` 最近記錄。
3. 查看 rollback 是否成功。
4. 找到 quarantine DB。
5. 比對 drifted DB 與 restored DB 的 2026-05 baseline。
6. 查是否有新的 excluded receipt rows。
7. 查同來源單據號下是否存在歷史正常 receipt。
8. 跑 baseline tests。
9. 不要先改 UI。

建議命令：

```bash
.venv/bin/python -m pytest tests/test_database_rollback.py -q
.venv/bin/python -m pytest tests/test_phase2_precheck_acceptance.py -q
.venv/bin/python scripts/system_manager.py acceptance
```

---

## 8. 事件結論

這次事件的核心教訓：

```text
Full snapshot upload 不是單純追加新月份資料；
它可能帶入對歷史單據的新狀態。
Frozen baseline 必須阻止這類新狀態回溯改寫已驗收月份。
```

因此後續所有涉及 upload / upsert / revenue scope / rollback 的改動，都要先保護 baseline，再談 UI 或效能優化。

