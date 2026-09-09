# P1 contract repair

User authorized P1 contract repair, independent review and acceptance using the current model.

First checkpoint: correct canonical nested ledger slots and reject contaminated pairs.
Allowlist: backend/agents/agent_eval_report.py, tests/test_agent_eval_report.py.
Preserve existing unrelated changes. No production database, baseline or runtime edits.
Verification: report regression tests, then independent findings-first Review.
Subsequent checkpoints cover ingestion, storage, full 72-slot CLI fixtures and release gates.
Live baseline requires a concrete runner, frozen manifest and bounded token/cost configuration.
Checkpoint contract: docs/briefs/p1-repair-contract.json.

## Checkpoint evidence

Current checkpoint only changes the report and its regression tests, plus this brief.
Preserved earlier-turn files: pipeline.py; scripts/build_verification_runtime_profile.py;
tests/test_build_verification_runtime_profile.py; backend/agents/agent_eval_dataset.py;
backend/agents/agent_eval_manifest.py; backend/agents/agent_eval_statistics.py;
backend/agents/agent_eval_store.py; docs/agents/AGENT_EVAL_P1_RUNBOOK.md;
scripts/agent_eval_report.py; tests/fixtures/agent_eval_cases_v1.json;
tests/test_agent_eval_cli.py; tests/test_agent_eval_dataset.py;
tests/test_agent_eval_manifest.py; tests/test_agent_eval_statistics.py; tests/test_agent_eval_store.py.
Their presence is not an approval or completion claim for subsequent P1 checkpoints.
The reviewed report includes inherited code; this checkpoint only claims nested-slot handling
and suppression of comparisons for duplicate slots, calls or reused sessions.
Ingestion, quality provenance, complete statistics and storage remain pending.

Latest focused regression: 12 passed; duplicate synthetic observation first reproduced as
synthetic_only, now invalid with all differences suppressed.
Runner compatibility repair adds only known display aliases for gpt-5.6-luna and gpt-6-astra;
unknown display names remain blocked.

- Report regression suite: 11 passed after three new tests first failed.
- Canonical nested ledger slots retain all 72 planned executions.
- Duplicate records and reused sessions invalidate the report and suppress all token differences.
- Independent Review attempted through scripts/review_agent.py, current-model CLI.
- Review blocked: installed codex-cli 0.150.1 receives HTTP 400 stating gpt-6-astra requires a newer Codex version. Static preflight ready did not establish live capability.
- Status: implemented_pending_review. No Review PASS, Hermes PASS or release PASS claimed.
- Remaining: full ingestion and provenance validation, complete statistics, storage hardening, canonical 72-slot CLI end-to-end test, independent review and release gates.
- Live baseline configuration not yet resolved: runner, manifest hash, token/cost caps and frozen price table. No live baseline calls executed.
