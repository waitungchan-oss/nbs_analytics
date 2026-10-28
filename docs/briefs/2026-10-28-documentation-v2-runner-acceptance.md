# Documentation v2 and Phase 1 runner acceptance

## Objective

Repair documentation v2 preview evidence so the approved task brief is bounded, hash-bound, and available to the runner; preserve canonical draft validation. Repair the Phase 1 implementation CLI so an approved command such as `codex exec --json --sandbox workspace-write -m gpt-5.6-luna` is forwarded intact to the sandbox runner.

## Scope

- Bind approved brief content, relative path, and SHA-256 into documentation v2 evidence; fail closed on missing, unsafe, changed, invalid UTF-8, or oversized briefs.
- Constrain the documentation runner to evidence-supported claims and canonical draft fingerprints.
- Make `implementation_agent.py --agent-command` preserve runner option tokens and test exact argv forwarding.
- Include focused regression tests and directly relevant governance text updates.

## Out of scope

No SQLite, revenue/business rules, baseline, export schema, release gate policy, arbitrary documentation paths, direct handoff/runbook application, or external publication.

## Acceptance

Focused tests, findings-first Strict Review, full pytest, Hermes, UI acceptance, and deterministic release aggregate must use the exact same sealed source before the official documentation v2 preview and fixed-target apply are attempted.
