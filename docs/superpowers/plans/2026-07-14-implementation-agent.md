# Implementation Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個受嚴格權限約束的 Implementation Agent，每次只執行一個已獲批准的 implementation plan Task，於隔離 worktree 內依 TDD 修改白名單檔案，並把 diff、測試與治理證據交回 Review Agent、Codex 與 Hermes。

**Architecture:** 沿用現有 Evidence Bundle Pipeline，新增 machine-readable `ImplementationTaskContract`、寫入範圍 guard、受控 validation runner 與單 Task orchestration service。Agent 可以在隔離 worktree 直接修改明確白名單內的 source/test/docs，但不能接觸正式 DB、runtime、baseline、secret、Git index/commit/merge、服務管理或網絡；每次執行後由 deterministic collector 驗證變更歸屬，再交 Review Agent，最後仍由 Codex 與 Hermes 完成正式驗收。

**Tech Stack:** Python 3.10、標準函式庫 `dataclasses` / `json` / `pathlib` / `subprocess` / `hashlib`、pytest、Git worktree、現有 `backend/agents` Evidence Bundle、Context Agent、Review Agent 與 Hermes acceptance。

## Global Constraints

- 正式營收口徑固定為「不含掛賬核銷與 TT 退款轉團款」。
- 2026-05 全部正式分社加正式四人專職銷售組 baseline 必須保持 HKD 12,057,968。
- Phase 1 一次只允許執行一個已批准 plan Task；不得接受自由形式「自行完成整個功能」。
- Agent 必須在 `codex/` 分支的隔離 Git worktree 執行；不得直接在 `main` 或使用者目前工作目錄寫入。
- Agent 只可修改 contract 的 `allowedWritePaths`；預設拒絕 SQLite、Excel/CSV、runtime、logs、exports、secrets、`.git` 與 symlink/path traversal。
- Agent 不得執行 `git add`、`git commit`、`git merge`、`git rebase`、`git reset`、`git stash`、`git push` 或建立 PR。
- Agent 不得啟停服務、安裝 dependency、執行 upload/upsert/rollback/promote、連接網絡或使用任意 shell。
- Validation command 必須是 argv allowlist、`shell=False`、有 timeout；不得以字串 shell command 執行。
- 行為修改必須保留 RED -> GREEN 證據；不得刪除、skip 或弱化既有測試，除非批准的 Task 明確列出該測試檔。
- 單次預設限制：最多 8 個可寫檔案、800 行 diff、2 次修復迴圈、12,000 input tokens、2,000 output tokens。
- 高風險 surface `upload`、`sqlite`、`baseline`、`rollback`、`revenue`、`business_rules`、`export_schema` 在 Phase 1 一律 hand off 給 Codex，不由 Implementation Agent 寫入。
- Agent runtime artifact 只可寫入 `.nbs_agent_runtime/implementation/`，而且不得納入 Git。
- Review Agent PASS 只代表可進入完整驗證；Hermes 仍是 runtime、SQLite、baseline 與服務的 final acceptance。
- 不把 Agent runtime 放入 `app.py`、Streamlit pages、FastAPI router、Vue runtime 或 Hermes script。

---

## File Map

### New files

- `docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md`：人類可讀權限、狀態、輸入輸出與 handoff 契約。
- `agent_config/implementation_policies.json`：Phase 1 高風險 surface、禁止路徑、檔案/diff/迴圈上限。
- `agent_config/implementation_commands.json`：validation argv allowlist 與 timeout。
- `backend/agents/implementation_models.py`：Task contract、run report、validation result 的 immutable models。
- `backend/agents/implementation_guard.py`：project/worktree/head/path/diff/symlink 防線。
- `backend/agents/validation_runner.py`：無 shell 的受控命令 runner。
- `backend/agents/implementation_agent_service.py`：單 Task 狀態機與 runner orchestration。
- `scripts/implementation_agent.py`：JSON CLI；預設 collect/validate，只有明確 `--agent-command` 才調用批准 runner。
- `tests/test_implementation_models.py`：schema、fingerprint、status 測試。
- `tests/test_implementation_guard.py`：worktree、write path、high-risk 與 diff boundary 測試。
- `tests/test_validation_runner.py`：allowlist、shell-free、timeout 與 output cap 測試。
- `tests/test_implementation_agent_service.py`：單 Task、TDD、repair loop 與 report 測試。
- `tests/test_implementation_agent_cli.py`：CLI JSON、exit code 與 invalid runner output 測試。
- `tests/test_implementation_agent_integration.py`：隔離 worktree、越界寫入回絕與 read-only formal-state 測試。

