# Workspace Strict Review Brief — 2026-08-26

## Objective

Strict, findings-first Review of the `nbs_analytics` workspace current state
(main worktree dirty diff + `codex/pandas-futurewarning-task1` worktree diff
for the pandas cleanup series), producing a repo review report.

## Review scope (dimensions)

- runner / runtime / evidence: review runner availability, runtime preflight,
  and evidence read-only integrity.
- cache compatibility: models cache schema vs runner CLI and requested model.
- timeout: runner timeout configuration and behavior when exceeded.
- no-write: Review/Context agents and verification commands must not write
  SQLite, baseline, revenue scope, business rules, export schema, runtime or
  Git.
- provenance: base/head diff identity, brief identity, worktree fingerprint.
- freshness: verification evidence must be current against HEAD/worktree.
- SQLite / baseline / revenue-business rules / export schema: must be
  unchanged; formal scope `不含掛賬核銷與TT退款轉團款`; 2026-05 baseline
  `HKD 12,057,968`.
- findings-first: concrete findings with file/line evidence; blockers listed
  separately.
- focused tests / full pytest / Hermes: report exact results; do not claim
  isolated-runtime or Hermes acceptance that was not executed.

## Approved brief identity

`docs/briefs/2026-08-26-workspace-strict-review-brief.md` (this file).

## Review model / severity

Model: `DeepSeek-V4-Flash` (requested). Severity: `High`.

## Boundary

Review is read-only. Do not modify source, tests, SQLite, baseline, runtime,
Git, or unrelated dirty files.
