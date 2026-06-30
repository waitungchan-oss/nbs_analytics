# Phase 2K-1 Vue Read-only Alignment Design

## Goal

Move the stable operational-analysis views into Vue without duplicating the
official revenue calculation in the browser.

## Scope

Phase 2K-1 includes:

- yearly channel summary;
- monthly revenue trend;
- complete branch ranking;
- complete specialist ranking;
- branch and specialist product composition.

Data Quality and Forecast remain Phase 2K-2 because their current Streamlit
implementation depends on AI and backtest caches with separate lifecycle rules.
Upload, export generation, GMV, and business-rule writes remain outside Vue.
Streamlit may defer AI cache rebuilds after upload; the explicit `補算 AI`
button remains in Streamlit, not Vue.

## Backend Contract

`POST /api/dashboard/analytics` accepts the existing `DashboardFilters` and
returns one read-only payload:

- `annualSummary`;
- `monthlyTrend`;
- `branchRanking`;
- `specialistRanking`;
- `productDrilldown`;
- `reconciliation`.

All sections derive from the same official `s1` and `s2` dashboard facts created
after excluding write-off and TT refund-transfer orders. Vue displays the
returned values and never reconstructs the official scope from detail rows.

`reconciliation` proves:

- annual and monthly totals reconcile to the filtered combined revenue;
- branch and specialist ranking totals reconcile to their channel totals;
- product composition reconciles to the same channel totals.

## Vue Experience

The cockpit adds:

- a monthly trend chart built with native CSS/SVG-free HTML bars;
- annual channel cards;
- full ranking tables with a compact top-10 default and an expand control;
- product composition bars for branch and specialist channels;
- a visible reconciliation status.

Existing filters remain the single control surface. Refreshing filters reloads
both the summary and analytics payload.

## Verification

- service tests validate conservation across every new view;
- API tests fix response field names and types;
- the May 2026 acceptance test remains `HKD 12,057,968`;
- Vue contract/build and browser checks confirm all new sections render;
- the complete regression suite remains green.
