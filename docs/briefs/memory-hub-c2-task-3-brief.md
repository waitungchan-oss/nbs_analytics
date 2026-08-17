# C2 Task 3 Review Brief: Policy Decision Service

## Scope

Review only the deterministic read-only policy decision engine:

- `backend/agents/memory_hub_policy_service.py`
- `tests/test_memory_hub_policy_service.py`

## Contract

Decision order is identity, catalog integrity/project binding, agent/team
resolution, kind/scope allowlists, explicit rule/fixed deny default, then
record freshness/source/fingerprint checks. Outcomes are `allow`, `deny`, or
`blocked`; deny/blocked decisions must not expose record summary or source
metadata. Decision fingerprints are re-derived and the service performs no
filesystem writes, network calls, SQLite access, Graph calls, or dispatch.

## Forbidden scope

Do not modify MemoryHubService integration, catalogs, SQLite, baseline, revenue
rules, export schema, runtime state, UI, network, dispatch behavior or Git
history.

## Verification evidence

- Focused pytest: `tests/test_memory_hub_policy_service.py` (7 passed)
- `py_compile` and `git diff --check` passed.