### Modified files

- `backend/agents/evidence_models.py`：讓 report envelope 接受 implementation statuses，保留舊 Context/Review 行為。
- `backend/agents/agent_runtime.py`：提供 implementation runtime subdirectory 與 telemetry event。
- `backend/agents/__init__.py`：只 export 穩定 public interfaces。
- `agent_config/token_budgets.json`：加入 implementation budget。
- `.gitignore`：確認 `.nbs_agent_runtime/` 全目錄被排除。
- `docs/agents/NBS_AGENT_ARCHITECTURE.md`：把 Implementation Agent 從 roadmap 升級為 Phase 1 contract，保留 Hermes 邊界。
- `docs/agents/CODEX_AGENT_DISPATCH.md`：新增允許/拒絕 dispatch 條件與 handoff。
- `AGENTS.md`：新增 Codex 使用 Implementation Agent 的 repo-level 規則。
- `NBS_CODEX_WORKER_WORKFLOW.md`：加入 Context -> Plan -> Implementation -> Review -> Hermes 流程。
- `scripts/hermes_post_change_check.py`：只新增契約檔存在性與 targeted test 命令，不調用 Implementation Agent。
- `tests/test_agent_dispatch_contract.py`：驗證 dispatch 文件與 machine-readable rules。
- `tests/test_agent_read_only_contract.py`：保證 Context/Review 仍 read-only，Implementation 也不能修改正式 state。
- `tests/test_hermes_post_change_check.py`：驗證 Hermes coverage 更新。

## Public Interfaces

```python
@dataclass(frozen=True)
class ImplementationTaskContract:
    schema_version: str
    task_id: str
    plan_path: str
    plan_fingerprint: str
    objective: str
    approved_base_sha: str
    approved_worktree: str
    allowed_write_paths: tuple[str, ...]
    validation_commands: tuple[str, ...]
    risk_surfaces: tuple[str, ...] = ()
    max_changed_files: int = 8
    max_diff_lines: int = 800
    max_repair_loops: int = 2

@dataclass(frozen=True)
class ValidationResult:
    command_id: str
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

@dataclass(frozen=True)
class ImplementationRunReport:
    schema_version: str
    status: str
    task_id: str
    contract_fingerprint: str
    start_head: str
    end_head: str
    changed_files: tuple[str, ...]
    diff_stat: dict[str, int]
    red_evidence: tuple[ValidationResult, ...]
    green_evidence: tuple[ValidationResult, ...]
    findings: tuple[dict, ...]
```

Allowed terminal statuses:

```text
completed
changes_required
blocked_invalid_contract
blocked_dirty_worktree
blocked_wrong_branch
blocked_head_mismatch
blocked_scope
blocked_high_risk
blocked_diff_limit
validation_failed
context_overflow
invalid_agent_output
runtime_error
```

---

### Task 1: Contract Models, Policy And Runtime Isolation

**Files:**
- Create: `backend/agents/implementation_models.py`
- Create: `agent_config/implementation_policies.json`
- Create: `agent_config/implementation_commands.json`
- Create: `tests/test_implementation_models.py`
- Modify: `backend/agents/evidence_models.py`
- Modify: `backend/agents/agent_runtime.py`
- Modify: `agent_config/token_budgets.json`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `canonical_fingerprint(value: object) -> str` and `load_json_config(project_root, relative_path)` from `backend.agents.evidence_models`.
- Produces: `ImplementationTaskContract.from_dict()`, `.to_dict()`, `.fingerprint`; `ValidationResult`; `ImplementationRunReport`; `load_implementation_policy(project_root)`.

- [ ] **Step 1: Write failing model and config tests**

```python
def test_contract_rejects_unversioned_or_multi_task_payload():
    payload = valid_contract_payload()
    payload["schemaVersion"] = ""
    with pytest.raises(ValueError, match="schemaVersion"):
        ImplementationTaskContract.from_dict(payload)


def test_contract_fingerprint_is_order_independent():
    left = ImplementationTaskContract.from_dict(valid_contract_payload())
    right = ImplementationTaskContract.from_dict(dict(reversed(list(valid_contract_payload().items()))))
    assert left.fingerprint == right.fingerprint


def test_policy_denies_formal_state_and_caps_work():
    policy = load_implementation_policy(PROJECT_ROOT)
    assert "*.db" in policy["deniedWritePatterns"]
    assert policy["limits"] == {
        "maxChangedFiles": 8,
        "maxDiffLines": 800,
        "maxRepairLoops": 2,
    }
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_implementation_models.py -q`

