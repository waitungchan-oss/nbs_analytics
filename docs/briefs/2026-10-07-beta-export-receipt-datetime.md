# Beta export receipt datetime

## Objective

Export `收款時間` consistently as real Excel datetime values in workbook detail sheets, even when input rows mix text timestamps, pandas timestamps, and blanks.

## Scope

- Normalize only the exported representation of `收款時間`.
- Preserve workbook sheets, source rows, values, aggregations, official exclusions, and Beta-only sales-point rules.
- Fail closed on invalid nonblank timestamps; keep blank timestamps blank.
- Add regression coverage for Excel datetime types/format, detail-sheet totals, and invalid input.

## Acceptance

- Focused tests and full pytest pass.
- Hermes post-change check passes independently.
- Strict Review verifies the source-bound patch; UI acceptance remains a separate gate.
- No SQLite, frozen-baseline, revenue-rule, or export-schema changes.
