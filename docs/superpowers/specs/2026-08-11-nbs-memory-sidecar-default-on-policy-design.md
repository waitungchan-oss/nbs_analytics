# NBS Memory Sidecar Default-on Policy Design Spec

## Status

- Status: proposed for implementation planning; inactive until the contract amendment gate passes
- Date: 2026-08-11
- Scope: ordinary development workflow recall policy only
- Related contract: `docs/agents/MEMORY_SIDECAR_CONTRACT.md`
- Related live evidence: `docs/superpowers/specs/2026-08-11-nbs-live-hermes-ab-acceptance-design.md`

## 1. Goal

在完成明確的 governance contract amendment 後，讓一般 development task 預設使用 Memory Sidecar recall，以降低 Context Agent 重複探索與輸入 token；同時讓 governance、acceptance、baseline、data、security 與 runtime mutation task 維持 recall-off。Contract amendment 未通過前，effective default 必須仍是 recall-off。Sidecar 永遠只是 bounded、non-authoritative hint provider；canonical evidence、Review、Verification、Hermes、SQLite、baseline、Git 與 workflow control 不變。

## 2. Non-goals

- 不把 writer 開啟；`writer_enabled` 永遠是 `false`。
- 不把 recall hint 當成正式 evidence、approval、Review、Verification 或 Hermes PASS。
- 不修改 `800 ms` sidecar recall budget、三筆／6000 bytes caps、provider allowlist 或 sourceRef policy。
- 不自動建立 Graph snapshot、修改 baseline、SQLite、revenue scope、business rules、export schema 或 Git。
- 不在 provider unavailable、timeout、stale、invalid、conflict 或 permission failure 時阻塞普通 workflow。
- 不把 default-on 擴展到正式 acceptance、資料修復、baseline promotion、security 或 runtime state mutation。

## 3. Governance prerequisite

現行 `MEMORY_SIDECAR_CONTRACT.md` 與 Live A/B design 明定 ordinary workflow 不可 auto-enable。因此本 spec 要求一個由外部治理流程先批准的 amendment；implementation Task 不得建立、修改或把它標成 approved。唯一輸入位置與 schema 為：

`docs/agents/approved/memory-sidecar-default-on-amendment-v1.json`

```text
contractAmendmentStatus = approved
liveAcceptanceStatus = ready
writerEnabled = false
providerInvocationPermission = explicit
amendmentId = "sha256-derived-id"
revision = "contract-revision"
approvedBy = "governance-role"
approvedAt = "ISO-8601"
artifactFingerprint = "lowercase-sha256"
signature = "base64-ed25519-signature"
keyId = "trusted-governance-key-id"
allowedTelemetrySchemas = ["memory-hints-v1", "memory-sidecar-telemetry-v1", "memory-sidecar-policy-decision-v1"]
```

只有已存在且由 trusted external Ed25519 key verifier（驗證公鑰不由 implementation path、CLI 或 artifact 自身提供）驗證 signature/keyId、fingerprint／approver／revision／allowedTelemetrySchemas 的 immutable amendment artifact，加上同一 trust root 綁定的三次 live evidence manifest 同時證明上述條件時，eligible development 才可 default-on。Task 0 只接受 read-only `TrustedGovernanceKeyProvider.resolve_public_key(key_id)` dependency；key bundle 必須由受控 deployment injection point 提供，不能由 repo、CLI 或待驗證 artifact 指定。canonicalization 固定為 UTF-8、sorted-key、compact JSON、without signature fields；crypto verifier 使用既有受控 Ed25519 dependency。key bundle unavailable、unknown key、signature mismatch 或 canonicalization failure 一律 `blocked_policy` 且 `providerInvocationAllowed=false`。缺少、竄改、過期、current Git/policy/provider identity 不符，或 implementation Task 自行產生的 artifact 一律視為不存在；resolver 一律 `recall_off`。本 spec 不修改 contract，也不宣稱 rollout 已啟用。

