# Acceptance Pipeline Hardening 設計 Spec

> 狀態：Draft，供使用者 review
> 日期：2026-09-11
> 適用專案：`nbs_analytics`
> 設計來源：2026-09-10 驗收板塊診斷

## 1. 摘要

本 spec 將驗收板塊整理成一個可恢復、可追溯、fail-closed 的 Acceptance Control Plane。目標不是降低 Review、Full pytest、Hermes 或 UI acceptance 的門檻，而是消除「阻塞後不能續跑、source freshness fail-open、跨模組契約互相打架，以及 verification complete 被誤讀為 release PASS」造成的循環。

現有 P1 保存紀錄包含 53 個 verification sessions、40 個不同 head；其中 33 次為 `review_changes_required`，11 次為 runner 阻塞。這表示主要浪費來自狀態恢復、證據重建與契約修補，而不是單純 pytest 太慢。本 spec 以這些已觀察症狀作為驗收優化的基準。

正式業務口徑維持「不含掛賬核銷與 `TT 退款轉團款`」；2026-05 frozen baseline 維持 `HKD 12,057,968`。

## 2. 問題定義

### 2.1 阻塞 session 的續跑路徑不一致

CLI 允許 `blocked_runner_capability` 與 `blocked_runner_transport` 進入 Review 命令，但 `run_pre_review()` 仍只接受 `sealed`。因此 timeout 或 transport failure 後，操作者不能在原 session 只重試失敗步驟，通常只能重建 session。

### 2.2 session reload 可能跳過 source freshness

`VerificationChain.load()` 沒有恢復 `SourceProbe` 時，`_check_fresh()` 會直接回傳 True。這會令後續 gate 只相信歷史 seal，而不是重新核對當前 HEAD、brief、dirty worktree 與 diff。

### 2.3 Review finding 的契約語意未被單一 truth table 固定

曾出現兩種互相衝突的判斷：一種把任何 session reuse 視為污染；另一種則指出同一 slot 的 observation、ledger、quality 必須共享 session。若沒有共同 binding validator，修正一個 finding 可能製造下一個 finding。

### 2.4 完成狀態有兩套語意

Verification chain 的 completion attestation 目前只觀察 Strict Review、Full pytest 與 Hermes；正式 release gate 另外要求 UI acceptance 與 deterministic aggregate。兩者若使用相似的 `complete`／`PASS` 文案，容易讓人誤以為已具備 release 條件。

### 2.5 CI 存在可移除的重複準備工作

Release aggregate 只需讀取三份 bounded JSON，卻重新建立 virtualenv 並安裝完整 `requirements.txt`。PR 亦未明確取消已被新 commit 取代的舊 run，造成等待時間與 runner 消耗。

## 3. 設計目標

### 必須達成

1. 任何 retryable runner transport failure 都能在 source 未變時續跑原 session，且不重跑已通過的 gate。
2. 每個後續 gate 都必須重新確認 source freshness；沒有可靠 probe 時必須 `blocked`，不可 fail-open。
3. 用單一 binding truth table 同時約束 preflight、Review、Full pytest 與 Hermes 使用的 paired evidence。
4. 清楚分離 `review_passed`、`verification_complete` 與 `release_ready`。
5. 保留 Full pytest、Hermes、UI acceptance 的獨立性與 fail-closed 行為。
6. 所有優化都能以 fingerprint、attempt、duration、retry、cache hit/miss 與 blocked reason 衡量。
7. 保留既有 `verification-*`、`*-gate-v1` 與 `release-gate-result-v1` 讀取相容性；不以大規模 schema rename 作為優化手段。

### 不追求

- 不承諾用快取取代 fresh acceptance。
- 不承諾把 Full pytest、Hermes 或 UI acceptance 降為抽樣測試。
- 不以歷史 PASS、Memory Hub、Memory Sidecar、Governance Graph 或 Agent Operations 取代本輪 evidence。
- 不把環境不備妥、model mismatch 或 malformed evidence 靜默轉成 retry。

## 4. 核心設計

### 4.1 驗收狀態模型

流程分成兩層：

```text
sealed
  -> pre_review
  -> strict_review / review_passed
  -> full_pytest / full_verification_passed
  -> hermes / hermes_passed
  -> verification_complete

verification_complete + ui_acceptance + deterministic aggregate
  -> release_ready
```

狀態語意固定如下：

| 狀態 | 大白話 | 可否直接宣稱 release PASS |
|---|---|---|
| `sealed` | 已把本輪 source 狀態封存 | 否 |
| `review_passed` | Review 認為程式與需求可進完整驗收 | 否 |
| `verification_complete` | Review、Full pytest、Hermes 已通過 | 否，仍缺 UI/aggregate |
| `release_ready` | 三個 release child gates 與 aggregate 都通過 | 是 |
| `blocked_*` | 執行條件不足，尚未證明失敗或成功 | 否 |
| `stale_source` | 原 seal 已不能代表目前 source | 否，必須重新 seal |
| `*_failed` | 該 gate 有可重現的失敗證據 | 否，需修正後重跑 |

