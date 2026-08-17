# C2 Task 6 Review Brief: Consolidated Acceptance

## Scope

Review the final fail-closed integration fixes and verification-runtime
acceptance repairs after Tasks 1-5:

- `backend/agents/memory_hub_policy_service.py`
- `backend/agents/memory_hub_service.py`
- `tests/test_memory_hub_service.py`
- `tests/test_memory_hub_ui_service.py`
- `scripts/system_manager.py`
- `tests/test_system_manager.py`
- `scripts/hermes_post_change_check.py`

Tasks 1-5 already have independent strict Review PASS reports and are treated
as immutable reviewed commits.

## Acceptance boundaries

C2 must remain two independent immutable catalogs plus one shared read-only
policy service. No catalog generation/mutation, membership mutation, policy
approval, dispatch, recall toggle, Graph write, SQLite write, baseline/revenue
change, export schema change, or external network/provider is allowed.

## Evidence

- C2 focused regression: 129 passed.
- Full pytest: 1967 passed.
- `py_compile` and `git diff --check` passed.
- Profile-bound `system_manager.py status/acceptance`: PASS for Streamlit,
  FastAPI and Vue on ports 18502/18601/15173 with matching PID, cwd and
  profile identity.
- Hermes profile acceptance: PASS; targeted Hermes tests 744 passed.
- Profile snapshot baseline and Monthly Baseline Governance are matched;
  frozen May 2026 baseline remains `HKD 12,057,968`.
- Primary and feature-worktree SQLite/baseline SHA-256 fingerprints are
  unchanged; no formal DB, baseline registry, or primary runtime writes.
