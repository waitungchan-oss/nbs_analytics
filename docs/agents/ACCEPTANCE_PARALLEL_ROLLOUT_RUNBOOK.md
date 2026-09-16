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
與 process namespace。parallel wall-clock 是第一個 child launch 到最後一個 child
completion 的 critical path，不是 shard durations 的總和。失敗時不自動 retry、改變
population 或改寫 threshold。

Execution child 使用 `reserved-fd-v1` handoff：parent 將每個 reserved listening socket
以 bounded `name=fd:port` metadata 傳入 child，child adoption 後核對 loopback endpoint
identity，並透過 `READY`／`START` handshake 等 controller 收齊所有 child readiness
後才啟動 pytest；不得重新 bind 同一組已被 handoff 的 ports。Child-side service
若需要 endpoint，必須透過 `scripts.full_pytest_shard.activated_socket()`
消費 inherited listening socket，由 wrapper 在 pytest 結束時統一關閉。

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
其中 `verification-session-v1.sourceFingerprint` 是 canonical session identity；它不等同
於 `git archive HEAD` 的 archive hash。未提供 source seal 時，serial control 才使用 archive
identity 做 clean-worktree 檢查。
CI 的 diagnostic manifest 也以 `git archive HEAD` hash 作為自己的
`sourceFingerprint` contract；兩者不可在同一 artifact 中混用。

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

## Hermes and memory boundaries

Hermes 只 read-only 驗證已產生的 `acceptance-parallel-rollout-v1` artifact：schema、
authority、formal flag、population、lineage 與 canonical evidence fingerprint。
Hermes 不啟動 pytest、不寫 artifact、不改 lifecycle、不改正式 release state。

Memory Hub、Memory Sidecar、Governance Graph 與 Agent Operations 只能提供 bounded、
read-only、non-authoritative context；它們 cannot approve、不能 dispatch、retry、promote 或
替代 fresh serial／parallel execution。

## Rollback

發現 runner、fixture、coverage、parity、stability 或 Hermes 問題時，立即關閉這條
手動 rollout；serial release path 不需修改：

```bash
gh workflow run release-gates.yml -f enable_acceptance_parallel_rollout=false
```

parallel candidate 的 artifact 不得送進 `full-pytest-gate-v1` 或
`release-gate-result-v1`，也不代表 formal release PASS。
