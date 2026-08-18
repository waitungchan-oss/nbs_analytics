# Context Agent Skill Memory

For context collection, use the fixed deployment query contract:

- consumer: `context-agent`
- project: `nbs_analytics`
- scope: `project`
- memory kinds: `governance`, `evidence`, `skill`
- maximum items: 3
- maximum bytes: 6000
- timeout: 800 ms

Only fresh, verified, policy-allowed records may be projected as
non-authoritative `memoryHints`. Any provider exception or identity/fingerprint
mismatch fails closed to canonical-only context.