三次 evidence 必須由獨立 `memory-sidecar-live-ab-evidence-v1` manifest 綁定，且 `attempts` 正好三筆；manifest 也必須含 `gitHead`、`policyFingerprint`、`provider`、`model`、`configFingerprint`、`createdAt`、`expiresAt`（最長 30 日）、`evidenceFingerprint`、`signature` 與 `keyId`。signature 必須覆蓋 canonicalized manifest payload，並由同一外部 trust root 驗證；resolver 不接受自簽、未知 keyId 或只靠欄位自我宣告的 ready 狀態。每筆含唯一 `attemptId`、control/treatment receipt fingerprints、同一 immutable policy／Git identity、`originalVerdict`、`originalReasons`、`nonLatencyGatesPassed` 與 evidence fingerprint。三筆都必須是完整真實 receipts、只因 latency rejected 或 ready，且任一缺失、重複、過期、current identity mismatch 或非 latency failure 都使 manifest 不 ready。

## 4. Policy decision

新增一個 provider-neutral runtime policy，將「是否允許本次 recall」與「目前 provider 能否成功 recall」分開：

```text
policy decision = eligible_development ? recall_on : recall_off
provider result  = ready | empty | timeout | degraded | blocked
effective result = bounded hints only when both policy=recall_on and provider=ready
```

預設規則（僅在 governance prerequisite 通過後生效）：

| Task class | Default recall | Writer | Failure behavior |
|---|---:|---:|---|
| `development` | on | off | canonical-only fallback |
| `governance` | off | off | no recall |
| `acceptance` | off | off | no recall |
| `baseline` | off | off | no recall |
| `data` | off | off | no recall |
| `security` | off | off | no recall |
| `runtime_mutation` | off | off | no recall |
| unknown／missing class with protected markers | off | off | canonical-only fallback |

An explicit per-run `recall=off` override always wins. An explicit `recall=on` override is accepted only for `development` and only when no protected marker exists. The policy resolver must never infer `recall=on` for a protected task.

## 5. Task classification and protected markers

The resolver consumes a trusted bounded task descriptor, not CLI free text, LLM output or brief prose alone. A descriptor factory derives it from workflow stage/action, approved contract scope and immutable brief fingerprint:

```json
{
  "schemaVersion": "memory-sidecar-runtime-request-v1",
  "taskClass": "development",
  "recallOverride": null,
  "protectedMarkers": [],
  "taskFingerprint": "lowercase-sha256"
}
```

Allowed `taskClass` values are exactly the seven classes in the table above. Protected markers include `approval`, `dispatch`, `acceptance`, `baseline`, `sqlite`, `revenue`, `security`, `credential`, `runtime-write`, `rollback`, `merge`, `push`, and `snapshot`. The marker set is schema-controlled and bounded; a marker makes the effective mode `recall_off` even when the caller labels the task `development`. Missing, unknown or descriptor/brief fingerprint mismatch is `blocked_policy` with `mode=recall_off`.

The resolver returns an immutable decision:

```python
resolve_memory_sidecar_mode(request: MemorySidecarRuntimeRequest) -> MemorySidecarRuntimeDecision
```

The decision exposes `mode` (`recall_on` or `recall_off`), `status` (`allowed`, `protected`, `blocked_policy`, `disabled`), `provider_invocation_allowed`, `reason`, `policy_fingerprint`, `task_fingerprint`, `writer_enabled=False`, and `shadow_mode` as an observational flag only. Protected／invalid／explicit-off decisions must have `provider_invocation_allowed=False`; `shadow_mode=True` must never invoke a provider. It contains no query, prompt, secret, raw task text, or absolute path.

## 6. Integration boundary

The policy is applied at the Context Agent recall request boundary, before `MemorySidecarService.recall`. Existing canonical collection still runs first. A provider-neutral invocation seam receives the trusted descriptor, bounded query derived from `bundle.task.objective`, and only declared sourceRefs from canonical evidence; it cannot construct a request for an off/blocked decision. When the decision is `recall_on`, the existing bounded `MemorySidecarRecallRequest` and provider-neutral adapter are used unchanged. `context_agent_service.py` continues to label returned hints as `authority=non_authoritative_memory`; the canonical bundle fingerprint remains independent of hints. Policy metadata is telemetry-only, not part of the strict Context evidence payload.