Expected: collection fails because `backend.agents.implementation_models` does not exist.

- [ ] **Step 3: Implement immutable models and exact policy files**

`implementation_policies.json` must contain:

```json
{
  "schemaVersion": "implementation-policy-v1",
  "requiredBranchPrefix": "codex/",
  "deniedRiskSurfaces": ["upload", "sqlite", "baseline", "rollback", "revenue", "business_rules", "export_schema"],
  "deniedWritePatterns": [".git/**", "*.db", "*.sqlite", "*.sqlite3", "*.xlsx", "*.xls", "*.csv", ".env", ".env.*", ".nbs_runtime/**", ".nbs_agent_runtime/**", "logs/**", "exports/**", "backups/**"],
  "limits": {"maxChangedFiles": 8, "maxDiffLines": 800, "maxRepairLoops": 2}
}
```

`implementation_commands.json` must define IDs, not free-form command strings:

```json
{
  "schemaVersion": "implementation-commands-v1",
  "commands": {
    "pytest_targeted": {"prefix": [".venv/bin/python", "-m", "pytest"], "timeoutSeconds": 300},
    "py_compile": {"prefix": [".venv/bin/python", "-m", "py_compile"], "timeoutSeconds": 120},
    "vue_verify": {"exact": ["npm", "run", "verify"], "cwd": "frontend", "timeoutSeconds": 300},
    "vue_build": {"exact": ["npm", "run", "build"], "cwd": "frontend", "timeoutSeconds": 300}
  }
}
```

Add implementation status constants without changing existing Context/Review status sets. Add `implementation` budget with `inputTokens: 12000`, `outputTokens: 2000`, `maxRepairLoops: 2`. Runtime helper must resolve only under `.nbs_agent_runtime/implementation/`.

- [ ] **Step 4: Run model and existing Agent tests**

Run: `.venv/bin/python -m pytest tests/test_implementation_models.py tests/test_evidence_models.py tests/test_agent_runtime.py -q`

Expected: all pass; existing Context/Review envelope tests remain unchanged.

- [ ] **Step 5: Commit Task 1**

```bash
git add .gitignore agent_config backend/agents/implementation_models.py backend/agents/evidence_models.py backend/agents/agent_runtime.py tests/test_implementation_models.py
git commit -m "feat: define implementation agent contract"
```

---

### Task 2: Worktree, HEAD And Write-Scope Guard

**Files:**
- Create: `backend/agents/implementation_guard.py`
- Create: `tests/test_implementation_guard.py`

**Interfaces:**
- Consumes: `ImplementationTaskContract`, implementation policy JSON.
- Produces: `validate_preconditions(project_root, contract) -> GuardDecision`; `capture_worktree_state(project_root) -> WorktreeState`; `validate_changes(project_root, contract, before) -> GuardDecision`.

- [ ] **Step 1: Write failing precondition tests**

```python
@pytest.mark.parametrize("branch", ["main", "feature/free-form", "HEAD"])
def test_guard_requires_codex_branch(tmp_git_repo, branch):
    checkout_branch(tmp_git_repo, branch)
    decision = validate_preconditions(tmp_git_repo, contract_for(tmp_git_repo))
    assert decision.status == "blocked_wrong_branch"


def test_guard_rejects_dirty_start(tmp_git_repo):
    (tmp_git_repo / "tracked.py").write_text("changed\n", encoding="utf-8")
    decision = validate_preconditions(tmp_git_repo, contract_for(tmp_git_repo))
    assert decision.status == "blocked_dirty_worktree"


def test_guard_rejects_approved_head_mismatch(tmp_git_repo):
    contract = contract_for(tmp_git_repo, approved_base_sha="0" * 40)
    assert validate_preconditions(tmp_git_repo, contract).status == "blocked_head_mismatch"
```

- [ ] **Step 2: Write failing path and post-diff tests**

