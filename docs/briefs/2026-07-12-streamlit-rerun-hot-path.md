---
type: codex-brief
project: nbs_analytics
status: planned
priority: P1
created: 2026-07-12
tags: [nbs_analytics, codex, streamlit, performance, rerun]
---

# 2026-07-12 Streamlit Rerun Hot Path

## 任務目標

針對 Streamlit 頁面刷新後的固定等待，找出並移除約 12–15 秒的 no-op 等待或重複初始化，縮短頁面由 SQLite loaded 到 dashboard facts ready 的時間。

## Scope

- 只處理 Streamlit rerun / page-load hot path。
- 先以 profiling 與 timing evidence 定位固定等待來源。
- 保留正式 DB、baseline、monthly gate、rollback、cache generation 與 report 計算語義。
- 以 cache hit、session rerun、hidden tab 與服務重啟情境分別驗證。

## 不屬於本 Brief

- 不修改正式營收口徑或任何 baseline。
- 不改 upload single-writer contract。
- 不把 Forecast、Export 或 UI 顯示層當成資料正確性修正層。
- 不進行語言重寫或資料庫替換。

## 前置證據

P0 Upload Single-Writer Contract 已於 2026-07-12 完成服務、Hermes、baseline、dry-run 與測試驗收；應以該版本為起點。

## 驗收方向

- 先量測 rerun 各階段時間，確認固定等待是否可重現。
- 變更後跑 targeted tests、服務 acceptance 與 Hermes。
- 確認 2026-05 baseline 仍為 `HKD 12,057,968`，且正式 DB 未被 page load 改寫。