The CLI and workflow entry point expose an explicit `--recall {auto,on,off}` value. `auto` is the default and delegates to the policy resolver. `off` is always honored. `on` is allowed only for eligible development tasks after the governance prerequisite; protected tasks return a bounded `blocked_policy` decision and continue canonical-only. CLI input cannot supply or override the trusted task class.

No global Hermes configuration, NBS `scripts/hermes_post_change_check.py` behavior, writer path, or ordinary approval path is changed.

## 7. Failure and fallback behavior

- Policy schema, trusted descriptor, task fingerprint, class or override invalid: `blocked_policy`, no provider call, canonical-only context.
- Protected marker or protected task class: `recall_off`, no provider call.
- Eligible development + provider ready: inject at most three fresh hints under the existing 6000-byte／800 ms caps.
- Eligible development + provider timeout/degraded/empty: preserve explicit telemetry and use canonical-only context.
- Stale, invalid, path-violating, sensitive or conflicting hints: block hints; never downgrade canonical evidence or approval state.
- Writer remains disabled in every outcome.

The policy decision is recorded in a separate exact `memory-sidecar-policy-decision-v1` event under `.nbs_agent_runtime/telemetry/memory_sidecar_policy.jsonl`; the external amendment must explicitly allowlist this schema and existing `memory-sidecar-telemetry-v1` remains unchanged. The exact envelope keys are `schemaVersion`, `runId`, `taskFingerprint`, `policyFingerprint`, `mode`, `status`, `providerInvocationAllowed`, `reason`, `fallback`, `latencyMs`, and `writerEnabled` with no extra keys. `schemaVersion` is literal `memory-sidecar-policy-decision-v1`; `runId` matches `^[A-Za-z0-9_.-]{1,128}$`; task/policy fingerprints are lowercase 64-hex SHA-256; `mode` is `recall_on|recall_off`; `status` is `allowed|protected|blocked_policy|disabled|fallback`; `reason` is `amendment_missing|evidence_missing|identity_mismatch|protected_task|explicit_off|provider_ready|provider_empty|provider_timeout|provider_degraded|invalid_hint|stale_hint`; `providerInvocationAllowed`, `fallback` and `writerEnabled` are strict booleans; `latencyMs` is integer `0..800`. No field may contain query, prompt, response, path, secret or customer data. Malformed or symlinked telemetry is ignored and reported as blocked; retention follows existing runtime telemetry policy.

## 8. Token and latency expectations

The default-on policy optimizes future development tasks; it does not retroactively recover prior token usage. The live evidence manifest described above is a prerequisite input; if it is unavailable, this policy must not claim a token reduction. The policy treats latency as an observed diagnostic, not an automatic gate change. Ordinary tasks may spend up to the existing 800 ms recall budget, and timeout falls back without blocking.

Success is measured by lower Context input tokens while preserving canonical evidence and workflow success. A later task may revise latency policy only with new immutable evidence; this policy does not alter the 800 ms budget or the 1900 ms live A/B candidate gate.

## 9. Acceptance gates

1. An approved contract amendment and `ready` live evidence are present before `auto` can default to `recall_on`.
2. `auto` defaults to `recall_on` only for eligible development tasks from the trusted descriptor factory.
3. All protected classes and markers remain recall-off even if an on override is supplied.
4. Explicit off always disables provider invocation; shadow mode cannot bypass this.
5. Writer, approval, dispatch, SQLite, baseline, Graph, Git and runtime mutation boundaries remain unchanged.
6. Existing hint schema, caps, freshness, sourceRefs and canonical bundle fingerprint tests remain green.
7. Provider failure produces canonical-only fallback and bounded telemetry; no ordinary task is blocked.
8. A/B or fixture evidence reports token/latency deltas without claiming project-wide savings.
9. Full pytest, system acceptance and Hermes post-change check pass.

## 10. Rollback

Set `defaultRecallEnabled=false` in the exact runtime policy or revoke the contract amendment; pass `--recall off` for an individual run. No code or canonical artifact rollback is required. Removing ignored sidecar telemetry is optional and must not remove canonical runtime evidence.
