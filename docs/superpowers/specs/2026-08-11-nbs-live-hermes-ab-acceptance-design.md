# NBS Live Hermes A/B Acceptance：Isolated DeepSeek Runner Design Spec

## Status

- Status: approved for plan creation
- Date: 2026-08-11
- Scope: 以一次性 isolated `HERMES_HOME` 完成真實 `recall_off`／`recall_on` A/B evidence

## Goal

在不修改全域 Hermes 設定、不暴露或保存 credential、不啟用普通開發流程 recall 的前提下，讓兩個真實 DeepSeek V4 Flash Max model turns 產生可驗證的 control／treatment receipts，並由既有 validator 判定 Live A/B PASS、REJECTED 或 BLOCKED。

## Non-goals

- 不修改 `~/.hermes/config.yaml`、`~/.hermes/plugins`、正式 SQLite、baseline、Graph authority 或 Git history。
- 不將 recall 設為普通 workflow 預設，也不新增 approval、dispatch、runtime state write 或自動 snapshot。
- 不把 prompt、raw output、API key、完整 logs 或 customer data 寫入 receipt。
- 不以 fixture、probe、UI token counter 或模型自述代替真實 model-turn receipt。
- 不在 credential 缺失、provider identity 不符、usage 缺失或 provenance 不完整時宣稱 PASS。

## Acceptance vocabulary

- `ready`: control 與 treatment 均有真實、完整、canonical-bound receipt，且所有 A/B gates 通過。
- `acceptance_rejected`: 兩個 receipt 都是真實且完整，但 token reduction、latency 或 safety gate 未達門檻。
- `blocked_runner_capability`: 任一真實回合無法執行或無法產生完整 evidence，例如 credential 缺失、live endpoint 不可用、usage／provenance／activation receipt 缺失。

## Architecture

### 1. Isolated runtime profile

每次 acceptance 建立唯一 ignored runtime root：

```text
.nbs_agent_runtime/live-ab/<acceptance-id>/
├── hermes-home/
│   ├── config.yaml
│   └── plugins/nbs_sidecar/
├── control/turn-input.json
├── treatment/turn-input.json
├── control/receipt.json
├── treatment/receipt.json
└── comparison.json
```

`hermes-home` 只包含本次受控 session 所需的 sidecar plugin 與最小 Hermes config。runner 透過 process-local `HERMES_HOME` 指向該目錄；全域 Hermes home 永遠只讀。

### 2. Credential boundary

runner 只接受明確 allowlist 的 `DEEPSEEK_API_KEY` 與 `DEEPSEEK_BASE_URL` environment variables；本 phase 不支援 protected file descriptor，避免兩套 transport 造成不同 inheritance／redaction 語義。credential：

- 不寫入 config、manifest、receipt、logs 或 Git。
- 不在 stdout、stderr、報告或 exception message 出現。
- 只在單次 child process 存活期間存在，process 結束即釋放。
- 缺少任一 credential 時直接產生 bounded blocked evidence，不進行假 call。

### 3. Immutable A/B identity

control 與 treatment 必須共用：

- full immutable Git HEAD SHA
- clean worktree fingerprint
- project、workspace、task、brief、allowed-files、commands fingerprints
- provider `hermes`
- model `deepseek-v4-flash`
- UI reasoning profile `max`（DeepSeek V4 Flash Max）；`max` 必須在 manifest、plugin envelope、turn input、receipt、validator 與 comparison 全鏈路一致，禁止任何 medium fallback。
- identical bounded query and sourceRefs

唯一允許差異為：

```text
control: recallMode=off, sequence=1
treatment: recallMode=on, sequence=2
```

### 4. Real receipt contract

每個 receipt 必須由 isolated Hermes child process 的真實 response 產生，至少包含：

- actual prompt／completion token usage
- measured wall-clock latency
- response identity and replay check
- sourceRefs expected／covered counts and derived provenance coverage
- sensitive capture scan result
- canonical activation receipt bound to manifest, run, session and recall mode；control 使用 `status=disabled` 的 canonical receipt，treatment 使用 `status=activated`，兩者都必須通過 identity binding
- immutable identity and safety flags

