# Verification Runtime Profile Task 6 Brief

## Objective

Make Hermes post-change checks explicitly consume a validated verification
profile, route health/baseline/service acceptance to that profile, and expose a
bounded profile identity without writing runtime artifacts.

## Allowed files

- `scripts/hermes_post_change_check.py`
- `NBS_HERMES_MONITORING.md`
- `tests/test_hermes_post_change_check.py`
- `tests/test_verification_runtime_profile_integration.py`
- this brief

## Required behavior

- `--verification-profile <path>` validates the profile before checks.
- Profile checks pass the profile flag to system status/acceptance and baseline
  CLIs; profile monitor history is not written.
- Invalid/missing profile returns bounded `blocked_runner_capability` evidence.
- No-profile behavior remains unchanged.
- Hermes remains read-only and does not repair, promote, prune, or write SQLite.

## Verification

Focused integration tests, py_compile, diff check, strict Review, full pytest,
system acceptance, and Hermes remain separate gates.
