# Phase 2K-2 Data Quality and Forecast Read-only Alignment Design

## Goal

Expose the existing Streamlit Data Quality and official Forecast views through
stable read-only backend contracts and render them in Vue without triggering
model training or changing forecast weights.

## Architecture

### Data Quality

`backend/services/data_quality_service.py` reads SQLite raw data, applies the
same official revenue scope, and returns:

- overall score and health;
- latest date, missing dates, unmatched rows, and exclusion rate;
- five dimension scores;
- bounded field-completeness and diagnostic detail rows.

The service is pure and does not use Streamlit session state.

### Forecast

`backend/services/forecast_read_service.py` reads only the newest valid
`.nbs_runtime_cache/ai_*.pkl` payload matching the current cache version. It:

- uses the existing horizon-aware weight schedule;
- returns official Daily 7-day preview and bounded 30-day detail;
- returns 7-Day Macro and Month-End Macro;
- returns the best available WAPE and model label for each view;
- reports cache path, modification time, and `ready` or `not_ready`.

An API request never trains ARIMA, Prophet, or LightGBM. Missing or invalid
cache returns an actionable `not_ready` payload while Data Quality remains
available.

If Streamlit intentionally defers the AI cache during upload, the manual
`補算 AI` action in the AI Forecast section is the explicit path that triggers
the full cache rebuild; the read-only API does not auto-recompute it.

### API and Vue

Two endpoints keep responsibilities independent:

- `GET /api/insights/data-quality`
- `GET /api/insights/forecast`

Vue adds Data Quality and Forecast navigation sections. Forecast values remain
the official cached output; Vue performs formatting only.

## Verification

- unit tests lock quality scoring and forecast cache parsing;
- API tests fix both response contracts;
- Vue contract/build and browser checks confirm rendering;
- Forecast API values are compared with the latest cache-derived values;
- the May 2026 revenue baseline remains unchanged.
