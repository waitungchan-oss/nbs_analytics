# Task brief: permanent branch aliases and official daily amount dates

## Objective

Ensure daily monetary summaries reconcile to the official branch operations summary by using the financial main table's normalized receipt date, and permanently aggregate the ten previously confirmed `門市` names under their matching `分社` names in reporting.

## Approved scope

- Exact aliases only: 大埔門市→大埔分社; 太古門市→太古分社; 將軍澳門市→將軍澳分社; 屯門門市→屯門市廣場分社; 旺角銀行中心門市→旺角銀行中心分社; 沙田門市→沙田分社; 荃灣門市→荃灣綠楊坊分社; 西九龍門市→西九龍站分社; 觀塘門市→觀塘分社; 銅鑼灣門市→銅鑼灣分社.
- Apply aliases to report/calculation copies for both existing and future uploaded rows; preserve original values in SQLite and source-detail fields.
- Daily revenue sheets use official normalized receipt date (`統一日期`); do not change transaction-time detail or tour-person/ticket-category rules.
- Invalidate affected report/export caches and version the Beta export variant so stale workbooks cannot mask these semantics; formal business scope and SQLite stay unchanged.
- Revenue scope remains excluding `掛賬核銷` and `TT 退款轉團款`; keep May 2026 frozen baseline at HKD 12,057,968.
- Repair the configured Strict Review runner's narrow display-identity mapping only: accept generic `gpt-5` when the exact configured CLI selection is `gpt-5.6-terra`; keep unknown aliases and mismatched CLI selection fail-closed.

## Out of scope

- SQLite mutation, historical row rewriting, new sale-point inference, ticket product classification, tour headcount allocation, Forecast behavior, unrelated exports/UI refactors, commit/push/PR/merge.

## Verification

- Add RED/GREEN regression tests for receipt-date daily amounts, all exact alias mappings, source-frame immutability, daily amount reconciliation to official branch summary, and the configured runner's exact-model generic display alias.
- Read-only validation against current local SQLite and generated report facts; quantify any remaining differences separately without widening scope.
- Run focused tests, full pytest, findings-first Review, Hermes post-change check, and applicable UI acceptance as independent gates with fresh source-bound evidence.