既有 gate artifact schema 維持不變；新增或調整的是狀態映射、恢復規則與顯示文案，避免破壞現有 consumers。

### 4.2 Recovery policy

每個 session 只在 source fingerprint、task contract、policy fingerprint 與 runner identity 仍相容時恢復。

| 阻塞類型 | 行為 | 自動重試 |
|---|---|---|
| `blocked_runner_transport` | 原 session 只重試目前失敗的 runner gate；已 PASS gate 不重跑 | 最多 2 次 |
| `blocked_runner_capability` | 顯示缺少的 executable/model/cache/capability；修正 runner 後建立新 session 再做 preflight，不能盲目重試舊 session | 不自動重試 |
| `invalid_evidence` | 重新驗證 evidence；source 未變且修正後合法才可繼續 | 最多 1 次重驗 |
| `review_changes_required` | 代表 source 或實作需要改變，建立新 source-bound session | 不在原 session 重跑 |
| `stale_source` | 舊 evidence 不得沿用，重新 seal | 不可重試 |
| Full/Hermes deterministic failure | 保留失敗 evidence 與 diagnostics；程式或正式資料狀態改變後建立新 session | 不自動重試 |

每次嘗試寫入 bounded attempt metadata：`attemptId`、`gate`、`sourceFingerprint`、`runnerFingerprint`、`startedAt`、`finishedAt`、`outcome`、`blockedReason`、`cacheHit` 與可用時的 token usage。attempt 不可覆寫既有 PASS evidence。

### 4.3 Source freshness

所有由 CLI reload 的 chain 都必須依 session manifest 重建 source probe，至少核對：

- current `HEAD` 與 sealed `headSha`；
- brief bytes fingerprint；
- task contract 與 policy fingerprint；
- base/head diff fingerprint；
- dirty worktree 的檔案路徑與實際內容 fingerprint，包括 untracked evidence；
- source schema／collector version。

缺少 project root、brief path、base SHA 或無法執行 probe 時，結果為 `blocked_source_probe`，不能視為 fresh。每一個 gate boundary 都要 probe；probe 也應在長時間 runner 完成後再次執行，防止執行期間 source 漂移。

### 4.4 Session binding truth table

每筆 paired evidence 以以下 tuple 綁定：

```text
(slotId, sessionId, role, recordFingerprint)
```

合法案例：

- 同一 `slotId`、同一 `sessionId` 下各有一筆 observation、ledger、quality；
- 每筆 record fingerprint 穩定，沒有覆寫；
- 比較時所有角色的 slot/session 關係一致。

非法案例：

- 同一 session 出現在不同 slot；
- 同一 slot 重複註冊同一 role；
- 相同 identity 對應不同 record fingerprint；
- 缺少 role、slot 或 session；
- observation、ledger、quality 的 binding 不一致。

這個 validator 必須是單一 read-only pure function，所有入口共用；Memory Hub、Memory Sidecar 與 Governance Graph 只能提供 bounded context，不能改寫 binding 結果。

### 4.5 Review 與 evidence cache

Review batch cache 只能在以下 identity 完全相同時命中：session、batch、source、patch、payload、Review schema 與 result fingerprint。cache hit 只能省略同一批 LLM invocation，不可省略 source freshness、coverage validation 或 final aggregate。

若任何 fingerprint 改變，必須建立新 batch result；不得以「內容看起來相似」重用。重複 finding 只在 canonical finding fingerprint 相同時去重，不能因文字相似而刪除不同風險。

### 4.6 Release gate coordination

`release-gate-result-v1` 保持為正式 release readiness aggregate。它只接受同一 `commitSha`、同一 source fingerprint、fresh 且 schema 合法的：

- `full_pytest`；
- `hermes`；
- `ui_acceptance`。

Verification chain 的 `verification_complete` 是內部驗證完成，不自動升格為 `release_ready`。只有 deterministic aggregate 產生 `PASS`，才可顯示 release-ready。

### 4.7 CI 執行效率

在不改 gate 語意的前提下：

- aggregate job 改為最小 Python runtime 或沿用已可驗證的 runner，不重裝完整依賴；
- child jobs 使用 setup-python dependency cache；
- PR workflow 以 head SHA 隔離 artifact，並取消已被更新 commit 取代的 in-progress PR run；release tag run 不可被取消；
- 最終 integration 前先同步 base branch，避免「CI 綠後才發現 branch behind，再整輪重跑」；
- child gate 仍各自執行並上傳 bounded artifact，aggregate 只做 deterministic validation。

## 5. 介面與檔案邊界

預計 implementation 只觸及以下驗收控制面：

