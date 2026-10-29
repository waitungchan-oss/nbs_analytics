# NBS 本地 Agent 與記憶元件：重新盤點與優化建議

更新日期：2026-10-29；版本：1.1（延續 2026-09-05 初盤）

本次 source 以乾淨的 `main@4f287ec76a552826214d558e7954fa28c9036648` worktree 為準；runtime metadata 取自 `/Users/chanwaitung2025/Downloads/nbs_analytics` 的本機工作目錄。兩者不是同一 checkout：該工作目錄目前在 `codex/beta-export-dedup`，有未提交修改及未追蹤檔案。所有資料均為 2026-10-29 唯讀觀察；沒有呼叫模型／Memory Hub、建立 workflow、清理 cache、改 runtime 或正式資料。最新合併紀錄為 PR #79，merge commit `4f287ec`。

## 1. 先說結論

系統已有多個本地 Agent、證據與驗收元件；本次追蹤確認 Memory Hub catalog 內三個來源的期限已於 2026-09-17 到期，不能把舊快照中的查詢成功當成目前可用。Workflow CLI 現有 381 個 canonical run status，其中 370 個仍等待授權；這些是歷史狀態，不是正在執行的 Agent 數量。指定 cache 目錄配置量約 5.34 GiB，仍未判定哪些可以安全清理。

本次沒有查詢 Memory Hub，也沒有驗證過期內容是否會被拒絕；因此「catalog 有三筆 ready 記錄」不等於它們仍可供 Agent 使用。Sidecar／Offload 程式存在也不代表所有日常 Agent 都在使用。

你回報的省 Token 與良好實作結果是使用觀察。本次沒有跑 A/B，因此不把整體成效單獨歸因於其中一個元件。

## 2. 大白話速查

| 元件 | 它幫你做什麼 | 本次狀態 |
|---|---|---|
| Evidence Collector | 開工前的資料整理員：先把相關文件、程式和 Git 差異找出來，裁成一份小資料包，讓你和 AI 少翻資料。 | 已有程式與流程接線 |
| Context Agent | 先讀案情的助理：幫你整理任務範圍、涉及檔案、風險與應該怎樣驗證。 | Hub 接入路徑已核對 |
| Implementation Agent | 按批准工單做事的工程師：執行你批准的一個修改任務，在隔離區改指定檔案，再由可信任控制程式套用。 | 已有程式；本次未啟動 |
| Review Agent | 專門找遺漏的審稿人：拿需求對照實際修改，找錯誤、缺漏與沒有被測到的情況。 | 已有程式；本次未啟動 |
| Documentation Agent | 驗證之後整理紀錄的編輯：根據已驗證的結果提出文件更新草稿，讓以後的人知道改了什麼。 | 已有 runner；本次未啟動 |
| Memory Hub | 共用的精簡手冊櫃：保存整理好的專案知識，先檢查來源、期限和誰有權限，再交給需要的人。 | 3 筆 catalog 記錄；來源期限已過，本次未查詢 |
| Memory Sidecar | 只遞幾張有用便條的助理：把記憶來源送來的提示檢查一遍，限量、限時交給 Agent；無效就省略。 | 受控讀取；writer 關閉 |
| Short-term Offload | 大份工作資料的臨時櫃：把很長的工具輸出先存檔，工作時只帶摘要和引用，需要細節再取回。 | 有實作；主部署目錄缺席 |
| Workflow CLI | 管理進度與批准點的工單台：把任務串起來，記錄走到哪一步，應該等誰批准。它本身不需要像模型一樣思考。 | 有歷史 artifacts；非即時狀態 |
| Runner / Adapter | 把工作送給模型的接線員：把輸入交給指定 CLI 或 API，限制時間與輸出大小，再解析結果。 | CLI 與遠端路徑已有 source |
| Local Model | 真正把模型腦袋放在自己電腦：只有模型權重與推論 runtime 真的在本機，才能說模型也本地部署。 | identity schema；部署未驗證 |
| Canonical Artifacts | 工作的原始收據與檔案櫃：保存 Task、Review、驗證與版本對應的正式證據，供人查核。 | 主部署已有歷史紀錄 |
| Governance Graph | 把任務和證據連起來的地圖：讓你看見某個 Task 對應哪些修改、驗證與風險，方便追溯。 | runs 中找到 4 份投影 |
| Agent Operations | 讓你查看工作的進度看板：把 workflow 和證據整理成人能看懂的畫面。 | 有 Streamlit 讀取程式 |
| Hermes Acceptance | 檢查系統能否交付的驗收員：核對服務、資料完整性、baseline 和相關測試，輸出驗收結果。 | 有檢查程式；本次未執行 |
| Release Gates | 交付前的分項成績單：Review、完整測試、Hermes、UI 分開評分，再核對是否屬於同一版本。 | 規則與 scripts 已有 |

