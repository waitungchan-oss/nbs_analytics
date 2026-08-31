# Sandbox Capability Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在測試與 CI 開始前辨識 nested macOS `sandbox-exec` capability，將受限 host 的大量重複 failures 轉為單一、source-bound、fail-closed 的 `blocked_environment`，並保留合資格 macOS runner 的完整 security tests。

**Architecture:** 新增純 Python preflight/evidence module，執行最小 temporary probe 並以 exact schema 保存結果。pytest 將 sandbox integration tests 以 marker 分離，由 session-level preflight 控制；CI 使用獨立 required macOS job，Hermes 只 read-only 報告 evidence。既有 `SandboxedSubprocessAgentRunner` security policy 不放寬，非 sandbox tests 不受影響。

**Tech Stack:** Python 3.10、pytest plugin hooks/markers、macOS `sandbox-exec`、JSON、SHA-256、`tempfile`、`subprocess`、existing `.nbs_agent_runtime` atomic writers、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-08-31-sandbox-capability-preflight-design.md`

## Global Constraints

- Formal scope 固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite、baseline、revenue、GMV、退款、export schema、business rules 或 application runtime。
- 不移除/放寬 `SandboxedSubprocessAgentRunner` 的 filesystem、process、network、write 或 symlink protections。
- 不使用 `sudo`、不關閉 sandbox policy、不使用 `continue-on-error`，不把 `blocked_environment` 轉成 PASS。
- 不以 `skipif(sys.platform == "darwin")` 隱藏 macOS sandbox contract；只在非 macOS 回傳 `not_applicable`。
- Capability evidence 是 execution evidence，不是 Review、full pytest、Hermes、UI、baseline 或 business acceptance proof。
- Governance Graph、Memory Hub、Memory Sidecar 永遠 read-only/non-authoritative；Memory hints 最多 3 items、6000 bytes、800 ms。
- 每一個 Task 都要先寫 failing tests、執行 focused verification、`py_compile`、`git diff --check`，再交給 findings-first Review。
- full pytest、sandbox integration、Hermes、UI acceptance 是獨立 gates；任何 blocked gate 必須明確報告。

## File Map

| File | Responsibility |
|---|---|
| `backend/agents/sandbox_capability_preflight.py` | probe request/result models、macOS capability probes、status classification、exact evidence validation。 |
| `backend/agents/sandbox_capability_receipt.py` | `sandbox-capability-evidence-v1` canonical fingerprint、redaction、atomic read/write、stale/source binding。 |
| `tests/conftest.py` | session-level preflight fixture/plugin、marker registration、single blocker reporting；不影響一般 tests。 |
| `tests/test_sandbox_capability_preflight.py` | unit tests、fake backend、restricted/available/not-applicable classifications。 |
| `tests/test_sandbox_pytest_integration.py` | pytest collection/runtime gate 與 no-duplicate-failure tests。 |
| `tests/test_agent_runtime.py` | 為現有 macOS sandbox tests 加 `sandbox` marker，保留原 security assertions。 |
| `tests/test_implementation_agent_integration.py` | 為 production sandbox integration tests 加 `sandbox` marker，保留原 assertions。 |
| `.github/workflows/hermes-governance-graph.yml` | 保持既有 Graph check；不得在其中執行 sandbox write/probe。 |
| `.github/workflows/sandbox-integration.yml` | 新增合資格 macOS required sandbox job。 |
| `scripts/hermes_post_change_check.py` | read-only 消費 preflight evidence，報告 status/failure code。 |
| `tests/test_hermes_post_change_check.py` | Hermes preflight report boundary tests。 |
| `docs/agents/NBS_HERMES_MONITORING.md` | 只有 gates 通過後補充 evidence contract；不改 Hermes authority boundary。 |

## Task 1: 建立 capability models 與 exact evidence contract

**Files:**

- Create: `backend/agents/sandbox_capability_preflight.py`
- Create: `backend/agents/sandbox_capability_receipt.py`
- Create: `tests/test_sandbox_capability_preflight.py`

**Interfaces:**

- `SandboxCapabilityError(ValueError)`。
- `SandboxCapabilityStatus = Literal["available", "blocked_environment", "not_applicable", "invalid_evidence"]`。
- `SandboxProbeRequest(expected_platform: str, workspace_fingerprint: str, backend_path: Path, probe_root: Path, timeout_seconds: float, output_limit_bytes: int, probe_profile_fingerprint: str)`。
- `SandboxCapabilityEvidence` with `to_dict() -> dict[str, object]` and `from_dict(payload) -> SandboxCapabilityEvidence`。
- `SandboxCapabilityPreflight.probe(request) -> SandboxCapabilityEvidence`。
- Exact receipt fields：`schemaVersion`、`status`、`platform`、`backendPathFingerprint`、`probeProfileFingerprint`、`workspaceFingerprint`、`capabilities`、`failureCode`、`diagnostics`、`startedAt`、`finishedAt`、`evidenceFingerprint`。

- [ ] **Step 1: Write failing schema tests**

```python
def test_evidence_fingerprint_and_exact_fields():
    evidence = available_evidence()
    payload = evidence.to_dict()
    assert set(payload) == EXPECTED_FIELDS
    assert SandboxCapabilityEvidence.from_dict(payload).to_dict() == payload

