# Hermes CLI Transport Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改變現有 Remote API、正式業務資料或獨立 verification gates 的前提下，建立 Hermes Local CLI 的 bounded transport adapter、identity/source binding 與 fail-closed receipt evidence。

**Architecture:** 以既有 `RunnerIdentity` 作為 immutable runner identity source，新增 provider-neutral CLI request/result model、allowlisted subprocess adapter 與 exact-schema transport receipt。`hermes_live_ab_runner.py` 只在明確選用 Local CLI 時接入；`hermes_turn_receipt.py` 的 Remote API path 保持不變。Governance Graph、Memory Hub、Memory Sidecar 僅作 read-only bounded context，不能成為 capability 或 acceptance authority。

**Tech Stack:** Python 3、`dataclasses`/`Protocol`、`subprocess`、JSON、SHA-256、既有 `.nbs_agent_runtime` atomic artifact writers、pytest、`py_compile`。

**Spec:** `docs/superpowers/specs/2026-08-31-hermes-cli-transport-adapter-design.md`

## Global Constraints

- 正式收入口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite、baseline、revenue scope、GMV、退款 ledger、旅行團／票務人數、Dashboard、正式 export schema 或 application business runtime。
- 不改變現有 Remote API invocation、provider routing、API key、network policy 或 Local Model runtime。
- 不新增 Governance Graph node/edge/build/write path；Graph 僅 read-only projection，blocked 不得轉成 ready。
- Memory Hub/Memory Sidecar 僅接受 bounded read-only hints：最多 3 items、6000 bytes、800 ms；失敗必須 fallback 至 canonical evidence。
- 不啟動 Gateway、provider installation、network recall、distillation、prune、apply、approval、dispatch 或 workflow state mutation。
- `RunnerIdentity` 是 identity 唯一來源；不得從 executable basename、model name、環境變數或 argv 推斷 runner identity。
- `hermes-cli-transport-receipt-v1` 採 exact-field validation；任何非 `ready` 狀態均不可作 PASS 或 acceptance proof。
- 每個 implementation Task 必須在 fresh isolated worktree 執行，完成 targeted tests、`py_compile`、`git diff --check`、findings-first Review，再由主 Codex 執行 full pytest/Hermes 等獨立 gates。
- Implementation Agent 只執行本 plan 明確批准的一個 Task，不自行 commit、merge、push 或開始下一個 Task。

## File Map

| File | Responsibility |
|---|---|
| `backend/agents/hermes_cli_transport.py` | CLI request/result models、allowlisted subprocess invocation、probe、normalize、failure classification。 |
| `backend/agents/hermes_cli_transport_receipt.py` | `hermes-cli-transport-receipt-v1` exact schema、redaction、digest、source/identity binding、atomic persistence adapter。 |
| `backend/agents/runner_identity.py` | 重用既有 canonical identity；只在需要時補充 CLI transport mapping，不新增第二套 fingerprint。 |
| `backend/agents/runner_identity_envelope.py` | 重用既有 envelope reader/writer，不改其 security contract。 |
| `scripts/hermes_live_ab_runner.py` | 僅加入明確 Local CLI selection/wiring；Remote API default path 不變。 |
| `scripts/hermes_turn_receipt.py` | 保持 Remote API；若共用 validator，只引入 provider-neutral validation，不改 transport。 |
| `tests/test_hermes_cli_transport.py` | adapter model、probe/invoke、failure、security、bounded output tests。 |
| `tests/test_hermes_cli_transport_receipt.py` | receipt exact schema、redaction、binding、tamper、atomic write tests。 |
| `tests/test_hermes_live_ab_runner.py` | explicit CLI selection、legacy/default Remote API regression。 |
| `tests/test_hermes_turn_receipt.py` | Remote API compatibility regression。 |
| Existing agent/Hermes contract docs | 只在實作與 gates 通過後更新 evidence；本 plan 不預先改 governance contract。 |

