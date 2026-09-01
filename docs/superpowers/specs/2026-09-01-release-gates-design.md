# Release Gate Standardization Specification

## 1. 文件目的

本 spec 定義 NBS Analytics 將 `full pytest`、Hermes 與 UI acceptance 固定為 release gates 的 contract。目標是讓每次 PR merge、release tag 或 production release 都有同一 commit、fresh source-bound evidence，並在任一 gate 失敗、阻塞或證據過期時 fail closed。

本 spec 只處理 release verification 與 CI gate coordination，不改變正式業務口徑、SQLite、baseline、GMV／退款規則、export schema 或任何正式 business state。

正式業務邊界仍為「不含掛賬核銷與 TT 退款轉團款」；2026-05 frozen baseline 固定為 `HKD 12,057,968`。

## 2. 現況與問題

目前 repository 已有以下能力：

- `scripts/verification_chain.py` 提供 full pytest 與 Hermes 的分段 verification flow。
- `scripts/hermes_post_change_check.py` 提供 Hermes read-only acceptance。
- `scripts/run_gmv_ui_acceptance.py` 提供 bounded HTTP/UI acceptance evidence validation。
- `.github/workflows/hermes-governance-graph.yml` 與 `.github/workflows/sandbox-integration.yml` 已提供部分獨立 CI checks。

目前缺少一個明確的 release-level contract，將三個 gates 綁定到同一 commit 並形成可審計的 aggregate decision。因此可能出現：

- Full pytest PASS，但 UI acceptance 沒有執行。
- Hermes PASS 被誤當成 full verification 或 UI PASS。
- 使用舊的 handoff／workflow artifact 取代本次 commit 的 fresh evidence。
- sandbox 或 UI runner 被環境阻塞，卻被當作可發布。

## 3. 設計原則

1. 三個 gates 必須獨立執行、獨立產生 evidence、獨立報告結果。
2. Release aggregator 只讀取並驗證 evidence，不執行測試、不修改正式狀態。
3. 所有 evidence 必須綁定同一 `commitSha`、source fingerprint、schema version 與產生時間。
4. `PASS`、`FAIL`、`BLOCKED` 與 `MISSING` 必須分開；只有三個 gate 都是 fresh `PASS` 才能 release。
5. 歷史 handoff、舊 CI run、Governance Graph projection、Memory Hub context、Memory Sidecar hints 都不能單獨滿足 release gate。
6. Governance Graph、Memory Hub、Memory Sidecar、Agent Operations 與 Hermes 都維持 read-only／non-authoritative，不得批准、dispatch、改寫或回寫 release state。
7. Release gate 不得改變正式 SQLite、baseline、revenue scope、GMV／退款資料、runtime business state 或 export schema。

## 4. 整體架構

```text
full-pytest job ───────┐
Hermes job ────────────┼──> release-gate aggregator ──> PASS / BLOCKED
UI acceptance job ────┘
```

### 4.1 Full pytest gate

Full pytest gate 使用 repository 定義的 dependency bootstrap 與 pytest entrypoint，必須包含適用環境下的 sandbox integration tests。Gate evidence 至少包含：

- `schemaVersion: full-pytest-gate-v1`
- `status: PASS | FAIL | BLOCKED`
- `commitSha`
- `sourceFingerprint`
- `commandId` 與 bounded command representation
- `passed`, `failed`, `skipped`, `durationSeconds`
- sandbox preflight status（如適用）
- `startedAt`, `finishedAt`, `evidenceFingerprint`

在 qualified macOS runner 上，sandbox tests 必須以 `--sandbox-preflight required` 執行。Capability 不可用時回報 `BLOCKED`，不得以 skip 或普通 subprocess 替代真實 sandbox test。

### 4.2 Hermes gate

Hermes gate 執行 `scripts/hermes_post_change_check.py` 或既有 verification-chain 的 Hermes stage，要求 fresh `overallStatus=pass`。Gate evidence 至少包含：

