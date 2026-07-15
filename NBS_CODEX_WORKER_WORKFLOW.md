# NBS Codex Worker Workflow

更新日期：2026-06-30  
專案路徑：`/Users/chanwaitung2025/Downloads/nbs_analytics`  
角色：Hermes = monitoring coordinator / orchestrator；Codex = isolated implementation worker

---

## 1. Purpose

本文件定義 `nbs_analytics` 如何使用 Codex worker 進行可協調、可分派、可監控的開發。

核心目標：

- 每個 worker 都在獨立 branch / worktree 工作。
- Hermes 不接受未驗證結果。
- 所有 Codex 產出必須以 Git diff、測試、baseline、runtime health 證據收斂。
- revenue-scope / full snapshot upload / baseline drift 類問題優先保護 frozen baseline。

---

## 2. Non-negotiable safety rules

1. 不要修改 `/Users/chanwaitung2025/Downloads/dashboard-project`。
2. 不要把 dashboard-project 月報 dashboard 工作混入 `nbs_analytics`。
3. 正式收入口徑固定為：`不含掛賬核銷與TT退款轉團款`。
4. 遇到以下關鍵字時，預設高風險：
   - baseline drift
   - revenue-scope
   - full snapshot upload
   - 掛賬核銷
   - TT 退款轉團款
   - 收款單號
   - 來源單據號
   - rollback
   - stability_gate_history
5. 高風險任務先規劃、等待授權；不得直接跨檔修改。
6. 不要重寫 historical validated rows。
7. 不要先從 analysis layer 做廣義排除來掩蓋資料漂移。
8. 不要執行 upload / upsert / rollback apply，除非使用者明確授權。
9. 不要刪除 backup / quarantine / logs。
10. 完成條件不能只看 UI；至少要有 tests、baseline、API 或 DB replay / integrity 證據。

---

## 3. Standard worker lifecycle

### Step 0: Preflight by Hermes

Hermes 在分派前執行：

```bash
git status --short --branch
git log -1 --oneline
```

要求：主工作區必須乾淨，除非使用者明確指定要在已有 diff 上工作。

### Step 1: Create isolated worktree

```bash
mkdir -p ../nbs_analytics-worktrees
git worktree add -b codex/<task-slug> ../nbs_analytics-worktrees/<task-slug> main
```

命名規則：

```text
codex/<area>-<short-task>
```

例：

```text
codex/ops-post-change-check
codex/baseline-phase2j-cli
codex/vue-summary-contract
```

### Step 2: Dispatch Codex worker

Codex worker 必須收到：

- 任務目標
- 允許修改的檔案範圍
- 禁止事項
- 必跑測試
- baseline 驗收命令
- 回報格式

Codex 必須先建立並批准 Task contract，且每次只分派一個 Task。worker 不得自行決定下一 Task；完成後 Codex 檢查 final implementation report 與實際 diff，必須交給 Review Agent，再處理 findings、完整驗證及 Hermes。

### Step 3: Monitor worker

Hermes 監控：

```bash
git status --short --branch
git diff --stat
git diff --name-only
git diff
```

如 worker 修改高風險檔案，Hermes 必須升級為 `needs_review`：

- `database.py`
- `pipeline.py`
- `app_workflows.py`
- `rules_config.json`
- `backend/services/revenue_scope_service.py`
- `backend/services/upload_preflight_service.py`
- `backend/services/upload_rollback_service.py`
- `backend/services/upload_action_service.py`
- `backend/services/stability_service.py`
- `backend/services/stability_history_service.py`

### Step 4: Post-change gate

最小驗收：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python scripts/hermes_post_change_check.py
```

若 script 不可用，手動執行：

```bash
git status --short --branch
git diff --stat
git diff --name-only
.venv/bin/python scripts/system_manager.py status
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/system_manager.py monitor
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python - <<'PY'
from pathlib import Path
from scripts.phase2j_baseline_check import check_phase2_baseline
import json
print(json.dumps(check_phase2_baseline(Path('nbs_marketing_data.db')), ensure_ascii=False, indent=2))
PY
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -m pytest \
  tests/test_phase2_precheck_acceptance.py \
  tests/test_dashboard_service.py \
  tests/test_dashboard_api.py \
  tests/test_database_rollback.py \
  tests/test_stability_history_service.py \
  tests/test_system_health_service.py \
  tests/test_restore_drill_service.py \
  tests/test_upload_preflight_service.py \
  tests/test_upload_rollback_service.py \
  tests/test_upload_api.py \
  -q -p no:cacheprovider
```

### Step 5: Review and merge decision

Hermes 回報：

- changed files
- risk level
- test results
- baseline result
- runtime health
- whether DB/cache/log artifacts remain ignored
- recommendation: merge / request changes / abandon worker branch

只有使用者確認後才 merge / commit 到 main。

---

## 4. Codex worker prompt template

```text
你是 Codex worker，工作在 nbs_analytics 專案的獨立 worktree。

專案路徑：<WORKTREE_PATH>
任務：<TASK>
允許修改：<ALLOWED_FILES>
禁止修改：<FORBIDDEN_FILES_OR_AREAS>

請先閱讀：
- NBS_ANALYTICS_HANDOFF.md
- NBS_ANALYTICS_SYSTEM_MAP.md
- NBS_HERMES_MONITORING.md
- NBS_CODEX_WORKER_WORKFLOW.md

安全規則：
1. 不要修改 dashboard-project。
2. 正式收入口徑是「不含掛賬核銷與TT退款轉團款」。
3. 保護 frozen baseline；不要重寫 historical validated rows。
4. 不要用 analysis layer 廣義排除掩蓋 baseline drift。
5. 不要執行 upload / upsert / rollback apply。
6. 不要刪除 backup / quarantine / logs。
7. 只做任務要求的最小修改，不做無關重構。
8. 不得 commit 或 merge，不得修改正式 SQLite；不得自行進行 full verification 或 Hermes。
9. final implementation report 與實際 diff 必須交給 Review Agent。

開發規則：
- 使用 TDD：先寫 failing test，確認 fail，再實作，最後確認 pass。
- 若測試無法寫，先回報原因，不要直接改 production code。

完成時請回報：
- changed files
- tests run and exact output
- baseline check output
- risks / assumptions
- any files intentionally not touched
```

---

## 5. Monitoring status table

| Status | Meaning |
|---|---|
| pending | worker 尚未開始 |
| in_progress | worker 正在修改或測試 |
| blocked | 缺依賴、環境失敗、需求不清或需要授權 |
| needs_review | worker 完成但需 Hermes diff/test review |
| verified | tests/baseline/health 已通過 |
| done | 使用者接受並完成 merge/commit |

---

## 6. Merge policy

- Low-risk docs/tests-only change：可在 tests/baseline 通過後建議 merge。
- Code change：至少 targeted tests + baseline check。
- Upload / rollback / revenue-scope / database change：必須 expanded monitoring pack + 使用者明確授權。
- 若 2026-05 baseline 不等於 `HKD 12,057,968`：FAIL，不得 merge。

---

## 7. Standard Hermes report format

```text
Overall status: PASS / WARNING / FAIL

Observed state:
- Git:
- Changed files:
- Runtime:
- Baseline:
- Tests:

Evidence:
- Commands run:
- Key outputs:

Risks:
- ...

Recommended next action:
- ...
```
