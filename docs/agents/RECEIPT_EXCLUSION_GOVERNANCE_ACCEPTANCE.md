# Receipt Exclusion Governance Acceptance

## Scope

This acceptance covers the Persistent Receipt Exclusion Registry implementation on branch
`codex/receipt-exclusion-registry-design`. It does not activate any receipt exclusion in the
formal production SQLite database.

## Fixed Guardrails

- Formal revenue scope remains `不含掛賬核銷與TT退款轉團款`.
- May 2026 frozen baseline remains `HKD 12,057,968`.
- The governance lifecycle uses exact receipt number, source order number and exclusion kind.
- Registry, quarantine evidence and events live only in the explicitly supplied SQLite path.

## Lifecycle Evidence

Disposable integration evidence uses `31NZY6629115617 / SK2606005393` and verifies:

1. Drift diagnosis creates a public confirmation proposal containing only the exact candidate.
2. An unconfirmed candidate does not modify the disposable DB hash.
3. Confirmation writes registry, quarantine evidence and an activation event atomically.
4. A repeated source snapshot is automatically filtered by the active exact rule.
5. Revocation is previewed through a disposable DB replay; a `-HKD 1,270` drift blocks revocation and leaves the rule active.

The test constants retain the governing checks of `HKD 9,083,241.29` for June 2026 and
`HKD 12,057,967.92` for May 2026. Production evidence must use the exact formal baseline
formatting and be captured only after an explicit production-activation authorization.

## Verification Record

- Focused lifecycle test: `tests/test_receipt_exclusion_integration.py`
- Registry and governance unit tests: `tests/test_receipt_exclusion_*.py`
- Upload, rollback, stability history and API regressions are run separately before merge.
- Review evidence is read-only; no Documentation Agent auto-apply is requested by this task.

### 2026-07-23 Isolated Acceptance

- Branch: `codex/receipt-exclusion-registry-design`.
- Implementation commits: `2d4488b`, `49b83a4`, `cc8f276`, `a81fea7`, `d7146d2`,
  `48d6163`, `c7cfdd3`, `a9facbf`, `35c123d`.
- Focused protected suite: `97 passed`.
- Full suite: `981 passed`; two failures reproduce unchanged on `main`:
  `test_backend_health.py::test_health_check_returns_runtime_status` reports a missing runtime
  cache directory, and `test_verified_backfill_integration.py::test_verified_backfill_can_preview_then_apply_only_approved_targets`
  reports the pre-existing `partially_applied` approval-state behaviour.
- System manager acceptance: `passed`.
- Hermes post-change check: `overallStatus: pass`.
- Formal DB SHA-256 before/after: `fa39b91c74eaf1eb783eee00331884990c26936ea300d4f29a6bebd206995770`.
  Only a byte-identical disposable worktree copy was used for baseline acceptance.
- Monthly blocking governance matched January through June. The preserved totals include
  January `HKD 10,711,054`, February `HKD 9,765,695`, March `HKD 14,628,841`,
  April `HKD 10,506,208`, May `HKD 12,057,968`, and June `HKD 9,083,241`.
- The formal production registry was not activated by this implementation or acceptance run.

## Residual Risks And Production Gate

- A Streamlit/FastAPI confirmation requires the still-selected input files. Changed or absent files fail closed as a stale proposal.
- The worktree DB may be an isolated snapshot. A full monthly baseline acceptance must run against a disposable copy of the current formal DB, never the formal path.
- Production activation remains a separate, explicit user authorization. It must capture formal DB SHA-256 before/after, run the overlay preflight, then run rollback/history/cache/Hermes acceptance.