```python
def test_guard_rejects_path_escape_and_symlink(tmp_git_repo):
    contract = contract_for(tmp_git_repo, allowed_write_paths=("src/allowed.py", "link/out.py"))
    (tmp_git_repo / "link").symlink_to(tmp_git_repo.parent, target_is_directory=True)
    assert validate_preconditions(tmp_git_repo, contract).status == "blocked_scope"


def test_guard_rejects_unapproved_changed_file(tmp_git_repo):
    contract = contract_for(tmp_git_repo, allowed_write_paths=("src/allowed.py",))
    before = capture_worktree_state(tmp_git_repo)
    (tmp_git_repo / "README.md").write_text("outside scope\n", encoding="utf-8")
    assert validate_changes(tmp_git_repo, contract, before).status == "blocked_scope"


def test_guard_rejects_diff_limits(tmp_git_repo):
    contract = contract_for(tmp_git_repo, max_changed_files=1, max_diff_lines=2)
    before = capture_worktree_state(tmp_git_repo)
    write_three_line_change(tmp_git_repo / "src/allowed.py")
    assert validate_changes(tmp_git_repo, contract, before).status == "blocked_diff_limit"
```

- [ ] **Step 3: Run guard tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_implementation_guard.py -q`

Expected: collection fails because guard interfaces do not exist.

- [ ] **Step 4: Implement deterministic guard**

Use subprocess argv only for read operations:

```python
READ_ONLY_GIT = {
    "branch": ("git", "branch", "--show-current"),
    "head": ("git", "rev-parse", "HEAD"),
    "status": ("git", "status", "--porcelain=v1", "-z"),
    "diff_numstat": ("git", "diff", "--numstat", "--"),
}
```

Resolve every allowed path with `Path.resolve(strict=False)`, require it remains below resolved project root, reject existing symlinks in every path component, reject denied glob patterns, and require an exact current HEAD match. Post-run comparison must include tracked, untracked and deleted paths and must not call any mutating Git command.

- [ ] **Step 5: Run Task 2 and regression tests**

Run: `.venv/bin/python -m pytest tests/test_implementation_guard.py tests/test_agent_read_only_contract.py -q`

Expected: all pass; no Git index or formal runtime modification.

- [ ] **Step 6: Commit Task 2**

```bash
git add backend/agents/implementation_guard.py tests/test_implementation_guard.py
git commit -m "feat: guard implementation agent writes"
```

---

### Task 3: Approved Validation Runner

**Files:**
- Create: `backend/agents/validation_runner.py`
- Create: `tests/test_validation_runner.py`

**Interfaces:**
- Consumes: command ID plus argv suffix, `agent_config/implementation_commands.json`.
- Produces: `ValidationRunner.run(command_id: str, arguments: tuple[str, ...]) -> ValidationResult`.

- [ ] **Step 1: Write failing security tests**

```python
@pytest.mark.parametrize("value", ["; rm -rf .", "$(touch hacked)", "| cat .env", "../outside.py"])
def test_runner_rejects_shell_and_path_escape(project_root, value):
    runner = ValidationRunner(project_root)
    with pytest.raises(CommandRejected):
        runner.run("pytest_targeted", (value,))


def test_runner_rejects_unknown_command(project_root):
    with pytest.raises(CommandRejected, match="not allowlisted"):
        ValidationRunner(project_root).run("system_manager_start", ())
```

- [ ] **Step 2: Write failing timeout/output tests**

```python
def test_runner_uses_shell_false(monkeypatch, project_root):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append((a, kw)) or completed())
    ValidationRunner(project_root).run("py_compile", ("app.py",))
    assert calls[0][1]["shell"] is False


def test_runner_reports_timeout_without_retry(monkeypatch, project_root):
    monkeypatch.setattr(subprocess, "run", raise_timeout)
    result = ValidationRunner(project_root).run("py_compile", ("app.py",))
    assert result.timed_out is True
    assert result.exit_code == 124
```

- [ ] **Step 3: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_validation_runner.py -q`

Expected: collection fails because `ValidationRunner` is missing.

- [ ] **Step 4: Implement minimal runner**

Allow `pytest_targeted` arguments only when every target is under `tests/` and every option is one of `-q`, `-v`, `-x`, `--maxfail=<integer>`. Allow `py_compile` only for repo-relative `.py` files. Exact frontend commands accept no suffix. Capture UTF-8 output, cap each stream at 32,000 characters, record duration, and never retry a timeout automatically.

