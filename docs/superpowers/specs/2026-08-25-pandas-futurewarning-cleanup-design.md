# Pandas FutureWarning Cleanup Design

**Status:** Approved for implementation planning

**Date:** 2026-08-25

**Scope:** Business export and GMV calculation compatibility only

## 1. Decision Summary

Adopt the approved **Approach A + Approach B**:

1. Replace the three warning-producing implicit `fillna` downcasts in `pipeline.py` with explicit dtype handling, and route the one structurally identical date path through the same bounded helper.
2. Add focused compatibility tests under `future.no_silent_downcasting=True` and `FutureWarning`-as-error.
3. Keep the accelerated path only when business values and export artifacts remain semantically equivalent to the current trusted result.

This is a compatibility hardening change. It must not alter revenue values, refund deductions, workbook schemas, SQLite, active versions, or the formal revenue scope.

## 2. Problem and Evidence

The current full test suite passes but emits 90 pandas `FutureWarning`s. They are repeated executions of three code paths:

- `pipeline.py:821`: formatted transaction date falls back through `Series.fillna` on object-like data.
- `pipeline.py:868`: a merged DataFrame receives a whole-frame `.fillna(0)`.
- `pipeline.py:890`: the second formatted transaction-date path repeats the implicit fallback.

`pipeline.py:843` contains the same date fallback expression. Current fixtures do not trigger a warning there, but leaving it unchanged would retain a data-dependent compatibility defect. It is therefore included as a bounded latent-path fix.

The focused command below currently fails because the warnings become errors:

```bash
.venv/bin/python -m pytest \
  tests/test_gmv_export_performance.py \
  tests/test_gmv_one_click_merge_integration.py \
  -q -W error::FutureWarning
```

Observed baseline: `5 failed, 2 passed`. The prior full suite baseline was `2185 passed, 90 warnings`.

## 3. Goals

- Eliminate the three pandas silent-downcasting warnings at their source.
- Make dtype intent explicit and deterministic under the future pandas behavior.
- Preserve exact business values and existing artifact contracts.
- Detect future regressions before a pandas upgrade turns warnings into runtime failures.
- Keep the implementation bounded to the warning-producing business paths.

## 4. Non-goals

- No pandas or dependency upgrade.
- No global warning suppression or warning filters that hide defects.
- No repository-wide `fillna` rewrite.
- No redesign of aggregation, serialization, cache, or export architecture.
- No changes to SQLite, upload/upsert, refund ledger, active-version pointers, migrations, revenue scope, business rules, or export schema.
- No changes to the formal scope: `不含掛賬核銷與TT退款轉團款`.
- No change to the frozen 2026-05 baseline: `HKD 12,057,968`.

## 5. Options Considered

### Option 1 — Explicit dtype handling plus future-mode tests

Selected. It removes ambiguity where it occurs and converts the future pandas behavior into an executable contract.

### Option 2 — Broad dataframe normalization helper

Deferred. A generic normalizer would touch more columns and paths than necessary, increasing the chance of changing dates, identifiers, or numeric display semantics.

### Option 3 — Pin pandas or suppress warnings

Rejected. Pinning only postpones the incompatibility; suppression removes the signal without making behavior deterministic.

## 6. Detailed Design

### 6.1 Date fallback contract

Introduce one small private helper in `pipeline.py` for the two warning-producing transaction-date paths and the structurally identical ticket-date path.

Conceptual contract:

```python
def _coalesce_date_strings(primary: pd.Series, fallback: pd.Series) -> pd.Series:
    ...
```

Rules:

- Preserve the original index and row order.
- Normalize both operands to pandas nullable `string` dtype before filling.
- Use `fallback` only when `primary` is genuinely missing (`NA`/`NaN`).
- Do not treat an empty string as missing, because the current `fillna` behavior does not.
- A valid formatted transaction date always wins over the fallback date.
- Returned values must match the legacy values row-for-row; dtype becomes explicit rather than inferred.
- Apply the helper only at the three equivalent date expressions around lines 821, 843, and 890; do not expand it to unrelated date handling.

### 6.2 Numeric fill contract

Replace the whole-frame `.fillna(0)` at the warning-producing merge path with a small private helper, `_fill_gmv_numeric_columns(frame, columns)`, that explicitly assigns only the intended numeric columns:

- the dynamic transaction-count column (`t_name`)
- `郵輪交易人數`

Rules:

