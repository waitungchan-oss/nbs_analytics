# Hermes CLI Transport Adapter Spec

狀態：Draft，供 implementation review

日期：2026-08-31

## 1. 目的

為 Hermes 建立一個明確、可驗證、可 fail-closed 的 Local CLI transport adapter contract，統一 Local CLI、Remote API、Local Model 在 runner naming、identity evidence、bounded invocation 與 receipt validation 上的介面語意。

此 spec 只定義 Hermes CLI transport 的邊界與 evidence contract；不在本 Task 實作 adapter、不改變正式業務狀態，也不改變現有 Remote API path。

## 2. 背景與問題

目前 `hermes_live_ab_runner.py` 負責 bounded child invocation 與 control/treatment receipt binding；`hermes_turn_receipt.py` 直接使用 OpenAI-compatible Remote API。兩者已有部分 timeout、output、receipt 與 identity 檢查，但 transport-specific command policy 尚未形成單一 contract，容易造成：

- 以 executable basename、model name 或環境變數推斷 runner identity。
- 不同 runner 對 argv、timeout、output limit、exit code 或 JSON event stream 的解讀不一致。
- capability failure、transport failure、identity mismatch 與 malformed evidence 被混成一般執行失敗。
- CLI stdout/stderr、prompt 或 secret 被寫入 receipt，令 evidence 不再 bounded 或可安全重播。
- 將 Memory Sidecar 或 Governance Graph 的非權威結果誤當成 runner readiness 或 acceptance proof。

## 3. 設計原則

1. `RunnerIdentity` 說明「誰／哪個 immutable runner」；transport adapter 說明「如何呼叫」。兩者不可互相推斷。
2. `RunnerIdentity` 使用既有 `runner-identity-v1` canonical fingerprint；不得另造 CLI-only identity hash。
3. CLI adapter 只接受 allowlisted executable、固定 argv policy、bounded timeout、bounded output 與明確 environment allowlist。
4. 所有無法可靠證明的狀態均 fail-closed；不得由 blocked 轉成 PASS。
5. adapter 只能產生 bounded transport evidence；canonical business artifacts、SQLite、baseline、revenue、formal scope、rollback、export schema 與 Git state 不由 adapter 寫入。
6. Local CLI、Remote API、Local Model 使用相同 identity field semantics，但 transport failure taxonomy 可按 transport 特性分開。
7. Strict Review、full pytest、Hermes、UI acceptance 仍是互相獨立的 gates；CLI transport receipt 不可取代任何 gate。

## 4. Scope

### In scope

- Hermes Local CLI 的 invocation、version/capability probe、bounded response normalization。
- CLI transport receipt 的 schema、identity/source binding、failure taxonomy 與 redaction policy。
- 與既有 `RunnerIdentity`、`runner_identity_envelope.py`、`hermes_live_ab_runner.py`、`hermes_turn_receipt.py` 的 compatibility boundary。
- CLI adapter 的 unit、fixture integration、security regression 與 deterministic verification requirements。

### Out of scope

- Remote API provider migration、API key、model routing 或 network policy 改動。
- Local Model runtime、model weights、inference server 或 provider installation。
- 新 Governance Graph、Memory Hub、Agent orchestration、approval、dispatch、workflow control。
- 任何 SQLite、baseline、formal revenue scope、GMV、退款、旅行團／票務人數或 export schema 修改。
- UI acceptance、dashboard 行為或正式業務資料回寫。

## 5. Canonical identity contract

CLI adapter 必須接收完整 `RunnerIdentity`，不得從 command line 重新組裝 identity。identity 至少包含既有欄位：

```text
schemaVersion
runnerId
transport = local_cli | remote_api | local_model
provider
model
profile
executionEnvironment
identityFingerprint
```

對 CLI，`runnerId`、`provider`、`model`、`profile`、`executionEnvironment` 必須先經既有 slug/schema validation；`identityFingerprint` 必須由 canonical identity fields 計算。CLI executable path、argv template、工作目錄與版本是 transport evidence，不得偷偷塞入 identity fingerprint；如需綁定，使用獨立 `commandShapeFingerprint` 或 `cliVersion`。

最低 identity acceptance：

- `transport=local_cli` 不得以 `remote_api` 或 `local_model` receipt 冒充。
- observed CLI version/model 若與 expected profile 不一致，結果為 `blocked_runner_capability`。
- receipt、turn input、activation envelope、source evidence 的 identity fingerprint 不一致，結果為 `invalid_evidence`。
- 舊 Hermes receipt 可由既有 legacy mapping 讀取；新 adapter 不得重寫或放寬舊 receipt validation。

## 6. Adapter boundary

建議的 provider-neutral interface（名稱可在 implementation plan 階段落實）：

```python
class HermesCliTransportAdapter(Protocol):
    def probe(self, request: CliProbeRequest) -> CliProbeResult: ...
    def invoke(self, request: CliInvokeRequest) -> CliInvokeResult: ...
```

### Request requirements

`CliProbeRequest` / `CliInvokeRequest` 必須包含：

