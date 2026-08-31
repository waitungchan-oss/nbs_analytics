# Sandbox Capability Preflight Spec

狀態：Draft，供 implementation review

日期：2026-08-31

## 1. 目的

建立一個 deterministic、source-bound、fail-closed 的 sandbox capability preflight，避免外層受限 execution environment 將 macOS `sandbox-exec` capability 缺失誤報成大量 test failures，同時確保正式 CI／release gate 不會把 capability 缺失誤判為 PASS。

目標不是關閉 sandbox 或降低安全要求，而是在測試開始前辨識 runner 是否具備測試所需的 sandbox capability，將結果清楚分類為 `available`、`blocked_environment` 或 `not_applicable`。

## 2. 已驗證問題

本專案曾出現 28 個 failures，共同錯誤為：

```text
sandbox-exec: sandbox_apply: Operation not permitted
```

同一份 source 與 test suite 在具備完整本機執行權限的環境下通過 `2572 passed`。因此 failure boundary 是外層 execution policy 與 nested macOS sandbox 的 capability mismatch，不是 `SandboxedSubprocessAgentRunner` 測試所驗證的 filesystem、network、process 或 write policy 本身。

## 3. 設計原則

1. Capability preflight 必須先於 sandbox integration tests 執行。
2. `blocked_environment` 不等於 `passed`，也不等於 product code failure。
3. Local developer convenience 可以顯示明確 blocked remediation；CI／release gate 必須 fail-closed，要求合資格 runner。
4. 不得以 `skipif(sys.platform == "darwin")` 隱藏 macOS sandbox tests；macOS 正是這組 contract 的 target platform。
5. 不得移除 sandbox policy、改用 `sudo`、關閉 filesystem/network restrictions 或放寬 allowed-write paths 來「修復」測試。
6. Preflight evidence 只描述 execution capability，不宣稱 business correctness、Review PASS、Hermes PASS、baseline PASS 或 UI acceptance PASS。
7. Formal scope 固定為「不含掛賬核銷與TT退款轉團款」；2026-05 frozen baseline 固定為 `HKD 12,057,968`，本 spec 不改任何 business state。

## 4. Scope

### In scope

- macOS `sandbox-exec` executable、nested sandbox application、最小 filesystem/write/process/network probes。
- pytest collection gate 與明確 `blocked_environment` reporting。
- CI runner capability assertion、artifact schema、diagnostic remediation。
- Hermes post-change 對 capability evidence 的 read-only reporting。
- 與既有 `SandboxedSubprocessAgentRunner`、implementation-agent integration tests、full pytest 的最小接入。

### Out of scope

- 修改正式 SQLite、baseline、revenue、GMV、退款、export schema 或 business rules。
- 改寫 `SandboxedSubprocessAgentRunner` 的 security policy 以迎合受限 host。
- 新 agent orchestration、approval、dispatch、workflow control、Governance Graph build 或 Memory write path。
- 將一般 unit tests 搬入 sandbox、替換 macOS sandbox backend，或導入新的 container runtime。
- UI 行為與 dashboard acceptance。

## 5. Terminology and gate semantics

| Status | 意義 | Developer run | CI／release gate |
|---|---|---|---|
| `available` | probe 完整通過，具備執行該 sandbox contract 的能力 | 執行完整 tests | 可繼續 full pytest |
| `blocked_environment` | host、outer sandbox、entitlement、executable 或 policy 不允許 capability | 顯示 blocker 與 remediation | gate fail，不能 PASS |
| `not_applicable` | 非 macOS，且該 suite 明確只適用 macOS | 可不執行 macOS-only tests | 必須另有 Linux/non-macOS contract，不能代替 macOS gate |
| `invalid_evidence` | probe output、fingerprint、schema 或 environment identity 不可信 | blocked | gate fail |

`blocked_environment` 只可讓測試結果變得可診斷，不可把 exit code 轉成 0。若 CI 必須先跑全套非 sandbox tests，應將 sandbox pack 作為獨立 required gate，而非 silently skip。

## 6. Capability probe contract

建議新增 provider-neutral module：

```python
class SandboxCapabilityPreflight(Protocol):
    def probe(self, request: SandboxProbeRequest) -> SandboxCapabilityEvidence: ...
```

### Request

`SandboxProbeRequest` 必須包含：