def test_tampered_or_stale_evidence_is_invalid():
    payload = available_evidence().to_dict()
    payload["workspaceFingerprint"] = "f" * 64
    with pytest.raises(SandboxCapabilityError):
        SandboxCapabilityEvidence.from_dict(payload, expected_workspace_fingerprint="a" * 64)
```

測試 status enum、capability booleans、bounded diagnostics、SHA-256 fields、timestamp format、exact keys、symlink path、stale workspace 與 fingerprint tamper。

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_sandbox_capability_preflight.py -q
```

Expected：新 models/validator 尚不存在，測試失敗。

- [ ] **Step 3: Implement immutable models and canonical fingerprint**

使用 frozen dataclass 或等價 immutable model；所有 fields exact-validate，`evidenceFingerprint` 僅由其他 exact fields 的 canonical JSON 計算。不得保存完整 command、environment、project path、raw stdout/stderr 或 secrets。

- [ ] **Step 4: Implement atomic receipt persistence**

重用既有 runtime path boundary、regular-file/symlink rejection 與 atomic write pattern。reader 必須檢查 expected workspace fingerprint；stale、malformed、over-cap、permission denied 皆回傳 `invalid_evidence` 或 raise bounded error，不能 fallback 成 available。

- [ ] **Step 5: Verify Task 1**

```bash
.venv/bin/python -m pytest tests/test_sandbox_capability_preflight.py -q
.venv/bin/python -m py_compile backend/agents/sandbox_capability_preflight.py backend/agents/sandbox_capability_receipt.py
git diff --check
```

## Task 2: Implement minimal macOS sandbox probes

**Files:**

- Modify: `backend/agents/sandbox_capability_preflight.py`
- Modify: `backend/agents/sandbox_capability_receipt.py`
- Test: `tests/test_sandbox_capability_preflight.py`

**Interfaces:**

- `resolve_sandbox_backend() -> Path`：只接受 allowlisted regular executable、非 symlink。
- `run_sandbox_probe(request) -> SandboxCapabilityEvidence`：一次 bounded probe sequence。
- `classify_probe_failure(returncode: int | None, stderr: bytes, *, timed_out: bool) -> tuple[SandboxCapabilityStatus, str]`。
- Required reason codes：`platform_not_applicable`、`backend_missing`、`backend_not_executable`、`sandbox_apply_denied`、`probe_timeout`、`probe_profile_invalid`、`filesystem_policy_failed`、`process_policy_failed`、`network_policy_failed`、`probe_output_invalid`、`probe_evidence_invalid`。

- [ ] **Step 1: Write failing fake-backend tests**

使用 `tmp_path` fake executable/monkeypatch，驗證：platform not macOS、backend missing/symlink、`sandbox_apply: Operation not permitted`、timeout、non-zero、malformed output、oversized output、unexpected write/network/process result、成功 minimal probe。

```python
def test_outer_sandbox_denial_is_one_blocked_environment():
    backend = fake_backend(stderr=b"sandbox-exec: sandbox_apply: Operation not permitted", returncode=71)
    evidence = run_sandbox_probe(request_for(backend))
    assert evidence.status == "blocked_environment"
    assert evidence.failure_code == "sandbox_apply_denied"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_sandbox_capability_preflight.py -q
```

