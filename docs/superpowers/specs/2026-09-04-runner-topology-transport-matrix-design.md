# Runner Topology 與 Transport Matrix Design Spec

日期：2026-09-04

狀態：spec；使用者已要求完成 spec/self-review 後接續 implementation plan

盤點基準：`main@c23294c503b6321797566a21ddbf21ab84f9616f`

預定實作模型：`gpt-5.6-luna`，reasoning effort：`medium`

## 1. 目的與交付

建立一份工程師與 bounded agent 都能快速閱讀的 runner topology／transport matrix，回答：誰呼叫誰、在哪裡執行、實際通訊方式、identity 從何而來、哪些 evidence 能證明什麼，以及失敗應在哪一層排查。

正式交付為 `docs/agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md`，並在既有 architecture 與 dispatch 文件各增加一個導航連結。這是現有實作的 source-backed reference；不新增 runtime registry、JSON schema、CLI、transport adapter、policy 或執行權限。

本輪先交付本 spec 與對應 implementation plan；Context Agent 使用的 compact context 僅為 execution artifact。實作時才建立 reference 文件及導航連結。既有 `.nbs_runtime/` 未追蹤內容保留，不納入文件工作。

## 2. 方案比較與決策

| 方案 | 成本與用途 | 決策 |
|---|---|---|
| 一份 Markdown reference，附 Mermaid、matrix、source/test 索引 | 最小改動；reader 可從每列直接找到 owner；需在 runner 變更時同步核對 | 採用 |
| JSON registry 加 generator／validator | 可自動化部分一致性，但增加 schema、維護與測試面，容易被誤當 routing source | 本次不做 |
| 統一所有 runner base class／transport protocol | 改動 interfaces 與呼叫行為，超出本次可讀性需求 | 本次不做 |

最快的下一步是依第 4 節已查核的 source 建立 reference 的 topology 與核心 matrix；預期讓後續 Task 只讀該列及直接 owner，而非重掃全部 agent 模組。

## 3. Scope 與固定邊界

- 只描述現有 Local CLI、Remote API、Local Model identity 類別與實際 caller paths。
- 補充 Context、Review、sandboxed Implementation、Documentation 與 deterministic Hermes 的角色，避免同名 Hermes 混淆。
- Governance Graph、Memory Hub、Memory Sidecar、Agent Operations 只提供 read-only／non-authoritative context 或 projection。
- 不修改 orchestration、approval、dispatch、workflow state、runner defaults、credentials、provider、模型安裝、服務生命週期或 GitHub branch protection。
- 不改 SQLite、baseline、正式資料、business rules、export schema、Dashboard 或 GMV 行為。
- 正式收入口徑固定為「不含掛賬核銷與TT退款轉團款」；2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 原正式營收不變；正式淨 GMV 只扣「已退款」；總退款保留比較維度；退款不減旅行團／票務人數；TT 退款轉團款不得重複扣減。
- Strict Review、Full pytest、Hermes、UI acceptance 獨立；release aggregate 只聚合後三者，同一 commit/source 且 fresh；文件、Graph、Memory hints 不能補推 PASS。

## 4. Source-backed inventory

以下為本輪 source inspection，不是全部 transport 的 live acceptance。實作前需重新確認 HEAD 與引用。

