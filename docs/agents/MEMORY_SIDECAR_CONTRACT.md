# NBS Agent Memory Sidecar Contract

狀態：pilot governance contract

## Authority and scope

Memory Sidecar is provider-neutral and **non-authoritative**. `memory-hints-v1` can only offer bounded context hints; it is never a canonical artifact, approval, dispatch, Review, runtime, SQLite, baseline, rollback, business-rule, export-schema or Git authority. A failed, malformed, stale, over-cap or permission-denied sidecar result must be ignored or blocked with an explicit fallback to canonical evidence.

The pilot only accepts bounded `memory-hints-v1` and `memory-sidecar-telemetry-v1` evidence. Hints retain the fixed caps of at most three items, 6000 bytes and 800 ms; telemetry is read as bounded diagnostic evidence only. Absolute paths, symlinks, malformed schema and over-cap files are invalid or blocked.

## Hermes boundary

NBS Hermes is not Tencent Hermes and is not a TencentDB Gateway provider. Its `memory-sidecar-hermes-report-v1` is a read-only inspection report with `policy=read-only`, `invocations=0` and `writes=0`. Hermes never starts Gateway, installs a provider, makes a network call, recalls or distils memory, prunes, applies, approves, dispatches, changes runtime state, or writes sidecar evidence.

Hermes only reports bounded artifact counts, fallback checks and diagnostics. Its result does not authorize a real Gateway installation or change the ordinary Review, full-verification and Hermes acceptance gates.

## Failure fallback

Timeout and degraded recalls fall back to canonical evidence. Stale, invalid or permission-denied evidence is blocked and must not be injected into context. No sidecar state may override a canonical artifact or turn a blocked workflow into an approved one.

## Runner capability evidence consumption

Task 5 only consumes `result=ready` from bounded `runner-capability-evidence-v1` records. Before any Task 5 evaluation, it must create and bind its own `memory-sidecar-ab-acceptance-v1` record to the same immutable inputs: Git head, project/workspace identity, task, brief, allowed-files, and commands fingerprints.

The only runner capability outcomes are `ready`, `blocked_runner_capability`, and `acceptance_rejected`. For `blocked_runner_capability` or `acceptance_rejected`, `recall_enabled=false` remains mandatory: there is no auto-enable and the evidence cannot be reused as acceptance proof. This consumption rule does not authorize a runner invocation, change the existing writer-disabled/shadow-mode defaults, or alter any canonical authority.
