# Release Gate Branch Protection Enforcement Specification

## 1. 文件目的

本 spec 定義如何把已運作的 `Release gate aggregate` 從「會執行的 CI check」提升為 GitHub `main` 分支不可繞過的 required status check。目標是讓 Pull Request 只有在 Full pytest、Hermes、UI acceptance 三份同 commit、fresh、source-bound evidence 全部通過後才能合併。

本 Task 是受控 GitHub tooling／repository policy 工作。它不修改正式 SQLite、baseline、revenue scope、GMV／退款規則、export schema、Agent approval／dispatch、Governance Graph、Memory Hub 或 Memory Sidecar authority。

正式業務口徑維持「不含掛賬核銷與 TT 退款轉團款」；2026-05 frozen baseline 維持 `HKD 12,057,968`。

## 2. 已批准設計決策

只把以下 check 設為 required：

```text
Release gate aggregate
```

不把 Full pytest、Hermes、UI acceptance 三個 child checks 重複加入 branch protection。Aggregate 已對三份 child evidence 做 deterministic、fail-closed 驗證；child check 失敗、被阻塞、缺失、過期或 identity mismatch 時，aggregate 不會取得成功狀態，因此 PR 仍被阻擋。

Strict Review、Required macOS sandbox capability、Hermes Governance Graph、Memory Hub、Memory Sidecar 與 Agent Operations 維持獨立 gates／observations，不併入本次 required-check policy。

## 3. Live baseline

2026-09-03 的 read-only inventory 顯示：

- Repository：`waitungchan-oss/nbs_analytics`，visibility `PUBLIC`。
- Default branch：`main`。
- 執行者具 repository `admin` 權限。
- `main` 尚未建立 branch protection；repository rulesets 為空。
- 最新成功的 `Release gate aggregate` 由 GitHub Actions app 提供，`app.slug=github-actions`、`app.id=15368`。
- PR #51 的 Full pytest、Hermes、UI acceptance、aggregate、sandbox 與 Governance Graph checks 均成功；這只證明 workflow 可運作，不代替本 Task 的 post-apply protection verification。

所有 live baseline 值在實作前必須重新查詢。若 repository、default branch、權限、check name、GitHub App identity 或現有 protection 與本節不同，實作者必須停止 mutation 並回報 drift，不得自行覆蓋未知 policy。

## 4. Protection contract

### 4.1 Target identity

```json
{
  "repository": "waitungchan-oss/nbs_analytics",
  "branch": "main",
  "requiredCheck": {
    "context": "Release gate aggregate",
    "appId": 15368,
    "appSlug": "github-actions"
  }
}
```

Required check 必須綁定 GitHub Actions app，不使用 `app_id=-1` 或 any-source。GitHub 官方文件說明，required check 可指定提供狀態的 GitHub App；這可避免其他來源以相同 context 名稱滿足 merge requirement。

### 4.2 Required status checks

GitHub Branch Protection API 的 desired state：

```json
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {
        "context": "Release gate aggregate",
        "app_id": 15368
      }
    ]
  }
}
```

Live API compatibility decision（2026-09-03）：GitHub Update Branch Protection 對
`contexts: []` 與非空 `checks` 同時存在回傳 HTTP 422；因此 PUT payload 不帶
`contexts`。成功 PUT 後，GitHub GET 可能 materialize
`contexts: ["Release gate aggregate"]`；validator 接受空陣列或這個 canonical
single-item representation，仍以 `checks` exact match 防止 child gate 混入。

`strict=true` 表示 PR branch 必須先包含最新 `main`，再以最新 commit 重新取得 aggregate PASS。舊 commit、舊 PR run 或歷史 handoff 不得滿足 requirement。

### 4.3 Pull Request 與 admin enforcement

`main` 必須要求 Pull Request，但本 Task 不新增人工 reviewer 數量要求：

```json
{
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0,
    "require_last_push_approval": false
  }
}
```

GitHub API 明確允許 `required_approving_review_count=0`，其效果是要求 PR flow，但不額外要求 reviewer。不得設定 `bypass_pull_request_allowances`；response 中該欄位只能缺失或為空集合。

