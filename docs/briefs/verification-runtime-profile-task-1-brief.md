# Verification Runtime Profile Task 1 Brief

## Approved objective

Implement only the immutable `verification-runtime-profile-v1` profile model
and loader. The loader must validate exact schema keys, frozen baseline
identity, self-fingerprint, expected Git HEAD, expected worktree fingerprint,
profile-scoped artifact references, read-only database metadata, allocated
non-zero service ports, safe paths, and frozen dataclass behavior.

## Allowed files

- `backend/services/verification_runtime_profile.py`
- `tests/test_verification_runtime_profile.py`
- `docs/briefs/verification-runtime-profile-task-1-brief.md`
- `docs/superpowers/specs/2026-08-17-nbs-verification-runtime-profile-design.md`
- `.nbs_agent_runtime/review_inputs/verification-profile-task-1-*`

## Explicitly out of scope

Task 2 snapshot building, Task 3 path injection and empty-database guards,
Task 4 service lifecycle and process identity, Task 5 deterministic clocks,
Task 6 Hermes integration, formal SQLite, baseline, runtime cache contents,
service startup, Git writes, and network calls.

## Acceptance evidence

Focused Task 1 tests, Python compilation, and `git diff --check` must pass.
Review must attribute every dirty file and report the remaining Task 2-6 work
as residual scope rather than a Task 1 finding.
