# Pandas FutureWarning Cleanup Brief

## Objective

消除 GMV/export 測試目前由 `pipeline.py` 觸發的 90 個 pandas `FutureWarning`，同時保持所有報表內容、欄位、排序、數值、正式收入範圍與 frozen baseline 完全一致。

## Approved approach

- 對日期 fallback 欄位採用明確 string dtype，不依賴 `fillna()` 的 silent downcasting。
- 對 merge 後的交易人數欄位只做明確 numeric coercion 與 zero fill，不對整個 DataFrame 廣泛 `fillna(0)`。
- 在 focused compatibility tests 開啟 `future.no_silent_downcasting=True`，並把相關 `FutureWarning` 視為 failure。

## Scope

- `pipeline.py` 中目前觸發 warning 的日期與 numeric aggregation 路徑。
- GMV export performance、one-click merge integration 與必要的正式報表 regression tests。
- Spec、implementation plan、Review、full pytest 與 Hermes acceptance。

## Non-goals

- 不全域重寫所有 `fillna()`。
- 不新增 pandas dependency 或升級 pandas。
- 不修改 SQLite、upload、refund ledger、active version、baseline、revenue scope、Dashboard KPI、Forecast/WAPE 或 Excel schema。
- 不以 warning filter 或固定舊 pandas 版本作為完成方案。

## Acceptance

- 原本三個 warning source 在 focused tests 下為 0 warning。
- `-W error::FutureWarning` 與 `future.no_silent_downcasting=True` compatibility tests 通過。
- GMV total/paid 及三個 scope 的 artifact semantic equivalence 為 0 mismatch。
- Full pytest 無 failure；原本 90 個 warnings 被消除，不引入新 warnings。
- 2026-05 baseline 維持 `HKD 12,057,968`。
- 正式口徑維持「不含掛賬核銷與TT退款轉團款」。
- Review PASS 與 Hermes PASS。