### 4.4 其他 branch safety settings

本 Task 固定以下安全設定：

```json
{
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": false,
  "lock_branch": false,
  "allow_fork_syncing": false
}
```

這些值只避免因完整 PUT payload 意外啟用 force push、branch deletion 或 branch lock；本 Task 不引入 CODEOWNERS、signed commits、linear history、merge queue、conversation resolution、deployment gate 或 push restrictions。

## 5. Canonical policy artifact

實作應建立：

```text
agent_config/release_gate_branch_protection.json
```

Exact schema：

```json
{
  "schemaVersion": "nbs-release-gate-branch-protection-v1",
  "repository": "waitungchan-oss/nbs_analytics",
  "branch": "main",
  "requiredCheck": {
    "context": "Release gate aggregate",
    "appId": 15368,
    "appSlug": "github-actions"
  },
  "strict": true,
  "enforceAdmins": true,
  "requirePullRequest": true,
  "requiredApprovingReviewCount": 0,
  "bypassActors": [],
  "allowForcePushes": false,
  "allowDeletions": false
}
```

Unknown、missing、duplicate、wrong-type 或 unsafe values 必須 fail closed。Policy artifact 是 desired configuration，不是 GitHub live state、release verdict 或正式 business state。

## 6. Deterministic local tooling

實作應建立：

```text
scripts/release_gate_branch_protection.py
```

它只負責 deterministic policy rendering 與 live-response validation，不持有 token、不直接 mutate GitHub：

```python
load_policy(path: Path) -> BranchProtectionPolicy
build_update_payload(policy: BranchProtectionPolicy) -> dict[str, object]
validate_live_protection(
    policy: BranchProtectionPolicy,
    payload: Mapping[str, object],
) -> tuple[str, ...]
```

CLI contract：

```text
render --policy PATH --output PATH
verify --policy PATH --input PATH --output PATH
```

- `render` 只輸出 Branch Protection API PUT payload。
- `verify` 只驗證已保存的 GET response，輸出 bounded JSON report。
- Validation 無錯誤時 exit `0`；policy／live drift 時 exit `2`；CLI misuse 時 exit `3`。
- CLI 不執行 `gh api`、不讀環境 token、不 commit、不 push、不 merge。

GitHub mutation 由主 Codex 使用明確、單次、可稽核的 `gh api --method PUT --input ...` 執行。Governance Graph、Memory Hub、Memory Sidecar、本地 Agent 與 LLM 不得自行發出 mutation。

所有 GitHub REST calls 固定帶入 `Accept: application/vnd.github+json` 與 `X-GitHub-Api-Version: 2026-03-10`，避免 client default 或 API version drift。

## 7. Preflight 與 mutation transaction

Mutation 前必須依序通過：

1. Working tree clean，current branch 與 remote state 已知。
2. `gh auth status` 成功。
3. Repository identity exact match `waitungchan-oss/nbs_analytics`。
4. Default branch exact match `main`。
5. Token permissions 顯示 `admin=true`。
6. 最新成功 `Release gate aggregate` 的 context exact match，provider exact match `github-actions`，`app.id=15368`。
7. GET current protection 並保存 before snapshot；HTTP 404 保存為 `{"exists": false}`。
8. 若 existing protection 非空且與 spec baseline 不同，停止並要求新的 reconciliation；不得 destructive overwrite。
9. 使用 deterministic renderer 產生 PUT payload，保存 payload fingerprint。
10. 執行一次 PUT；不得 retry 不確定的 mutation。若 transport response 不確定，先 GET live state再決定是否重試。
11. 重新 GET、以 deterministic validator 驗證 exact desired state。

Snapshot、rendered payload、response 與 verification report 只保存於 `.nbs_agent_runtime/reports/`，不得 commit token、headers、absolute home path 或 raw secret。

## 8. Rollback

Rollback 必須由 before snapshot 決定：

- Before snapshot 是 `exists=false`：呼叫 `DELETE /repos/waitungchan-oss/nbs_analytics/branches/main/protection`。
- Before snapshot 有 protection：把 snapshot 正規化為 GitHub PUT request schema，再以單次 PUT 還原；不得直接把 GET response 原封不動送回 API。

