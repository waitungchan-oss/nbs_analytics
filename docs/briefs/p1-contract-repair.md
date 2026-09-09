# P1 contract repair

User authorized P1 contract repair, independent review and acceptance using the current model.

Checkpoint scope: complete the P1 report-integrity implementation, including canonical
nested slots, provenance/binding validation, fail-closed contaminated observations,
bounded storage, statistics, CLI wiring and the Luna Review runner compatibility fix.
The contract allowlist is the authoritative file scope.
Preserve existing unrelated changes. No production database, baseline or runtime edits.
Verification: report regression tests, then independent findings-first Review.
Subsequent checkpoints cover ingestion, storage, full 72-slot CLI fixtures and release gates.
Live baseline requires a concrete runner, frozen manifest and bounded token/cost configuration.
Checkpoint contract: docs/briefs/p1-repair-contract.json.

## Checkpoint evidence

The P1 checkpoint includes the files listed in the contract and is sealed from the
approved base `bed4317086b8e8570451ef000ef151e00d1b52b5`. Unrelated dirty files are
preserved: pipeline.py; scripts/build_verification_runtime_profile.py;
tests/test_build_verification_runtime_profile.py.

Latest focused regression: 12 passed; duplicate synthetic observation first reproduced as
synthetic_only, now invalid with all differences suppressed.
Runner compatibility repair adds only known display aliases for gpt-5.6-luna and gpt-6-astra;
unknown display names remain blocked.

- Report regression suite: 11 passed after three new tests first failed.
- Canonical nested ledger slots retain all 72 planned executions.
- Duplicate records and reused sessions invalidate the report and suppress all token differences.
- Independent Review runs through the current-model CLI with the Luna runner identity.
- Status: implementation checkpoint pending independent Review, full verification,
  Hermes and release gates. No gate PASS is claimed by this brief.
- Remaining: canonical 72-slot CLI end-to-end acceptance and live baseline authorization.
- Live baseline configuration not yet resolved: runner, manifest hash, token/cost caps and frozen price table. No live baseline calls executed.