**Agent 是做事的人，Memory Hub 是手冊櫃，Sidecar 是遞便條的助理，Offload 是臨時資料櫃。** Runner 把工作送給模型，Workflow 管理批准點，Graph 和 Operations 幫你查看證據。

模型推論可能在遠端；本地 CLI、檔案與記憶不表示模型權重也在本機。MCP 是另一種接入方式，不能從 service／adapter／plugin 名稱推斷已提供 MCP server。

## 3. 2026-10-29 唯讀追蹤結果

| 核對事項 | 結果 | 解讀限制 |
|---|---|---|
| Source | 乾淨 `main` worktree：`4f287ec76a552826214d558e7954fa28c9036648` | 對照用 runtime 目錄是另一個 dirty checkout；不混稱同一 source |
| Memory Hub catalog | 3 records、3 sources、4,711 bytes；policy `defaultDecision=deny`，僅列 `context-agent` | catalog 的 `builtFromHead` 為 `476cc809...`，早於目前 main；本次未發查詢 |
| Hub freshness | 三個來源 `expiresAt=2026-09-17T00:00:00Z`，已逾期 | catalog record 的 `freshness= fresh` 欄位不能覆蓋來源期限；需先核對來源再走正式刷新流程 |
| Offload | `.nbs_agent_runtime/short-term-offload` 不存在 | 只代表這個本機工作目錄；不推論其他 worktree／隔離實驗 |
| Workflow status | CLI `list` 回傳 381 個 run：370 awaiting_authorization、9 completed、2 blocked | 唯讀 `list` 使用 canonical `status.json`；不是 active process 數 |
| runs 根目錄 | 393 個直接子目錄；CLI 可解析 381 個，另有 12 個未列為 canonical run | 不推斷未列目錄的用途或狀態；未修改、未清理 |
| Governance Graph | 找到 4 個 `governance-graph.json` | 只確認檔案存在，未驗每份 schema／freshness |
| 指定目錄容量 | cache 5,597,332 KiB；runs 21,376 KiB；reports 3,840 KiB；Memory Hub 12 KiB | `du -sk` 配置用量，僅涵蓋表格列出的目錄，不代表可刪除空間 |
| PR #79 | merged；Full pytest、Hermes、UI acceptance、Hermes Governance Graph、macOS sandbox 與 release aggregate 均成功 | 這些是該文件 PR 的 CI；不等於本次靜態快照做了完整 NBS runtime acceptance |

本次追蹤沒有呼叫 Hub adapter，故不確認目前可取回哪些 hints。原 2026-09-05 對 adapter 與 Sidecar 的接線觀察保留作歷史 source note；不得解讀為目前 query 命中結果。

來源：[Hub 查詢實作](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/memory_hub_service.py:76)、[Context 接線](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/context_agent.py:105)、[Offload 呼叫](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/hermes_live_ab_runner.py:265)。

## 4. 磁碟盤點

以下是 `du -sk` 的磁碟配置用量；1 MiB = 1,024 KiB，1 GiB = 1,048,576 KiB。各列是不重疊的指定目錄，但不是整個專案總量。

