# Local CLI / Remote API / Local Model Runner Identity Contract

> 狀態：draft for review
> 日期：2026-08-31
> 適用範圍：NBS Analytics Agent tooling、Strict Review、verification evidence、Hermes evidence 與 cache identity

## 1. 摘要

本 spec 定義一個共用的 `runner-identity-v1` contract，統一描述 Local CLI、Remote API 與 Local Model 的執行身分。目標是讓 Strict Review、Hermes、verification artifacts 與 runner cache 能辨認「哪個 runner、透過哪種 transport、使用哪個 provider/model/profile/environment」產生結果。

這是 tooling identity 變更，不是業務資料變更。它不改正式營收、正式淨 GMV、退款規則、SQLite、2026-05 frozen baseline `HKD 12,057,968`、Dashboard 指標、Forecast 或 export schema。

## 2. 現況與問題

目前存在三組不完全一致的 identity 表達：

1. Local CLI 的 `RunnerProfile` 主要使用 `executable`、`model`、`cachePath` 與 CLI version floor。
2. Hermes evidence 主要使用固定的 `provider`、`model`、`reasoningProfile`，並在不同 script 重複驗證。
3. Verification session 有 source seal，但 runner identity 沒有成為一致的 artifact binding；部分 Strict Review wiring 會從 command 臨時推導 model。

已觀察到的具體風險：

- profile artifact 可使用 `DeepSeek-V4-Flash`，而現行 cache identity 使用 `gpt-5.4`，造成 alias/case/display name 混淆。
- Local CLI 與 Hermes Remote API 的 `provider`、`model`、`reasoningProfile` 欄位不能直接互換。
- `runner-capability-v1` 有 executable/model/cache/environment fingerprint，但沒有 `transport`、`runnerId` 或 `profile`。
- `verification-v1` 與 `verification-session-v1` 有既有 exact schema，直接增加 top-level 欄位會有 parser compatibility 風險。
- Governance Graph catalog 目前回報 `invalid`，Memory Sidecar historical AB evidence 回報 `blocked_runner_capability`；兩者都不能被升格為 runner capability PASS。

## 3. 目標與非目標

### 3.1 目標

- 建立唯一的 canonical runner identity schema 與 fingerprint 規則。
- 明確區分 `runnerId`、`transport`、`provider`、`model`、`profile` 與 `executionEnvironment`。
- 讓新產生的 Strict Review、Hermes、verification 與 cache evidence 可互相核對 identity。
- 舊 artifact 可相容讀取，但缺少 canonical identity 時不得偽造為已驗證的新 identity。
- identity 缺失、alias 不可解析、producer/consumer 不一致時 fail closed。
- 保持各 runner 的實際 capability proof、token/latency/provenance evidence 與 gate verdict 分離。

### 3.2 非目標

- 不把 runner identity contract 變成 approval、dispatch、workflow control 或 scheduler。
- 不建立新的 Governance Graph node/edge、Graph write path 或 Memory Hub write path。
- 不把 Memory Hub、Memory Sidecar 或 Graph hints 當成 canonical evidence。
- 不把 Remote API 改造成 Local CLI，也不在本 Task 內遷移 Hermes transport。
- 不修改 SQLite、baseline、revenue scope、GMV、退款 ledger、正式 cache contents 或 export schema。
- 不新增外部 provider、認證系統、資料庫或常駐服務。

## 4. 設計決策

### 4.1 採用 canonical identity model + adapter

採用一個純 Python、無 network、無 SQLite dependency 的 identity model，所有既有 producer 透過 adapter 建立 canonical identity。identity model 只描述身分，不執行 runner、不驗證模型能力、不作 gate verdict。

Capability probe、provider credential、CLI cache compatibility 與 Hermes acceptance 仍由既有 runner-specific component 負責；它們只引用 canonical identity fingerprint。

### 4.2 保持既有 artifact parser 相容

`verification-v1` command payload、`review-report-v1`、`verification-session-v1` 與 Hermes 既有 exact schema 不直接塞入任意新 top-level fields。第一階段使用 bounded companion identity envelope：artifact 保存 `runnerIdentityFingerprint` 或受既有 schema 允許的位置，完整 identity 以同一 runtime root 下的 allowlisted companion artifact 保存。

若某個既有 schema 沒有可安全擴充的欄位，consumer 必須以「identity companion 缺失」回報 blocked/legacy，而不是把舊欄位猜測成 canonical identity。

### 4.3 Alias 只在輸入邊界解析

`provider`、`model` 與 display name alias 只可在 profile/command input boundary 解析一次；canonical output 永遠使用 normalized slug。未知 alias、大小寫以外的模糊匹配、同一 alias 對應多個 canonical model 均 fail closed。

## 5. Canonical `runner-identity-v1` contract

```json
{
  "schemaVersion": "runner-identity-v1",
  "runnerId": "stable-allowlisted-slug",
  "transport": "local_cli|remote_api|local_model",
  "provider": "normalized-provider-slug",
  "model": "normalized-model-slug",
  "profile": "execution-profile-slug",
  "executionEnvironment": "normalized-environment-slug",
  "identityFingerprint": "lowercase-sha256"
}
```

