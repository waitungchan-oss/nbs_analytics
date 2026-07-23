# Fix Monthly Drift Diagnosis

## Objective

When a governed monthly blocking check drifts while the legacy 2026-05 top-level check remains matched, Drift Diagnosis must diagnose the actual monthly drift and identify the responsible source order and receipt.

## Reproduction

- `monthlyRevenue:2026-06` expected HKD 9,083,241.29, actual HKD 9,081,971.29, delta HKD -1,270.
- Source order `31NZY6629115617` gains receipt `SK2606005393` with payment method `TT 退款轉團款`.
- Existing receipt `SK2606005395` contributes HKD 1,270 and is excluded by the order-level official-scope rule.
- Current diagnosis incorrectly returns `no_drift` because it reads legacy top-level 2026-05 values.

## Scope

- Update Drift Diagnosis selection/diagnosis logic and focused tests only.
- Preserve official scope, all monthly baselines, SQLite, upload write path, rollback, cache, and export behavior.

## Acceptance

- A failing test proves the current bug before production edits.
- Diagnosis reports month `2026-06`, delta `-1270`, source order `31NZY6629115617`, and receipt `SK2606005393`.
- Existing legacy-core diagnosis behavior remains compatible.
- Targeted tests, compile, full required validation, and Hermes post-change check pass.