| 路徑／角色 | 查核到的行為 | Owner／驗證定位 |
|---|---|---|
| Canonical identity | `runner-identity-v1` 六欄為 `runnerId`、`transport`、`provider`、`model`、`profile`、`executionEnvironment`；transport enum 為三類 | `backend/agents/runner_identity.py`：`RunnerIdentity.from_dict`；`tests/test_runner_identity.py` |
| Source envelope | `runner-identity-envelope-v1` 另外保存 identity、`sourceFingerprint`、`artifactKind`、`envelopeFingerprint`；不是所有 callers 都自動使用 | `backend/agents/runner_identity_envelope.py`；`tests/test_runner_identity_envelope.py` |
| Context／一般 Review subprocess | `SubprocessAgentRunner.run` 將 JSON 經 stdin 交給明確批准的 command，解析 JSON 或 Codex final agent_message；預設 timeout 120 秒 | `backend/agents/agent_runtime.py`；`scripts/context_agent.py`；`scripts/review_agent.py`；`tests/test_agent_runtime.py` |
| Strict Review Local CLI profile | profile 執行檔／model／cache 與 static/live capability checks；`to_runner_identity` 需顯式 provider/profile/environment | `backend/agents/review_runner_profile.py`；`tests/test_review_runner_profile.py` |
| Hermes Local CLI adapter | `CliProbeRequest`／`CliInvokeRequest`；argv + `stdin=DEVNULL`，bounded stdout/stderr，JSON 或最後一行 JSON object；`probe` 與 `invoke` 是分開方法 | `backend/agents/hermes_cli_transport.py`；`tests/test_hermes_cli_transport.py` |
| Hermes Local CLI caller | `run_local_cli_transport` 是可呼叫 Python helper，呼叫 invoke 後寫 receipt；不可宣稱已有通用 `--transport` CLI switch 或自動 probe | `scripts/hermes_live_ab_runner.py`：`run_local_cli_transport`；`tests/test_hermes_live_ab_runner.py` |
| Hermes Remote API model turn | `hermes_turn_receipt.py` 透過 Hermes transport／OpenAI-compatible client 執行 real turn；legacy identity 映射 `remote_api`，即使外層 launcher 是本地 Python process | `scripts/hermes_turn_receipt.py`；`scripts/hermes_live_ab_runner.py`；`tests/test_hermes_turn_receipt.py` |
| Local Model | `RunnerIdentity.from_dict` 接受 `local_model`；本輪在 `backend/`、`scripts/` 搜尋未找到使用該值的獨立 production invocation adapter | `backend/agents/runner_identity.py`；`tests/test_runner_identity.py`；部署與 live inference 標記未驗證 |
| Implementation worker | production coding worker 使用 `SandboxedSubprocessAgentRunner` 與 disposable tracked-files staging；network model transport 需與 offline coding worker 分離 | `backend/agents/agent_runtime.py`；`scripts/implementation_agent.py`；`docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md` |
| Documentation runner | `CodexDocumentationRunner` 使用固定 read-only command，消費 evidence、產生 draft，再由 service 驗證 proposal；不能假設 caller argv 的 model/profile flags 會原樣轉交 | `backend/agents/documentation_codex_runner.py`；`backend/agents/documentation_agent_service.py`；`tests/test_documentation_codex_runner.py` |
| NBS Hermes acceptance | `hermes_post_change_check.py` 為 deterministic inspection；`hermes_gate.py` 產生 release evidence，與 Hermes model turn 是不同責任 | `scripts/hermes_post_change_check.py`；`scripts/hermes_gate.py`；`NBS_HERMES_MONITORING.md` |

### 已知資訊缺口的呈現

1. Generic subprocess 與 Documentation wrapper 並非每次都直接注入 canonical `RunnerIdentity`；逐列標示實際 owner，不能寫「所有 runner 已端到端統一」。
2. Hermes CLI invoke request 有 source/turn/manifest fingerprints，但目前 receipt exact fields 沒有 turn/manifest；helper 未給 command-shape fingerprint 時使用零值 fallback。matrix 要區分 request validation、receipt persistence 與上層 acceptance，不把它們合稱完整 binding。
3. `probe` 檢查 version/model 回應，`invoke` 不自動先 probe；不能宣稱有 version floor enforcement 或 CLI executable sandbox，除非引用其他明確 consumer。
4. 不將 `profile` 一律解讀為 reasoning effort；Strict Review mapping 可把 profile name 同時用作 runnerId/profile。`medium` 是本次執行設定，不新增 identity 欄位。
5. 說明中使用的 process、wire format、inference location 是文件欄位，不新增 `runnerKind`、`transportKind` 等 canonical JSON keys。

這些差異先準確記錄。若發現可重現、直接影響本交付的缺陷，可依使用者授權修正；會改變現有 runtime contract 的修復必須獨立列明檔案與驗證，不能藏在文件 Task 裡。

## 5. Reference 文件規格

### R1 — 三個語義層

分開定義：role（任務責任）、canonical `transport`（identity 分類）、wire/process hop（subprocess/stdin/argv/HTTP）。本地啟動 CLI 不足以證明模型權重位於本機；一個 remote_api model turn 可以有本地 subprocess 外殼。

### R2 — 一張 topology

用 Mermaid 表達 existing callers → wrappers/adapters → inference endpoint/worker → result validator → evidence；旁邊標示 Graph／Memory 的讀取方向。將 deterministic Hermes inspection 畫為獨立路徑。未接線的 Local Model 以虛線或文字「schema-only；live 未驗證」呈現。

