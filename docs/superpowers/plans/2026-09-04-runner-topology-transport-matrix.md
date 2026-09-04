# Runner Topology 與 Transport Matrix Implementation Plan

> **For agentic workers:** 使用 `superpowers:executing-plans`，以 Inline Execution 逐步實作及設 checkpoints。依專案要求只使用本地 `scripts/agent_workflow.py`；不使用對話內 subagent orchestration。

**Goal:** 交付一份可由 source 與 tests 逐列核對的 runner topology／transport reference，以及兩個既有文件導航連結。

**Architecture:** 採 docs-only reference，維持既有 runtime contracts。以 role、identity transport、process/wire、inference 四個維度呈現差異；Graph／Memory 僅供 bounded 探索。單一 Task 修改三份 Markdown，無新模組或 schema。

**Tech Stack:** Markdown、Mermaid、Git、Python stdlib、既有 agent CLI 與 pytest。

**Spec:** [Design spec](../specs/2026-09-04-runner-topology-transport-matrix-design.md)。執行前讀取完整 spec。

**Context brief:** execution-only compact context；只供 collector 控制輸入大小，不取代 spec，runtime summary 保留於 `.nbs_agent_runtime/reports/`。

**Execution profile:** `gpt-5.6-luna` / `medium`；本計畫不切換主 session 或全域設定。

## Global Constraints

- 正式口徑「不含掛賬核銷與TT退款轉團款」；2026-05 baseline `HKD 12,057,968`。
- 原正式營收不變；正式淨 GMV 只扣「已退款」；總退款比較維度保留；退款不減旅行團／票務人數，TT 退款轉團款不得重複扣減。
- 禁止修改 runtime、runner、JSON schema、model/provider default、approval、dispatch、workflow、SQLite、business rules、export schema、UI、CI 或 branch protection。
- Graph／Memory Hub／Memory Sidecar／Agent Operations 維持 read-only／non-authoritative；缺失或 blocked 用 source fallback。
- 本 Task 僅三份 Markdown、diff <500 行；reference 目標 ≤280 行。
- Context input ≤12k estimated tokens、output ≤1.5k；Review 依 `agent_config/token_budgets.json`，不擴大上限。
- Task 完成後 fresh findings-first Review、checkpoint；Full pytest、Hermes、UI acceptance、aggregate 為獨立 final gates，不能沿用舊 PASS。
- spec/plan 是規劃結果；不將 planning self-review 當作 implementation Strict Review。

## Execution setup

- [ ] 確認使用者已要求執行此 plan，再開始 Task；已授權的同範圍修正不重複詢問。
- [ ] 模型使用 `gpt-5.6-luna` 與 `medium`。主執行者依實際 session 設定核對，不聲稱文件中的 model 字串會自動切換模型。
- [ ] 依既有 worktree 流程建立或使用隔離的 `codex/runner-topology-transport-matrix` 分支。先 `git worktree list`／`git branch --list`；若已存在，核對後續用，不建立重複分支。
- [ ] spec、plan 與 compact context summary 必須在 execution checkout 可讀；不拷貝 `.nbs_runtime/`。planning documents 的 commit 與 implementation checkpoint 分開。
- [ ] `git status --short --branch`、`git log -5 --oneline`；確認 source 未新增相關變更。若 HEAD 不同於 spec snapshot，只重查受影響 runner rows；保留 unrelated dirty files。
- [ ] 每 Task bounded context；使用者指定 Inline Execution 時在本 session 維持 checkpoints，不用新增對話代理繞過本地 workflow。

## Task 1：建立 source-backed reference 與導航

**Task ID:** `task-runner-topology-01`

**Type:** documentation

**Model / effort:** `gpt-5.6-luna` / `medium`

**Files:**