Expected：probe executor 與 failure classification 尚未完成，新增 cases FAIL。

- [ ] **Step 3: Implement bounded executor**

使用 `shell=False`、固定 argv、explicit environment、ephemeral probe root、timeout、stdout/stderr byte cap 與 process-group cleanup。probe 不可讀寫 project root、SQLite、baseline、exports 或 credential files。

- [ ] **Step 4: Implement probe sequence and classification**

依序執行 platform、backend、minimal application、filesystem、process、network deny、receipt checks；任一 required probe 失敗即停止。`Operation not permitted` 必須分類為 `blocked_environment`，不得讓 sandbox integration tests 逐個啟動後才失敗。

- [ ] **Step 5: Verify Task 2**

```bash
.venv/bin/python -m pytest tests/test_sandbox_capability_preflight.py -q
.venv/bin/python -m py_compile backend/agents/sandbox_capability_preflight.py backend/agents/sandbox_capability_receipt.py
git diff --check
```

## Task 3: Integrate pytest markers and session-level gate

**Files:**

- Modify: `tests/conftest.py`
- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_implementation_agent_integration.py`
- Create: `tests/test_sandbox_pytest_integration.py`
- Test: `tests/test_sandbox_capability_preflight.py`

**Interfaces:**

- Register pytest marker `sandbox` with a clear description。
- `pytest_sessionstart(session)` runs preflight once and stores evidence in `config._nbs_sandbox_capability`。
- `pytest_collection_modifyitems(config, items)` applies the single preflight result only to `sandbox` tests。
- `render_sandbox_blocker(evidence) -> str` must include status, failure code, remediation and evidence fingerprint, but no raw path/secret。
- `NBS_ANALYTICS_SANDBOX_PREFLIGHT=required|auto|off` default is `required` in CI and `auto` for local developer runs；`off` is rejected for required CI jobs。

- [ ] **Step 1: Mark existing sandbox tests**

在 `tests/test_agent_runtime.py` 與 `tests/test_implementation_agent_integration.py` 中，將目前只在 macOS 執行且需要 `sandbox-exec` 的 tests 加上 `@pytest.mark.sandbox`；保留原 security assertions，不修改 test intent。

- [ ] **Step 2: Write failing pytest integration tests**

測試 preflight invocation count 為 1、`available` 允許 sandbox tests、restricted outer sandbox 只產生一個 `blocked_environment`、non-macOS 為 `not_applicable`，以及一般 unit tests 不受影響。

```python
def test_blocked_preflight_does_not_emit_duplicate_sandbox_failures(pytester):
    result = pytester.runpytest("-m", "sandbox")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*blocked_environment*sandbox_apply_denied*"])
```

- [ ] **Step 3: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_sandbox_pytest_integration.py tests/test_sandbox_capability_preflight.py -q
```

Expected：marker/session hook 尚未存在，tests FAIL。

- [ ] **Step 4: Implement session gate**

session 開始只跑一次 preflight；`available` 才執行 sandbox tests。`blocked_environment` 必須以明確非零結果終止 sandbox pack，不能 skip、不能 exit 0、不能把 blocker 展開成每個 test 的相同 stack trace。一般 `-m "not sandbox"` 不得依賴 sandbox capability。

- [ ] **Step 5: Verify Task 3**

```bash
.venv/bin/python -m pytest -m "not sandbox" -q
.venv/bin/python -m pytest tests/test_sandbox_pytest_integration.py tests/test_sandbox_capability_preflight.py -q
.venv/bin/python -m py_compile tests/conftest.py tests/test_agent_runtime.py tests/test_implementation_agent_integration.py
git diff --check
```

Expected：non-sandbox suite 不受影響；restricted host 顯示單一 blocker；合資格 host 保留完整 sandbox assertions。

## Task 4: Add CI capability matrix and required gate

**Files:**

- Create: `.github/workflows/sandbox-integration.yml`
- Modify: `pyproject.toml` or `pytest.ini` only if marker registration is not maintained in `tests/conftest.py`
- Create: `tests/test_sandbox_ci_contract.py`

**Interfaces:**

- Job name：`pytest-sandbox-integration`。
- Runner：原生 macOS runner，固定 Python version 與 dependency install。
- Required commands：preflight、`pytest -m sandbox -q`、receipt validation。
- Non-sandbox job command：`pytest -m "not sandbox" -q`。
- Failure policy：missing job、blocked preflight、invalid evidence、test failure 均 non-zero；禁止 `continue-on-error`。