| 類別 | KiB | 約值 |
|---|---:|---:|
| 報表／AI cache | 5,597,332 | 5.34 GiB |
| Agent 專用 Codex home | 82,236 | 80.31 MiB |
| Workflow runs | 21,376 | 20.88 MiB |
| Reports | 3,840 | 3.75 MiB |
| Review inputs | 2,204 | 2.15 MiB |
| System runtime | 1,076 | 1.05 MiB |
| Memory Hub | 12 | 12 KiB |

報表／AI cache 約 5.34 GiB，比 Memory Hub 更值得先做可清理性盤點。這不代表它全部是垃圾：active version、manifest、trusted reference 及其他輸出可能仍依賴其中資料。

現有 Offload 的 TTL 預設 30 分鐘、上限 24 小時，每 run 最多 20 個 artifacts／200,000 bytes。Workflow 設定是 90 天、保留最新 30 個 terminal runs；單 stage 5 MiB hard cap，單 run 25 MiB soft cap。soft cap、過期、符合清理條件、實際釋放磁碟，是不同狀態。

Workflow 的 housekeeping 已接到 run/start 後，無須先新建一個清理器。但現行 retention 只 compact 合資格 completed runs；370 份 awaiting_authorization 不能直接列為可刪除候選。

## 5. 逐項解釋

### Evidence Collector

先把相關文件、程式和 Git 差異找出來，裁成一份小資料包，讓你和 AI 少翻資料。

- 工作方式：輸入：Brief、repo、搜尋條件。輸出：附來源與指紋的 compact evidence。
- 和記憶的關係：不是長期記憶；它整理這次工作的當前證據。
- 本次核對：Python collector 已存在；collect-only 可以不呼叫模型。這次盤點直接使用唯讀工具，沒有建立新 workflow run。
- 優化方向：記錄資料包大小與實際使用的來源，減少無關內容；先利用已有工具。

來源：[backend/agents/evidence_collector.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/evidence_collector.py:147)、[scripts/context_agent.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/context_agent.py:71)。

### Context Agent

幫你整理任務範圍、涉及檔案、風險與應該怎樣驗證。

- 工作方式：輸入：證據包＋可選提示。輸出：任務理解、限制、檔案與測試建議。
- 和記憶的關係：policy catalog 只列 context-agent；本次未查詢 Hub，且三個來源已逾期。
- 本次核對：本次沒有呼叫 Hub adapter；catalog 來源到期，不能由舊查詢結果推論目前可用。
- 優化方向：先核對來源期限與內容，再用批准且 bounded 的任務追蹤 hints 是否實際被消費。

來源：[scripts/context_agent.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/context_agent.py:51)、[scripts/context_agent.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/context_agent.py:105)、[backend/agents/context_agent_service.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/context_agent_service.py:91)。

### Implementation Agent

執行你批准的一個修改任務，在隔離區改指定檔案，再由可信任控制程式套用。

- 工作方式：輸入：批准的 Task contract、證據、允許修改清單。輸出：diff 與實作報告。
- 和記憶的關係：可接收可選的 Memory Hub observation；不是全體 Agent 已能查詢共享記憶。
- 本次核對：隔離 worker 與 controller 分工已實作。不得自行改正式資料或 commit；本次沒有派工。
- 優化方向：用 Review 返工原因改善工單與完成條件；保留既有 sandbox 與授權邊界。

來源：[backend/agents/implementation_agent_service.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/implementation_agent_service.py:105)、[docs/agents/CODEX_AGENT_DISPATCH.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/CODEX_AGENT_DISPATCH.md:101)。

### Review Agent

拿需求對照實際修改，找錯誤、缺漏與沒有被測到的情況。

- 工作方式：輸入：Task、diff、測試證據。輸出：findings、coverage 與 Review verdict。
- 和記憶的關係：可讀可選的記憶觀察；舊經驗不能取代對當前 diff 的檢查。
- 本次核對：Review service 有 memory_hub_evidence 輸入；runtime policy 只列 context-agent。
- 優化方向：統計同類 findings，把可以提前檢查的問題移到本地自檢。

來源：[backend/agents/review_agent_service.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/review_agent_service.py:399)、[docs/agents/REVIEW_AGENT_CONTRACT.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/REVIEW_AGENT_CONTRACT.md:1)。

