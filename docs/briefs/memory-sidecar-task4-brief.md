# Memory Sidecar Task 4 Brief
Objective: integrate bounded memory hints into Context Agent as a non-authoritative read-only field.
Allowed files: backend/agents/context_agent_service.py, tests/test_memory_sidecar_context_integration.py, tests/test_context_agent_service.py.
Required evidence: context evidence schema, canonical bundle fingerprint, existing token caps, MemoryHints strict model.
Forbidden: SQLite, baseline, runtime writes, Git authority, approval, dispatch, workflow control, raw memory content outside bounded hints.
Acceptance: memory hints remain separate from canonical evidence, stale/non-ready/invalid hints fail closed, and memory_hints=None is byte-for-byte compatible.