- `schemaVersion: hermes-gate-v1`
- `status: PASS | FAIL | BLOCKED`
- `commitSha`、`sourceFingerprint`
- Hermes report fingerprint 與 bounded result summary
- Governance Graph／Memory Hub／Memory Sidecar report 的 read-only policy indicators
- `startedAt`, `finishedAt`, `evidenceFingerprint`

Hermes 不得取代 Full pytest 或 UI acceptance，不得執行 Graph／Memory write、Gateway、provider network recall、prune、apply、Git mutation 或 release approval。

### 4.3 UI acceptance gate

UI acceptance gate 使用 HTTP server／Streamlit acceptance，不能使用 `file://`。它應消費既有 bounded UI evidence，並透過 `scripts/run_gmv_ui_acceptance.py` 或等價的 deterministic adapter 驗證：

- HTTP target 與 evidence route 一致。
- HTTP/resource probe 成功且 status 不為 error。
- UI evidence 的 `commitSha`、source fingerprint 與本次 release 一致。
- bounded smoke flow 通過，例如頁面載入、核心資料顯示與必要下載路徑。
- fixture root 位於 temporary directory，不觸碰正式 database、production cache 或正式 business data。
- browser／server failure 回報 `FAIL` 或 `BLOCKED`，不得被轉成 PASS。

UI acceptance evidence 至少包含：

- `schemaVersion: ui-acceptance-gate-v1`
- `status: PASS | FAIL | BLOCKED`
- `commitSha`、`sourceFingerprint`、`route`
- bounded probe／scenario results
- `startedAt`, `finishedAt`, `evidenceFingerprint`

## 5. Evidence identity 與 freshness contract

Aggregator 必須拒絕下列 evidence：

- 缺少或不符合 exact schema。
- `commitSha` 不等於被驗證的 release commit。
- source fingerprint 不一致。
- fingerprint 重算不一致。
- 超過設定 freshness window 的 artifact。
- absolute path、symlink escape、secret 或超過 size cap 的 payload。
- status 不是明確的 `PASS`、`FAIL` 或 `BLOCKED`。

Release evidence 應以 immutable CI artifact 傳遞；aggregator 不應從 handoff、Graph、Memory Hub 或 Memory Sidecar 推導缺失的 gate 結果。

建議的 aggregate schema：

以下 JSON 只展示欄位形狀；`<...>` 僅為文件示例，實際 validator 必須拒絕未替換的 placeholder。

```json
{
  "schemaVersion": "release-gate-result-v1",
  "status": "PASS",
  "commitSha": "<40-char-sha>",
  "gates": {
    "fullPytest": {"status": "PASS", "evidenceFingerprint": "<sha256>"},
    "hermes": {"status": "PASS", "evidenceFingerprint": "<sha256>"},
    "uiAcceptance": {"status": "PASS", "evidenceFingerprint": "<sha256>"}
  },
  "freshness": {"status": "fresh", "maxAgeSeconds": 1800},
  "evidenceFingerprint": "<sha256>"
}
```

實際 implementation 不得把此 aggregate JSON 寫入正式 SQLite 或當作 business state；它只是 CI／release evidence。

## 6. Release policy

### PR merge

PR 必須要求三個 gate checks 都通過。任一 gate 為 `FAIL`、`BLOCKED`、`MISSING`、stale 或 identity mismatch，PR 不得標記為 release-ready。

### Release tag／production release

Release tag 或 production deployment 必須重新執行三個 gates，不得沿用 PR run 的結果。Release workflow 必須驗證 tag commit 與三份 evidence 的 `commitSha` 完全一致。

### Gate independence

以下判斷一律無效：

- Hermes PASS ⇒ Full pytest PASS。
- Full pytest PASS ⇒ UI acceptance PASS。
- UI acceptance PASS ⇒ Hermes PASS。
- Governance Graph／Memory Hub／Memory Sidecar 有 context ⇒ 任一 gate PASS。
- 舊 handoff PASS ⇒ 本次 release PASS。

## 7. Failure、blocked 與 rollback

