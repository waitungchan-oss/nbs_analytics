# Verification Runtime Profile Task 2 Brief

## Approved objective

Build a disposable read-only SQLite snapshot and profile builder under the
ignored verification runtime. Validate source and snapshot integrity, retain
separate source and snapshot fingerprints, copy only bounded generation
metadata, record cache inventory metadata without copying cache contents, and
bind the result to the immutable Task 1 profile contract.

## Allowed files

- `backend/services/verification_runtime_snapshot.py`
- `scripts/build_verification_runtime_profile.py`
- `tests/test_verification_runtime_snapshot.py`
- `tests/test_build_verification_runtime_profile.py`
- `docs/briefs/verification-runtime-profile-task-2-brief.md`
- `.nbs_agent_runtime/review_inputs/verification-profile-task-2-*`

## Explicitly out of scope

Task 3 application path injection, Task 4 service lifecycle and process
identity, Task 5 deterministic clocks, Task 6 Hermes integration, formal
SQLite writes, baseline changes, cache content copying, service startup,
network calls, and Git integration.

## Acceptance evidence

Focused Task 2 tests, Python compilation, and `git diff --check` must pass.
The source database bytes and metadata must remain unchanged.
