# Memory Sidecar Task 3 Brief

Read completed canonical workflow artifacts and emit only verified, bounded, non-authoritative `MemoryCandidate` values.

Scope: `backend/agents/memory_sidecar_gate.py`, `backend/agents/memory_sidecar_sanitizer.py`, this brief, and the two focused test files.
Gates: completed + Review PASS + full verification PASS + Hermes PASS + explicit `no_doc`; bind run/commit/source SHA-256, canonical content, freshness/TTL, redaction and deterministic fingerprints.
Fail closed on missing, stale, blocked, protected, sensitive, non-canonical or unverifiable input. Read-only only: no SQLite, baseline, revenue, runtime, Git, network or raw-content writes.