- [ ] **Step 5: Run Task 3 tests**

Run: `.venv/bin/python -m pytest tests/test_validation_runner.py tests/test_implementation_models.py -q`

Expected: all pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/agents/validation_runner.py tests/test_validation_runner.py
git commit -m "feat: add allowlisted validation runner"
```

---

### Task 4: Single-Task Implementation Service

**Files:**
- Create: `backend/agents/implementation_agent_service.py`
- Create: `tests/test_implementation_agent_service.py`
- Modify: `backend/agents/__init__.py`

**Interfaces:**
- Consumes: `EvidenceBundle`, `ImplementationTaskContract`, guard, validation runner and approved external runner argv.
- Produces: `ImplementationAgentService.collect(contract) -> EvidenceBundle`; `.execute(contract, agent_command) -> ImplementationRunReport`.

- [ ] **Step 1: Write failing single-task and runner protocol tests**

```python
def test_service_rejects_more_than_one_plan_task(service, contract):
    contract = replace(contract, task_id="Task 2, Task 3")
    assert service.execute(contract, fake_runner).status == "blocked_invalid_contract"


def test_service_sends_bundle_and_contract_as_json(service, contract, runner_spy):
    report = service.execute(contract, runner_spy.command)
    request = runner_spy.last_request
    assert request["schemaVersion"] == "implementation-request-v1"
    assert request["contractFingerprint"] == contract.fingerprint
    assert request["task"]["taskId"] == contract.task_id
    assert report.status == "completed"


def test_service_rejects_invalid_agent_json(service, contract, invalid_json_runner):
    assert service.execute(contract, invalid_json_runner).status == "invalid_agent_output"
```

- [ ] **Step 2: Write failing state-machine tests**

```python
def test_service_stops_before_runner_when_high_risk(service, high_risk_contract, runner_spy):
    report = service.execute(high_risk_contract, runner_spy.command)
    assert report.status == "blocked_high_risk"
    assert runner_spy.calls == 0


def test_service_stops_after_out_of_scope_write(service, contract, writes_outside_scope):
    report = service.execute(contract, writes_outside_scope.command)
    assert report.status == "blocked_scope"
    assert "README.md" in report.findings[0]["paths"]
```

- [ ] **Step 3: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_implementation_agent_service.py -q`

Expected: collection fails because service does not exist.

- [ ] **Step 4: Implement the exact state machine**

```text
load contract
-> validate schema and one task
-> enforce token/file/diff/risk limits
-> validate branch/worktree/head/path preconditions
-> collect compact Context evidence
-> invoke explicitly approved runner once
-> validate runner response schema
-> inspect actual filesystem/Git diff
-> run approved validation commands
-> allow at most contract.max_repair_loops runner calls
-> inspect final diff again
-> emit report and telemetry
```

The runner response may contain status, summary and requested validation command IDs. Actual `changedFiles` and `diffStat` must always come from the deterministic guard, never from runner claims. Cache may reuse read-only Context evidence but must never replay or skip a write run.

- [ ] **Step 5: Run service and existing Agent tests**

Run: `.venv/bin/python -m pytest tests/test_implementation_agent_service.py tests/test_context_agent_service.py tests/test_review_agent_service.py -q`

Expected: all pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/agents/__init__.py backend/agents/implementation_agent_service.py tests/test_implementation_agent_service.py
git commit -m "feat: orchestrate one approved implementation task"
```

---

### Task 5: TDD Evidence, Diff Attribution And Repair Gate

**Files:**
- Modify: `backend/agents/implementation_agent_service.py`
- Modify: `backend/agents/implementation_models.py`
- Modify: `tests/test_implementation_agent_service.py`

**Interfaces:**
- Consumes: ordered `redCommands` and `greenCommands` in contract runner request.
- Produces: report fields `redEvidence`, `greenEvidence`, `repairLoopsUsed`, `testFilesChanged`, `productionFilesChanged`.

- [ ] **Step 1: Write failing TDD-gate tests**

```python
def test_behavior_change_requires_red_evidence(service, behavior_contract, green_only_runner):
    report = service.execute(behavior_contract, green_only_runner.command)
    assert report.status == "changes_required"
    assert report.findings[0]["code"] == "missing_red_evidence"