## Task 1: Define CLI transport models and bounded command policy

**Files:**

- Create: `backend/agents/hermes_cli_transport.py`
- Test: `tests/test_hermes_cli_transport.py`
- Reuse: `backend/agents/runner_identity.py`

**Interfaces:**

- `CliTransportError(ValueError)`。
- `CliTransportStatus = Literal["ready", "blocked_runner_capability", "blocked_runner_transport", "invalid_evidence"]`。
- `CliProbeRequest`：validated `RunnerIdentity`、resolved executable、argv tuple、environment mapping、timeout seconds、stdout/stderr/response byte caps、working directory、`command_shape_fingerprint`。
- `CliInvokeRequest`：`CliProbeRequest` fields 加上 source/turn/manifest fingerprint 與 bounded payload；不得保存完整 prompt 到 receipt。
- `CliProbeResult`：status、`cli_version`、observed model、reason code、bounded diagnostics。
- `CliInvokeResult`：status、exit code、timeout flag、observed identity/model、bounded response、stdout/stderr byte metadata、response fingerprint、reason code。
- `HermesCliTransportAdapter.probe(request) -> CliProbeResult`。
- `HermesCliTransportAdapter.invoke(request) -> CliInvokeResult`。

- [ ] **Step 1: Write failing model and policy tests**

```python
def test_cli_request_requires_local_cli_identity():
    identity = RunnerIdentity.from_dict({
        "schemaVersion": "runner-identity-v1",
        "runnerId": "hermes-remote",
        "transport": "remote_api",
        "provider": "hermes",
        "model": "deepseek-v4-flash",
        "profile": "max",
        "executionEnvironment": "hermes-local",
        "identityFingerprint": canonical_fingerprint({
            "schemaVersion": "runner-identity-v1",
            "runnerId": "hermes-remote",
            "transport": "remote_api",
            "provider": "hermes",
            "model": "deepseek-v4-flash",
            "profile": "max",
            "executionEnvironment": "hermes-local",
        }),
    })
    with pytest.raises(CliTransportError, match="transport"):
        CliProbeRequest(identity=identity, executable=approved_cli, argv=("--version",))

def test_command_policy_rejects_shell_string_and_unapproved_flags():
    with pytest.raises(CliTransportError):
        build_cli_argv("sh -c 'echo unsafe'")
```

測試 fixture 先在 `tmp_path` 建立 executable `hermes-cli`，再以 `approved_cli = tmp_path / "hermes-cli"` 傳入；`canonical_fingerprint` 使用既有 `RunnerIdentity` 的 canonical helper，不新增第二套 fingerprint。