Rollback 後重新 GET：

- 原本不存在 protection 時，預期 HTTP 404。
- 原本存在 protection 時，預期與 normalized before snapshot contract 相符。

不得用 rollback 修改 workflow、刪除 CI evidence、重寫 Git history、force push 或改正式業務資料。

## 9. Verification 與 acceptance criteria

完成條件：

1. Policy exact-schema tests PASS。
2. Renderer tests 證明只要求 `Release gate aggregate`，`strict=true`、`app_id=15368`、`enforce_admins=true`。
3. Validator tests 覆蓋 missing check、extra required check、wrong app、strict false、admins false、PR requirement missing、bypass actor、force push 與 deletion enabled。
4. GitHub GET live protection 顯示：
   - `required_status_checks.strict == true`
   - `required_status_checks.checks == [{"context":"Release gate aggregate","app_id":15368}]`
   - GitHub GET 可同步 materialize `contexts == ["Release gate aggregate"]`；只接受空陣列或這個 canonical aggregate，不接受其他 context。
   - `enforce_admins.enabled == true`
   - `required_pull_request_reviews.required_approving_review_count == 0`
   - 無 bypass actors；GitHub GET 可省略 `restrictions`，省略或 `null` 都代表無 branch restrictions，非 null 必須拒絕
   - force push 與 deletion disabled
5. `Release gate aggregate` 仍由 GitHub Actions 產生，workflow name 與 job name未漂移。
6. Strict Review、focused tests、full pytest、Hermes、UI acceptance 與 GitHub aggregate 各自使用 fresh evidence；不得以 API GET 或 Graph／Memory observation取代任何 gate。
7. `main`、`origin/main`、正式 SQLite、baseline、cache、business runtime 與受保護 canonical artifacts未被本地 tooling 修改。

不以直接 push、force push、建立故意失敗 PR 或修改正式 workflow 來測試 enforcement。GitHub live protection response 加上 required-check provider identity 是本 Task 的外部 acceptance evidence。

## 10. Model execution contract

實作模型固定為 `gpt-5.6-luna`、reasoning effort `medium`。為配合此模型的可靠執行範圍：

- 每個 Task 只處理一個明確 deliverable，目標不超過 15 turns。
- 每步提供 exact path、function signature、JSON shape、命令、expected exit code與 checkpoint。
- 先寫 failing test，再最小實作；禁止一次生成跨 Task 大型 patch。
- 每個 Task 使用 fresh Context bundle；不得把上一 Task PASS 當下一 Task evidence。
- 本地 Context／Review Agent 只用 approved local CLI runner；不在 Codex 對話內 spawn／poll agents。
- Governance Graph、Memory Hub、Memory Sidecar 只提供 read-only、bounded、non-authoritative evidence；status `invalid`、`not_started`、stale 或缺失不改變 canonical plan。
- 遇到 403、404、422、app identity drift、existing protection drift 或 uncertain PUT response，立即停止 mutation並保留 evidence，不自行放寬 contract。

## 11. Scope boundaries

In scope：

- Canonical branch-protection policy artifact。
- Deterministic render／verify CLI 與 focused tests。
- `main` branch protection 的 snapshot、single PUT、GET verification與 rollback contract。
- Handoff live snapshot與受控 GitHub evidence記錄。

Out of scope：

- GitHub rulesets、merge queue、CODEOWNERS、required human approvals、signed commits或deployment gate。
- 把 child gates、sandbox或Governance Graph重複設為 required checks。
- 新 Governance Graph、Memory Hub、Memory Sidecar、Agent orchestration、approval、dispatch或workflow control。
- 修改 release evidence schema、Full pytest、Hermes、UI acceptance business behavior或正式資料。

## 12. 官方依據

- GitHub Protected Branches：<https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
- GitHub REST Branch Protection API：<https://docs.github.com/en/rest/branches/branch-protection>
- GitHub Required Status Check troubleshooting：<https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks>