def test_docs_only_task_does_not_require_red_evidence(service, docs_contract, docs_runner):
    report = service.execute(docs_contract, docs_runner.command)
    assert report.status == "completed"
    assert report.red_evidence == ()


def test_failing_green_command_blocks_completion(service, behavior_contract, failing_green_runner):
    report = service.execute(behavior_contract, failing_green_runner.command)
    assert report.status == "validation_failed"
```

- [ ] **Step 2: Write failing anti-test-weakening tests**

```python
@pytest.mark.parametrize("marker", ["pytest.skip", "@pytest.mark.skip", "xfail", "# noqa"])
def test_new_test_bypass_marker_requires_explicit_approval(service, contract, marker_runner, marker):
    report = service.execute(contract, marker_runner(marker).command)
    assert report.status == "changes_required"
    assert report.findings[0]["code"] == "test_safety_violation"
```

- [ ] **Step 3: Run targeted tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_implementation_agent_service.py -q`

Expected: new assertions fail because TDD evidence and test-safety fields are absent.

- [ ] **Step 4: Implement TDD and repair gates**

Require at least one approved test command with non-zero exit before production code changes for `taskType=behavior`; store only capped output and command metadata. Require all final green commands to return zero. Detect newly added skip/xfail directives from the unified diff and reject them unless the exact test path is listed in `approvedTestBehaviorChanges`. Stop after two repair loops and emit `validation_failed` without further runner calls.

- [ ] **Step 5: Run Task 5 tests**

Run: `.venv/bin/python -m pytest tests/test_implementation_agent_service.py tests/test_validation_runner.py tests/test_implementation_guard.py -q`

Expected: all pass.

- [ ] **Step 6: Commit Task 5**

```bash
git add backend/agents/implementation_agent_service.py backend/agents/implementation_models.py tests/test_implementation_agent_service.py
git commit -m "feat: enforce implementation tdd evidence"
```

---

### Task 6: JSON CLI And Safe Operator Experience

**Files:**
- Create: `scripts/implementation_agent.py`
- Create: `tests/test_implementation_agent_cli.py`

**Interfaces:**
- Consumes: `--contract PATH`, optional approved `--agent-command ...`, `--collect-only`.
- Produces: one JSON document on stdout; diagnostics on stderr; stable exit codes.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_collect_only_emits_bundle_and_does_not_invoke_runner(tmp_path):
    result = run_cli("--contract", contract_path, "--collect-only")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["schemaVersion"] == "evidence-bundle-v1"


def test_cli_requires_explicit_runner_for_execution():
    result = run_cli("--contract", contract_path)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked_invalid_contract"


def test_cli_maps_validation_failure_to_nonzero_exit(fake_runner):
    result = run_cli("--contract", contract_path, "--agent-command", *fake_runner)
    assert result.returncode == 3
    assert json.loads(result.stdout)["status"] == "validation_failed"
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_implementation_agent_cli.py -q`

Expected: script is missing.

- [ ] **Step 3: Implement CLI**

Exit code contract:

```text
0 completed or collect-only ready
2 invalid contract, scope, branch, worktree, HEAD or high-risk block
3 validation failed or changes required
4 invalid agent output or context overflow
5 runtime error
```

Parse the contract as UTF-8 JSON. Do not infer an agent command from PATH, config or environment. Redact environment variables and absolute paths outside project root from JSON output. Catch exceptions at the CLI boundary and return `runtime_error` without traceback on stdout.

- [ ] **Step 4: Run CLI and Agent regression tests**

Run: `.venv/bin/python -m pytest tests/test_implementation_agent_cli.py tests/test_agent_cli.py -q`

Expected: all pass.

- [ ] **Step 5: Commit Task 6**

```bash
git add scripts/implementation_agent.py tests/test_implementation_agent_cli.py
git commit -m "feat: expose implementation agent cli"
```

---

### Task 7: Dispatch, Review Handoff And Governance Documentation

**Files:**
- Create: `docs/agents/IMPLEMENTATION_AGENT_CONTRACT.md`
- Modify: `docs/agents/NBS_AGENT_ARCHITECTURE.md`
- Modify: `docs/agents/CODEX_AGENT_DISPATCH.md`
- Modify: `AGENTS.md`
- Modify: `NBS_CODEX_WORKER_WORKFLOW.md`
- Modify: `tests/test_agent_dispatch_contract.py`

**Interfaces:**
- Consumes: final implementation report and actual diff.
- Produces: deterministic Codex routing rule and Review Agent handoff contract.

- [ ] **Step 1: Write failing governance tests**

```python
def test_dispatch_contract_requires_approved_plan_and_single_task():
    text = DISPATCH_PATH.read_text(encoding="utf-8")
    assert '"requiresApprovedPlan": true' in text
    assert '"maxTasksPerRun": 1' in text