另測試：missing identity、非 regular/symlink executable、path escape、超過 timeout/output cap、空 argv、`sh`/`bash`/`-c`、unbounded argument 與 command fingerprint mismatch。

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_hermes_cli_transport.py -q
```

Expected：新 model、policy 與 adapter interface 尚不存在而失敗；既有 Hermes tests 不執行任何新 CLI。

- [ ] **Step 3: Implement immutable request/result models**

使用 frozen dataclass 或等價 immutable model；request 建構時完成 identity transport、path、argv、cap、timeout 與 fingerprint validation。不要把 executable path 寫入 `RunnerIdentity.identityFingerprint`。

- [ ] **Step 4: Implement command/environment policy**

只接受 argv list、`shell=False`、explicit environment allowlist、bounded argument length、approved working directory 與 non-symlink regular executable。禁止從 parent environment 傳遞完整環境。

- [ ] **Step 5: Verify Task 1**

```bash
.venv/bin/python -m pytest tests/test_hermes_cli_transport.py -q
.venv/bin/python -m py_compile backend/agents/hermes_cli_transport.py
git diff --check
```

Expected：models/policy focused tests PASS；沒有 process launch、network、SQLite 或 artifact write。

## Task 2: Implement bounded probe/invoke and response normalization

**Files:**

- Modify: `backend/agents/hermes_cli_transport.py`
- Test: `tests/test_hermes_cli_transport.py`

**Interfaces:**

- `LocalCliExecutor.run(argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str], timeout_seconds: float, output_limit_bytes: int) -> RawCliExecution`。
- `HermesCliTransportAdapter.probe(...) -> CliProbeResult`：只執行一次固定 version/capability probe。
- `HermesCliTransportAdapter.invoke(...) -> CliInvokeResult`：單次 bounded invocation，不自動 retry。
- `normalize_cli_response(stdout: bytes, *, response_limit_bytes: int) -> Mapping[str, object]`：只接受 allowlisted JSON 或 JSON event stream。

- [ ] **Step 1: Write failing execution tests**

建立 repository-local fake CLI fixture（測試 runtime directory 內，非正式 artifact），覆蓋：success JSON、valid event stream、version mismatch、malformed JSON、mixed unexpected text、non-zero exit、timeout、oversized stdout/stderr/response、child process failure。

```python
request = make_valid_cli_request(executable=fake_cli, argv=("run", "--json"))
result = adapter.invoke(request)
assert result.status == "ready"
assert result.response_fingerprint
assert result.stdout_bytes <= request.stdout_limit_bytes
```

`make_valid_cli_request` 必須回傳 Task 1 定義的 immutable `CliInvokeRequest`，並使用 fixture identity `transport="local_cli"`、bounded caps 與 test runtime working directory；不得繞過 request validation。

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_hermes_cli_transport.py -q
```

Expected：executor、probe、normalizer 尚未提供，新增 cases FAIL。

- [ ] **Step 3: Implement subprocess executor**

使用 `subprocess.Popen` 或等價 bounded primitive，明確 `shell=False`；建立 process group，timeout 後終止 child group；stdout/stderr 以 bytes cap 收集，避免先無限讀入 memory。不得在 exception 中保留 secret 或 raw payload。

- [ ] **Step 4: Implement probe classification**

固定執行 version/capability argv；missing executable、permission、probe timeout、non-zero、schema mismatch、observed model/profile mismatch 均回傳 `blocked_runner_capability`，不猜測版本、不 retry。

- [ ] **Step 5: Implement response normalization**

解析 allowlisted JSON/event stream；截斷、空 response、mixed unexpected text、invalid schema 或 response cap exceeded 不得回傳 `ready`。對 valid response 計算 bounded response fingerprint，只將 normalized response 留在記憶體供 caller 使用。

- [ ] **Step 6: Verify Task 2**

```bash
.venv/bin/python -m pytest tests/test_hermes_cli_transport.py -q
.venv/bin/python -m py_compile backend/agents/hermes_cli_transport.py
git diff --check
```

Expected：所有 fake CLI cases PASS；未連外、未啟動 model provider、未寫 authoritative state。

## Task 3: Add exact transport receipt and evidence binding

**Files:**

- Create: `backend/agents/hermes_cli_transport_receipt.py`
- Modify: `backend/agents/hermes_cli_transport.py`
- Test: `tests/test_hermes_cli_transport_receipt.py`

**Interfaces:**

- `CLI_RECEIPT_SCHEMA = "hermes-cli-transport-receipt-v1"`。
- `CliTransportReceipt.from_result(result: CliInvokeResult, *, source_fingerprint: str, command_shape_fingerprint: str) -> CliTransportReceipt`。
- `CliTransportReceipt.to_dict() -> dict[str, object]`。
- `validate_cli_transport_receipt(value: Mapping[str, object], *, expected_identity_fingerprint: str, expected_source_fingerprint: str) -> CliTransportReceipt`。
- `write_cli_transport_receipt(path: Path, receipt: CliTransportReceipt) -> Path`；重用既有 atomic/path-safe writer。
- exact fields：`schemaVersion`、`status`、`runnerIdentityFingerprint`、`sourceFingerprint`、`commandShapeFingerprint`、`cliVersion`、`observedModel`、`exitCode`、`timedOut`、`stdoutDigest`、`stderrDigest`、`responseFingerprint`、`startedAt`、`finishedAt`、`diagnostics`、`stdoutBytes`、`stderrBytes`、`stdoutTruncated`、`stderrTruncated`、`receiptFingerprint`。

