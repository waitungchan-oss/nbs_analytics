# NBS Hermes Runner Capability Hook：受控 live A/B Design Spec

## Status

- Status: approved bounded task
- Date: 2026-08-10
- Scope: 在既有 runner-capability evidence validator 之前補上可受控的執行／receipt 邊界

## Goal

建立一個不會被普通開發流程自動呼叫的 Hermes runner hook，讓受控操作者在同一 immutable HEAD 下分別執行 `recall_off` 與 `recall_on`，並輸出可被既有 `runner_capability_evidence` validator 消費的 bounded live receipt。

## Non-goals

- 不修改 `recall_enabled=false`、`writer_enabled=false`、`shadow_mode=true` 的 production 預設。
- 不把 hook 變成 approval、dispatch、workflow control、SQLite、baseline、Git write 或 canonical evidence 入口。
- 不接受 prompt 自述作為 recall 已啟用證據；沒有 activation receipt 就輸出 `blocked_runner_capability`。
- 不執行 network、任意 shell、任意絕對路徑或未 allowlist 的 command。

## Design

新增 `scripts/hermes_runner_capability_hook.py`，提供兩個明確子命令：

1. `prepare`：read-only 驗證目前 HEAD 等於指定 full SHA，計算 task／brief／allowed-files／commands fingerprints，產生 bounded run manifest。manifest 明確記錄 `recallMode`、`sequence`、provider/model、workspace fingerprint 與 writer disabled。
2. `record`：只接受同一 manifest 與 Hermes 產生的 bounded completion receipt；驗證 session/run identity、model、recall mode、completion、token counts、latency、provenance、cache replay 與安全 flags，輸出既有 `capability-input.json`。缺少真實 activation receipt 時，receipt 必須是 `blocked_runner_capability`，不可自行升級為 ready。

Hook 只允許宣告的 local runner adapter（`hermes-desktop`）與固定的 read-only probe command。它不會自行啟動第二次 cohort、不會修改 config；兩次執行必須由操作者在 Hermes UI 以 `deepseek-v4-flash` medium 明確發起。

## Data boundary

所有輸出只保存 bounded metadata：run id、sequence、mode、immutable fingerprints、provider/model、status、token、p95、coverage、sensitive count、cache flag、writer/baseline/formal-scope flags。禁止 raw prompt、raw output、credentials、absolute path、full logs、customer data 與 raw hints。

## Acceptance

- `prepare` 拒絕 branch name、縮寫 SHA、HEAD mismatch、未知 mode、非 allowlisted runner 與危險 command。
- `record` 拒絕 receipt fingerprint mismatch、model/provider mismatch、sequence/run reuse、cache replay、缺 token、缺 activation receipt 或不一致 immutable inputs。
- 真實 `recall_off` 與 `recall_on` receipt 才能交給既有 validator；任何不完整狀態保持 `blocked_runner_capability`。
- focused pytest、py_compile、git diff check 通過；不執行 Hermes post-change check。

## Rollback

刪除 ignored runtime receipt 即可回退；production recall 旗標永遠維持 off。Hook 不寫正式 SQLite、baseline 或 Git。
