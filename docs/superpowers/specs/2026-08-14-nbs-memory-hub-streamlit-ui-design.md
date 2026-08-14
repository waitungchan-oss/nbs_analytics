# NBS Memory Hub Streamlit UI Design Spec

## 1. 目的

在現有 NBS Analytics Streamlit cockpit 增加一個只讀的 Memory Hub tab，讓使用者可以直接觀察 C-0/C-1 Memory Contract、immutable catalog、scope filtering、freshness、ACL decision 與 source drill-down 的實際作用。

本階段只建立 observation／diagnostic UI，不建立獨立網站、FastAPI endpoint、Node.js Gateway、SQLite migration、Wiki、CodeGraph 或 Candidate Memory。

## 2. 使用者價值

使用者可以在瀏覽器確認：

- Memory Hub 是否有已建立且可驗證的 catalog。
- catalog 內有哪些 verified records 與三類允許 source。
- query 是否因 scope、freshness、identity 或 catalog 狀態被 allow、deny 或 blocked。
- 每筆 memory 是否能追溯到 artifact ref、artifact SHA-256、run ID、Git head 與 expiry。
- Memory Hub projection 是否仍屬 `non_authoritative_memory`，不會取代 canonical context。

沒有 catalog 時，UI 必須明確顯示 `catalog_missing` 或 `blocked`，不能自行建立 catalog 或補造 records。

## 3. 非目標與治理邊界

- UI 不得建立、更新、重建或刪除 catalog／snapshot。
- UI 不得修改 canonical artifacts、SQLite、baseline、revenue scope、business rules、export schema、Git 或 workflow state。
- UI 不得成為 approval、dispatch、runtime control、Graph authority 或 recall switch。
- 不新增自動 recall；現有 sidecar defaults 維持 recall disabled、writer disabled、shadow mode enabled。
- 不引入新的資料庫、背景 job、外部 API、Node.js service 或 credential。
- Candidate memory 必須與本 UI 隔離，留待後續獨立 spec。

## 4. 建議架構

### 4.1 Read model adapter

新增 `backend/agents/memory_hub_ui_service.py`，作為 Streamlit 與 C-0/C-1 service 之間的 bounded adapter：

- 接收已載入的 `MemoryCatalog | None`、`MemoryHubService` 所需 project identity 與固定 query limits。
- 只呼叫 `MemoryHubService.query()` 與 `resolve_source()`；不呼叫 `build_catalog()`。
- 輸出 UI 專用、可排序的 read model，不暴露 raw artifact content。
- catalog 缺失、schema invalid、fingerprint mismatch、stale、scope mismatch、timeout 或 provider unavailable 時輸出 bounded status 與 reason code。

### 4.2 Streamlit integration

在 `app_pages.py` 現有四個 top-level tabs 中加入第五個 `Memory Hub` tab，沿用現有 `Agent Operations` 的 cockpit shell 與 theme tokens。

新增 `memory_hub_rendering.py`，只負責 Streamlit rendering；不直接讀檔、不建立 catalog、不操作 SQLite。`app_pages.py` 只負責注入 project root、明確 catalog provider 與 runtime identity。

若 deployment 沒有提供合法 catalog provider，tab 仍要正常載入，顯示 `尚無已建 Memory Hub catalog；此頁不會自行建立或更新 catalog。`。

## 5. UI contract

### 5.1 Catalog status card

顯示：

- status：`ready`、`missing`、`invalid`、`blocked`
- catalog fingerprint
- built-from Git head
- source count／record count
- policy fingerprint／policy version
- freshness summary

不顯示 secret、raw prompt、完整 artifact content 或絕對 filesystem path。

### 5.2 Query controls

提供：

- bounded query text
- scope：project／agent／team
- memory kind：governance／evidence／skill
- consumer identity／team identity
- 固定 `maxItems=3`、`maxBytes=6000`、`timeoutMs=800`

Query 只產生 read-only result；頁面重新整理與篩選不得建立 catalog 或 snapshot。

### 5.3 Record table

每筆 record 顯示：

- memory ID
- memory kind
- summary
- owner
- scope
- freshness
- record status
- source count

summary 必須使用 bounded text；不直接渲染來源檔案全文。

### 5.4 Source drill-down

選取 record 後顯示其 verified source metadata：

- source kind：`governance_document`、`verified_evidence`、`approved_skill`
- artifact ref
- artifact SHA-256
- run ID（如有）
- Git head（如有）
- generated／expires time
- source ID／source fingerprint
- resolve status

Source resolve 失敗只顯示 reason code，例如 `stale`、`scope_mismatch`、`missing_source`、`blocked_identity`。

### 5.5 ACL／failure panel

顯示 query result 的 allow／deny／blocked decision 與 reason，並將 `empty`、`timeout`、`degraded`、`blocked` 與 `catalog_missing` 分開呈現。不可把 blocked 或 empty 顯示成 ready。

### 5.6 Authority notice

Ready projection 必須顯示：

`Memory Hub 是 non-authoritative read-only memory；canonical artifacts 與正式 context 仍是真相來源。`

## 6. Data flow

```text
explicit catalog provider
        ↓
load_catalog (read-only validation)
        ↓
MemoryHubService.query / resolve_source
        ↓
MemoryHub UI read model adapter
        ↓
Streamlit Memory Hub tab
```

任何 load／query 失敗都在 adapter 層轉成 bounded diagnostic；不得由 UI fallback 到自行掃描 repository 或自行推測 records。

## 7. 測試與驗收

- UI adapter tests：ready、missing、invalid、stale、scope mismatch、blocked identity、source drill-down。
- Rendering contract tests：tab label、status card、query controls、record table、authority notice、empty／blocked copy。
- Regression tests：Agent Operations、Governance Graph、existing context fingerprint 與 sidecar defaults 不變。
- Browser smoke：`http://127.0.0.1:8502/` 載入 Memory Hub tab；驗證 catalog missing、ready query、record detail 與 blocked state。
- Full pytest、system acceptance、Hermes read-only check。

## 8. Success criteria

1. 使用者可在現有 Streamlit 看到 Memory Hub tab。
2. 沒有 catalog 時明確顯示 missing／blocked，且頁面不建立任何 catalog 或 snapshot。
3. 有合法 catalog 時可查詢 records、查看 ACL decision、進行 source drill-down。
4. 所有顯示內容都能追溯到 Memory Hub service result 與 fingerprint。
5. canonical context、SQLite、baseline、workflow authority 與 recall defaults 維持不變。
