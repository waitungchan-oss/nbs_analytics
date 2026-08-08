# NBS Agent Memory Sidecar：Hermes + DeepSeek v4 Flash 受控 Integration Design Spec

## Status

- Status: Draft for review
- Date: 2026-08-07
- Scope: Scheme A provider integration and real A/B acceptance
- Related pilot: `docs/superpowers/specs/2026-08-05-nbs-agent-memory-sidecar-pilot-design.md`
- Related contract: `docs/agents/MEMORY_SIDECAR_CONTRACT.md`

## 1. Goal

在不改變 canonical artifacts、Governance Graph、Review、Hermes acceptance、SQLite、baseline、Git 或 workflow control 邊界的前提下，驗證受控的 external provider integration 是否能降低 Context Agent 的重複探索成本。

本階段使用已配置的 Hermes desktop project `nbs_analytics` 與 `deepseek-v4-flash` 作為 implementation runner 的模型來源，但模型輸出只屬於執行 evidence，不得成為正式治理真相。這裡的 desktop Hermes 與 NBS `scripts/hermes_post_change_check.py` 是兩個不同角色；後者仍只做 read-only acceptance。

## 2. Non-goals

本 spec 不包含：

- 將 DeepSeek 或 TencentDB Memory Gateway 寫入 NBS canonical pipeline。
- 直接修改正式 SQLite、frozen baseline、revenue scope、business rules 或 export schema。
- 讓 memory sidecar 執行 approval、dispatch、runtime control、Graph snapshot 建立或 Review/Hermes PASS 判定。
- 自動啟用 memory writer、遠端 embedding、長期 retention 或跨專案 memory。
- 以模型文字、UI 或報表格式化修正正式口徑。

## 3. Design alternatives

### A. Deterministic local provider fixture

優點是成本與風險最低，適合先驗證 schema、caps、fallback 與 A/B harness；缺點是無法證明真實模型的 token 與 latency 效益。

### B. Hermes + DeepSeek v4 Flash controlled integration（採用）

由 desktop Hermes 提供受控 implementation session，使用 `deepseek-v4-flash` 執行 allowlisted task；NBS 透過 provider-neutral adapter 消費 bounded hints，並用固定 fixture 與真實 task evidence 執行 A/B。若模型、workspace、權限或 provider 不符合契約，必須退回 deterministic fixture 或 recall-off。這不會改變 NBS Hermes 的 read-only 契約。

### C. Direct Context Agent integration

直接把模型接入主 Context Agent 流程，改動面與治理風險最大，會把 provider、runtime 與 canonical context 過早耦合，因此不採用。

## 4. Architecture

```text
Hermes desktop (deepseek-v4-flash)
        |
        | controlled implementation evidence only
        v
Provider-neutral memory adapter ----> bounded memory-hints-v1
        |                                      |
        | fail-closed                         v
        +------------------------------> Context Agent (optional recall)

Canonical artifacts / Review / Hermes acceptance / Graph
        ^
        | remain authoritative and read-only to sidecar
```

### 4.1 Provider adapter

Adapter MUST expose provider/model identity, request fingerprint, schema version, sourceRefs, generatedAt and fallback status. It MUST enforce the existing caps: at most 3 hints, 6000 bytes total, 800 ms budget, allowed-path and symlink checks, and no sensitive content capture.

The adapter MUST be provider-neutral. `deepseek-v4-flash` is configuration and evidence metadata, not a business rule or canonical source.

### 4.2 Hermes runner boundary

The Hermes desktop runner MUST be treated as an external, controlled implementation runner:

- workspace fixed to `/Users/chanwaitung2025/Downloads/nbs_analytics` or an isolated worktree;
- allowed files and commands declared per Task;
- no credentials, tokens, or security settings are entered by automation;
- no direct approval, merge, push, SQLite write, baseline write, runtime-state write or Hermes acceptance write;
- runner output is captured as implementation evidence and reviewed by the normal Review Agent;
- if the UI cannot prove model, workspace or task boundary, the run is `blocked_runner_capability` and no code change is accepted.

### 4.3 Feature flags

Defaults remain safe:

- `recall_enabled=false`
- `writer_enabled=false`
- `shadow_mode=true`

Real A/B may enable recall only inside the harness and only for the declared cohort. Writer remains disabled throughout this phase.

## 5. A/B protocol

### 5.1 Cohorts

- **A / control**: recall disabled, same task brief, HEAD, allowed files, commands and timeout.
- **B / treatment**: recall enabled through the adapter, with the same controls and a bounded hint set.

Each run records `run_id`, `cohort`, `git_head`, `task_fingerprint`, `provider`, `model`, `request_fingerprint`, `hint_ids`, `sourceRefs`, token counts, latency, fallback reason, test result, Review result and Hermes result.

### 5.2 Acceptance gates

The treatment is eligible only when all gates below pass:

1. Input context reduction is at least 20% against A, or an evidence-backed alternative benefit is approved in the acceptance record.
2. Evidence coverage is 100% for emitted hints; every hint has valid `sourceRefs` and identity.
3. p95 sidecar recall latency is at most 800 ms; timeout or malformed response fails closed.
4. Review Agent findings show no boundary regression; Hermes post-change check remains PASS.
5. Sensitive-content capture is 0; forbidden paths and symlinks are rejected.
6. Canonical data, frozen baseline (`HKD 12,057,968`) and formal scope remain unchanged.
7. Turning the sidecar off produces the same canonical pipeline result as the control cohort.

Failure of any gate keeps production defaults at recall-off and produces a blocked or rejected acceptance record; it must not be hidden by UI formatting.

## 6. Evidence and observability

The adapter emits only `memory-sidecar-telemetry-v1` and the harness emits `memory-sidecar-ab-acceptance-v1`. Evidence is append-only, deterministic where possible, and contains no secret or full prompt payload. Drill-down must identify source artifact, hint identity, provider/model, cohort and fallback reason without allowing mutation.

NBS Hermes remains a read-only acceptance/monitoring layer. It may report provider integration evidence, but it does not approve rollout or substitute for Review Agent PASS.

## 7. Failure handling

The following states are explicit and fail closed: `blocked_runner_capability`, `provider_unavailable`, `model_unavailable`, `schema_mismatch`, `stale_hint`, `conflict`, `timeout`, `sensitive_capture`, `path_violation`, `evidence_incomplete` and `acceptance_rejected`.

No fallback may silently invent a relationship or source. When fallback is used, the report records the reason and the cohort remains attributable to recall-off behavior.

## 8. Rollout sequence

1. Validate Hermes capability and task boundary without editing files.
2. Run deterministic contract and sanitizer fixtures.
3. Run controlled A/B harness with recall-off/on; writer remains off.
4. Submit implementation diff to findings-first Review Agent.
5. Run full verification and `scripts/hermes_post_change_check.py`.
6. Enable recall only after all acceptance gates pass; otherwise retain pilot defaults.

## 9. Completion criteria

This design is complete only when the implementation plan tasks are complete, contract and A/B tests pass, evidence is complete, Review Agent PASS, full verification PASS, Hermes PASS, and no canonical/baseline/governance boundary regression is found.