- `backend/agents/verification_chain.py`：狀態恢復、source probe、attempt metadata、completion status mapping。
- `scripts/verification_chain.py`：CLI reload、resume/preflight 與錯誤分類。
- `backend/agents/verification_session.py`：必要的 session／attempt schema 相容性。
- `backend/agents/agent_eval_report.py`：共用 session binding validator。
- `backend/agents/release_gate_models.py`、`scripts/release_gate.py`：release-ready 映射與 aggregate 邊界。
- `backend/agents/review_agent_service.py`：cache/coverage/telemetry 的最小補強。
- `.github/workflows/release-gates.yml`：CI 安裝、快取與 concurrency 調整。
- 對應 `tests/test_verification_chain*.py`、`tests/test_agent_eval_report.py`、`tests/test_release_gate*.py`、`tests/test_review_agent_service.py` 與 workflow contract tests。

本 spec 不授權修改正式 SQLite、baseline、revenue scope、business rules、export schema、production cache、Git integration 或任何正式資料寫入流程。

## 6. 驗收標準

### 正確性

1. Transport timeout 後，同一 source-bound session 能只重試失敗 gate；測試證明前置 gate invocation count 不增加。
2. CLI reload 後若 HEAD、brief、diff 或 dirty file 內容改變，必須 `stale_source` 或 `blocked_source_probe`，不能繼續。
3. 沒有 source probe 時不能回傳 fresh 或 PASS。
4. session binding truth table 的合法與非法案例在 preflight、Review 與比較流程中結果一致。
5. `verification_complete` 不包含 UI acceptance；`release_ready` 只由三個 child gates 的 deterministic aggregate 產生。
6. malformed、stale、identity mismatch、FAIL、BLOCKED、MISSING evidence 仍 fail closed。
7. 正式 72-slot acceptance 必須逐 slot 綁定、排除污染資料，污染資料不能進入 paired comparison，也不能只靠總數相等而通過。

### 效率

1. stable fingerprint 下的 Review batch 不重複呼叫 runner。
2. transient retry 不重跑已通過 gate。
3. CI aggregate 不再進行不必要的完整依賴安裝。
4. telemetry 能比較修正前後的總耗時、等待時間、retry 次數、cache hit rate 與 token usage；時間改善以實測結果報告，不預先宣稱固定倍數。

### 相容性與安全

- 既有 v1 artifact 可被讀取；未知欄位不得破壞 fail-closed parser。
- 既有模型 alias／runner identity 不因本 spec 自動改名；新 identity 只在明確配置與 preflight 通過時使用。
- canonical evidence 的 authority 不轉移給 Memory Hub、Memory Sidecar、Governance Graph 或 UI。
- tracked worktree 中現有 3 個 unrelated dirty files 必須保留，不得被本 Task 加入修改或 commit。

## 7. 測試與觀測方案

採 TDD，先補最小 regression tests，再逐項修改實作：

1. state transition matrix：每種 blocked／failed 狀態的 resume、new-session、terminal 行為。
2. source probe reload：CLI load、dirty content mutation、HEAD mutation、probe unavailable。
3. session binding：同 slot 合法 pairing、跨 slot reuse、同 role duplicate、fingerprint overwrite。
4. Review cache：same fingerprint hit、changed payload miss、incomplete batch coverage fail closed。
5. release aggregate：三 gate identity mismatch、freshness expiry、missing gate、`verification_complete` 不升格。
6. workflow contract：cache、concurrency、最小 aggregate runtime 與 artifact isolation。
7. controlled benchmark：記錄基準版本與優化後版本，不把單次本地速度直接當成 release acceptance。

## 8. 風險與回退

- **狀態 schema 相容性風險**：先以 adapter／向後相容讀取處理，保留 v1 artifact，不能直接刪舊狀態。
- **retry 放寬造成污染**：只有 source、contract、policy、runner identity 全部相容才允許 retry；attempt 不可覆寫 PASS。
- **CI cache 誤用舊證據**：cache 只快取 dependencies 或同一 fingerprint 的 bounded Review batch；release child artifacts 必須以 commit/source identity 驗證。
- **契約修正再度互相衝突**：所有 session binding 規則先由 truth table tests 鎖定，Review finding 必須附可重現案例。
- **回退方式**：若任一 gate 出現新誤判，保留完整 failure artifact，回退該單一 checkpoint；不修改正式資料、不刪除歷史 evidence。

## 9. 明確不納入本計畫

- P2 的新 agent／memory 功能本身；
- 修改模型權重、購買或自動選擇外部模型；
- 將 Memory Hub、Memory Sidecar、Governance Graph 變成 approval、dispatch、runtime 或 release authority；
- 修改正式 revenue 計算、SQLite schema、baseline promotion、rollback apply 或 export identity；
- 以手工產生的 PASS artifact、歷史 CI 結果或 UI snapshot 取代本輪 fresh gate。
