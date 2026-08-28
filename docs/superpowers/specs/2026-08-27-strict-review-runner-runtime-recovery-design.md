# Strict Review Runner Runtime Recovery Spec

## 1. 目的

讓 NBS Analytics 在不放寬 Strict Review gate 的前提下，穩定產生本輪可驗證的 `verification-v1` evidence bundle，並消除本地 Codex runner 的 cache/schema mismatch 與 timeout 所造成的 degraded runtime。完成後，Review 能取得真實的命令證據、執行受控的 read-only runner，並在成功時輸出可重現的 `PASS`。

## 2. 已確認問題

1. `verification-v1` 必須符合現有 Review parser 的固定 contract：最外層只能有 `commands`，每項只能含 `label`、`argv`、`exitCode`、`stdoutTail`、`stderrTail`。過去的 blocker 是缺少符合本輪實際執行結果的 evidence，而不是缺少更多欄位。
2. 本地 `codex-cli 0.142.5` 曾讀到不相容的 models cache，出現 `missing field base_instructions`。
3. cache schema 修復後，預設 `gpt-5.6-luna` 仍要求較新版 Codex；明確指定 `gpt-5.4` 可啟動，但完整 Review payload 曾超過 runner 的 120 秒 timeout。
4. Review、full pytest、Hermes 與 UI acceptance 是不同 gate；任一 gate 沒有真實證據時，不得宣稱完整 PASS。

## 3. 範圍與非範圍

### In scope

- Review runner preflight：CLI version、model availability、cache schema 與 executable allowlist。
- `verification-v1` evidence 的本輪生成、內容 bounded 化與 fingerprint 綁定。
- Review runner timeout、stderr、exit code 的明確 diagnostics。
- Review payload 的 compact projection，避免傳送完整 logs、SQLite rows、Excel 或 secrets。
- Strict Review result 與 verification evidence 的 provenance linkage。
- cache repair 的可回復操作與 runtime-only backup。

### Out of scope

- 不修改正式 SQLite、baseline、revenue scope、business rules 或 export schema。
- 不以 synthetic agent response、手工 PASS 或舊 review artifact 取代本輪 runner。
- 不把 Memory Hub、Governance Graph 或 Agent Operations 變成 authority。
- 不新增外部服務，不在 application runtime 內自動安裝或升級 Codex。

## 4. 設計決策

採用「preflight + compact evidence + bounded runner + fail-closed」方案：

- 先檢查 runner 是否可用，再執行 Review；preflight 失敗時輸出 `blocked_runtime`，並指出可恢復原因。
- runner profile 明確指定可用 model，例如 `gpt-5.4`，禁止使用需要更新 CLI 的 default model。
- cache 只接受與 CLI schema 相容的檔案；修復前保留 timestamped backup。若 cache 仍不相容，不自動猜測或合成 model metadata。
- 將 Review input 壓縮為 brief、context summary、diff summary、verification command tails 與必要 contract，不注入原始營銷資料。
- timeout 分成 startup、agent turn、overall 三段診斷；任何 timeout 都是 `blocked_runtime`，不得降級成 Review PASS。
- Review PASS 必須同時滿足：evidence schema valid、所有 required commands exit code 0、agent response schema valid、無 high/critical finding、head/worktree fingerprints 一致。

## 5. Architecture

```text
verification commands
        │
        ▼
verification-v1 writer ──► bounded evidence bundle
        │                              │
runner preflight ──► approved runner profile ──► Strict Review
        │                              │
        └──────────── diagnostics ◄────┘
                       │
                       ▼
              review result + provenance
```

### Components

- `scripts/review_agent.py`：維持現有 CLI，增加 preflight/diagnostic output 與 verification freshness 檢查。
- `backend/agents/agent_runtime.py`：提供 bounded timeout、stderr/exit normalization；不改變 allowlist 或 read-only boundary。
- 新 runner profile module：解析固定 model、CLI version floor、cache schema requirement、timeout budget；不執行安裝。
- `verification-v1` writer：從實際命令結果建立固定格式 evidence，寫入 runtime reports，不寫正式資料。
- Hermes：仍只負責 read-only system acceptance；不取代 Strict Review。

## 6. Data contracts

### verification-v1

為維持現有 Review compatibility，payload 嚴格保持：

```json
{
  "commands": [
    {
      "label": "full pytest",
      "argv": [".venv/bin/python", "-m", "pytest", "-q"],
      "exitCode": 0,
      "stdoutTail": "2310 passed in ...",
      "stderrTail": ""
    }
  ]
}
```

不得加入未被 parser 允許的 top-level 欄位。bundle 的檔案 identity、head SHA、brief fingerprint 與 review fingerprint 由外層 Review artifact 保存，避免破壞既有 contract。

### runner preflight result

```json
{
  "status": "ready | blocked_runtime",
  "executable": "/absolute/path/to/codex",
  "cliVersion": "0.142.5",
  "model": "gpt-5.4",
  "cacheSchemaStatus": "compatible | incompatible | unavailable",
  "diagnostics": [],
  "recovery": []
}
```

若 `status != ready`，Review 不呼叫 agent，直接輸出 bounded residual risk。

### Review result

既有 `review-report-v1` 不改 schema；`verdict` 只可為 `pass`、`changes_required` 或 `blocked`。runner timeout 一律 `blocked`，並在 `residualRisk` 保存原因與 recovery action。

## 7. Error handling and recovery

- cache parse/schema mismatch：保留原檔為可回復 backup，建立相容 cache 後再做 read-only probe；不可直接改動 project data。
- model unavailable：明確鎖定 allowlisted model；若沒有可用 model，停止並標記 `blocked_runtime`。
- timeout：保留 bounded stderr/stdout tail、phase、elapsed milliseconds；不重試超過一次，避免重複消耗與不可控 latency。
- stale verification：若 command results 不屬於目前 head/brief/worktree fingerprint，Review blocked。
- dirty process artifact：只允許 evidence collector 明確標記的 `.superpowers/` preserved artifact；其他未歸屬 dirty file 仍 fail-closed。

## 8. Acceptance criteria

1. 使用目前 `de790b0` 與最新 worktree，可生成 parser 接受的 verification-v1 bundle。
2. cache schema preflight 不再出現 `missing field base_instructions`；model preflight 不選用要求新版 CLI 的 default model。
3. runner 可在 bounded timeout 內完成 Strict Review，並取得真實 agent response；完成後 verdict 為 `pass` 或有明確 findings。
4. runner 不可用、timeout、cache mismatch 時均為可診斷 `blocked_runtime`，不得輸出 PASS。
5. failure injection 證明 verification stale、cache invalid、runner timeout 不會修改 SQLite、baseline、active export 或 trusted reference pointer。
6. full pytest、Hermes、system acceptance 與 Strict Review evidence 各自有可追溯結果。

## 9. Rollout

- Phase 0：只讀 preflight 與 evidence writer，預設不改 Review verdict。
- Phase 1：在 local/main runtime 使用 pinned supported model，Strict Review shadow 執行。
- Phase 2：連續兩次完整成功且無 degraded runtime 後，允許作為正式 Review gate。
- Rollback：停用 runner profile，回到 `blocked_runtime`；保留舊 cache backup 與既有 review artifacts，不回滾 SQLite 或正式 exports。

