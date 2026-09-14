# Acceptance Pipeline Hardening Runbook

## 目的

本 runbook 說明如何在 source-bound session 上完成驗收，以及如何判讀「驗證完成」和「可 release」的差異。它不降低 Strict Review、Full pytest、Hermes、UI acceptance 或 deterministic aggregate 的門檻。

正式業務口徑仍是「不含掛賬核銷與 TT 退款轉團款」；2026-05 frozen baseline 仍為 `HKD 12,057,968`。

## 最短操作路徑

1. 以目前 source seal 建立或載入 verification session。
2. 先跑 `run-preflight`，再跑 `run-review`、`run-full`、`run-hermes`。
3. 用 `attest` 取得 `completion-attestation-v1`。`complete` 只表示 `verification_complete`，不是 release PASS。
4. 由三個同一 `commitSha`／`sourceFingerprint` 的 child artifacts 產生 `release-gate-result-v1`。
5. 需要操作員查看狀態時，使用：

   ```bash
   python scripts/verification_chain.py status --session <session-id>
   python scripts/release_gate.py stage --session <status-or-session-json> --release <release-gate-result.json>
   ```

只有 stage 為 `release_ready`，且 aggregate `status` 為 `PASS`，才可對外宣稱 release PASS。

## Retry 與 cache 規則

- Review batch 只在 session、batch、source、patch、request fingerprint 與 result fingerprint 都一致時命中 cache。
- cache hit 仍會做 bounded identity／coverage validation；不會以 Memory Hub、Memory Sidecar、Governance Graph 或歷史 PASS 取代 fresh gate。
- Review runner 的 transport failure 最多執行兩次 transport attempt；每次都寫入 session-bound `review/batches/<sessionId>/attempts.json`。
- `cacheHit`、`outcome`、source／runner fingerprint 和時間只作 telemetry，不會覆寫既有 PASS evidence。
- `blocked_runner_capability`、`stale_source`、`blocked_source_probe` 不應盲目 retry；先修復 capability 或重新 seal。

## 失敗分類

| 狀態 | 處理 |
|---|---|
| `blocked_runner_transport` | source 未變時最多重試兩次；仍失敗則保留 attempts，重新確認 runner transport。 |
| `blocked_runner_capability` | 修復 executable、profile、cache 或 model capability，必要時建立新 session。 |
| `blocked_source_probe` | 透過 project-bound CLI 重新載入，不能把歷史 seal 當 fresh。 |
| `stale_source` | HEAD、brief、dirty file 或 diff 改變；重新 seal，舊 evidence 不得沿用。 |
| `review_changes_required`／`verification_failed` | 保留 failure evidence，修正 source 後建立新 session。 |
| 72-slot contamination | report 必須 `invalid`，paired comparisons 不得有 token delta；不要修改 baseline 或資料來湊 PASS。 |

## CI 效率邊界

- PR 以 PR number 分組並取消被新 commit 取代的 in-progress run；release tag run 不取消。
- Full pytest、Hermes、UI acceptance 各自執行並以 `github.sha` 隔離 artifact。
- child jobs 使用 setup-python dependency cache；aggregate 只用最小 Python runtime 讀取三份 bounded JSON，不重裝完整 `requirements.txt`。
- aggregate 以 `if: always()` 收集 child 結果；缺少 artifact 或 identity 不一致時保持 fail closed。

## 本地 Fast Lane（只作開發回饋）

變更後可先用 advisory precheck 縮短回饋時間：

```bash
python scripts/fast_acceptance_precheck.py \
  --project-root . \
  --base-ref origin/main \
  --output .nbs_agent_runtime/fast-precheck.json
```

輸出 schema 是 `acceptance-fast-precheck-v1`，固定帶有 `authority=advisory` 與
`fullGateRequired=true`。它只選取受影響的 deterministic tests；即使回傳 `PASS`，也不能取代
Full pytest、Hermes、UI acceptance 或 release aggregate。

## Manifest 與 shard prototype 邊界

要觀察 Full pytest 的 collection 成本，可先產生 source-bound manifest：

```bash
python scripts/pytest_manifest.py \
  --project-root . \
  --commit-sha "$(git rev-parse HEAD)" \
  --source-fingerprint <source-seal-sha256> \
  --output .nbs_agent_runtime/pytest-manifest.json
```

`pytest-test-manifest-v1` 只是 nodeid selection evidence，不代表測試通過。`full-pytest-shard-v1`
與 `full-pytest-shard-aggregate-v1` 目前是 opt-in、diagnostic-only prototype，必須使用每個 shard
獨立且尚未存在的 temporary fixture root；aggregate 會檢查同一 source／manifest identity、完整
shard index，以及每個 nodeid exactly once。任何 FAIL、BLOCKED、duplicate、missing 或 unknown
nodeid 都必須 fail closed。

在 fixture isolation audit、controlled benchmark 與 branch-protection rollout review 完成前，正式
Full pytest 維持 serial；prototype 不可輸出 `full-pytest-gate-v1`，也不可被
`release_gate.py` 消費成正式 PASS。

Memory Hub、Memory Sidecar、Governance Graph 和 Agent Operations 只能提供 bounded、read-only、non-authoritative context；它們不能批准、dispatch、改寫 session、改寫正式資料或把 blocked 狀態升格為 release-ready。

## Manual shard rollout（serial authority 保留）

`acceptance-shard-preflight`、`acceptance-shard-canary` 和
`acceptance-shard-aggregate` 只在 `workflow_dispatch` 且明確設定
`enable_acceptance_shards=true` 時執行。Repository 預設保持：

```text
ACCEPTANCE_SHARDS_ENABLED=false
formalReleaseEnabled=false
```

既有 `full-pytest`、`hermes`、`ui-acceptance` 與正式 `aggregate` job 不依賴
shard jobs；缺少或失敗的 advisory shard artifact 不會改變 serial release path。
在任何 rollout 問題下，回退方式是重新 dispatch：

```bash
gh workflow run release-gates.yml -f enable_acceptance_shards=false
```

shard aggregate 只能驗證同一 source／manifest 的 diagnostic evidence，不能輸出
`full-pytest-gate-v1`，也不能被 `release_gate.py` 消費成正式 PASS。只有另行批准的
L3 promotion 才能改變這個邊界。
