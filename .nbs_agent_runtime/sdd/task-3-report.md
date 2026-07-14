# Task 3 Implementation Report

## Scope

- Added `backend/agents/agent_runtime.py`.
- Added `tests/test_agent_runtime.py`.
- Runtime artifacts remain under `.nbs_agent_runtime/`.
- No DB, export, Hermes, frozen baseline, or `.superpowers/sdd/progress.md` changes.

## Contract Coverage

- `AgentRunner` protocol and `SubprocessAgentRunner` with executable allowlist, argv execution, JSON stdin/stdout object handling, `shell=False`, and timeout.
- `agent_request_fingerprint` includes source bundle, public evidence, instructions, and output schema.
- `AgentRuntime` enforces configured context budgets (`12000` input / `1500` output by default, loading `agent_config/token_budgets.json` when available).
- Input overflow returns `context_overflow` without invoking the runner.
- Invalid schema and output overflow fail explicitly and never become cache success.
- Cache reports use atomic replacement; telemetry is compact JSONL metadata without prompts, evidence contents, secrets, or full logs.
- Runtime output paths are restricted to `.nbs_agent_runtime/`.

## Review Follow-up

- Executable allowlist now compares only canonically resolved executable paths; basename fallback was removed.
- `AgentRuntime` rejects any resolved runtime root whose final directory name is not exactly `.nbs_agent_runtime`.
- Per-fingerprint `fcntl.flock` locks under `.nbs_agent_runtime/locks/` serialize cache fills, recheck the report after lock acquisition, and release on exceptions.
- Telemetry status is reduced to the allowlisted status enum or `unknown`; agent names and records are bounded, and JSONL rotates at 1 MiB under a telemetry lock.

## TDD And Verification

- RED: four review regression tests failed before the hardening changes.
- GREEN: focused runtime tests passed after the hardening changes.
- Compile: passed for runtime and tests.
- Focused runtime: `13 passed`.
- Focused + related: `23 passed` (`tests/test_agent_runtime.py tests/test_evidence_collector.py`).
- Full suite: `248 passed`.
- `git diff --check`: passed.