### Documentation Agent

根據已驗證的結果提出文件更新草稿，讓以後的人知道改了什麼。

- 工作方式：輸入：documentation-evidence-v1。輸出：proposal；由批准的流程決定是否套用。
- 和記憶的關係：文件可能成為未來知識來源，但不會自動寫入 Memory Hub。
- 本次核對：文件流程支援 v1 proposal 與 v2 固定 target 的 preview／明確批准 apply；不得把靜態快照更新當作正式文件 runner application。
- 優化方向：優先留下決策理由、失敗教訓與證據引用，避免重複貼完整 logs。

來源：[backend/agents/documentation_codex_runner.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/documentation_codex_runner.py:38)、[docs/agents/DOCUMENTATION_AGENT_CONTRACT.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/DOCUMENTATION_AGENT_CONTRACT.md:1)。

### Memory Hub

保存整理好的專案知識，先檢查來源、期限和誰有權限，再交給需要的人。

- 工作方式：輸入：identity、scope、knowledge kinds 與 query。輸出：最多 3 條有來源的提示。
- 和記憶的關係：本機三份 catalog 共 4,711 bytes；配置用量 12 KiB；三筆記錄涵蓋治理／證據／skill，但來源期限已過。
- 本次核對：本次未查詢。catalog 仍有 3 個 records／3 個 sources，來源期限 2026-09-17 08:00 香港時間已過；record freshness 欄位不能覆蓋已過期的 source expiry。
- 優化方向：先審核並規劃到期更新；只在有查詢需求時補精簡業務經驗與相關性篩選，不必先上向量資料庫。

來源：[backend/agents/memory_hub_service.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/memory_hub_service.py:76)、[backend/agents/context_memory_hub_adapter.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/context_memory_hub_adapter.py:52)、[agent_config/memory_hub_catalog_deployment.json](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/agent_config/memory_hub_catalog_deployment.json:1)。

### Memory Sidecar

把記憶來源送來的提示檢查一遍，限量、限時交給 Agent；無效就省略。

- 工作方式：輸入：provider hints。輸出：經 schema、freshness、容量檢查的 memory-hints。
- 和記憶的關係：最多 3 條／6,000 bytes、等待 800ms；目前禁止 writer。它不是已自動累積經驗的資料庫。
- 本次核對：有 service、provider adapter、Hermes plugin/A-B 接線。沒有據此證明所有日常 Task 已啟用 recall。Hub adapter 也使用 MemoryHints 格式，但不代表繞經 Sidecar provider。
- 優化方向：先看真實 Task 的提示消費與 fallback 統計；依既有規則驗證 recall_on 效益，再決定擴用。

來源：[backend/agents/memory_sidecar_service.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/memory_sidecar_service.py:18)、[backend/agents/memory_sidecar_provider_adapter.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/memory_sidecar_provider_adapter.py:259)、[integrations/hermes_nbs_sidecar/plugin.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/integrations/hermes_nbs_sidecar/plugin.py:45)、[agent_config/memory_sidecar_policy.json](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/agent_config/memory_sidecar_policy.json:1)。

### Short-term Offload

把很長的工具輸出先存檔，工作時只帶摘要和引用，需要細節再取回。

- 工作方式：輸入：run/session、工具輸出、摘要。輸出：可校驗的引用和分段讀取。
- 和記憶的關係：TTL 預設 30 分鐘、上限 24 小時；每 run 上限 20 件、200,000 bytes。跨 run 總上限尚未證明。
- 本次核對：cleanup_expired 已實作；找到 CLI/service 清理入口和隔離 Hermes A/B 寫入接線。主部署沒有 short-term-offload 目錄；不代表其他 worktree 從未使用。
- 優化方向：先確認你真實 workflow 是否需要它、是否正在用它。需要時驗證清理時機與跨 run 容量，再考慮更廣接入。