箭頭只表達實際 data/call dependency，不賦予 Graph／Memory 任何 approval 或 dispatch 能力。避開單一「local runner」節點把 offline worker 與 cloud-backed CLI 混為一談。

### R3 — 核心 matrix

每列使用第 4 節的 role/path 名稱，必要時將 identity/envelope 放在共同 contract 區。欄位固定為：

1. Role / caller path。
2. Identity transport（未接入時明寫未接入，不猜測）。
3. Process / wire input-output。
4. Inference / network boundary（observed、conditional 或未驗證）。
5. Timeout / output limits 與各自 owner。
6. Identity / source binding owner。
7. Capability／receipt／report 與不足以證明的事項。
8. Source symbol 與 test file。

為可讀性可拆為 transport matrix 與 evidence matrix，以相同 row key 對照；避免超寬單表。不能把 adapter 的 1 MB 預設 cap 套到 generic subprocess，或把 Codex event parser 當作 Hermes CLI parser。

### R4 — 支援狀態

每條路徑分別列出 source support、caller wiring、deployment、本輪驗證。使用自然語言狀態，例如「已實作」「helper 接線」「schema-only」「本輪未驗證」「測試證據」，不是新增 runtime enum。

有 unit test 或 source 檔案不代表本機已部署 provider。有 executable/version 不代表選定模型可完成 real turn。缺 evidence 必須直接寫明缺少的驗證。

### R5 — Identity 與 evidence crosswalk

列出 identity 六欄、identity fingerprint、source envelope、Strict Review capability/cache、Hermes CLI receipt、remote turn receipt、release gate evidence 的對照。沿用原 exact schema 名稱，不要求給所有 artifacts 加欄位。

至少明列：identity 不含 `commitSha`；envelope 不含獨立 `commitSha`；release reports 有 `commitSha` 與 `sourceFingerprint`；CLI receipt 的 `ready` 僅證明該 adapter 回應，不能代替 Strict Review、baseline、UI 或 aggregate。

### R6 — Failure lookup

以實際 layer 分組：profile/static preflight、live capability、transport、JSON/parser、identity/source/receipt validation、release freshness。記錄各 owner 的 exception/status/CLI exit，不能虛構統一 exit code。

對每類給一個 read-only 下一步，例如核對 executable/profile、讀同次 bounded receipt、重算 source fingerprint、檢查同次 gate report；不提供自動 retry、fallback 換模型或安全限制繞過。

### R7 — 小型驗收案例

至少包含六個情境：本地 CLI 呼叫 remote model；local_model 只通過 schema；Hermes CLI 與 Codex JSONL parser 差異；probe ready 但 invoke failed；transport ready 但 receipt/source mismatch；文件 commit 後 gate evidence 失效。各案例指定 source/test 與預期判讀，不新增 fake live PASS。

### R8 — Source 與更新規則

文件開頭記錄 inspected source commit、日期與範圍；每列用 repo-relative Markdown link 加 symbol。更新相關 runner 時，只重查受影響列、其 caller 和直接 test。舊 commit 的結果保留為 snapshot，不繼承為 current readiness。

reference 是導航資料，不是新的 authority；與程式／契約不一致時列出差異，以 live source 重查，不自動修改 source 以符合文件。

### R9 — 探索輔助與 Token 控制

- 先用 `context_agent.py --collect-only`，經 Memory Hub 的既有 adapter 取得 bounded hints；只消費 `ready` 且可核對的內容。
- 本地 agent invocation 走 `agent_workflow.py`；Context/Review 各最多一次 bounded 呼叫起步，有具體輸出缺陷才重試。
- Graph 使用既有 `status`／`query`／`validate` 只讀入口；不為本文件 build/rebuild projection。
- Sidecar 使用既有 bounded artifact inspection；missing/stale/blocked 時回到 source，不啟動 Gateway 或實測 network recall。
- 實作每 Task 限 1–3 tracked files、diff <500 行、短 context；agent input ≤12k estimated tokens，Context output ≤1.5k tokens；Review 依既有上限。
- 記錄估算與實際 telemetry 的區別。節省比例是目標，沒有同工作量比較前不報百分比成效。

### R10 — `gpt-5.6-luna` / `medium` 執行約束