### 5.1 欄位語義

| 欄位 | 規則 |
|---|---|
| `schemaVersion` | 固定為 `runner-identity-v1`。 |
| `runnerId` | 穩定、allowlisted、opaque slug；不可由 command line 或 display name 臨時產生。 |
| `transport` | 只允許 `local_cli`、`remote_api`、`local_model`；不能填 model 名稱。 |
| `provider` | normalized provider slug；例如 provider wrapper 與模型供應者需按現有 producer 語義明確區分。 |
| `model` | normalized model slug；不得使用未解析的 display name。 |
| `profile` | 執行用途/profile，例如 Strict Review 或 Hermes capability profile；不是 transport。 |
| `executionEnvironment` | bounded environment identity，例如 local macOS、isolated verification profile 或 controlled remote endpoint class；不得保存 secret。 |
| `identityFingerprint` | 對前七個欄位的 canonical JSON 產生 SHA-256；不包含 token、API key、prompt、response 或完整 path。 |

### 5.2 Validation

- 所有欄位必須是 bounded non-empty string，enum/slug 使用既有 safe identifier 規則。
- exact key validation；未知 key、缺 key、null、空字串與 type mismatch 都拒絕。
- `identityFingerprint` 必須等於 canonical identity payload 的 deterministic fingerprint。
- `runnerId` 的 allowlist 由 tooling config 提供，但 config 本身不能讓 identity bypass transport validation。
- `executionEnvironment` 不得包含 credential value、完整 command、prompt、raw endpoint secret 或 business data。

## 6. 三種 transport 的 mapping

| Transport | identity 來源 | capability proof | 不可推導的欄位 |
|---|---|---|---|
| `local_cli` | approved runner profile + executable/model normalization | CLI version、cache schema、live probe、environment fingerprint | 不可由 executable basename 推導 provider 或 runnerId |
| `remote_api` | approved provider/client profile + endpoint class | credential presence marker、provider/model response、latency/provenance receipt | 不可由 URL 或 model display name 推導完整 identity |
| `local_model` | approved local model runtime profile | local runtime/model load receipt、model fingerprint、environment binding | 不可把 local model runtime 當成 remote API |

每個 transport 都必須產生同一種 canonical identity，但 capability evidence 可保持 transport-specific。`runner-identity-v1` 不宣稱 runner ready；ready/blocked 仍由 capability contract 判定。

## 7. Artifact binding

### 7.1 必須綁定的 artifacts

- Strict Review runner request/report：保存 `runnerIdentityFingerprint`，並與實際 command/profile 解析結果一致。
- Hermes manifest/receipt/evidence：保存 canonical identity 及其 fingerprint；保留既有 provider/model/reasoning 欄位作相容性驗證。
- `runner-capability-v1` receipt/evidence：以 identity fingerprint 補足現有 executable/model/cache/environment identity。
- Verification session：不改既有 source-seal semantics；以 companion identity reference 綁定 gate evidence。
- Runner/cache manifest：cache key 必須包含 identity fingerprint；identity 改變時不得命中舊 runner cache。

### 7.2 Binding rules

```text
source seal + task/brief identity
        + runner identity fingerprint
        + capability evidence fingerprint
        -> bounded gate artifact
```

- producer 寫出的 identity fingerprint 與 consumer 解析出的 fingerprint 不一致，回報 `blocked_runner_capability`。
- identity 相同但 executable/cache/environment 改變，既有 capability receipt 依現有 expiry fingerprint 失效，不能因 runnerId 相同而重用。
- identity 相同不代表 capability PASS；仍須有 fresh static/live proof。
- 舊 artifact 沒有 identity reference 時，只能標示 `legacy_identity_unbound`，不得補寫歷史 artifact 使其看似 fresh。

## 8. Legacy compatibility

相容讀取只允許明確 mapping：

- 舊 Local CLI `executable + model + cachePath`：若有 approved profile mapping，可轉成 `local_cli` identity；否則 blocked。
- 舊 Hermes `provider + model + reasoningProfile`：只有既有 fixed profile mapping 能完整決定 transport/profile/environment 時才可轉換。
- 只有 `model` 的 payload：不得猜測 transport、provider、runnerId 或 executionEnvironment。
- 舊 cache 只可被讀取作 migration diagnostic；新寫入不得沿用未綁定 identity 的 cache key。

Legacy mapping 必須在 output diagnostics 中指出缺少的 canonical 欄位與修復方式，不能靜默 default-on。

## 9. Error and security contract

統一錯誤分類：

| 情況 | 結果 |
|---|---|
| schema/key/type invalid | `invalid_evidence` 或 bounded input error |
| identity 欄位缺失或 alias 不可解析 | `blocked_runner_capability` |
| producer/consumer fingerprint mismatch | `blocked_runner_capability` |
| static capability 不通過 | `blocked_runner_capability` |
| live transport timeout、non-zero、invalid response | `blocked_runner_transport` |
| identity valid 但 token/provenance/latency guardrail 不合格 | capability acceptance rejected，不得轉成 PASS |