def test_repo_instructions_forbid_agent_git_and_formal_state_writes():
    text = AGENTS_PATH.read_text(encoding="utf-8")
    for phrase in ["不得 commit 或 merge", "不得修改正式 SQLite", "必須交給 Review Agent"]:
        assert phrase in text
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_agent_dispatch_contract.py -q`

Expected: assertions fail because implementation dispatch rules are absent.

- [ ] **Step 3: Add exact dispatch rules**

The machine-readable block must include:

```json
{
  "implementation": {
    "requiresApprovedPlan": true,
    "requiresExplicitAuthorization": true,
    "requiresIsolatedWorktree": true,
    "requiredBranchPrefix": "codex/",
    "maxTasksPerRun": 1,
    "allowedTaskTypes": ["behavior", "refactor", "test", "documentation", "configuration"],
    "deniedRiskSurfaces": ["upload", "sqlite", "baseline", "rollback", "revenue", "business_rules", "export_schema"],
    "after": ["review_agent", "full_verification", "hermes"],
    "never": ["commit", "merge", "push", "service_management", "dependency_install"]
  }
}
```

Document that Codex creates/approves the contract, invokes one Task, inspects the report, runs Review Agent, resolves findings, performs full verification and invokes Hermes. The Implementation Agent must not decide its next Task.

- [ ] **Step 4: Run governance tests**

Run: `.venv/bin/python -m pytest tests/test_agent_dispatch_contract.py -q`

Expected: all pass.

- [ ] **Step 5: Commit Task 7**

```bash
git add AGENTS.md NBS_CODEX_WORKER_WORKFLOW.md docs/agents tests/test_agent_dispatch_contract.py
git commit -m "docs: govern implementation agent dispatch"
```

---

### Task 8: Isolation Integration, Hermes Coverage And Full Acceptance

**Files:**
- Create: `tests/test_implementation_agent_integration.py`
- Modify: `tests/test_agent_read_only_contract.py`
- Modify: `scripts/hermes_post_change_check.py`
- Modify: `tests/test_hermes_post_change_check.py`

**Interfaces:**
- Consumes: all previous Task interfaces.
- Produces: end-to-end proof that bounded source changes are possible while formal state, Context/Review read-only behavior, Git index and Hermes ownership remain intact.

- [ ] **Step 1: Write failing isolated-worktree integration test**

```python
def test_agent_changes_only_approved_file_in_isolated_worktree(agent_fixture):
    before = agent_fixture.formal_state_hashes()
    report = agent_fixture.run_task(
        allowed_write_paths=("sandbox/example.py", "tests/sandbox/test_example.py"),
        runner="tests/fixtures/implementation_runner.py",
    )
    assert report.status == "completed"
    assert set(report.changed_files) == {"sandbox/example.py", "tests/sandbox/test_example.py"}
    assert agent_fixture.formal_state_hashes() == before
    assert agent_fixture.git_index_unchanged()
```

- [ ] **Step 2: Write failing hostile-runner integration test**

```python
def test_hostile_runner_cannot_receive_pass_after_formal_state_write(agent_fixture):
    report = agent_fixture.run_hostile_task(target="data/nbs_analytics.db")
    assert report.status == "blocked_scope"
    assert agent_fixture.formal_state_restored_from_fixture_copy()
```

The test must use disposable fixture copies only; it must never attempt a write against the real project DB. The production contract does not promise automatic rollback of arbitrary source edits; a blocked worktree remains quarantined for Codex inspection or deletion.

- [ ] **Step 3: Run integration tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_implementation_agent_integration.py tests/test_agent_read_only_contract.py -q`

Expected: new integration coverage fails until fixture runner and Hermes contract entries are wired.

- [ ] **Step 4: Implement integration fixtures and Hermes targeted coverage**

Hermes must verify file existence and run the implementation Agent test pack. It must not execute `scripts/implementation_agent.py`, create worktrees or allow writes. Add these commands to the Hermes targeted list:

