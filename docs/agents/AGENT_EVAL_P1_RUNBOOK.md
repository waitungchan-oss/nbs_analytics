# Agent Memory P1 離線量測 Runbook

本工具只重算已保存的 observation、task ledger 與獨立 quality record；不啟動模型、服務、網路或 recall。synthetic fixture 可驗證資料契約，但不是 production baseline。

## 使用

```bash
.venv/bin/python scripts/agent_eval_report.py \
  --root .nbs_agent_runtime/agent-eval \
  --manifest manifests/example.json \
  --inputs indexes/example.json \
  --format markdown
```

輸出預設到 stdout；`--save-id LABEL` 才會將單一 report 原子保存到專用 artifact root。沒有 execute、approve 或 recall-enable 子命令。

## 解讀

- `measured*` 只代表 producer 提供的 Usage；`estimated*` 另列，不能當帳單用量。
- `taskTotalTokens=null` 代表 ledger、call inventory 或 measured input/output 不完整；`0` 只代表已確認沒有呼叫。
- 少於 20 個樣本時 P95 為 `null`，不是零。
- failed、timeout、budget exceeded 和未執行 slot 都保留在分母。
- report status 為 `synthetic_only` 時不能宣稱真實記憶效果改善。

## 邊界與審核

實驗 artifact 總池上限 32 MiB、單一 experiment 最長 30 天；工具不自動 prune。修復缺測要產生新的 receipt，不修改舊 artifact。T9 真實 baseline 需要人類確認案例、獨立 scorer、approved runner、模型／預算／價格表與 fresh source-bound acceptance。

正式 baseline、Memory Hub、Memory Sidecar、Governance Graph、SQLite 與 workflow gate 均不由此工具改寫；任何候選結果仍需依既有治理流程人工審核。
