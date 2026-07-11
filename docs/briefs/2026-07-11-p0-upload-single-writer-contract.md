---
type: codex-brief
project: nbs_analytics
status: plan-ready
priority: P0
created: 2026-07-11
tags: [nbs_analytics, codex, upload, single-writer, baseline, hermes]
---

# 2026-07-11 P0 Upload Single-Writer Contract

## 任務目標

讓 Streamlit 與 FastAPI / Vue 兩個正式 upload 入口共用同一個 orchestration contract，並以跨 process lease 保證同一時間只有一個正式 upload transaction。

同時移除 preflight 對全域 `database.DB_FILE` 的暫時覆寫，統一：

- governed monthly baseline gate；
- rollback；
- stability history / monthly baseline snapshot；
- cache invalidation / rebuild 狀態；
- response 與 audit evidence。

## Repo

`/Users/chanwaitung2025/Downloads/nbs_analytics`

## 必讀上下文

- [[NBS 系統總覽]]
- [[Upload Write Path]]
- [[Revenue Scope Rules]]
- [[ADR-001 Frozen Baseline 保護]]
- [[2026-06-25 Full Snapshot Baseline Drift]]
- [[驗收基線]]
- [[Hermes 驗收標準]]

Repo design spec：

`docs/superpowers/specs/2026-07-11-p0-upload-single-writer-contract-design.md`

Implementation plan：

`docs/superpowers/plans/2026-07-11-p0-upload-single-writer-contract.md`

目前狀態：Brief、Design Spec 與 TDD implementation plan 已完成；正式程式碼尚未修改。下一步必須由使用者選擇執行模式後，依 plan 逐 Task 實作與驗證。

## Observed State

- Streamlit 與 FastAPI 各有一個 `threading.Lock`，但兩個服務是不同 PID，不能互相排斥。
- Preflight 暫時改寫 module-global `database.DB_FILE`，存在同 process request / thread 讀到 temp DB 的風險。
- Streamlit 與 FastAPI post-write gate、monthly history 與 cache response contract 不完全一致。
- FastAPI 目前沒有重建 Streamlit session cache，卻可能回報「已重建」。

## 採用方案

共享 `UploadOrchestrator` + 獨立 SQLite coordination lock：

- `.nbs_runtime/upload_coordination.db`
- 以 SQLite `BEGIN EXCLUSIVE` 作跨 process lease。
- 不新增第三方 lock dependency。
- process crash 後 connection 關閉，自動釋放 lock。
- 先取得 lease，再讀取 upload bytes；busy 時不做 Excel I/O、preflight、backup 或 history write。
- database / preflight / gate 使用明確 `db_path`，不修改 module-global DB target。

未採用：

- FastAPI-only writer：目前會讓 Streamlit 正式上傳過度依賴 API 與 HTTP multipart。
- Atomic lock file：無法解決兩入口 gate/history/cache 分歧，亦需要 stale lock recovery。

## 核心資料流

```text
Streamlit adapter OR FastAPI adapter
  -> acquire shared UploadLease
  -> read/normalize inputs
  -> preflight on explicit temp DB
  -> governed gate on temp DB
  -> formal backup + upsert on explicit live DB
  -> governed gate on live DB
  -> rollback + governed recheck when required
  -> one stability history record
  -> advance cache generation
  -> release lease
  -> render the same UploadResult contract
```

## 不可破壞

- 正式口徑仍為「不含掛賬核銷與TT退款轉團款」。
- `2026-05` baseline 仍為 `HKD 12,057,968`。
- `2026-01` 至 `2026-06` baseline 數值與 monitoring / blocking mode 不變。
- 不修改 report sheets、ranking、Forecast、GMV、UI filter 語義。
- 不重寫 historical validated rows 或既有 acceptance history。
- 不用 presentation layer 掩蓋 drift。
- 自動化測試不寫正式 DB。

## 允許改動範圍

- upload orchestrator / lock service
- `database.py` 的明確 `db_path` contract
- preflight、governed gate、rollback、history
- Streamlit / FastAPI upload adapters
- upload response schema
- cache generation evidence
- system health / Hermes evidence
- 對應 tests

若需修改 `pipeline.py` 正式計算、baseline registry、workbook schema 或 forecasting，必須停止並重新取得授權。

## 必須先寫的測試

- 兩個 process 同時 acquire，只有一個成功。
- holder process crash 後可重新 acquire。
- preflight 使用 temp DB 時，其他 request 仍只讀 live DB。
- 兩入口產生相同 gate、monthly baseline、rollback、history 與 cache contract。
- FastAPI post-write 使用 governed gate。
- busy path 不做 I/O、backup、preflight 或 history write。
- accepted upload 只寫一筆完整 history。
- rollback 後 generation 對應 restored accepted DB。

## 驗收方式

- compile：upload / DB / gate / history / system manager affected files
- targeted pytest：lock、orchestrator、preflight、upload API、rollback、history、monthly baseline
- expanded baseline / dashboard / DB rollback tests
- full pytest
- virtual upload dry-run，`liveDbUnchanged: true`
- `scripts/system_manager.py acceptance`
- `scripts/hermes_post_change_check.py --json`

## 必守結果

- SQLite integrity `ok`
- May baseline `HKD 12,057,968 matched`
- January to June monthly checks matched
- Hermes `overallStatus: pass`
- Git 建立正式 commit，回填後 worktree clean

## 不屬於本 Brief

- Streamlit rerun 12–15 秒優化
- no-op full-table repair scan
- hidden tab DB reload
- cache retention
- API dashboard snapshot cache
- background queue
- DuckDB / Polars / Postgres / 語言重寫

## 第二 Brief Gate

只有 P0 implementation 完成、full tests / baseline / acceptance / Hermes 全部 PASS、Obsidian 完成回填並建立 Git 版本節點後，才建立 `Streamlit Rerun Hot Path` Brief。

## 目前狀態

`awaiting-review`

本 Brief 尚未授權 production code 修改。下一步是使用者審閱書面 spec；批准後才撰寫 implementation plan。
