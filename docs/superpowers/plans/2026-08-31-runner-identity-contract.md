# Runner Identity Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改變正式業務資料或既有 verification-v1 parser 的前提下，建立並接入 `runner-identity-v1`，統一 Local CLI、Remote API、Local Model 的 identity 與 cache/evidence binding。

**Architecture:** 新增純 Python identity model，集中 exact schema、normalization、legacy mapping 與 fingerprint。既有 Local CLI、Hermes capability evidence、Strict Review gate evidence 與 cache 透過 adapter/companion envelope 接入；transport-specific capability probe 和 gate verdict 保持原責任。

**Tech Stack:** Python 3、frozen dataclass、canonical SHA-256 fingerprint、JSON、pytest、既有 `.nbs_agent_runtime` atomic artifact writers。

**Spec:** `docs/superpowers/specs/2026-08-31-runner-identity-contract-design.md`

## Global Constraints

- 正式收入口徑固定為「不含掛賬核銷與TT退款轉團款」。
- 2026-05 frozen baseline 固定為 `HKD 12,057,968`。
- 不修改 SQLite、baseline、revenue scope、GMV、退款 ledger、Forecast、Dashboard、正式 export schema 或 application runtime。
- 不新增 Governance Graph node/edge、Memory Hub/Memory Sidecar write path、approval、dispatch、scheduler 或外部 provider。
- `verification-v1` 維持既有 exact top-level schema：`{"commands": [...]}`。
- 舊 artifact 只做明確、read-only、fail-closed 相容讀取；不得補寫歷史 artifact 使其看似 fresh。
- Identity valid 不代表 capability ready；capability proof 與 gate verdict 必須分離。
- 每個 Task 都要有 targeted tests、`py_compile`、`git diff --check` 與 findings-first Review；不得自行 commit、merge 或 push。
- 每個 implementation Task 使用 fresh isolated worktree，且每個 workflow run 只批准一個 Task。

---

## File Map

| File | Responsibility |
|---|---|
| `backend/agents/runner_identity.py` | canonical model、normalization、legacy mapping、fingerprint。 |
| `backend/agents/runner_identity_envelope.py` | bounded companion envelope 的 atomic writer/reader 與 path safety。 |
| `backend/agents/review_runner_profile.py` | Local CLI profile adapter，保留 CLI/cache/live probe。 |
| `backend/agents/runner_capability_evidence.py` | capability evidence identity binding，保留既有 allowlist。 |
| `backend/agents/strict_review_evidence_cache.py` | canonical identity-aware cache key/hit。 |
| `backend/agents/verification_evidence_writer.py` | gate metadata identity reference，不改 verification-v1 payload。 |
| `backend/agents/review_agent_service.py` | Review identity reference validation。 |
| `scripts/review_agent.py` | approved command/profile identity normalization。 |
| `scripts/verification_chain.py` | chain-level identity binding 與 fail-closed mapping。 |
| `scripts/hermes_runner_capability_hook.py` | Hermes manifest/receipt canonical identity adapter。 |
| `scripts/hermes_live_ab_runner.py` | Remote API live AB identity adapter，不改 transport。 |
| `tests/test_runner_identity.py` | core schema、mapping、fingerprint。 |
| `tests/test_runner_identity_envelope.py` | envelope atomic/path/schema safety。 |
| Existing affected test files | Local CLI、capability、cache、verification、Review 與 Hermes regression。 |

---

## Task 1: 建立 canonical identity model 與 companion envelope

**Files:**

- Create: `backend/agents/runner_identity.py`
- Create: `backend/agents/runner_identity_envelope.py`
- Test: `tests/test_runner_identity.py`
- Test: `tests/test_runner_identity_envelope.py`

**Interfaces:**

- `RunnerIdentityError(ValueError)`。
- `RunnerIdentity.from_dict(payload: Mapping[str, Any]) -> RunnerIdentity`。
- `RunnerIdentity.to_dict() -> dict[str, str]`。
- `RunnerIdentity.identity_fingerprint -> str`。
- `RunnerIdentity.from_legacy_local_cli(...) -> RunnerIdentity` 與 `from_legacy_hermes(...)`；不明確 mapping 必須拒絕。
- `write_identity_envelope(path: Path, identity: RunnerIdentity, *, source_fingerprint: str, artifact_kind: str) -> Path`。
- `read_identity_envelope(path: Path, *, expected_source_fingerprint: str | None = None) -> IdentityEnvelope`。

- [ ] **Step 1: Write failing tests**

測試 exact keys、三種 transport、slug/type/enum、SHA-256 fingerprint、unknown/missing fields、未知 alias、只有 model 的 payload、source mismatch、symlink/path escape 與 malformed envelope。

```python
canonical = {
    "schemaVersion": "runner-identity-v1",
    "runnerId": "review-runner",
    "transport": "local_cli",
    "provider": "openai",
    "model": "gpt-5.4",
    "profile": "strict-review",
    "executionEnvironment": "local-macos",
}
identity = RunnerIdentity.from_dict({
    **canonical,
    "identityFingerprint": canonical_fingerprint(canonical),
})
assert identity.to_dict()["identityFingerprint"] == identity.identity_fingerprint
```