- [ ] **Step 1: Write failing receipt tests**

測試 exact top-level keys、status enum、non-negative bounded counters、timeout invariant、identity/source mismatch、tampered digest、malformed diagnostics、secret/prompt/raw argv rejection、symlink/path escape 與 atomic write。

```python
receipt = CliTransportReceipt.from_result(ready_result, source_fingerprint=source_fp, command_shape_fingerprint=command_fp)
assert set(receipt.to_dict()) == {
    "schemaVersion", "status", "runnerIdentityFingerprint", "sourceFingerprint",
    "commandShapeFingerprint", "cliVersion", "observedModel", "exitCode",
    "timedOut", "stdoutDigest", "stderrDigest", "responseFingerprint",
    "startedAt", "finishedAt", "diagnostics",
}
assert "api_key" not in json.dumps(receipt.to_dict())
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv/bin/python -m pytest tests/test_hermes_cli_transport_receipt.py -q
```

Expected：receipt model/validator 尚不存在，新增 tests FAIL。

- [ ] **Step 3: Implement exact receipt model and redaction**

只保存 digest、byte count、truncation flag、machine-readable diagnostics 與 fingerprints；不保存 raw prompt、raw response、完整 argv、secret。`status != ready` 永遠保留 blocked/invalid 結果。

- [ ] **Step 4: Implement binding and persistence**

驗證 identity/source/command shape/response fingerprints；重用既有 regular-file、symlink rejection、runtime path boundary 與 atomic write。receipt 寫入只限 bounded transport evidence，不觸碰 SQLite 或正式業務 artifacts。

- [ ] **Step 5: Verify Task 3**

```bash
.venv/bin/python -m pytest tests/test_hermes_cli_transport_receipt.py tests/test_hermes_cli_transport.py -q
.venv/bin/python -m py_compile backend/agents/hermes_cli_transport.py backend/agents/hermes_cli_transport_receipt.py
git diff --check
```

Expected：exact schema、security 與 binding tests PASS；任何 tamper/secret case fail-closed。

## Task 4: Add explicit Hermes caller wiring without changing Remote API default

**Files:**

- Modify: `scripts/hermes_live_ab_runner.py`
- Test: `tests/test_hermes_live_ab_runner.py`
- Test: `tests/test_hermes_turn_receipt.py`
- Do not modify transport behavior in: `scripts/hermes_turn_receipt.py` unless a shared validator import is strictly required。

**Interfaces:**

- Existing `run_live_ab(...)` default behavior remains unchanged。
- Add a typed/allowlisted `transport="local_cli"` branch; an absent/legacy selection continues the existing Remote API child command.
- CLI branch consumes Task 2 `CliInvokeResult` and Task 3 receipt writer; it must preserve current manifest、activation receipt、control/treatment、source binding 與 cleanup semantics。

- [ ] **Step 1: Write failing caller regression tests**

測試 default/legacy path still builds current `hermes_turn_receipt.py` command；explicit CLI path only accepts `RunnerIdentity.transport == "local_cli"`；Remote API identity cannot enter CLI branch；child receipt mismatch blocks run。

- [ ] **Step 2: Run focused regression tests**

```bash
.venv/bin/python -m pytest tests/test_hermes_live_ab_runner.py tests/test_hermes_turn_receipt.py -q
```

Expected：新增 explicit CLI expectations FAIL；existing Remote API tests remain baseline。

- [ ] **Step 3: Implement minimal explicit wiring**