- 已驗證的 `RunnerIdentity`。
- allowlisted executable 的 resolved path 與 immutable `commandShapeFingerprint`。
- approved profile、timeout、最大 stdout/stderr bytes、最大 response bytes。
- argv list；禁止 shell string、shell interpolation、`sh -c` 或未驗證的 user-supplied flags。
- environment allowlist；secret 只可由 child process runtime 讀取，不可進入 receipt 或 diagnostic。
- source/turn/manifest fingerprint，用於 evidence binding；不把完整 prompt 或 source content 寫入 transport receipt。

### Result requirements

result 必須明確區分：

- `ready`：probe/invoke 完成，response schema、identity 與 source binding 均通過。
- `blocked_runner_capability`：executable/profile/version/model/cache 或 required capability 無法證明。
- `blocked_runner_transport`：timeout、process launch failure、non-zero exit、output limit 或不可解析 transport response。
- `invalid_evidence`：receipt、identity、source binding、JSON schema 或 fingerprint 不一致。

result 可供 caller 暫存 bounded normalized response，但 artifact 只保存摘要與 fingerprint，不保存 raw prompt、raw response、完整 argv 或 secret。

## 7. Invocation lifecycle

1. **Preflight**：確認 executable 是 allowlisted regular file、非 symlink、可執行，並確認 profile 與 identity transport 一致。
2. **Probe**：以固定 argv 執行一次 version/capability probe；probe timeout、non-zero 或不符合 expected schema 均為 blocked，不得猜測版本。
3. **Invoke**：使用 `shell=False` 與 argv list；設定單次 bounded timeout、stdout/stderr byte cap、環境 allowlist 與 deterministic working directory。
4. **Normalize**：支援明確 allowlisted JSON 或 JSON event stream；只接受完整、可解析且符合 response schema 的結果。截斷、混入非預期文字或空 response 均不得標記 ready。
5. **Bind**：將 observed identity、source fingerprint、manifest/turn fingerprint、command shape fingerprint 與 response fingerprint 綁入 result。
6. **Record**：由既有 atomic artifact writer 寫入 bounded transport receipt；adapter 不直接寫 SQLite、正式 artifact 或 authoritative workflow state。
7. **Classify**：依第 6 節 taxonomy 回傳單一 terminal status；不自動 retry，不把 retry 結果與首次結果混在同一 receipt。

## 8. Proposed receipt schema

新增 schema 建議命名為 `hermes-cli-transport-receipt-v1`。第一版採 exact-field validation，最小欄位如下：

```text
schemaVersion
status
runnerIdentityFingerprint
sourceFingerprint
commandShapeFingerprint
cliVersion
observedModel
exitCode
timedOut
stdoutDigest
stderrDigest
responseFingerprint
startedAt
finishedAt
diagnostics
stdoutBytes
stderrBytes
stdoutTruncated
stderrTruncated
receiptFingerprint
```

規則：

- `diagnostics` 只容納 bounded、redacted、machine-readable codes；不得包含 secret、prompt、完整 response 或完整 command。
- stdout/stderr 只保存 digest、byte count、truncation flag；若需 debug，另存受控 ephemeral output，且不得被視為 acceptance evidence。
- `exitCode`、timing、byte counts 有明確非負上限；timeout 時 `timedOut=true` 且 status 不得是 `ready`。
- receipt 必須以 identity/source/command shape fingerprint 做 source binding，並通過既有 symlink、regular-file、atomic-write 規則。
- `receiptFingerprint` 必須由其餘 exact fields 的 canonical payload 計算；任何欄位變更均須被 validator 拒絕。
- receipt schema 不宣稱模型品質、token economics、provenance coverage 或 Review/Hermes PASS；那些是上層 gate 的獨立 evidence。

## 9. Security and hardening requirements

- 永遠 `shell=False`；argv 每一項均須 bounded 且通過 allowlist，避免 command injection。
- executable、working directory、output path 均須是 project/runtime allowlist 內的 regular path；拒絕 symlink、path traversal 與任意絕對路徑。
- child environment 使用 explicit allowlist；不得把整個 parent environment 傳入。
- timeout 後終止 child process group，且不得留下可繼續寫入 receipt 的 orphan process。
- stdout/stderr/JSON/event stream 必須有 byte cap；解析前後均做 cap check，避免 oversized output / memory exhaustion。
- 不做 implicit retry；如 future spec 需要 retry，必須產生不同 attempt identity 並另行驗證。
- secrets 不出現在 argv、receipt、logs、Graph projection、Memory Hub hint 或 Sidecar telemetry。
- CLI adapter 不可啟動 Gateway、provider installation、network recall、memory distillation、prune、apply、approval、dispatch 或 workflow state mutation。

## 10. Governance Graph、Memory Hub、Memory Sidecar boundary