- [ ] **Step 2: Verify the tests fail**

```bash
.venv/bin/python -m pytest tests/test_runner_identity.py tests/test_runner_identity_envelope.py -q
```

Expected: new interfaces are absent and tests fail。

- [ ] **Step 3: Implement the pure model**

只接受 `local_cli`、`remote_api`、`local_model`；對前七個欄位產生 canonical fingerprint；不保存 command、完整 path、credential、prompt、response 或 token。Legacy mapping 必須是明確 allowlist，不能猜測 transport/provider/runnerId。

- [ ] **Step 4: Implement the bounded envelope**

Envelope 保存 `schemaVersion`、identity、source fingerprint、artifact kind 與 envelope fingerprint。使用 atomic write，限制在 `.nbs_agent_runtime`，並拒絕 symlink、path traversal、absolute escape、非 regular file 與 source mismatch。

- [ ] **Step 5: Verify Task 1**

```bash
.venv/bin/python -m pytest tests/test_runner_identity.py tests/test_runner_identity_envelope.py -q
.venv/bin/python -m py_compile backend/agents/runner_identity.py backend/agents/runner_identity_envelope.py
git diff --check
```

Expected: focused tests PASS；正式 DB、baseline、Git index 與 application runtime 不變。

## Task 2: 接入 Local CLI、capability evidence 與 cache

**Files:**

- Modify: `backend/agents/review_runner_profile.py`
- Modify: `backend/agents/runner_capability_evidence.py`
- Modify: `backend/agents/strict_review_evidence_cache.py`
- Test: `tests/test_review_runner_profile.py`
- Test: `tests/test_runner_capability_evidence.py`
- Test: `tests/test_strict_review_evidence_cache.py`

**Interfaces:**

- Consumes Task 1 `RunnerIdentity`。
- `RunnerProfile.to_runner_identity(*, profile_name: str, execution_environment: str, provider: str) -> RunnerIdentity`。
- Capability receipt/run/evidence 保存 bounded identity reference；既有 provider/model/reasoning fields 保留。
- `cache_identity(...)` 以 canonical identity fingerprint 作 runner fingerprint；缺失或未驗證 identity 只能 cache miss。

- [ ] **Step 1: Write failing adapter/cache tests**

```python
identity = profile.to_runner_identity(
    profile_name="strict-review",
    execution_environment="local-macos",
    provider="openai",
)
assert identity.transport == "local_cli"
assert identity.model == "gpt-5.4"
assert cache_identity(source, command, policy, identity.identity_fingerprint)
```

另測試舊 `executable/model/cachePath` profile 沒有 approved mapping 時回傳 `legacy_identity_unbound` 或 blocked，不得猜測。

- [ ] **Step 2: Verify new expectations fail**

```bash
.venv/bin/python -m pytest tests/test_review_runner_profile.py tests/test_runner_capability_evidence.py tests/test_strict_review_evidence_cache.py -q
```

Expected: only new identity assertions fail；既有 static/live capability status 不可改變。

- [ ] **Step 3: Add the Local CLI adapter**

保留 executable、CLI version、model cache、live probe 與 `blocked_runner_transport` 行為。adapter 不得由 executable basename 推導 provider、runnerId 或 environment。

- [ ] **Step 4: Bind capability evidence**

驗證 canonical identity 與既有 provider/model/profile fields 一致。identity mismatch 回傳 `blocked_runner_capability`；live timeout/non-zero/invalid response 繼續回傳 `blocked_runner_transport`。

- [ ] **Step 5: Make cache identity canonical**

保留既有呼叫相容性；新 caller 傳入 canonical identity fingerprint。cache payload 缺 identity 或 fingerprint 不一致時只 miss，不刪除舊 artifact。

- [ ] **Step 6: Verify Task 2**

```bash
.venv/bin/python -m pytest tests/test_review_runner_profile.py tests/test_runner_capability_evidence.py tests/test_strict_review_evidence_cache.py -q
.venv/bin/python -m py_compile backend/agents/review_runner_profile.py backend/agents/runner_capability_evidence.py backend/agents/strict_review_evidence_cache.py
git diff --check
```

Expected: existing capability tests and new identity tests PASS；沒有 network call、SQLite write 或 cache deletion。

## Task 3: 接入 Strict Review、verification gate 與 Hermes evidence

**Files:**

- Modify: `backend/agents/verification_evidence_writer.py`
- Modify: `backend/agents/review_agent_service.py`
- Modify: `scripts/review_agent.py`
- Modify: `scripts/verification_chain.py`
- Modify: `scripts/hermes_runner_capability_hook.py`
- Modify: `scripts/hermes_live_ab_runner.py`
- Test: `tests/test_verification_evidence_writer.py`
- Test: `tests/test_review_agent_service.py`
- Test: `tests/test_verification_chain.py`
- Test: `tests/test_hermes_runner_capability_hook.py`
- Test: `tests/test_hermes_live_ab_runner.py`

**Interfaces:**

