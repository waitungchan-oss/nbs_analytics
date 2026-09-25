# Acceptance Parallel Rollout Runbook

## Boundary

這是一條 opt-in、diagnostic-only 的 Full pytest parallel candidate 路徑。
正式 release authority 永遠維持：

```text
serial Full pytest + Hermes + UI acceptance + formal release aggregate
```

候選 artifact 必須保持：

```text
authority=diagnostic
formalReleaseEnabled=false
rolloutMode=parallel_candidate
populationKind=full-pytest-nodeid
```

獨立的 `agent-eval-72-slot` 是 Agent／Memory 品質評估 population，不能出現在
Full pytest coverage 或 speedup denominator。72-slot 的 12 cases × 3 repeats ×
2 cohorts 必須另行報告，不能與 pytest nodeids 混成同一個 population。

## Source-bound sequence

所有步驟都使用同一份 fresh source seal、contract、manifest、runner、environment
與 dataset lineage；不要重用歷史 PASS、cache hit 或 Memory hint。

```text
fresh source seal
  -> contract
  -> full-pytest manifest
  -> serial control
  -> concurrent isolated shards
  -> exactly-once aggregate
  -> parity
  -> speedup/stability
  -> diagnostic rollout artifact
  -> Hermes read-only boundary check
```

每個 rollout run 需要獨立且尚未存在的 fixture root、SQLite path、cache path、port
與 process namespace。Artifact output root 亦須以 exclusive-create 建立；既有或 symlink
目錄必須在啟動 workers 前拒絕。Artifact 寫入失敗時，保留已寫入檔案作診斷，回傳帶寫入數量的
`BLOCKED` 結果，不得產生 aggregate 或 speedup evidence。

Timeout 或 sibling failure 時，controller 可終止 active process groups，但不可在 shard worker
仍執行時呼叫 runtime cleanup。Cleanup 只有一個 owner：worker 結束後才可確認一次；若 bounded
window 內無法 join worker，必須回報 cleanup unconfirmed 並維持 `BLOCKED`。

parallel wall-clock 從所有 pytest worker 完成 READY、readiness barrier
完成後，第一個 worker 即將收到 `START` 前的共同 boundary 起，計至最後一個 worker 完成執行與清理的 critical path，不是 shard durations
的總和。Serial 同樣從 READY/START 起計；source probe、fixture preparation、collection、
結果序列化不計入此執行時間。失敗時不自動 retry、改變
population 或改寫 threshold。

每次 measured parallel run 必須同時提供完整 execution lineage
（runner/environment/dataset fingerprints）與由 live worker capacity probe 產生的
`acceptance-runner-capability-v1` receipt。Receipt 綁定 `runnerFingerprint`，並以
canonical fingerprint 封存 `maxWorkers`；執行時重新以 OS CPU affinity／CPU count 上限驗證，
不接受 caller 自報 worker 數。`shardCount` 超過 live capacity 或缺少／錯綁 receipt 時立即
BLOCKED，不能產生可比較的 speedup evidence。

### Platform qualification

The `reserved-fd-v1` parallel launcher is qualified for POSIX macOS and Linux
runners only; the opt-in GitHub rollout currently selects `macos-14`. Windows
is explicitly unsupported because this handoff depends on inheriting reserved
listening file descriptors. The CLI must select no parallel runner on an
unsupported platform and emit a bounded `BLOCKED` artifact with
`failureCode=unsupported_platform`; it must not fall through to the low-level
launcher or report a fabricated timing value. Windows support requires a
separate socket-handoff design and its own platform-specific verification.

CI runner identity fingerprints cover the resolved `pip freeze --all` package set,
`requirements.txt`, Python/platform identity, and GitHub runner image metadata
(`ImageOS`/`ImageVersion` plus macOS version). The source-seal base for workflow
dispatch is the merge-base of the selected commit and `origin/main`, so a multi-commit
branch is not silently reduced to its final commit's parent.

Execution child 使用 `reserved-fd-v1` handoff：parent 將每個 reserved listening socket
以 bounded `name=fd:port` metadata 傳入 child，child adoption 後核對 loopback endpoint
identity，並透過 `READY`／`START` handshake 等 controller 收齊所有 child readiness
後才啟動 pytest；不得重新 bind 同一組已被 handoff 的 ports。Child 發出 `READY` 後，
qualified runner 才會在 child 已接管的 socket 邊界執行顯式 readiness probe，probe
通過後才發出 `START`；不得以 parent 尚未交給 child 的 reservation 狀態預判 ready。
Child-side service
若需要 endpoint，必須透過 `scripts.full_pytest_shard.activated_socket()`
消費 inherited listening socket，由 wrapper 在 pytest 結束時統一關閉。