只增加 caller selection、request construction、bounded receipt binding 與 failure propagation；不得改 model/API credentials、remote transport、manifest schema、SQLite、business rules 或 UI。

- [ ] **Step 4: Verify compatibility**

```bash
.venv/bin/python -m pytest tests/test_hermes_live_ab_runner.py tests/test_hermes_turn_receipt.py tests/test_hermes_cli_transport.py tests/test_hermes_cli_transport_receipt.py -q
.venv/bin/python -m py_compile scripts/hermes_live_ab_runner.py scripts/hermes_turn_receipt.py
git diff --check
```

Expected：Remote API default regression PASS；explicit Local CLI fixture PASS；兩種 transport 的 receipt/identity 不可互換。

## Task 5: Run review and independent verification gates

**Files:**

- No new implementation files unless a directly evidenced blocker requires a minimal fix。
- Review inputs/artifacts remain under `.nbs_agent_runtime` and are non-authoritative。

**Interfaces:**

- Consumes only fresh source-bound diff, tests and receipts from Tasks 1–4。
- Produces findings-first Review result、full pytest result、Hermes post-change result，以及必要的 handoff evidence；不產生 approval/dispatch/workflow mutation。

- [ ] **Step 1: Run targeted security and regression suite**

```bash
.venv/bin/python -m pytest tests/test_hermes_cli_transport.py tests/test_hermes_cli_transport_receipt.py tests/test_hermes_live_ab_runner.py tests/test_hermes_turn_receipt.py -q
```

- [ ] **Step 2: Run findings-first Strict Review**

使用 fresh context/evidence、明確 `--head WORKTREE`（若 review dirty worktree），依 `docs/agents/REVIEW_AGENT_CONTRACT.md` 執行；Review Agent 只讀，不修改 code、Git、runtime、SQLite 或 baseline。若 blocked，保留 blocked，不宣稱 PASS。

- [ ] **Step 3: Run full pytest independently**

```bash
.venv/bin/python -m pytest -q
```

完整 suite PASS 不取代 Strict Review 或 Hermes。

- [ ] **Step 4: Run Hermes post-change check**

依 `NBS_HERMES_MONITORING.md` 執行 `scripts/hermes_post_change_check.py`。確認 CLI receipt 不被誤當成 Hermes PASS，且 Sidecar report 維持 `policy=read-only`、`invocations=0`、`writes=0`。

- [ ] **Step 5: Check immutable business invariants**

以 fresh evidence 確認 formal scope、2026-05 baseline `HKD 12,057,968`、SQLite、business exports 與 authoritative write paths 未變；UI acceptance 僅在實際 UI diff 存在時執行。

- [ ] **Step 6: Documentation/handoff only after all applicable gates**

只有 Strict Review、full pytest、Hermes 與 applicable UI acceptance 均有 fresh result 後，才更新 handoff/live snapshot；若沒有 documentation change，保持 deterministic no-doc path。不要把 Context Agent、Graph、Hub 或 Sidecar 結果寫成 acceptance PASS。

## Rollback and stop conditions

- 任何 security regression、secret capture、shell injection risk、orphan process、unbounded output 或 identity mismatch 都立即停用 CLI branch，恢復既有 Remote API default。
- rollback 只關閉 explicit CLI selection、保留 non-authoritative bounded receipts；不刪除/覆寫歷史 receipt，不修改 SQLite、baseline、Graph 或 Memory state。
- 若 runner capability、Strict Review、full pytest、Hermes 或 UI gate blocked，停止在該 gate，回報具體 evidence；不可用其他 gate 替代。

## Task commit boundaries

每個 Task 在其 targeted tests、`py_compile`、`git diff --check` 與 findings-first Review 完成後才可交付主 Codex。Implementation Agent 不自行 commit、merge 或 push；integration/commit 順序由主 Codex 依使用者後續授權處理。