`cacheReplayDetected` 必須由 response ID 與 bounded prior-response set 實際比對；不得寫固定常數。每個 manifest 必須包含 `cleanWorktreeFingerprint`，record 在寫 receipt 前重新驗證 HEAD、clean status 與 fingerprint；任一不符即 exit 2。缺少 usage、response ID、source coverage、activation receipt 或 safety flag 時，producer exit 2 且不寫 completed receipt。

### 5. Read-only comparison

共用既有 `compare_capability_runs`，只讀取兩個完成 receipts，計算：

- input token reduction
- output token delta
- p95 latency delta
- provenance coverage
- sensitive capture / replay state
- baseline、formal scope、Review、Hermes regression flags

comparison service 不會改變 recall flag、workflow state、SQLite 或 canonical artifacts。

## Runner flow

1. 驗證 immutable HEAD、clean worktree 與 declared fingerprints。
2. 建立 isolated `HERMES_HOME`，複製受控 sidecar plugin 與最小 config。
3. 檢查 credential 只存在於 process environment；本 phase 不支援 protected FD，避免兩套 transport 產生不同 redaction／inheritance 語義。
4. 執行 control `recall_off` 真實 turn，收集 usage、latency、response identity 與 provenance evidence。
5. 以相同 identity 執行 treatment `recall_on` 真實 turn。
6. 產生兩份 canonical-bound receipts。
7. 交給 read-only comparison service。
8. 執行 Hermes read-only acceptance；只回報 `ready`、`acceptance_rejected` 或 `blocked_runner_capability`。

## Safety and failure handling

- isolated profile 建立失敗、copied plugin checksum 或 Hermes loader discovery 不符：`blocked_runner_capability / isolated_home_unavailable`
- credential 缺失或 endpoint 不可達：`blocked_runner_capability / live_identity_missing`
- model／reasoning identity 不符：`blocked_runner_capability / model_identity_mismatch`
- 任一 usage／latency／provenance／activation receipt 缺失：`blocked_runner_capability / completion_missing`
- control／treatment immutable identity 不一致：`blocked_runner_capability / identity_mismatch`
- control 沒有 `status=disabled` receipt 或 treatment 沒有 `status=activated` receipt：`blocked_runner_capability / activation_state_missing`
- receipts 完整但門檻未達：`acceptance_rejected`，recall 仍維持 off
- 任一 raw secret、raw output 或不受控 path 嘗試：立即停止並不寫 completed evidence

## Acceptance gates

Live A/B 只有同時滿足以下條件才是 `ready`：

1. control／treatment 都是實際 DeepSeek model turns。
2. provider、model、reasoning profile 與 runner identity 可由 evidence 驗證。
3. 兩個 receipts 綁定同一 immutable HEAD 與 fingerprints。
4. 兩個 receipts 綁定同一 `cleanWorktreeFingerprint`；record 階段重新驗證通過。
5. token usage、latency、response identity、provenance coverage 均來自真實 runner。
6. `provenanceCoverage=1.0`、`sensitiveCaptureCount=0`、`cacheReplayDetected=false`。
7. control disabled／treatment activated 的 activation receipts canonical fingerprint 驗證通過。
8. comparison 明確輸出 input reduction、output delta、p95 latency delta；reduction／latency 門檻通過。
9. Review、Hermes、baseline、formal scope 與 writer-disabled flags 無 regression。

## Verification

- 每個 implementation Task：TDD focused tests、`py_compile`、`git diff --check`。
- A/B runner：只在 isolated runtime 中執行；不得觸碰全域 Hermes home。
- 完整 acceptance：既有 runner validator、read-only comparison、Hermes post-change check。
- 若任何 gate blocked，報告實際錯誤與最小下一步，不宣稱完成。

## Rollback

刪除 ignored isolated runtime root 或停止 runner 即可 rollback。production `recall_enabled=false`、`writer_enabled=false`、`shadow_mode=true` 永遠不變。