來源：[backend/agents/short_term_offload_policy.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/short_term_offload_policy.py:13)、[backend/agents/short_term_offload_store.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/short_term_offload_store.py:117)、[scripts/short_term_offload.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/short_term_offload.py:34)、[scripts/hermes_live_ab_runner.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/hermes_live_ab_runner.py:265)。

### Workflow CLI

把任務串起來，記錄走到哪一步，應該等誰批准。它本身不需要像模型一樣思考。

- 工作方式：輸入：run／approve／status 等明確命令。輸出：run artifacts 和狀態。
- 和記憶的關係：流程紀錄是證據，不等同可直接供模型用的知識。
- 本次核對：Workflow CLI canonical list 讀到 381 個 status：370 awaiting_authorization、9 completed、2 blocked。這些是儲存狀態，不是正在運行的 Agent。
- 優化方向：先對照 Git/contract 確認哪些舊待批准 run 已無後續；現有 retention 保護非 completed run，不能直接清掉。

來源：[scripts/agent_workflow.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/agent_workflow.py:150)、[backend/agents/workflow_orchestrator.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/workflow_orchestrator.py:150)、[backend/agents/workflow_retention.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/workflow_retention.py:133)。

### Runner / Adapter

把輸入交給指定 CLI 或 API，限制時間與輸出大小，再解析結果。

- 工作方式：輸入：批准的執行設定＋payload。輸出：模型回應與身份／用量紀錄。
- 和記憶的關係：接線本身不會增加記憶；是否加提示由 caller 決定。
- 本次核對：local_cli 是啟動通道；其模型仍可能在遠端。另有 DeepSeek remote API 路徑，本次未發起模型請求。
- 優化方向：用同一 Task 的 identity、耗時與用量找出等待來源，避免把模型、記憶與工具的成本混在一起。

來源：[backend/agents/agent_runtime.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/agent_runtime.py:1)、[scripts/hermes_turn_receipt.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/hermes_turn_receipt.py:1)、[docs/agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md:48)。

### Local Model

只有模型權重與推論 runtime 真的在本機，才能說模型也本地部署。

- 工作方式：理想輸入：prompt。輸出：本機模型推論。此圖不宣稱該路徑已存在。
- 和記憶的關係：本地推論與本地記憶是兩件事：記憶在本地，不代表推論在本地。
- 本次核對：本次 source 支援 local_model 身份；沒有核實權重、runtime、production caller 或 live receipt。
- 優化方向：僅在有離線、隱私或可驗證成本需求時另做 feasibility，先測同一任務品質。

來源：[backend/agents/runner_identity.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/runner_identity.py:1)、[docs/agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/RUNNER_TOPOLOGY_AND_TRANSPORT_MATRIX.md:58)。

### Canonical Artifacts

保存 Task、Review、驗證與版本對應的正式證據，供人查核。

- 工作方式：輸入：各階段產生的紀錄。輸出：可追溯的原始 evidence。
- 和記憶的關係：保存證據和摘要記憶各有用途。Memory、Graph 只能引用它，不能反過來宣告它通過。
- 本次核對：runs 約 20.88 MiB，reports 約 3.75 MiB。檔案數量和 completed 字樣都不是本次 release PASS。
- 優化方向：保留來源與引用關係，先識別哪些 artifacts 合乎 retention 條件，再提出可回復的清理計畫。

來源：[backend/agents/workflow_store.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/workflow_store.py:1)、[agent_config/workflow_retention.json](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/agent_config/workflow_retention.json:1)。

### Governance Graph

讓你看見某個 Task 對應哪些修改、驗證與風險，方便追溯。

- 工作方式：輸入：canonical artifacts。輸出：read-only 關係投影。
- 和記憶的關係：不是主記憶資料庫；不自行批准、派工或把失敗改成通過。
- 本次核對：本機 run 目錄中找到 4 份 governance-graph.json；本次只核實存在，未驗 schema/freshness。
- 優化方向：讓節點清楚顯示來源與時間，避免把舊投影看成即時狀態。

來源：[backend/agents/governance_graph_service.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/agents/governance_graph_service.py:41)、[docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/NBS_GOVERNANCE_GRAPH_CONTRACT.md:1)。