此處 readiness 證明 pytest worker 已接收及驗證隔離的 socket namespace，
不是 Streamlit／MCP／health 網站已提供 HTTP 服務。本 benchmark 不啟動三個正式服務；
三項服務的 readiness 仍由獨立 Hermes／UI acceptance 驗收，不得以 worker READY 取代。
因此，不會在此 barrier 啟動或等待 Streamlit、MCP 或 health 服務；需要這些 endpoint
的測試只能在 START 後由其測試流程啟動或驗證服務。若產品驗收要求服務在 pytest 開始前已
健康，必須另行修訂 benchmark spec 與計時邊界，不能把這項要求隱含加入目前 worker barrier。

Qualified runner 必須從 clean、source-bound checkout 執行：`git status --porcelain`
必須沒有正式 source 變更，且 current `HEAD` 與 commit/source seal 必須完全一致。
若 runner 看到 dirty worktree 或 identity mismatch，serial control 會回傳
`BLOCKED`（`serial_source_dirty`／`serial_source_identity_mismatch`），不啟動 benchmark；
這是刻意的 fail-closed 前置條件，不是可用目前工作樹硬跑的 fallback。

Local inline diagnostic 若必須從尚未 commit 的 implementation worktree 執行，唯一例外是
明確提供 `--source-seal <verification-session-v1.json>`（或等價的
`source-seal-v1`）並通過 `headSha`、`sourceFingerprint` 與 live
`worktreeFingerprint` 的 exact match。這只允許同一份已 seal source 觀測，不會把任意
dirty worktree 視為可信，也不改變 CI 的 clean checkout 或 formal release authority。
其中 `sourceFingerprint` 由 `VerificationSession.source_fingerprint` 從正式 session.json
計算；session.json 本身沒有此欄位，不得手工添加。它不等同
於 `git archive HEAD` 的 archive hash。rollout 現在要求 source seal；即使 worktree clean，
未提供 source seal 也會以 `serial_source_seal_required` fail closed，不使用 archive hash
作為可執行 fallback。CI 的 diagnostic manifest 與 source seal 必須先採用同一 identity
contract，不能把 archive hash 與 canonical session fingerprint 混在同一份 rollout artifact。
必須同時提供 `--source-session` 作為 expected identity；執行前以及 serial/parallel 交界、
parallel 結束後重新 probe HEAD、brief、diff、worktree、Review contract 與 policy。
任一欄位漂移立即 BLOCKED，不能發布比較值。

## Speedup and stability

每次比較同一份 serial control 與 parallel candidate：

```text
speedupMultiple = serialWallSeconds / parallelWallSeconds
speedRatio      = parallelWallSeconds / serialWallSeconds
```

候選必須有 exactly 3 個 measured runs；每次 `speedRatio <= 0.80`，且 median
`speedupMultiple >= 1.25`，並通過 serial parity、完整 nodeid coverage 與 artifact
lineage validation。任何 mismatch、缺 shard、污染、fixture collision、FAIL 或
BLOCKED 都必須 suppress speedup，產生 `rolloutCandidate=ineligible`。
門檻使用未四捨五入數值；六位小數只供輸出顯示，非有限或超出上限的比率不能參與比較。
Performance v2 跨基線比較的 `totalSpeedRatio` 與 `totalSpeedupMultiple` 固定輸出六位小數；若任一指標四捨五入後變成零，comparison 回傳 `not_compared`／`speedup_metric_precision_lost`，不輸出倍率。

## Hermes and memory boundaries

Hermes 只 read-only 驗證已產生的 `acceptance-parallel-rollout-v1` artifact：schema、
authority、formal flag、population、lineage 與 canonical evidence fingerprint。呼叫端
必須另外提供同一份 sealed source/session lineage；artifact 自己宣稱的 commit、source、
contract、manifest、runner、environment、dataset 與 selection 欄位不能作為 expected
lineage。缺少或不一致時，驗證必須 fail closed。
Hermes 不啟動 pytest、不寫 artifact、不改 lifecycle、不改正式 release state。

Memory Hub、Memory Sidecar、Governance Graph 與 Agent Operations 只能提供 bounded、
read-only、non-authoritative context；它們 cannot approve、不能 dispatch、retry、promote 或
替代 fresh serial／parallel execution。

Strict Review batch result 的 `reviewFingerprint` 由 Review runtime 根據實際送出的 request
確定性綁定；模型輸出只提供 verdict/findings 等判斷欄位，不負責手工抄寫長 fingerprint。每批仍
綁定同一 sealed session、batch fingerprint 與 source fingerprint；aggregator 必須確認所有
planned batches 完整且唯一後才可產生 verdict。

## Rollback

發現 runner、fixture、coverage、parity、stability 或 Hermes 問題時，立即關閉這條
手動 rollout；serial release path 不需修改：

```bash
gh workflow run release-gates.yml -f enable_acceptance_parallel_rollout=false
```

parallel candidate 的 artifact 不得送進 `full-pytest-gate-v1` 或
`release-gate-result-v1`，也不代表 formal release PASS。