安全要求：

- 不保存 API key、完整 command、prompt、raw response、SQLite rows、Excel bytes 或完整 logs。
- diagnostics 只保存 bounded error code、缺少欄位、exit code、stderr tail/digest 等既有安全格式。
- identity artifact 必須位於 `.nbs_agent_runtime` allowlist，拒絕 symlink、path traversal、absolute escape 與非 regular file。
- canonical identity 不能由 Memory Hub、Memory Sidecar 或 Governance Graph hints 覆蓋。

## 10. 探索與治理邊界

本 spec 的探索使用：

- Local Context Agent：只做 evidence collection 與 bounded planning context；本輪 collection 回報 `contextOverflow=true`，因此不得把其完整 bundle 當成 spec authority。
- Memory Hub：提供 `memory-hints-v1`、最多 3 items/6000 bytes/800 ms 的 fresh non-authoritative hints，只用來提示既有 fail-closed、cache 與 evidence 邊界。
- Memory Sidecar：提供歷史 bounded AB/runner evidence；其 `blocked_runner_capability` 結果被保留為風險訊號，不作 capability PASS。
- Governance Graph：嘗試 read-only status/catalog validation；目前 catalog validation 為 `invalid`，並有 `snapshot_fingerprint_invalid`，所以不消費其內容作 canonical design evidence。

以上元件不得：

- 修改 canonical runner identity、正式 workflow state、approval、dispatch、SQLite、baseline、Git 或 application runtime。
- 建立新的 Graph relation 或由 memory 推測 runner authority。
- 將 hint、歷史 artifact 或 invalid Graph snapshot 轉成 fresh source-bound PASS。

## 11. 最小 implementation boundary

第一個 implementation Task 只包含：

1. 一個 pure `runner_identity` model/validator/normalizer。
2. Local CLI `RunnerProfile` 的 adapter 與 legacy mapping。
3. Hermes/runner capability evidence 的 identity reference adapter，不改 Hermes transport。
4. companion identity envelope 的 bounded writer/reader。
5. focused schema、legacy、mismatch、cache invalidation 與 fail-closed tests。

第一個 Task 不包含：

- 三種 transport 的完整 runtime migration；
- Hermes Remote API 改為 Local CLI；
- 新增 Local Model runtime；
- Verification session top-level schema rewrite；
- Governance Graph/Memory Hub/Memory Sidecar schema或write path；
- application、SQLite、baseline、GMV、退款或 export 改動。

## 12. Rollback

Rollback 必須是 tooling-only：

- 停用 canonical identity enforcement，保留舊 artifact read compatibility；或
- revert identity model/adapter/companion writer patch。

不得刪除 `.nbs_agent_runtime` evidence、重寫歷史 artifact、回退 SQLite、回退 baseline 或清除 cache/backup。若新 cache key 已產生，舊 key 仍只可在 identity compatibility 通過時讀取；否則 fail closed。

## 13. Verification and acceptance

### Targeted tests

- exact `runner-identity-v1` schema、slug/enum/type validation、fingerprint recomputation；
- Local CLI、Remote API、Local Model 三種 identity mapping；
- legacy artifact read 與未知 alias rejection；
- producer/consumer fingerprint mismatch；
- missing companion identity、cache invalidation、symlink/path escape；
- capability status 與 transport failure 的區分；
- no SQLite/baseline/Git/application runtime writes。

### Independent gates

1. targeted pytest + compile/static + `git diff --check`；
2. fresh Strict Review，確認 changed-surface、identity binding、findings-first；
3. full pytest；
4. Hermes read-only acceptance，確認 artifact identity、runtime、baseline 與 service state；
5. UI acceptance 只有在 UI surface 被改動時才適用，否則明確記錄 `not required / not run`。

所有 gate 必須使用當前 source-bound evidence；任何舊 PASS、invalid Graph snapshot、Memory hint 或 blocked Sidecar evidence 都不能代替 fresh gate。

## 14. Acceptance criteria

- 新寫入的 runner-related artifact 使用 canonical identity 或明確記錄 `legacy_identity_unbound`。
- 三種 transport 不再以 model name 互相冒充。
- identity mismatch 一律 fail closed，並指出缺少/衝突的欄位。
- capability receipt 仍能因 CLI/cache/environment 改變而失效。
- `verification-v1`、`verification-session-v1` 與既有 report parser 保持相容。
- 不改正式收入口徑「不含掛賬核銷與TT退款轉團款」及 2026-05 baseline `HKD 12,057,968`。
- 不新增 Governance Graph、Memory Hub、Memory Sidecar 的 authority 或 write path。

## 15. Implementation gate

本文件只代表 design/spec 交付，不代表 implementation authorization。開始跨檔 implementation 前，必須另建立單一 approved Task contract、明確 allowlist、fresh context evidence、isolated worktree 與對應 Review/verification gate。
