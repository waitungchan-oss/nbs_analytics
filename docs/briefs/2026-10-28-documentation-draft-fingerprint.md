# Documentation draft fingerprint normalization

## Objective

Make documentation v2 previews resilient when the approved Codex runner returns `draftFingerprint: null`: after validating the exact draft structure, evidence fingerprint, and selected target, the trusted runner adapter computes the canonical draft fingerprint.

## Scope

- Modify `backend/agents/documentation_codex_runner.py` only to normalize a v2 draft fingerprint deterministically while retaining strict schema, evidence, and target validation.
- Add a regression test in `tests/test_documentation_codex_runner.py` proving a null fingerprint is normalized to the canonical hash and malformed drafts remain rejected.

## Out of scope

Do not modify documentation targets, release gates, runtime data, SQLite, baseline, business rules, revenue scope, or other runner behavior.

## Acceptance

The focused regression test demonstrates the prior rejection and passes after the minimal fix. Findings-first Strict Review, full pytest, Hermes, UI acceptance, and release aggregate must bind to the same sealed source before recreating the formal documentation backfill run and fixed-target previews.