- Create: `docs/agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`，只加一個連結段落
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`，只加一個連結段落
- Tests: 使用既有六檔 source-regression suite，不新增實作鏡像測試

**Consumes:** spec R1–R10、當前 HEAD、允許路徑 source、Context bundle、可用的 bounded hints。

**Produces:** reference、navigation diff、source 索引、docs checks、fresh Review 與一個 checkpoint；不輸出 runtime authority。

### Checkpoint A — 先確認來源與輸入

- [ ] 讀 spec 第 4 節列出的每個 owner symbol 與相鄰 caller，優先以下命令；需要更多內容才沿直接依賴補讀。

```bash
rg -n 'class RunnerIdentity|_TRANSPORTS|def from_dict' backend/agents/runner_identity.py
rg -n 'class SubprocessAgentRunner|class SandboxedSubprocessAgentRunner|def _decode_json_or_codex_event_stream' backend/agents/agent_runtime.py
rg -n 'class CliProbeRequest|class CliInvokeRequest|def probe|def invoke|DEVNULL|_MAX_|_DEFAULT_' backend/agents/hermes_cli_transport.py
rg -n '_FIELDS|def from_result|command_shape_fingerprint' backend/agents/hermes_cli_transport_receipt.py scripts/hermes_live_ab_runner.py
rg -n 'local_model|RunnerIdentity|ChatCompletionsTransport|OpenAI' backend scripts -g '*.py'
rg -n 'command =|model|documentation-draft-v1' backend/agents/documentation_codex_runner.py
```

- [ ] 執行 collect-only，以 compact brief 取代完整 spec 當 collector brief；完整 spec 仍由主執行者閱讀。不要把額外 `--include` 無限制堆入 bundle。

```bash
.venv/bin/python scripts/context_agent.py --brief <execution-only-compact-context> --collect-only --output .nbs_agent_runtime/reports/runner-topology-task-context.json
.venv/bin/python scripts/agent_workflow.py run --brief <execution-only-compact-context> --no-notify
```

- [ ] 若需要語意壓縮，在上述 `run` 指定已批准的 `--context-agent-command`，最多一次起步。CLI invocation profile 為 `codex exec --ephemeral --sandbox read-only --json --model gpt-5.6-luna -c 'model_reasoning_effort="medium"'`，並指示只讀 stdin evidence、禁止工具、輸出契約 JSON。確認 executable 解析到已允許的 binary；此命令不適用於 offline Implementation worker。
- [ ] 讀取回傳 run ID 後用 `agent_workflow.py status`，不要虛構 run ID。run 保留 `awaiting_authorization` 或真實 blocked status，不把 inline authoring 手動寫成 orchestrator completed。
- [ ] Memory Hub 由 collect-only 自動 query；只讀 ready hints。Sidecar 用既有 `memory_sidecar_artifact_report`，Graph 用該 run 的 `status`，不呼叫 build/provision/recall。Graph snapshot 缺失就記 missing/blocked，不另開修復工作。
- [ ] input overflow 時縮小 brief／引用；dirty-worktree 時歸屬新文件與 unrelated runtime。既有 blocked Context 可作有標籤的提示，不能作 ready input；formal Review 之前重新建立合法 Context evidence。

**Checkpoint A 輸出：** 最多 8 行，列 HEAD、scope、reference rows、source 缺口、Context/Graph/Hub/Sidecar 結果及輸入估算。

### Checkpoint B — 編寫 reference 與自檢

- [ ] 用 `apply_patch` 建立 reference，下列章節順序固定，內容由 spec 第 4–5 節及本次 source 填成完整敘述：

```markdown
# Runner Topology 與 Transport Matrix