- expected platform：`darwin`。
- project/workspace identity fingerprint。
- sandbox backend 的 resolved absolute path。
- probe timeout、stdout/stderr cap 與 temporary probe root。
- probe profile fingerprint；不得直接重用 production write allowlist 作為 probe 的 implicit permission。

### Probe sequence

依次執行 bounded probes，任一 required probe 失敗即停止：

1. **Platform probe**：確認 `sys.platform == "darwin"`；非 macOS 回傳 `not_applicable`。
2. **Executable probe**：確認 `sandbox-exec` 是 allowlisted regular executable、非 symlink、可執行。
3. **Minimal application probe**：以固定、無 secrets、無 network 的最小 profile 呼叫 `sandbox-exec -p <profile> <probe-command>`。
4. **Filesystem policy probe**：確認 probe process 可讀取 staging root，並只能寫入明確 probe target；不得接觸 project DB 或正式 files。
5. **Process policy probe**：確認 child lifecycle 能正常啟動與結束；不得 fork unrestricted child。
6. **Network policy probe**：確認預期 deny policy 生效；不連外、不使用 production endpoint。
7. **Receipt probe**：確認 probe output 符合 exact schema、exit code 為 0，並產生 source-bound evidence。

任何 `sandbox_apply: Operation not permitted`、permission denied、profile apply failure、timeout、unexpected write/network success 或 output/schema mismatch 均回傳 `blocked_environment` 或 `invalid_evidence`，不得執行該 sandbox test pack。

## 7. Evidence schema

新增 schema 建議命名為 `sandbox-capability-evidence-v1`，採 exact top-level fields：

```text
schemaVersion
status
platform
backendPathFingerprint
probeProfileFingerprint
workspaceFingerprint
capabilities
failureCode
diagnostics
startedAt
finishedAt
evidenceFingerprint
```

規則：

- `capabilities` 只保存 bounded booleans，例如 `applicationApplied`、`filesystemPolicyEnforced`、`processPolicyEnforced`、`networkPolicyEnforced`。
- `diagnostics` 只保存 machine-readable code、bounded redacted message 與 exit metadata；不保存 secrets、完整 environment、完整 command、project path 或 raw probe output。
- `backendPathFingerprint` 由 resolved path 的 controlled identity metadata 計算，不把任意 path 直接暴露到 artifact。
- `evidenceFingerprint` 必須由其他 exact fields 的 canonical payload 計算；任何欄位變更均須被 validator 拒絕。
- evidence 必須以 immutable workspace/project identity binding；不接受跨 workspace、跨 branch 或 stale evidence reuse。
- artifact write 必須使用既有 runtime path、regular-file、symlink rejection 與 atomic write boundary。

## 8. pytest integration

### Test categorization

- 一般 unit tests：不依賴 macOS sandbox，不受 preflight 影響。
- sandbox integration tests：明確標記 `sandbox` marker，必須由 preflight gate 控制。
- non-macOS contract tests：只驗證 `not_applicable` 分類與不誤報。

### Collection/runtime behavior

1. pytest session start 執行一次 preflight，使用 deterministic probe input。
2. `available`：sandbox integration tests 正常 collection/run。
3. `blocked_environment`：sandbox pack 以單一明確 environment blocker 結束，顯示 remediation；不可產生 28 個重複 stack traces。
4. `not_applicable`：macOS-only tests 明確記錄 non-applicable；CI 仍由 platform matrix 確保 macOS required job 存在。
5. preflight 自身失敗或 evidence invalid：test session fail，不能 fallback 成 skip。

Recommended CLI separation：

```bash
.venv/bin/python -m pytest -m "not sandbox" -q
.venv/bin/python -m pytest -m sandbox -q
```

第一個命令不能代替第二個；第二個命令必須先通過 capability preflight。

## 9. CI and release policy

CI 必須至少提供兩個清楚分離的結果：

- `pytest-unit-and-non-sandbox`：驗證一般 code/test contract。
- `pytest-sandbox-integration`：在合資格 macOS runner 執行 preflight + sandbox tests。

Required policy：

- macOS sandbox job 缺失、probe blocked、evidence invalid、test failure 均為 required check failure。
- 不允許 CI 將 `blocked_environment` 轉為 success、使用 `continue-on-error` 或默默 skip。
- runner image、macOS version、sandbox backend、profile fingerprint 改變時，必須產生 fresh evidence。
- local Codex/restricted host 的 blocked 結果只能作 diagnostic；不得宣稱 release-ready。
- full pytest 與 sandbox integration gate 分開報告，但 release acceptance 需兩者都具備合資格 evidence。