### Agent Operations

把 workflow 和證據整理成人能看懂的畫面。

- 工作方式：輸入：既有 artifacts。輸出：session snapshot；手動更新。
- 和記憶的關係：不保存第二套真相，也不是任務操作台。
- 本次核對：此頁的新地圖是離線盤點展示，不是正式 Agent Operations，也不會啟動任何 Agent。
- 優化方向：優先呈現 missing／stale／last observed 等可解釋狀態，讓人知道下一步需要查什麼。

來源：[backend/services/agent_operations_service.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/backend/services/agent_operations_service.py:106)、[docs/agents/NBS_AGENT_ARCHITECTURE.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/NBS_AGENT_ARCHITECTURE.md:403)。

### Hermes Acceptance

核對服務、資料完整性、baseline 和相關測試，輸出驗收結果。

- 工作方式：輸入：當前 source、profile、runtime 與證據。輸出：bounded acceptance report。
- 和記憶的關係：驗收讀取當前證據；舊 Memory 提示不能替它宣告 PASS。
- 本次核對：NBS deterministic Hermes acceptance 不需要模型推論；和呼叫遠端模型的 Hermes runner 角色不同。
- 優化方向：維持獨立、同 source 的證據。除非有未解風險，不增加重複驗證層。

來源：[scripts/hermes_post_change_check.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/hermes_post_change_check.py:1)、[scripts/hermes_gate.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/hermes_gate.py:1)、[NBS_HERMES_MONITORING.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/NBS_HERMES_MONITORING.md:7)。

### Release Gates

Review、完整測試、Hermes、UI 分開評分，再核對是否屬於同一版本。

- 工作方式：輸入：同 commit/source 的 fresh evidence。輸出：可否進行 release 的判定。
- 和記憶的關係：歷史成功可以提供線索，不能替本次版本拿分。
- 本次核對：PR #79 的 Full pytest、Hermes、UI acceptance、Hermes Governance Graph、macOS sandbox 與 release aggregate 均 PASS；Strict Review 不由 CI aggregate 推定。
- 優化方向：先把結果摘要與原始證據連好，減少人來回找資料的成本。

來源：[scripts/release_gate.py](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/scripts/release_gate.py:1)、[docs/agents/CODEX_AGENT_DISPATCH.md](/Users/chanwaitung2025/Downloads/nbs_analytics/.worktrees/beta-category-004014-live/docs/agents/CODEX_AGENT_DISPATCH.md:6)。


## 6. 下一步怎樣優化

### 01. 先處理記憶的到期與可用性

原因：Hub 的三個來源已於 2026-09-17 08:00（香港）到期；catalog 的 `builtFromHead` 也早於目前 main。此次沒有嘗試讀取過期 hints。

最快下一步：先做來源仍有效的 read-only 複核，形成批准的刷新／provision 計畫；不要只把日期延後。

- 範圍：只涵蓋 catalog 來源、hash、freshness 與可用提示數；本次未修改 catalog。
- 驗證：過期 fixture 不回傳；有效且批准的來源能回傳；權限仍 default deny。
- 回復：部署刷新需保留上一版 artifact；若舊版已過期，退回 canonical-only，不強行恢復舊提示。

### 02. 讓記憶更貼近真實任務

原因：2026-09-05 曾觀察到不同 query 回傳相同三條治理摘要；這是歷史 evidence。本次沒有查詢 Hub，不能據此描述目前的檢索行為。

最快下一步：等來源複核及正式刷新後，再挑幾個真實 Task，追蹤哪些 hints 被實際消費；需求明確後才設計小型標籤／關鍵字篩選。

- 範圍：先在 fixture／內容草稿比較；不啟用 Sidecar writer、不擴大 policy consumer。
- 驗證：相關問題命中正確來源；無關／過期資料不注入；同 workload off/on 看品質、Token、延遲。
- 回復：新檢索可回復既有三條共用摘要或 canonical-only；不更動正式業務資料。

### 03. 把容量優化對準 cache