- Consumes Task 1 envelope and Task 2 adapters。
- `verification-v1` 仍只保存 `{"commands": [...]}`；identity 綁在 gate metadata/companion reference。
- `review_agent.py` 與 `verification_chain.py` 使用同一 normalizer，不各自產生 canonical runner name。
- Hermes 保留既有 `provider=hermes`、`model=deepseek-v4-flash`、`reasoningProfile=max` 等 transport-specific fields；本 Task 不改 live API transport。

- [ ] **Step 1: Write failing binding tests**

```python
assert verification_payload == {"commands": commands}
assert gate_metadata["runnerIdentityFingerprint"] == identity.identity_fingerprint
```

測試 missing identity、producer/consumer mismatch、legacy unbound artifact、Hermes mismatch 都不能產生 PASS。

- [ ] **Step 2: Verify binding tests fail**

```bash
.venv/bin/python -m pytest tests/test_verification_evidence_writer.py tests/test_review_agent_service.py tests/test_verification_chain.py tests/test_hermes_runner_capability_hook.py tests/test_hermes_live_ab_runner.py -q
```

Expected: new binding tests fail；既有 failure status mapping 保持不變。

- [ ] **Step 3: Bind gate metadata**

讓 gate metadata 引用 companion envelope 與 identity fingerprint；讀取時同時驗證 session/source fingerprint、identity fingerprint 與 capability evidence identity。不得修改 verification-v1 top-level。

- [ ] **Step 4: Unify Strict Review wiring**

兩個 launch path 先解析 approved profile/command，取得同一 `RunnerIdentity`。command/model 不一致時在 runner invocation 前 blocked，diagnostics 只保留 bounded code/field/exit information。

- [ ] **Step 5: Bind Hermes evidence**

Hermes manifest、receipt、live AB runner 使用 canonical `remote_api` identity reference；既有 Hermes fields 必須對照 identity。Memory hints 與 Graph evidence 不得成為 capability proof。

- [ ] **Step 6: Verify Task 3**

```bash
.venv/bin/python -m pytest tests/test_verification_evidence_writer.py tests/test_review_agent_service.py tests/test_verification_chain.py tests/test_hermes_runner_capability_hook.py tests/test_hermes_live_ab_runner.py -q
.venv/bin/python -m py_compile backend/agents/verification_evidence_writer.py backend/agents/review_agent_service.py scripts/review_agent.py scripts/verification_chain.py scripts/hermes_runner_capability_hook.py scripts/hermes_live_ab_runner.py
git diff --check
```

Expected: affected tests PASS；Strict Review、full pytest、Hermes 與 UI acceptance 仍分開。
## Task 4: Fresh source-bound verification and closeout

**Files:**

- No new production files。
- Use only fresh `.nbs_agent_runtime/verification_sessions/<sessionId>/` artifacts。

**Interfaces:**

- Consumes Tasks 1–3 outputs and the approved spec。
- Produces fresh Strict Review、full pytest、Hermes evidence，以及 UI acceptance applicability result。

- [ ] **Step 1: Check source boundary**

```bash
git status --short --branch
git log -5 --oneline
git diff --name-only
```

保留既有 `NBS_ANALYTICS_HANDOFF.md` dirty change；沒有 evidence 不得刪除或歸屬它。

- [ ] **Step 2: Run the affected regression pack**

執行 Tasks 1–3 的 exact test files、changed-surface compile 與 `git diff --check`，並確認 artifact 沒有 raw business data。

- [ ] **Step 3: Run fresh Strict Review**

使用 local `scripts/agent_workflow.py`/Strict Review CLI，並提供明確批准的 compatible runner profile。若 identity 或 transport proof 缺失，保留 `blocked_runner_capability`/`blocked_runner_transport`，不可使用舊 PASS。

- [ ] **Step 4: Run full pytest and Hermes independently**

依 verification-session contract 執行 full pytest 與 `scripts/hermes_post_change_check.py`。確認 baseline `HKD 12,057,968`，且沒有 DB/formal business scope write。

- [ ] **Step 5: Record UI applicability**

本計畫不改 UI，記錄 `not required / not run`，不可報告為 PASS。

- [ ] **Step 6: Handoff for findings-first Review**

把 implementation report、actual diff 與 fresh evidence 交給 Review。只有 Strict Review PASS、full verification PASS、Hermes PASS 才能視為接受；本計畫不含 commit/merge/push。

## Rollback

任何 parser incompatibility 或 false blocking 只回退 identity model/adapter/companion binding，或暫停新 enforcement 並保留 legacy read compatibility。不得刪除 runtime evidence、重寫歷史 artifact、清除 backup、改 SQLite、改 baseline 或改正式 revenue/GMV rules。

## Plan self-review

- Spec coverage：schema、三種 transport、artifact binding、legacy、fail-closed/security、rollback 與 independent gates 均已分配至 Tasks 1–4。
- Completeness scan：沒有未完成標記、未分配 requirement 或模糊的 error-handling step。
- Type consistency：Task 1 產出 `RunnerIdentity`/envelope；Task 2–3 消費；Task 4 驗證其 fresh evidence。
- Scope check：每個 Task 可獨立測試，且必須分別取得授權與 Review。