```text
.venv/bin/python -m pytest tests/test_implementation_models.py tests/test_implementation_guard.py tests/test_validation_runner.py -q
.venv/bin/python -m pytest tests/test_implementation_agent_service.py tests/test_implementation_agent_cli.py tests/test_implementation_agent_integration.py -q
```

- [ ] **Step 5: Run the complete Agent pack**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_evidence_models.py \
  tests/test_evidence_collector.py \
  tests/test_agent_runtime.py \
  tests/test_context_agent_service.py \
  tests/test_review_agent_service.py \
  tests/test_agent_cli.py \
  tests/test_agent_dispatch_contract.py \
  tests/test_agent_read_only_contract.py \
  tests/test_implementation_models.py \
  tests/test_implementation_guard.py \
  tests/test_validation_runner.py \
  tests/test_implementation_agent_service.py \
  tests/test_implementation_agent_cli.py \
  tests/test_implementation_agent_integration.py \
  tests/test_hermes_post_change_check.py -q
```

Expected: all pass.

- [ ] **Step 6: Run static and full test verification**

Run:

```bash
.venv/bin/python -m py_compile \
  backend/agents/implementation_models.py \
  backend/agents/implementation_guard.py \
  backend/agents/validation_runner.py \
  backend/agents/implementation_agent_service.py \
  scripts/implementation_agent.py \
  scripts/hermes_post_change_check.py
.venv/bin/python -m pytest -q
```

Expected: compile exits 0 and full pytest passes.

- [ ] **Step 7: Run system and Hermes acceptance**

Run:

```bash
.venv/bin/python scripts/system_manager.py start --no-browser
.venv/bin/python scripts/system_manager.py acceptance
.venv/bin/python scripts/hermes_post_change_check.py
```

Expected: services accepted; Hermes `overallStatus=pass`; monthly governance and SQLite integrity pass; 2026-05 baseline matches HKD 12,057,968.

- [ ] **Step 8: Perform a no-op telemetry trial**

Create a disposable contract that permits only a fixture file under a temporary Git worktree. Run `--collect-only`, then one fake approved runner execution. Verify telemetry records only task ID, status, duration, file/diff counts and estimated tokens; it must not contain source code, secrets, DB rows or full prompts.

- [ ] **Step 9: Commit Task 8**

```bash
git add scripts/hermes_post_change_check.py tests/test_implementation_agent_integration.py tests/test_agent_read_only_contract.py tests/test_hermes_post_change_check.py
git commit -m "test: verify implementation agent isolation"
```

---

## Completion Gate

Implementation Agent 只有在以下條件全部成立時才視為 Phase 1 完成：

- 8 個 Task 各自有 GREEN 測試與獨立 commit，且每個 Task 完成後已做 findings-first review。
- Agent 只能執行一個已批准 Task，且只有明確 `--agent-command` 才會調用 runner。
- `main`、非 `codex/` branch、dirty worktree、HEAD mismatch、高風險 surface、越界路徑、symlink、超過 diff limit 全部被阻擋。
- 真實 changed files 與 diff stats 由 deterministic guard 產生，不信任 Agent 自報。
- 行為變更具備 RED -> GREEN evidence；最終 validation 全部 PASS。
- Context Agent 與 Review Agent 保持 read-only；Implementation Agent 不可修改正式 DB/runtime/baseline/Git index。
- Review Agent、full pytest、system acceptance、Hermes 全部 PASS。
- 2026-05 baseline 仍為 HKD 12,057,968，正式營收口徑未改。
- 未執行 stage、commit、merge 或 push，直到使用者在完成驗收後另行授權 Git integration。

## Expected Operational Effect

- Codex 不再把完整 repo 與完整對話交給每個 coding step，只交付單一 Task contract 與 compact Evidence Bundle。
- 低至中風險、邊界清楚的 Task 可由較低成本 runner 完成初版程式與 targeted tests；Codex 集中處理設計、風險判斷、review findings 與正式驗收。
- Token 降幅不以固定百分比承諾；Telemetry 將記錄每個 Task 的 Context、Implementation、Review token estimate，累積 5 至 10 個真實 Task 後才建立可靠節省基線。
- 高風險營收、上傳、SQLite、baseline、rollback 與 export schema 仍由 Codex 主導，避免以節省 Token 換取正式口徑風險。
