# Memory Sidecar Task 6 Brief
Objective: document the provider-neutral memory sidecar boundary and add a read-only Hermes report without starting Gateway or changing authority state.
Allowed files: docs/agents/MEMORY_SIDECAR_CONTRACT.md, docs/agents/NBS_AGENT_ARCHITECTURE.md, docs/agents/CODEX_AGENT_DISPATCH.md, scripts/hermes_post_change_check.py, tests/test_memory_sidecar_hermes_boundary.py, tests/test_hermes_post_change_check.py.
Required evidence: memory-hints-v1 and memory-sidecar-telemetry-v1 caps, non-authoritative authority, read-only Hermes semantics, existing Hermes schema and fallback checks.
Forbidden: Gateway startup, provider install, network, prune, approval, dispatch, auto-apply, SQLite, baseline, runtime authority, Git writes.
Acceptance: docs state the boundary; Hermes emits memory-sidecar-hermes-report-v1 with policy read-only, invocations 0, writes 0, bounded artifact/fallback diagnostics; malformed/stale/over-cap/path/permission evidence is invalid or blocked.