- Governance Graph 僅可讀取 canonical/integration evidence，作為 impact/lineage 的 read-only projection；Graph `blocked` 不得被 adapter 轉譯為 runner ready，也不得觸發 build 或回寫。
- Memory Hub 僅提供 bounded `memory-hints-v1` context；遵守最多 3 items、6000 bytes、800 ms。timeout、stale、malformed、permission denied 或 over-cap 必須 fallback 到 canonical evidence。
- Memory Sidecar report 必須維持 `policy=read-only`、`invocations=0`、`writes=0`。sidecar hints 不可作為 CLI capability proof、approval 或 acceptance proof。
- adapter 本身不依賴 Graph/Hub/Sidecar 才能完成 transport validation；缺失時仍須以 canonical identity、profile、probe 與 receipt validation 決定結果。

## 11. Failure matrix

| 情境 | 結果 | 是否可作 ready evidence |
|---|---|---|
| executable missing / not executable | `blocked_runner_capability` | 否 |
| version probe timeout / schema mismatch | `blocked_runner_capability` | 否 |
| model/profile/identity mismatch | `blocked_runner_capability` | 否 |
| process launch error / non-zero exit | `blocked_runner_transport` | 否 |
| timeout / output cap exceeded | `blocked_runner_transport` | 否 |
| malformed JSON/event stream | `blocked_runner_transport` | 否 |
| receipt/source/identity fingerprint mismatch | `invalid_evidence` | 否 |
| valid bounded response and all bindings match | `ready` | 僅可作 CLI transport evidence |

任何非 `ready` 狀態都必須保留具體 machine-readable reason，並不得由 caller 自動降級成 PASS。

## 12. Compatibility and migration

採 additive migration：

1. 先新增 adapter contract、schema validator 與 deterministic fixture；不改現有 Remote API invocation。
2. 以 feature flag 或明確 caller selection 啟用 Local CLI；預設維持現有 path。
3. `hermes_live_ab_runner.py` 只在明確選用 CLI transport 時呼叫 adapter；control/treatment 的既有 manifest、activation receipt 與 source binding 規則維持不變。
4. `hermes_turn_receipt.py` 維持 Remote API provider path；未來若共用 protocol，只抽象共同 validation，不把 transport 行為混合。
5. 舊 artifact 不重寫、不刪除、不重新宣稱 PASS；新 receipt 以新 schema 與新 identity evidence 建立。

## 13. Verification plan

### Unit tests

- identity/transport mismatch、canonical fingerprint、exact receipt schema。
- argv allowlist、`shell=False`、environment allowlist、path/symlink rejection。
- probe success/failure、version mismatch、timeout、non-zero exit、malformed JSON/event stream。
- stdout/stderr/response byte caps、redaction、no-secret capture、no implicit retry。
- source/manifest/turn binding、receipt tamper detection、atomic write/read。
- Graph/Hub/Sidecar unavailable、stale、over-cap 時 canonical fallback。

### Fixture integration

使用 repository-local fake CLI executable，不連外、不啟動 model provider、不改 SQLite；驗證完整 probe → invoke → normalize → receipt lifecycle，並覆蓋 child process group cleanup。

### Independent gates

- Strict Review：依 `REVIEW_AGENT_CONTRACT.md` findings-first，使用 fresh source-bound evidence。
- full pytest：報告完整 suite 結果，不以 focused tests 取代。
- Hermes：依 `NBS_HERMES_MONITORING.md` 執行獨立 post-change check；CLI receipt 不等於 Hermes PASS。
- UI acceptance：只有本 Task 實際影響 UI 時才啟動，且不由 transport evidence 取代。

Acceptance 必須同時證明：正式 scope 仍為「不含掛賬核銷與TT退款轉團款」、2026-05 frozen baseline 仍為 `HKD 12,057,968`，且沒有 authoritative write path 變更。

## 14. Rollback

- 關閉 CLI adapter feature flag，恢復既有 caller/Remote API path。
- 保留已產生的 bounded receipt 作為歷史 evidence，但將失敗或未驗證 receipt 維持 non-authoritative。
- 不刪除、不覆寫舊 receipt，不回滾或修改 SQLite、baseline、正式 artifact、Graph 或 Memory state。
- 若發現 security regression，立即停用 CLI invocation，保留 deterministic fixture 與 failure evidence，待修正後重新通過全部獨立 gates。

## 15. Implementation guardrail

下一個 implementation Task 應限於：新增 transport contract/validator、fake CLI fixture、focused tests 與必要的最小 caller wiring；不得順手進行 Remote API migration、runner orchestration、Governance Graph build、Memory write 或業務資料修正。實作前仍需另立 implementation plan、明確 allowed-files 與 verification commands。

## 16. Evidence note

本 spec 的探索證據包括：

- 現有 `RunnerIdentity` / envelope contract 與 `hermes_live_ab_runner.py` 的 bounded child invocation。
- `hermes_turn_receipt.py` 的 OpenAI-compatible Remote API boundary。
- `MEMORY_SIDECAR_CONTRACT.md` 的 non-authoritative、read-only、bounded fallback 規則。
- Local Context Agent 的 `context-evidence-v1`；該次 brief 為舊 strict-review context 且標示 `contextOverflow=true`，因此僅採用其與現行 contract 一致的 guardrails，不把它當作 current PASS evidence。
- Governance Graph status 本輪為 `blocked`：workflow artifact 非 regular file；未執行 Graph build，也未把 Graph 結果當作 spec approval 或 readiness proof。
