# NBS Governance Graph Contract

狀態：active（Phase A）  
Schema：`nbs-governance-graph-v1`  
適用範圍：NBS Analytics 的 read-only Governance Graph projection。

## 1. Purpose 與 non-goals

Governance Graph 將可驗證的 canonical artifacts 投影成 bounded、deterministic
`snapshot`，讓工程師能檢查 Spec → Plan → Task → Review → Verification → Hermes
→ Documentation → Git integration 的 lineage、evidence 與 blocked reason。

Graph 是觀測與稽核的衍生層；它是 **not a control input**。Graph 不會 approve、
不會 dispatch、repair、apply、prune、delete、啟動 runner、改變 workflow 狀態，
也不會寫入 SQLite，或取代
Review Agent、Hermes、Documentation Controller、SQLite 與 Git 的 authority。

Phase A 不包含 UI、Agent Operations 寫入、telemetry、風險自動決策或新的 approval
state machine。Agent Operations Graph view 保留在 Agent Operations Phase B，且必須
維持 read-only。

## 2. Canonical artifacts 與 projection boundary

Canonical artifacts 是唯一真相來源，位於
`.nbs_agent_runtime/runs/<run-id>/`，包括 risk、spec/plan gate、implementation、
verification、review、Hermes、documentation 與 Git integration evidence。

`governance-graph.json` 是同一 run 的可重建 projection，不是 canonical artifact。
只有 `build` 可以透過 `WorkflowStore.write_projection()` 原子寫入此檔案；`validate`
與 `status` 必須 zero-write。任何缺失資料都必須呈現 `not_started`、`unknown`、
`missing` 或 `blocked`，不得由 Graph 推測不存在的關係。

Graph node 與 evidence ref 必須保存 artifact path、sha256、status 與 generatedAt，
並拒絕 absolute path、path traversal、symlink、non-regular file、超過 hard cap、
錯誤 schema、secrets、runner command、prompt、raw rows、完整 logs 或內部推理。

## 3. CLI contract

唯一 CLI 是 `scripts/governance_graph.py`，輸出 JSON envelope
`nbs-governance-graph-cli-v1`：

```bash
.venv/bin/python scripts/governance_graph.py build --run-id <run-id>
.venv/bin/python scripts/governance_graph.py validate --run-id <run-id>
.venv/bin/python scripts/governance_graph.py status --run-id <run-id>
```

`build` 只呼叫 `GovernanceGraphBuilder.persist()`；`validate` 只呼叫
`validate()`；`status` 只呼叫 `status()`。CLI 不提供 approve、dispatch、repair、
apply、prune、delete、runner command、model flag 或任何 control-plane 入口。

## 4. Risk、ownership 與 authorization

風險級別固定為：

- **R0**：無行為變更的說明、Markdown typo、格式或有效 cache reuse。
- **R1**：一般 code、test、agent contract 或 Graph projection 變更；必須有
  明確 Task contract、targeted tests、Review 與 Hermes evidence。
- **R2**：`upload`、`sqlite`、`baseline`、`rollback`、`revenue`、`business_rules`
  或 `export_schema` surface；不得由現有 Implementation Agent 自動執行，必須
  進入 protected governance decision。

Spec、Plan、Task contract 與 Final acceptance 的 ownership 不可互換：

1. Spec 定義目的、scope、non-goals 與 acceptance boundary。
2. Plan 定義 bounded implementation tasks 與驗證順序。
3. Task contract 定義 allowed files、禁止範圍與本次 authorization。
4. Implementation Agent 只執行一個已批准 Task，不得自行 commit、merge 或選擇下
   一個 Task。
5. Review Agent 做 findings-first diff review；Hermes 做 read-only runtime、
   baseline、SQLite integrity 與 acceptance check；兩者不可互相取代。
6. Final acceptance 由 Codex 彙整 evidence，只有在 gates、tests、Review、Hermes
   與必要 documentation 結果一致時才可結束。

預設 authorization mode 是 `per_task`；`approved_batch` 只有在明確批准同一批
immutable task contracts 時才可使用，不能藉此跳過每個 Task 的 evidence 或 Review。

## 5. Retry、Terra 與 freshness

Retry 只遵循 deterministic policy 與既有 repair budget。Luna 只可在已批准 Task
範圍內修復；Terra 僅作 diagnostic-only，不得直接寫入程式、SQLite、baseline、
runtime 或 Git。baseline drift、revenue scope conflict、unsafe DB path 等 R2
事件應標示為 `protected_incident`，不是 retry 或模型降級問題。

Graph 必須以 manifest Git identity、artifact hash、schema version、generatedAt
與 upstream evidence 判定 freshness。stale、missing、duplicate、malformed 或
identity mismatch 不得宣稱 PASS；stale upstream 會使 downstream evidence
`blocked` 或 `stale_descendant`。

## 6. Status 與 completion semantics

Graph 不使用 `running` 來虛構 node 狀態；執行中狀態仍由 canonical `status.json`
表達。重要狀態包括：

- `awaiting_authorization` / `not_started`：尚未具備下一階段 evidence。
- `awaiting_documentation`：Hermes PASS，但 documentation outcome 尚未存在。
- `ready_for_integration`：documentation PASS，但 Git integration 尚未完成。
- `blocked_missing_runner`：明確需要 approved Documentation runner，但 runner 缺失；
  不得由主 Codex LLM fallback。
- `protected_incident`：R2 或受保護資料面衝突，需人工治理決策。
- `blocked`：schema、security、freshness、gate、verification 或 integration
  failure；不可宣稱 Review/Hermes PASS。
- `completed`：必要 gate、verification、Hermes、documentation/no-doc outcome 與
  Git evidence 全部一致且可追溯。

`blocked_missing_runner` 不得與一般 `awaiting_documentation` 混用：前者是 runner
缺失，後者是 runner 存在但等待 proposal、target approval 或 apply。

## 7. Retention 與 Hermes

Retention 只可 compact old completed run 的 stage/projection artifacts；non-terminal
run、protected run、locked run 與合法 blocked/protected Graph projection 必須保留。
Retention plan 是 read-only；任何 apply 都必須經既有 WorkflowRetention policy，
且不得刪除 manifest、status、approval、events 或受保護 evidence。

Hermes 的 `governance-graph-hermes-report-v1` 只讀取安全 run directories 與
`governance-graph.json`，檢查 schema、JSON object、regular file、cap、run identity
與 status counts；固定 `policy=read-only`、`invocations=0`、`writes=0`。Hermes PASS
不等於 Review PASS。

## 8. Formal business guardrails

正式口徑固定為「不含掛賬核銷與TT退款轉團款」。2026-05 frozen baseline 固定為
`HKD 12,057,968`。Governance Graph、Hermes、UI、報表或 analysis layer 都不得
重算、修正、覆蓋或重新解釋這兩項正式口徑。

## 9. Change process

任何 Graph schema、policy、canonical mapping、CLI 或 retention 行為變更，必須
先更新 Design Spec/Plan，再執行 TDD、focused tests、findings-first Review、full
verification 與 Hermes。若文件需要回填，Documentation Agent 只消費
`documentation-evidence-v1` 並輸出 `documentation-proposal-v1`；沒有 approved
runner 時回報 `blocked_missing_runner`，不得由主 Codex 靜默寫入 Obsidian、System
Map 或 ADR。
