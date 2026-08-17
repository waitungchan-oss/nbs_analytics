# Verification Runtime Profile Task 3 Brief

## Approved objective

Route verification-mode health and baseline checks through explicit profile
paths. Resolve only the profile's isolated snapshot/runtime/cache paths,
reject missing or symlinked artifacts, and preserve primary-runtime defaults
when no profile is supplied. Do not mutate global database constants and do
not create an empty database as a fallback.

## Allowed files

- `backend/services/verification_runtime_paths.py`
- `backend/routers/health.py`
- `backend/services/system_health_service.py`
- `scripts/phase2j_baseline_check.py`
- `scripts/monthly_baseline_check.py`
- `tests/test_verification_runtime_paths.py`
- `tests/test_verification_runtime_cli_paths.py`
- `docs/briefs/verification-runtime-profile-task-3-brief.md`
- `.nbs_agent_runtime/review_inputs/verification-profile-task-3-*`
- Prior immutable Task 1/2 files already reviewed and attributed in their own
  reports are preserved and may appear in the aggregate dirty bundle.

Prior Task 1/2 files are preserved in the worktree and are attributed to
their own immutable review scopes; they are not part of Task 3 behavior.

## Explicitly out of scope

Task 4 service lifecycle/process identity, Task 5 deterministic clocks, Task 6
Hermes integration, formal SQLite writes, baseline registry changes, runtime
cache content copying, service startup, network calls, and Git integration.

## Acceptance evidence

Focused path tests, affected-module compilation, and `git diff --check` must
pass. Profile mode must never fall back to checkout-local DB/runtime paths.