原因：報表／AI cache 配置量約 5.34 GiB，Memory Hub 為 12 KiB；先列唯讀候選及核查引用，不直接刪除。

最快下一步：產生唯讀清理候選表，核對 active manifest、版本引用與重建成本，再提出 retention 規格。

- 範圍：candidate list 本身 zero-delete；排除 active artifacts、SQLite、備份、quarantine 和未授權 evidence。
- 驗證：無錯刪 active 版本；cache hit／下載不回歸；暫存 fixture 證明空間真的釋放。
- 回復：任何後續清理需先安排可回復隔離或可驗證重建；成本不明就保留。

### 04. 釐清累積的待批准紀錄

原因：canonical CLI `list` 目前列出 381 個 run status，其中 370 個 awaiting_authorization。這是儲存狀態，不是當前工作數。

最快下一步：對照 Task、contract、Git 與最新 evidence，找出重複或已無後續的 run；只提出狀態處理建議。

- 範圍：現行 retention 只處理合資格 completed；不可把待批准改成 completed 來清理。
- 驗證：保留可追溯關係；任何狀態變更需明確授權與契約支持。
- 回復：候選表可直接捨棄；不修改既有 run 或 event。

### 05. 把省 Token 的有效方法留下來

原因：你已觀察到更少 Token 和良好結果；下一步是找出哪些元件貢獻最大。

最快下一步：沿用既有用量報表，為相近 Task 補上模型、cache、提示是否被消費、Review 返工與耗時。

- 範圍：只整理現有 bounded metadata，不複製完整對話或敏感資料。
- 驗證：缺數據標未提供；相同 immutable workload 比較；不以小樣本宣稱普遍成效。
- 回復：停止額外紀錄即可；不影響原開發工作流。


目前優先事項是先核對 Hub 三個過期來源的 canonical 文件與 fingerprint，再決定是否依正式流程刷新；cache 與 workflow retention 盤點應分開進行。這些是建議，並非執行授權。

在引入向量庫、新 Agent、MCP 或本地模型之前，先明確需要改善哪個結果，例如找資料時間、提示命中、返工、等待或磁碟增長。現有簡單做法有效時可以保留。

## 7. 如何使用互動工具

以 HTTP 開啟本目錄的 index.html：

1. 「元件地圖」：點任一節點，讀作用、目前證據和下一步；可切換 2D／3D，用滑桿旋轉。
2. 「下一步」導覽：示意一個 Task 從收集證據走到驗證；不觸發真實 workflow。
3. 「大白話清單」：逐項閱讀，並跳回地圖看細節。
4. 「記憶與磁碟」：用線性圖比較實際容量；對數模式只是方便看小項目，長度不表示比例。
5. 「盤點證據」：下載 snapshot.json 與本 Markdown。

這是截至 2026-10-29 的靜態盤點快照；重新整理頁面不會重查 runtime 或 Hub。index.html 自帶資料，不依賴 CDN 或遠端字體；snapshot.json 和 REPORT.md 是獨立下載附件。需要重新開啟本地 HTTP 服務，可在此目錄執行：

```bash
python3 serve.py
```

服務只綁定 127.0.0.1，自選可用 port，以 stdout 印出的 URL 為準。僅提供 index.html、snapshot.json 與 REPORT.md；以 Ctrl+C 停止。

## 8. 驗證與授權邊界

本報告不是 Strict Review、full pytest、Hermes 或 UI release gate PASS。圖中的 connections 是 source／工作流程的說明，不是封包追蹤，也不是常駐 Agent 狀態。

正式口徑仍為「不含掛賬核銷與TT退款轉團款」，2026-05 baseline 仍以既有契約的 HKD 12,057,968 為準；本次沒有讀取或重新計算正式業務數值。

本次僅更新 `Summay/AGENT_MEMORY_MAP_2026-09-05/` 內的報告、QA、snapshot 與互動頁內嵌資料。正式 source、Memory Hub catalog、Sidecar writer、workflow state、SQLite、cache 與 retention policy 均未修改；此互動頁仍是人工更新的靜態快照，不是即時監控。
