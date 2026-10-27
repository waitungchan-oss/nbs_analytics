# Beta tour headcount alignment

## Approved objective
Align the Beta-only `分社經營統計_含銷售員` `旅行團交易人數` with `分社每天旅行團交易人數` by branch and date. Use the same globally deduplicated source order sequence and assign each retained record to its first retained branch/salesperson. Count non-cruise tour records in `旅行團交易人數`; cruise headcount remains separately represented by the official daily sheet's `郵輪交易人數`.

## Scope
Only adjust the Beta-added branch-salesperson sheet's tour-people calculation and tests that assert its relationship to the official daily and Beta workbook sheets. Preserve official branch classification, revenue, forecast, SQLite, baseline, receipt exclusions, and unrelated export behavior.

## Acceptance
- Salesperson-sheet tour people equals official daily-sheet tour people in the focused fixture.
- Cruise people are not included in the tour-people field.
- The full source-data regenerated Beta workbook reconciles at branch/date grain.
- Official and Beta export tests assert tour and cruise headcounts as separate fields.
