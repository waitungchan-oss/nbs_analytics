# Stability Gate Classification Design

## Goal

Separate business-definition drift from normal data freshness movement so a successful upload does not report a core revenue-scope failure merely because new rows or a new receipt date arrived.

## Classification

- `coreValidation`: checks `combinedRevenue` and `revenueScope`. Its status controls the overall gate status.
- `freshnessUpdate`: observes `maxDate`, `analysisRows`, and `excludedRows`. A changed value is reported as `updated`, not as core drift.
- The existing five-item `checks` list remains available for audit and backward compatibility.

## User Experience

- Core matched and freshness changed: show success, with a secondary “資料已更新” notice.
- Core drift: show warning and list only core drift items as blocking issues.
- The detail expander and Excel export contain separate core and freshness sections.

## Compatibility

Existing baseline totals, official scope rules, database writes, and dashboard calculations are unchanged. Existing top-level fields remain available while new grouped fields are added.

## Verification

- Unit test: normal freshness movement produces overall `matched`.
- Unit test: revenue drift still produces overall `drift`.
- Contract tests: grouped response fields and Streamlit labels are present.
- Live data check: current dataset reports `HKD 12,057,968` as matched and `2026-06-23` as freshness updated.