- `FAIL`：測試或 acceptance 已執行且結果不符合 contract；保留 logs 與 bounded evidence，阻擋 release。
- `BLOCKED`：環境、dependency、runner capability 或 server 無法提供必要驗證；阻擋 release，不得降級為 PASS。
- `MISSING`：required artifact 未產生、未上傳或無法讀取；阻擋 release。
- rollback 以 Git revert commit 為主，不能透過 release gate 寫回 SQLite、baseline 或任何正式業務資料。
- release gate workflow 修改本身應可由 revert 還原，且不得刪除原始 evidence artifact。

## 8. Security 與治理邊界

- Full pytest、Hermes、UI acceptance runner 都必須使用 allowlisted commands、bounded timeout、bounded output 與安全 temporary fixture。
- UI job 不得使用正式 DB、正式 cache、正式 Excel／CSV 或 production runtime path。
- Hermes 只讀取 canonical／integration evidence；不批准 gate、不 dispatch agent、不啟動 Gateway、不執行 Memory recall 或 provider write。
- Governance Graph 只能作 canonical artifacts 的 read-only projection。
- Memory Hub 與 Memory Sidecar 只能提供 bounded non-authoritative context／hints；缺失或 stale hints 不得改變 gate decision。
- Release aggregator 不得接受 LLM 自由文字作為唯一 gate verdict；所有 verdict 必須由 deterministic schema validation 產生。

## 9. 最小實作範圍

implementation plan 應優先包括：

1. 建立三種 gate evidence 的 exact schema、fingerprint 與 freshness validator。
2. 將現有 Full pytest、Hermes、UI acceptance 入口包裝成 commit-bound gate jobs。
3. 建立 read-only release aggregator 與 fail-closed exit code。
4. 在 CI 設定 PR merge required checks 與 release tag fresh rerun。
5. 補充 malformed、missing、stale、mismatched commit、blocked environment、secret/path escape 與 artifact cap tests。
6. 更新 handoff、Hermes monitoring 與 agent dispatch 文件，明確記錄三 gate 的獨立性。

不在本 Task 內：

- 新增 Governance Graph、Memory Hub、Memory Sidecar 的 authority 或 write path。
- 重寫 agent orchestration、approval、dispatch 或 workflow control。
- 修改正式 SQLite、GMV／退款流程、baseline、revenue scope 或 export schema。
- 建立新的 production data migration 或大型架構抽象。

## 10. 驗證與 acceptance criteria

Spec 對應的 implementation 完成條件：

- Full pytest gate 在 qualified runner 上 fresh PASS，並保留 passed／failed／skipped 計數。
- Hermes gate fresh `overallStatus=pass`，且 read-only indicators 維持 zero write／zero approval／zero dispatch。
- UI acceptance gate 以 HTTP／Streamlit runner fresh PASS，且 route、commit、source fingerprint 一致。
- 三個 gate 的 commit SHA 完全相同；任何 stale／missing／blocked evidence 都使 aggregator fail closed。
- PR workflow 與 release workflow 都能阻擋未滿足三 gate 的 release。
- full pytest、Hermes、UI acceptance、Strict Review 與 sandbox gate 的結果分開報告，不互相取代。
- frozen baseline 仍為 `HKD 12,057,968`，正式 scope 與 GMV／退款規則保持不變。
- working tree、SQLite、正式 runtime business state 與受保護的 canonical artifacts 未被 gate runner 修改。

## 11. Rollback 與觀測

Rollback 只需 revert release-gate implementation／workflow commits，重新驗證 `main`。每次 gate 應上傳：

- bounded gate evidence JSON
- command／environment summary
- failure 或 blocked reason
- source commit 與 fingerprints
- workflow run URL

Hermes 可 read-only 檢查這些 artifacts 的 schema、status、cap 與 permission，但不得修改、刪除、prune、批准或重跑它們。

## 12. 探索來源與 authority 判定

本 spec 的探索由本地 Context Agent、既有 Governance Graph contract、Memory Hub／Memory Sidecar read-only contract 與 repository workflow／verification scripts 輔助完成。這些來源只用於 bounded context、risk discovery 與 contract reconciliation；正式 release decision 仍只由 deterministic gate evidence 與 aggregator contract 產生。