## 10. Hermes、Governance Graph、Memory Hub、Memory Sidecar boundary

- Hermes 只 read-only 消費 `sandbox-capability-evidence-v1`，報告 `available`／`blocked_environment`／`not_applicable` 與 failure code；不改 test result、不啟動 sandbox、不修改 runner policy。
- Governance Graph 只將 probe evidence 作 read-only lineage/projection；不執行 build、不回寫 policy、不把 Graph `blocked` 轉成 available。
- Memory Hub 只能提供最多 3 items、6000 bytes、800 ms 的 bounded hints，例如既有 runner/environment troubleshooting；hints 不能成為 capability proof。
- Memory Sidecar report 必須維持 `policy=read-only`、`invocations=0`、`writes=0`；timeout/stale/malformed/permission denied 時 fallback 到 canonical probe evidence。
- 缺少 Graph、Hub 或 Sidecar 不得阻止 canonical preflight；反之，它們也不能解除 `blocked_environment`。

## 11. Security requirements

- probe command 使用 `shell=False`、固定 argv 與 explicit environment allowlist。
- probe root 使用 ephemeral temporary directory；禁止 project root、SQLite、baseline、正式 exports、credential files。
- profile 僅允許 probe 所需的最小 read/write/process/network rules；不得從 production profile 複製未審查權限。
- stdout/stderr 有 byte cap；timeout 後終止 process group，不留下 orphan process。
- probe diagnostics redaction 必須覆蓋 API key、token、password、credential、private key、完整 environment 與 raw path。
- symlink、hardlink race、path traversal、unexpected write、unexpected network success 均為 blocked/invalid evidence。
- preflight 不能使用 `sudo` 或放寬 outer sandbox 來取得 capability。

## 12. Verification plan

### Unit tests

- platform classification、executable/path/symlink validation。
- successful minimal probe 與 exact evidence fingerprint。
- `sandbox_apply: Operation not permitted` → `blocked_environment`。
- timeout、non-zero、malformed output、output cap、unexpected write/network/process result。
- stale evidence、workspace mismatch、tamper、secret capture rejection。
- preflight runs once per pytest session；沒有 28 個重複 failure。

### Integration tests

- 合資格 macOS runner：preflight `available`，sandbox suite 執行並通過。
- restricted outer sandbox：preflight `blocked_environment`，只產生一個明確 blocker，exit code 非 0。
- non-macOS runner：preflight `not_applicable`，platform matrix 規則仍要求 macOS job。
- full pytest：一般 suite 與 sandbox gate 都取得各自 source-bound evidence。

### Independent gates

- Strict Review：確認 test categorization、fail-closed semantics 與 security policy，使用 fresh evidence。
- full pytest：報告 unit/non-sandbox 與 sandbox integration 結果，不以一者替代另一者。
- Hermes：確認 services、baseline、formal scope、runtime reports 與 sandbox evidence；不把 preflight PASS 等同 Hermes PASS。
- UI acceptance：只有 UI diff 存在才執行。

## 13. Rollback

- 若 preflight regression，保留一般 tests 與既有 sandbox tests；停用新的 pytest integration hook，恢復獨立 sandbox job，但不得把 blocked 當 PASS。
- 保留 bounded evidence 與 failure diagnostics，不刪除或覆寫歷史 acceptance evidence。
- 不修改 SQLite、baseline、business artifacts、Governance Graph、Memory Hub 或 Memory Sidecar state。
- 若發現 probe profile 過寬或 secret capture，立即停用該 profile，修正最小權限後重新產生 fresh evidence。

## 14. Acceptance criteria

本 spec 的 implementation 只有在以下條件全部具備時才算完成：

1. restricted environment 不再產生 28 個重複 `sandbox_apply` failures，而是單一、可診斷的 `blocked_environment`。
2. 合資格 macOS runner 能執行 sandbox integration suite，且不降低原有 security assertions。
3. full pytest、sandbox integration、Hermes、Strict Review 是獨立結果；任何 blocked gate 不得被其他 gate 覆蓋。
4. fresh evidence 綁定 workspace、runner backend、probe profile 與 timestamps；stale/tampered evidence fail-closed。
5. formal scope、2026-05 baseline `HKD 12,057,968`、SQLite 與 authoritative business state 完全不變。
6. Governance Graph、Memory Hub、Memory Sidecar 維持 read-only/non-authoritative，無任何回寫或 approval side effect。