- Apply `pd.to_numeric(..., errors="coerce")` per intended numeric column, then fill missing values with `0`.
- Never apply numeric fill to date, identifier, grouping, branch, product, or text columns.
- Preserve row order, column order, and the existing downstream aggregation result.
- Integer-like and float-like inputs may have an explicit numeric dtype, but workbook values must remain semantically identical.
- The helper must preserve missing values and original content in text/date/identifier columns.
- The helper is scoped to this GMV export path; it is not a repository-wide DataFrame normalizer.

### 6.3 Warning enforcement

Add a focused compatibility test module rather than immediately making every repository warning globally fatal.

The compatibility tests must cover both execution modes:

- `pd.option_context("future.no_silent_downcasting", True)`
- `warnings.simplefilter("error", FutureWarning)`

The legacy warning gate must also run with `future.no_silent_downcasting=False` so existing warnings are observed and removed rather than merely hidden by the future option.

The CI/acceptance command must also run the existing warning-producing test files with `-W error::FutureWarning`.

### 6.4 Trusted result and equivalence

The current business output is the trusted reference for this change. Adoption requires zero semantic mismatch across:

- formal revenue totals and frozen baseline checks
- GMV total-refund and paid-refund views
- `all`, `no_writeoff`, and `official` report variants
- workbook sheet names, column names/order, row counts, and normalized cell values
- cache/artifact manifest contracts already covered by the export tests

Byte-for-byte Excel equality is not required because workbook metadata can vary. Semantic workbook equivalence is authoritative.

## 7. Data Contract

### Inputs

- Existing in-memory pandas DataFrames produced by the current pipeline.
- Existing transaction-date, unified-date, transaction-count, and cruise-passenger columns.

### Outputs

- Same DataFrame columns and ordering.
- Same business values and null/empty-string meaning.
- Same export workbook filenames, sheet names, schemas, and normalized cells.
- No new persisted data and no changed external API contract.

### Invariants

- No negative or additional refund deductions are introduced.
- No excluded revenue enters the formal scope.
- Identifiers are never coerced to numeric values.
- Date fallback remains row-aligned and deterministic.

## 8. Failure and Fallback Behavior

- There is no runtime fallback to implicit downcasting.
- A compatibility warning is a test failure, not a suppressed condition.
- Any business-value or artifact-equivalence mismatch blocks adoption of the new code.
- If explicit conversion reveals an unexpected non-numeric value in a numeric-only column, existing tests must expose the mismatch; implementation must not silently broaden coercion to unrelated columns.
- Production SQLite and runtime business data are not used as writable test fixtures.

## 9. Test Matrix

| Layer | Cases | Required result |
|---|---|---|
| Date helper | primary valid, primary missing, fallback missing, empty string, mixed object/string inputs, non-default index | Legacy-equivalent values, stable index, no warning |
| Numeric fill | present/missing/zero/int/float/numeric string values in the two numeric columns | Numeric-only zero fill, no text-column mutation |
| Affected pipeline | all three equivalent transaction-date paths and merged numeric path | No `FutureWarning` with future option enabled |
| GMV exports | total refund and paid refund | Exact business totals and semantic artifact equivalence |
| Report variants | `all`, `no_writeoff`, `official` | Same sheets, columns, row counts, normalized values |
| Formal revenue | revenue scope and May 2026 baseline | `HKD 12,057,968`; exclusions unchanged |
| Full regression | entire pytest suite | No failures; the current 90 warnings removed; no new warnings |
| Governance | findings-first Review and Hermes | PASS under documented boundaries |

## 10. Rollout Strategy

1. Work in an isolated `codex/` worktree and preserve the current dirty main worktree.
2. Add failing compatibility tests first.
3. Apply the smallest implementation at the three proven warning sites plus the one structurally identical latent date path.
4. Run focused warning-as-error tests and semantic export equivalence tests.
5. Run formal-scope/baseline tests, then full pytest.
6. Perform findings-first Review; resolve findings before acceptance.
7. Run Hermes only after Review and full verification pass.
8. Commit, push, PR, merge, and synchronize local `main` only after explicit Git integration instruction.

## 11. Acceptance Criteria

- The focused warning-as-error command passes.
- The full suite passes with the current 90 pandas warnings eliminated and no replacement warnings introduced.
- All trusted-reference semantic comparisons report zero mismatches.
- Formal revenue scope and the `HKD 12,057,968` frozen baseline are unchanged.
- No forbidden persistence, schema, cache-pointer, or workflow-control changes appear in the diff.
- Review and Hermes pass independently.