## Snapshot 與適用範圍
## Role、transport、process 與 inference
## Runner topology
## Transport matrix
## Identity 與 evidence matrix
## 支援、部署與驗證狀態
## Failure lookup
## 六個驗收情境
## Source/test 索引與更新方式
```

- [ ] Snapshot 使用實際 40-character HEAD、盤點日期、source/fixture/live 各自範圍。使用 repo-relative links 加 symbol，避免 username、本機絕對路徑、secrets、原始 log 或 runner command 出現在 topology rows。
- [ ] Mermaid 最多 12 個節點；至少涵蓋 caller、generic CLI、Hermes CLI、remote model turn、offline worker、validators/evidence、read-only Graph/Memory、deterministic Hermes。Local Model 的未接線狀態用虛線/註記，不畫成已運作 data path。
- [ ] 以下八個 row key 用於兩張 matrix 對照；這些只是文件標籤，不新增 runtime ID：

| Row key | 必須呈現的差異 | 最少 source anchor |
|---|---|---|
| context-review-cli | stdin JSON；解析 Codex final agent_message；120s 預設；generic output 沒有 Hermes streaming byte-cap 保證 | `SubprocessAgentRunner.run` |
| strict-review-profile | static cache/version 與 live probe 分開；identity 明確 mapping | `RunnerProfile.to_runner_identity`、`preflight_runner`、`probe_runner` |
| hermes-cli | argv／DEVNULL；JSON 或最後 JSON line；timeout 預設 60s、上限 600s；各 output 預設 1,000,000 bytes、上限 10,000,000 | `CliProbeRequest`、`HermesCliTransportAdapter` |
| hermes-remote-api | local launcher 不改變 remote_api inference hop；SDK credentials 不進 receipt | `scripts/hermes_turn_receipt.py`：`real_turn_runner` |
| local-model | identity enum 接受；本輪 production invocation／deployment／live evidence 未驗證 | `RunnerIdentity.from_dict` |
| implementation-worker | disposable staging、network denied、trusted controller apply；不是任意 cloud CLI 的 coding worker | `SandboxedSubprocessAgentRunner` |
| documentation-cli | fixed argv、draft → service proposal；不推論 argv 轉交模型參數 | `CodexDocumentationRunner.run` |
| hermes-acceptance | deterministic inspection；獨立 release gate；不是模型 transport readiness | `hermes_post_change_check`、`hermes_gate` |

- [ ] evidence matrix 必須顯示 identity 六欄、identity envelope、capability/cache、CLI receipt、remote receipt、release reports 的實際 fields；明列 request 中 turn/manifest 不等於 receipt 已持久化該欄位。
- [ ] failure lookup 以 owner 為單位寫 status/exception/exit，保留 `blocked_runtime`、`blocked_runner_capability`、`blocked_runner_transport`、`invalid_evidence` 各自來源；不要將 Python exception 重新命名為不存在的 CLI status。
- [ ] 六個案例逐一寫明預期判讀及 source/test：

| 案例 | 預期判讀 | 直接驗證來源 |
|---|---|---|
| 本機 CLI 使用遠端推論 | local process 不證明 offline model | `agent_runtime.py` 與 explicit model/provider profile |
| local_model schema roundtrip | 僅 schema 支援；不是 live deployment | `test_supported_transports_are_accepted` |
| Codex JSONL vs Hermes CLI JSONL | parser 不互換；不能宣稱支援相同 event protocol | `test_subprocess_runner_extracts_codex_jsonl_agent_message`、Hermes parser source |
| probe ready 後 invoke 失敗 | 兩次結果分開；保留 transport failure | `tests/test_hermes_cli_transport.py` |
| ready response 但 source/receipt 不符 | evidence validation 拒絕；ready 不升級 acceptance | `test_envelope_rejects_source_mismatch`、`tests/test_hermes_cli_transport_receipt.py` |
| 文件 commit 變更 HEAD/source | 重新建立 gates；不得借用前一 commit PASS | `scripts/release_gate.py`、`tests/test_release_gate.py` |

- [ ] 在 `NBS_AGENT_ARCHITECTURE.md` 第 6 節 Responsibility Matrix 後、Memory Sidecar boundary 前加入以下單一段落：

```markdown
Runner 的現有呼叫關係、transport 與 evidence 差異，見 [Runner Topology 與 Transport Matrix](RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md)；該文件為 source-backed reference，權限與執行規則仍以本契約為準。
```

- [ ] 在 `CODEX_AGENT_DISPATCH.md` 的「執行邊界」節第一段前加入以下單一段落：

```markdown
選讀 runner source 前，可先查 [Runner Topology 與 Transport Matrix](RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md) 的 caller、transport 與 evidence 對照；該 reference 不提供 runner selection、approval 或 dispatch 能力。
```

- [ ] 執行 `git diff --check`，逐一核對新增 links 的目標檔案與 symbol 存在、Mermaid 箭頭方向與 code fences 成對；用 Markdown preview 檢查表格寬度，過寬就拆為兩張以 row key 對照的表。
- [ ] 核對下表的 R1–R10 coverage，任何未引用的現況敘述改成「本輪未驗證」，而非自行補推 capability。
- [ ] 執行既有六檔 suite；這個小型 suite 驗證本文件引用的 contract，不能當 live model test 或 full pytest：

```bash
.venv/bin/python -m pytest -q tests/test_runner_identity.py tests/test_runner_identity_envelope.py tests/test_hermes_cli_transport.py tests/test_hermes_cli_transport_receipt.py tests/test_hermes_live_ab_runner.py tests/test_hermes_turn_receipt.py
```

**Checkpoint B 輸出：** 三檔 diff stat、Markdown 核對結果、focused command/exit/result、source gap 清單。此時只稱「文件自檢完成」。

### Checkpoint C — fresh Review 與 checkpoint commit

- [ ] 整理單 Task contract：id `task-runner-topology-01`，type documentation，allowed-files 精確三檔，禁止 source/runtime/Git/network model transport 變更。contract 與 verification 依現有 `ImplementationTaskContract`／`verification-v1` builder 建立，不手填 hash 或編造測試證據。
- [ ] 使用 `scripts/verification_chain.py seal` 為當前 base／`WORKTREE` 建立 source seal，並依 `docs/agents/REVIEW_AGENT_CONTRACT.md` 執行 preflight／fresh Strict Review；approved brief、actual diff、Context、focused verification、runner capability 都須同次 binding。
- [ ] Review command/profile 明確使用 `gpt-5.6-luna` / `medium`，檢查 CLI capability 和 actual argv。Context/Review 只能讀 evidence；本 Task 不經 offline Implementation worker 去呼叫 cloud model。
- [ ] Review 必須能看到三檔完整 diff；新文件不得因 untracked 而漏收。planning documents 與 unrelated runtime 分別歸屬／preserve，不增加 implementation allowlist 來掩蓋它們。
- [ ] `changes_required` 時僅修本 Task findings，再做 affected docs checks／fresh Review；沒有正式 verdict 不建立 checkpoint。
- [ ] Review PASS 後由 Codex 精確 stage 三檔，再使用 `scripts/task_checkpoint.py validate` 檢查 actual parent HEAD、staged diff、allowlist、contract、Review 與 focused fingerprints；沿用 `task-checkpoint-evidence-v1`。
- [ ] checkpoint subject 為 `checkpoint(task-runner-topology-01): document transport matrix`（小於 72 字元），使用 `backend/agents/task_checkpoint_models.py` 的現有 message model／`trailers()` 保留 `NBS-Checkpoint-Version: 1` 與 `Final-Acceptance: pending`。validator 有 untracked dirty worktree 等阻擋時，修正工作區歸屬／使用乾淨隔離 checkout，不修改 validator、不刪 unrelated artifacts。

**Checkpoint C 輸出：** Review verdict/fingerprint、Task parent/diff fingerprint、checkpoint commit；不宣稱 release-ready。

## Final verification 與交付

- [ ] 依 `docs/agents/CODEX_AGENT_DISPATCH.md` 與 `.github/workflows/release-gates.yml` 跑獨立 Full pytest／Hermes／UI acceptance／aggregate。docs-only 不免除既有 release gates；本步不新增或更改 gate。
- [ ] 所有最終 tracked 文件先納入預定 commit，再取得一次完整 SHA 與 archive fingerprint。每份 gate 使用同一組值：

```bash
git rev-parse HEAD
git archive HEAD | shasum -a 256
```

- [ ] Full pytest 使用 `scripts/full_pytest_gate.py`，Hermes 使用 `scripts/hermes_gate.py` 並明確標示 profile，UI 依 workflow 建立全新 temporary fixture、啟動對應 HTTP server、跑 `streamlit_ui_smoke.py` 及 `ui_acceptance_gate.py`。每次重跑 UI smoke 要用未被上一輪 merge flow 消費的新 fixture。
- [ ] `scripts/release_gate.py aggregate` 驗證三份 reports 的 commit/source/freshness；文件再改或 commit 再變就重建受契約要求的 evidence。所有 output 留在 `.nbs_agent_runtime/`，不將 raw logs 或 tokens 放入文件。
- [ ] 若完成驗證鏈，依 `verification_chain.py attest` 的實際結果報告；不能以 release aggregate 取代缺少的 Strict Review/attestation。
- [ ] 交付 reference 路徑、checkpoint、fresh gates 與 unresolved limitations。Git push／PR／merge 沿用使用者對當次工作範圍的授權；本 planning request 不自動執行 remote integration。

## Spec coverage 與 self-review

| Spec requirement | Implementation location |
|---|---|
| R1 語義分層、R2 topology | Task 1 Checkpoint B 的定義與 Mermaid |
| R3 matrix、R4 支援狀態、R5 crosswalk | Task 1 Checkpoint B 的兩張 matrix 與八列 |
| R6 failure、R7 六案例 | Task 1 Checkpoint B 的 failure lookup／案例表 |
| R8 source/更新 | Checkpoint A fresh source、Checkpoint B links、final verification |
| R9 token/輔助 | compact brief、Checkpoint A bounded Context/Memory/Graph、單 Task |
| R10 模型/checkpoints | Execution setup、每個 checkpoint 與 fresh Review |

Plan self-review：單一 Task 三檔、沒有 runtime interface 變更、沒有新增假 CLI flags、無自動換模型或 agent orchestration；文件驗收與 release readiness 分開。Rollback 為 revert 本 Task 的 reference/navigation checkpoint，保留歷史 evidence。