- [ ] **Step 1: Write failing workflow contract tests**

驗證 workflow 是 pull request required candidate、使用 macOS runner、執行 preflight 與 sandbox marker、沒有 `continue-on-error`/silent skip、上傳 bounded evidence artifact。

- [ ] **Step 2: Run contract tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_sandbox_ci_contract.py -q
```

Expected：workflow 尚未存在，tests FAIL。

- [ ] **Step 3: Implement separated CI jobs**

新增 sandbox job，讓 capability blocked 直接 fail；保留 existing Hermes Governance Graph workflow 的 read-only scope，不將 sandbox probe 塞進 Graph job。

- [ ] **Step 4: Verify workflow and focused tests**

```bash
.venv/bin/python -m pytest tests/test_sandbox_ci_contract.py tests/test_sandbox_capability_preflight.py -q
git diff --check
```

Expected：CI contract test PASS；workflow 沒有放寬 security 或 required-gate semantics。

## Task 5: Hermes read-only reporting and independent verification

**Files:**

- Modify: `scripts/hermes_post_change_check.py`
- Modify: `tests/test_hermes_post_change_check.py`
- Modify: `docs/agents/NBS_HERMES_MONITORING.md` only after code/gates pass
- No changes: Governance Graph builder/query, Memory Hub writer, Memory Sidecar writer。

**Interfaces:**

- `sandbox_capability_artifact_report(project_root: Path = PROJECT_ROOT) -> dict[str, object]`。
- Report schema：`sandbox-capability-hermes-report-v1`，固定 `policy="read-only"`、`invocations=0`、`writes=0`。
- Report consumes only fresh validated `sandbox-capability-evidence-v1`; missing/stale/invalid evidence is reported blocked, not repaired。

- [ ] **Step 1: Write failing Hermes boundary tests**

測試 available/blocked/not_applicable/invalid evidence、missing artifact、stale fingerprint、read-only counters；確認 Hermes 不啟動 `sandbox-exec`、不寫 Graph/Hub/Sidecar、不中和其他 gates。

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_hermes_post_change_check.py -q
```

Expected：sandbox report function/schema 尚未存在，新增 tests FAIL。

- [ ] **Step 3: Implement read-only report**

只讀取 bounded evidence 與 metadata；不執行 probe、不呼叫 network、不啟動 services、不修改 runtime state。Memory Hub/Sidecar hints 只能作 troubleshooting context，不能解除 blocked。

- [ ] **Step 4: Run independent verification**

```bash
.venv/bin/python -m pytest tests/test_sandbox_capability_preflight.py tests/test_sandbox_pytest_integration.py tests/test_sandbox_ci_contract.py tests/test_hermes_post_change_check.py -q
.venv/bin/python -m pytest -q
.venv/bin/python scripts/hermes_post_change_check.py --json
```

Expected：

- 合資格 host：sandbox preflight `available`、sandbox tests PASS、full pytest PASS。
- 受限 host：sandbox gate 單一 `blocked_environment`、exit non-zero；不得宣稱 full acceptance PASS。
- Hermes：services、baseline、formal scope 與 read-only Graph/Hub/Sidecar reports 各自清楚報告。

- [ ] **Step 5: Fresh Review and documentation handoff**

依 `REVIEW_AGENT_CONTRACT.md` 做 findings-first Review；只在 Review、full verification、Hermes 與 applicable UI acceptance 全部有 fresh evidence 後更新 handoff。不得把 preflight `available` 寫成 Hermes PASS。

## Rollback and stop conditions

- 若 preflight hook 造成一般 tests regression，停用 pytest hook、保留 standalone sandbox CI job，禁止將 blocked 當 PASS。
- 若 probe profile 過寬、secret capture、unexpected write/network/process success，立即停在該 Task，修正最小權限後重新產生 evidence。
- 若 macOS required runner 不存在或 capability blocked，CI 必須 fail-closed；不可改用 Linux job 冒充 macOS sandbox acceptance。
- 不刪除/覆寫歷史 evidence，不修改 SQLite、baseline、formal business state、Graph、Memory Hub 或 Memory Sidecar。