每 Task 要有明確 model/effort、先讀清單、allowlist、輸出格式、完成條件、停止條件與 checkpoint。不要依賴長對話記憶或要求模型自行決定下一 Task。

`gpt-5.6-luna`／`medium` 是使用者指定的實作設定；本 spec 不聲稱模型已在本 session 切換，也不根據未測得的模型能力估計成功率或成本。執行者核對實際 profile；不可把不轉交 model flags 的 wrapper 當作設定成功。

## 6. 最小變更與驗證

Implementation allowlist：

- 新增 `docs/agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md`。
- 修改 `docs/agents/NBS_AGENT_ARCHITECTURE.md`：只增加 reference 連結。
- 修改 `docs/agents/CODEX_AGENT_DISPATCH.md`：只增加 reference 連結。

不更新 System Map、ADR 或 Obsidian；本輪 spec/plan 是使用者直接要求的設計交付，並非 post-acceptance documentation backfill。

驗證分兩層：spec/plan 的 source 核對、Markdown links／fences／無佔位符與 self-review；將來實作的 per-Task findings-first Review、focused verification、checkpoint，以及既有 fresh Full pytest／Hermes／UI／aggregate release gates。規劃完成不等於 implementation 或 release-ready。

## 7. Acceptance 與 rollback

1. R1–R10 均可定位到 reference 內容與 plan Task。
2. 第 4 節所有 runner roles 有對應列；Local Model 的未驗證項明示。
3. 所有 links 可解析，symbol 存在；limits/status 來源逐條可查。
4. 文件與兩個導航變更限定於 allowlist，原契約內容維持；無 runtime mutation。
5. 六個驗收情境的結論一致，尤其 transport ready 不提升為 release PASS。
6. 實作的 fresh Review 與 release evidence 獨立保存，不沿用此 spec 的盤點作為 PASS。

Rollback 為 revert 本次 reference／導航的 checkpoint commit，保留歷史 evidence；無資料 migration、provider 切換或服務操作。

## 8. Self-review

已核對：採 docs-only 方案；三類 transport 與 inference 位置分開；identity 欄位沿用 source；helper 與 public CLI、不同行為 parser、request 與 persisted binding 分開。R1–R10 映射到單一三檔 Task 及其 checkpoints，避免導航變更另開一次 agent cycle。

## 9. 本輪探索 evidence

- Live Git：`main` 與 `origin/main` 對齊至上述 source commit；既有 `.nbs_runtime/` 保留。
- 本地 CLI：`codex-cli 0.150.1`。Context command 明確指定 `--model gpt-5.6-luna` 與 `model_reasoning_effort="medium"`、read-only、ephemeral；不代表所有其他 wrappers 都已設定該模型。
- 第一個完整 spec workflow run：`run-6cec9dbfaab34b07be2a7f77ea666fbe`，`context_overflow`；在模型呼叫前拒絕，未增加預算上限。
- Compact brief workflow run：`run-3e89c6e454a0478aa056d58765741617`；一次 local Context Agent 呼叫，telemetry `estimatedInputTokens=8215`、`outputTokens=1145`、`cacheHit=false`。這些是 runtime token 估算／欄位，非帳戶計費實測。
- Context 結果為 `dirty_worktree`，因新建文件及既有 runtime lock；採用其具體提示重新查核 source，不將它轉成 ready、Review PASS 或 dispatch authorization。
- Memory Hub：`ready/enriched`，3 項 fresh governance/evidence/skill hints；只補充 bounded fallback 邊界，沒有 runner live capability proof。
- Memory Sidecar：既有 artifact inspection `status=pass`，1 份 hints、0 份 telemetry，`invocations=0`、`writes=0`；不代表 real recall 或 provider deployment 已驗證。
- Governance Graph：只呼叫第一個 run 的 `status`；回 `blocked`／`workflow artifact must be a regular file`。未 build projection、未修改 canonical artifacts；用 source/contract 繼續規劃。
- 本輪執行六檔現有測試：runner identity、identity envelope、Hermes CLI adapter／receipt、live A/B helper、remote turn receipt；`54 passed in 2.31s`。這是 source 行為核對，不是 live provider test 或整套 release acceptance。

上述 runtime reports 只在本機 `.nbs_agent_runtime/` 留存，供本輪追溯；後續新 source 必須重新收集。plan 路徑如下：
- [Implementation plan](../plans/2026-09-04-runner-topology-transport-matrix.md)
