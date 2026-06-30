"""ARIMA、Prophet、LightGBM 多軌預測引擎。"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from business_calendar import build_business_calendar_features, load_business_calendar_events
from config import (
    COL_BRANCH,
    COL_DATE,
    COL_DEPT,
    COL_DEST_CATEGORY,
    COL_MONEY,
    COL_ORDER_ID,
    COL_RECEIPT_OPERATOR,
    COL_SALESPERSON,
    COL_SOURCE_TAG,
    TARGET_DEPT_FOR_REP,
    load_business_rules,
)
from pipeline import ensure_numeric, format_date_to_daily, normalize_runtime_columns
from visuals import HAS_MATPLOTLIB

warnings.filterwarnings("ignore")

try:
    from statsmodels.tsa.arima.model import ARIMA

    HAS_ARIMA = True
except ImportError:
    HAS_ARIMA = False

try:
    from prophet import Prophet

    HAS_PROPHET = True
except ImportError:
    HAS_PROPHET = False

try:
    import lightgbm as lgb

    HAS_LIGHTGBM = True
except Exception:
    lgb = None
    HAS_LIGHTGBM = False

HAS_AI_LIBS = HAS_MATPLOTLIB and HAS_ARIMA

try:
    BUSINESS_CALENDAR_EVENTS = load_business_calendar_events()
except Exception:
    BUSINESS_CALENDAR_EVENTS = None

LGB_LAG_FEATURES = (1, 2, 7, 14, 28)
LGB_ROLLING_WINDOWS = (7, 14, 30)
MIN_SEGMENT_NONZERO_DAYS = 10
STRATEGY_TOTAL = "總額模型"
STRATEGY_SEGMENTED = "分線加總"
STRATEGY_BASELINE = "Baseline"
STRATEGY_DAILY_ADJUSTMENT = "Daily Adjustment"
STRATEGY_SPIKE_AWARE = "Spike-aware Selector"
STRATEGY_NORMAL_DAY_EXPERIMENT = "Normal-Day Experiment"
STRATEGY_TWO_LANE_SELECTOR = "Daily Two-Lane Selector"
MACRO_7D = "7-Day Macro"
MACRO_MONTH_END = "Month-End Macro"
MODEL_NAMES = ("ARIMA", "Prophet", "LightGBM")
BASELINE_MODEL_NAMES = (
    "Recent 7D Average",
    "Recent 14D Weighted Average",
    "Same Weekday 4W Average",
    "MTD Pace Daily Allocation",
    "Hybrid Baseline",
)
DAILY_ADJUSTMENT_MODEL_NAMES = (
    "LightGBM Low-Day Cap",
    "LightGBM Holiday Dampening",
    "LightGBM Directional Holiday",
    "LightGBM Risk Adjusted",
)
SPIKE_AWARE_MODEL_NAMES = (
    "Spike-aware Conservative",
    "Spike-aware Uplift",
    "Spike-aware Hybrid Selector",
)
NORMAL_DAY_MODEL_NAMES = (
    "Step 3A Current Best",
    "LightGBM Normal-Day Cap",
    "Median Weekday Baseline",
    "Recent Weighted Baseline",
    "Normal-Day Ensemble",
    "Normal-Day Bias Calibrated",
    "Normal-Day Adaptive Blend",
    "Normal-Day Downside Guardrail",
    "Normal-Day Tight Guardrail",
    "Normal-Day Bias Guardrail",
    "Normal-Day Quantile Guardrail",
)


def _build_revenue_timeseries_from_frames(frames: tuple[pd.DataFrame, ...], full_index: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    series_parts: list[pd.Series] = []
    for source_df in frames:
        if source_df.empty:
            continue
        work = ensure_numeric(normalize_runtime_columns(source_df.copy()), COL_MONEY)
        if COL_DATE not in work.columns or COL_MONEY not in work.columns:
            continue
        work["Date"] = pd.to_datetime(format_date_to_daily(work[COL_DATE]), errors="coerce")
        daily = work.dropna(subset=["Date"]).groupby("Date")[COL_MONEY].sum()
        if not daily.empty:
            series_parts.append(daily)

    if not series_parts:
        if full_index is None:
            return pd.DataFrame(columns=["Revenue"])
        return pd.DataFrame({"Revenue": 0.0}, index=full_index)

    revenue = series_parts[0].copy()
    for part in series_parts[1:]:
        revenue = revenue.add(part, fill_value=0)

    ts_data = revenue.reset_index()
    ts_data.columns = ["Date", "Revenue"]
    ts_data = ts_data.dropna().set_index("Date").sort_index().asfreq("D", fill_value=0)
    if full_index is not None:
        ts_data = ts_data.reindex(full_index, fill_value=0)
    return ts_data


def _build_revenue_timeseries(df_tour: pd.DataFrame, df_others: pd.DataFrame) -> pd.DataFrame:
    return _build_revenue_timeseries_from_frames((df_tour, df_others))


def _cruise_departments() -> set[str]:
    try:
        rules = load_business_rules()
        return {str(v).strip() for v in rules.get("CRUISE_DEPTS", []) if str(v).strip()}
    except Exception:
        return {"郵輪事業部-郵輪線業務組", "移走-郵輪事業部"}


def _split_business_line_timeseries(
    df_tour: pd.DataFrame,
    df_others: pd.DataFrame,
    full_index: pd.DatetimeIndex,
) -> dict[str, pd.DataFrame]:
    tour = normalize_runtime_columns(df_tour.copy()) if not df_tour.empty else pd.DataFrame()
    others = normalize_runtime_columns(df_others.copy()) if not df_others.empty else pd.DataFrame()
    cruise_depts = _cruise_departments()

    if not tour.empty and COL_DEPT in tour.columns:
        cruise_mask = tour[COL_DEPT].astype(str).str.strip().isin(cruise_depts)
        tour_regular = tour.loc[~cruise_mask].copy()
        tour_cruise = tour.loc[cruise_mask].copy()
    else:
        tour_regular = tour
        tour_cruise = pd.DataFrame(columns=tour.columns)

    return {
        "旅行團": _build_revenue_timeseries_from_frames((tour_regular,), full_index=full_index),
        "郵輪": _build_revenue_timeseries_from_frames((tour_cruise,), full_index=full_index),
        "票務": _build_revenue_timeseries_from_frames((others,), full_index=full_index),
    }


def _is_viable_segment(ts_data: pd.DataFrame) -> bool:
    if ts_data.empty or "Revenue" not in ts_data.columns:
        return False
    nonzero_days = int((pd.to_numeric(ts_data["Revenue"], errors="coerce").fillna(0) != 0).sum())
    return len(ts_data) >= 45 and nonzero_days >= MIN_SEGMENT_NONZERO_DAYS


def _calendar_feature_frame(dates) -> pd.DataFrame:
    features = build_business_calendar_features(dates, events=BUSINESS_CALENDAR_EVENTS)
    if features.empty:
        return pd.DataFrame(index=pd.to_datetime(pd.Series(list(dates))).dt.normalize())
    return features.set_index("Date")


def _add_lightgbm_features(ts_data: pd.DataFrame) -> pd.DataFrame:
    df_l = ts_data.copy()
    revenue = df_l["Revenue"]
    for lag in LGB_LAG_FEATURES:
        df_l[f"lag_{lag}"] = revenue.shift(lag)
    for window in LGB_ROLLING_WINDOWS:
        shifted = revenue.shift(1)
        df_l[f"rolling_mean_{window}"] = shifted.rolling(window).mean()
        df_l[f"rolling_std_{window}"] = shifted.rolling(window).std().fillna(0)

    calendar_features = _calendar_feature_frame(df_l.index)
    for col in calendar_features.columns:
        df_l[f"cal_{col}"] = calendar_features[col].reindex(df_l.index).fillna(0).astype(float)
    return df_l


def _build_lightgbm_future_row(seq: list[float], forecast_date: pd.Timestamp, columns: pd.Index) -> pd.DataFrame:
    row: dict[str, float] = {}
    for lag in LGB_LAG_FEATURES:
        row[f"lag_{lag}"] = float(seq[-lag])
    for window in LGB_ROLLING_WINDOWS:
        window_values = np.array(seq[-window:], dtype=float)
        row[f"rolling_mean_{window}"] = float(window_values.mean())
        row[f"rolling_std_{window}"] = float(window_values.std(ddof=1)) if len(window_values) > 1 else 0.0

    calendar_features = _calendar_feature_frame([forecast_date])
    if not calendar_features.empty:
        for col, value in calendar_features.iloc[0].items():
            row[f"cal_{col}"] = float(value)

    return pd.DataFrame([[row.get(col, 0.0) for col in columns]], columns=columns)


def _forecast_tracks_from_timeseries(ts_data: pd.DataFrame, f_steps: int = 30, seed: int = 42, use_prophet: bool = True):
    np.random.seed(seed)

    f_dates = pd.date_range(ts_data.index[-1] + pd.Timedelta(days=1), periods=f_steps, freq="D")
    mean_r = ts_data["Revenue"].mean()
    std_r = ts_data["Revenue"].std()
    min_r = ts_data["Revenue"].min()
    max_r = ts_data["Revenue"].max()
    wd_factors = (ts_data.groupby(ts_data.index.dayofweek)["Revenue"].mean() - mean_r).to_dict()

    a_preds = []
    if HAS_ARIMA:
        try:
            dynamic_p = min(7, len(ts_data) // 4)
            model_arima = ARIMA(list(ts_data["Revenue"].values), order=(dynamic_p, 1, 1))
            model_fit = model_arima.fit()
            a_preds = list(model_fit.forecast(steps=f_steps))
        except Exception:
            a_preds = []

    if len(a_preds) == 0:
        ema = ts_data["Revenue"].ewm(alpha=0.15).mean().iloc[-1]
        for d in f_dates:
            val = ema + wd_factors.get(d.dayofweek, 0.0) * 0.9 + np.random.normal(0, std_r * 0.05)
            a_preds.append(max(0.0, val))
            ema = ema * 0.85 + val * 0.15
    arima_fcst = pd.Series(a_preds, index=f_dates)

    p_preds = []
    if use_prophet and HAS_PROPHET:
        try:
            pdf = ts_data.reset_index()
            pdf.columns = ["ds", "y"]
            model = Prophet(yearly_seasonality=True, weekly_seasonality=True, daily_seasonality=False)
            model.fit(pdf)
            p_preds = list(model.predict(model.make_future_dataframe(periods=f_steps))["yhat"].tail(f_steps).values)
        except Exception:
            p_preds = []

    if len(p_preds) == 0:
        slope, inter = np.polyfit(np.arange(len(ts_data)), ts_data["Revenue"].values, 1)
        for i, d in enumerate(f_dates):
            p_preds.append(
                max(
                    0.0,
                    slope * (len(ts_data) + i)
                    + inter
                    + wd_factors.get(d.dayofweek, 0) * 1.05
                    + np.random.normal(0, mean_r * 0.03),
                )
            )
    prophet_fcst = pd.Series(p_preds, index=f_dates)

    l_preds = []
    if HAS_LIGHTGBM:
        try:
            df_l = _add_lightgbm_features(ts_data)
            train = df_l.dropna()
            X = train.drop(columns=["Revenue"]).reset_index(drop=True)
            y = train["Revenue"].reset_index(drop=True)
            model_lgb = lgb.LGBMRegressor(n_estimators=40, max_depth=3, min_child_samples=2, verbose=-1, random_state=42)
            model_lgb.fit(X, y)
            seq = list(ts_data["Revenue"].tail(max(LGB_LAG_FEATURES + LGB_ROLLING_WINDOWS)).values)
            for d in f_dates:
                pred_df = _build_lightgbm_future_row(seq, d, X.columns)
                pred = max(0.0, float(model_lgb.predict(pred_df)[0]))
                l_preds.append(pred)
                seq.append(pred)
        except Exception:
            l_preds = []

    if len(l_preds) == 0:
        for d in f_dates:
            l_preds.append(
                max(
                    min_r * 0.4,
                    min(max_r * 1.08, mean_r * 0.96 + wd_factors.get(d.dayofweek, 0) * 1.15 + np.random.normal(0, mean_r * 0.04)),
                )
            )
    lgb_fcst = pd.Series(l_preds, index=f_dates)
    return arima_fcst, prophet_fcst, lgb_fcst


def _safe_recent_average(revenue: pd.Series, window: int, fallback: float) -> float:
    recent = pd.to_numeric(revenue.tail(window), errors="coerce").dropna()
    if recent.empty:
        return float(fallback)
    return float(recent.mean())


def _safe_weighted_recent_average(revenue: pd.Series, window: int, fallback: float) -> float:
    recent = pd.to_numeric(revenue.tail(window), errors="coerce").dropna()
    if recent.empty:
        return float(fallback)
    weights = np.arange(1, len(recent) + 1, dtype=float)
    return float(np.average(recent.values, weights=weights))


def _same_weekday_4w_average(revenue: pd.Series, forecast_date: pd.Timestamp, fallback: float) -> float:
    values = []
    for weeks_back in (1, 2, 3, 4):
        date_value = forecast_date - pd.Timedelta(days=7 * weeks_back)
        if date_value in revenue.index:
            values.append(float(revenue.loc[date_value]))
    if not values:
        return float(fallback)
    return float(np.mean(values))


def _mtd_pace_daily_allocation(revenue: pd.Series, forecast_date: pd.Timestamp, fallback: float) -> float:
    train_end = revenue.index[-1]
    month_start = pd.Timestamp(forecast_date).replace(day=1).normalize()
    if train_end.to_period("M") == forecast_date.to_period("M"):
        mtd = pd.to_numeric(revenue.loc[month_start:train_end], errors="coerce").dropna()
        if not mtd.empty:
            return float(mtd.mean())
    previous_month = forecast_date.to_period("M") - 1
    previous_month_values = pd.to_numeric(revenue[revenue.index.to_period("M") == previous_month], errors="coerce").dropna()
    if not previous_month_values.empty:
        return float(previous_month_values.mean())
    return float(fallback)


def _forecast_baseline_tracks_from_timeseries(ts_data: pd.DataFrame, f_steps: int = 30) -> dict[str, pd.Series]:
    revenue = pd.to_numeric(ts_data["Revenue"], errors="coerce").fillna(0)
    f_dates = pd.date_range(ts_data.index[-1] + pd.Timedelta(days=1), periods=f_steps, freq="D")
    fallback = float(revenue.tail(30).mean()) if len(revenue) else 0.0
    seq = revenue.copy()
    predictions: dict[str, list[float]] = {model: [] for model in BASELINE_MODEL_NAMES}

    for forecast_date in f_dates:
        recent_7 = _safe_recent_average(seq, 7, fallback)
        recent_14_weighted = _safe_weighted_recent_average(seq, 14, fallback)
        same_weekday = _same_weekday_4w_average(seq, forecast_date, recent_14_weighted)
        mtd_pace = _mtd_pace_daily_allocation(seq, forecast_date, recent_14_weighted)

        calendar_features = _calendar_feature_frame([forecast_date])
        is_near_holiday = False
        is_near_expo = False
        if not calendar_features.empty:
            row = calendar_features.iloc[0]
            is_near_holiday = bool(int(row.get("is_public_holiday", 0) or 0) or int(row.get("is_near_public_holiday", 0) or 0))
            is_near_expo = bool(int(row.get("is_travel_expo", 0) or 0) or int(row.get("is_near_travel_expo", 0) or 0))

        day_of_month = forecast_date.day
        days_to_month_end = forecast_date.days_in_month - forecast_date.day
        if day_of_month <= 3 or days_to_month_end <= 3:
            hybrid = 0.55 * mtd_pace + 0.45 * recent_14_weighted
        elif is_near_holiday:
            hybrid = 0.60 * same_weekday + 0.40 * recent_14_weighted
        elif is_near_expo:
            hybrid = 0.50 * same_weekday + 0.30 * recent_14_weighted + 0.20 * recent_7
        elif forecast_date.dayofweek in (5, 6):
            hybrid = 0.65 * same_weekday + 0.35 * recent_14_weighted
        else:
            hybrid = 0.45 * same_weekday + 0.35 * recent_14_weighted + 0.20 * recent_7

        values = {
            "Recent 7D Average": recent_7,
            "Recent 14D Weighted Average": recent_14_weighted,
            "Same Weekday 4W Average": same_weekday,
            "MTD Pace Daily Allocation": mtd_pace,
            "Hybrid Baseline": hybrid,
        }
        for model_name, value in values.items():
            predictions[model_name].append(max(0.0, float(value)))
        seq.loc[forecast_date] = max(0.0, float(hybrid))

    return {
        model_name: pd.Series(values, index=f_dates)
        for model_name, values in predictions.items()
    }


def _forecast_daily_adjustment_tracks_from_timeseries(
    ts_data: pd.DataFrame,
    base_lightgbm: pd.Series,
    f_steps: int = 30,
) -> dict[str, pd.Series]:
    revenue = pd.to_numeric(ts_data["Revenue"], errors="coerce").fillna(0)
    f_dates = pd.date_range(ts_data.index[-1] + pd.Timedelta(days=1), periods=f_steps, freq="D")
    if revenue.empty or base_lightgbm.empty:
        return {model_name: pd.Series(dtype=float) for model_name in DAILY_ADJUSTMENT_MODEL_NAMES}

    nonzero = revenue[revenue > 0]
    profile = nonzero if not nonzero.empty else revenue
    q25 = float(profile.quantile(0.25)) if not profile.empty else 0.0
    q50 = float(profile.quantile(0.50)) if not profile.empty else 0.0
    q75 = float(profile.quantile(0.75)) if not profile.empty else 0.0
    q90 = float(profile.quantile(0.90)) if not profile.empty else q75
    q95 = float(profile.quantile(0.95)) if not profile.empty else q90
    fallback = float(profile.tail(30).mean()) if not profile.empty else 0.0

    seq = revenue.copy()
    predictions: dict[str, list[float]] = {model: [] for model in DAILY_ADJUSTMENT_MODEL_NAMES}

    for forecast_date in f_dates:
        if forecast_date not in base_lightgbm.index:
            continue
        base_pred = max(0.0, float(base_lightgbm.loc[forecast_date]))
        recent_14_weighted = _safe_weighted_recent_average(seq, 14, fallback)
        same_weekday = _same_weekday_4w_average(seq, forecast_date, recent_14_weighted)
        mtd_pace = _mtd_pace_daily_allocation(seq, forecast_date, recent_14_weighted)
        low_reference = max(0.0, float(np.median([same_weekday, recent_14_weighted, mtd_pace])))
        conservative_cap = max(q25 * 0.85, min(q75, low_reference * 1.20 if low_reference else q75))

        calendar_features = _calendar_feature_frame([forecast_date])
        is_public_holiday = False
        is_near_holiday = False
        is_near_expo = False
        if not calendar_features.empty:
            row = calendar_features.iloc[0]
            is_public_holiday = bool(int(row.get("is_public_holiday", 0) or 0))
            is_near_holiday = bool(is_public_holiday or int(row.get("is_near_public_holiday", 0) or 0))
            is_near_expo = bool(int(row.get("is_travel_expo", 0) or 0) or int(row.get("is_near_travel_expo", 0) or 0))

        prior_dates = [forecast_date - pd.Timedelta(days=days_back) for days_back in (1, 2, 3)]
        future_dates = [forecast_date + pd.Timedelta(days=days_ahead) for days_ahead in (1, 2, 3)]
        prior_calendar = _calendar_feature_frame(prior_dates)
        future_calendar = _calendar_feature_frame(future_dates)
        is_post_holiday = (
            not prior_calendar.empty
            and prior_calendar.get("is_public_holiday", pd.Series(dtype=float)).fillna(0).astype(int).eq(1).any()
        )
        is_pre_holiday = (
            not future_calendar.empty
            and future_calendar.get("is_public_holiday", pd.Series(dtype=float)).fillna(0).astype(int).eq(1).any()
        )

        day_of_month = forecast_date.day
        days_to_month_end = forecast_date.days_in_month - forecast_date.day
        is_month_edge = day_of_month <= 3 or days_to_month_end <= 3
        low_signal = low_reference <= max(q25, q50 * 0.72)
        high_base_with_low_ref = base_pred >= q75 and low_reference <= q50

        low_day_cap = base_pred
        if low_signal or high_base_with_low_ref or is_month_edge:
            low_day_cap = min(base_pred, conservative_cap)

        holiday_dampened = base_pred
        if is_public_holiday:
            holiday_dampened = 0.45 * base_pred + 0.55 * min(base_pred, max(q25 * 0.75, low_reference))
        elif is_near_holiday:
            holiday_dampened = 0.65 * base_pred + 0.35 * min(base_pred, max(q25, low_reference))
        elif is_near_expo:
            holiday_dampened = 0.78 * base_pred + 0.22 * min(base_pred, max(q50, low_reference))

        directional_holiday = holiday_dampened
        if is_post_holiday and forecast_date.dayofweek not in (5, 6):
            catchup_target = max(q90, min(q95 * 1.20 if q95 else q90, max(same_weekday, recent_14_weighted, mtd_pace) * 1.35))
            directional_holiday = max(base_pred, catchup_target)
        elif is_pre_holiday and not is_public_holiday:
            directional_holiday = 0.55 * base_pred + 0.45 * min(base_pred, max(q25, low_reference))

        risk_score = 0
        risk_score += 2 if is_public_holiday else 0
        risk_score += 1 if is_near_holiday and not is_public_holiday else 0
        risk_score += 1 if is_month_edge else 0
        risk_score += 1 if low_signal else 0
        risk_score += 1 if high_base_with_low_ref else 0
        risk_score += 1 if base_pred >= q90 and same_weekday <= q50 else 0

        if risk_score >= 3:
            risk_adjusted = min(base_pred, max(q25 * 0.80, 0.70 * low_reference + 0.30 * recent_14_weighted))
        elif risk_score == 2:
            risk_adjusted = 0.45 * min(base_pred, conservative_cap) + 0.55 * holiday_dampened
        elif risk_score == 1:
            risk_adjusted = 0.75 * base_pred + 0.25 * min(base_pred, low_reference if low_reference else base_pred)
        else:
            risk_adjusted = base_pred

        values = {
            "LightGBM Low-Day Cap": low_day_cap,
            "LightGBM Holiday Dampening": holiday_dampened,
            "LightGBM Directional Holiday": directional_holiday,
            "LightGBM Risk Adjusted": risk_adjusted,
        }
        for model_name, value in values.items():
            predictions[model_name].append(max(0.0, float(value)))
        seq.loc[forecast_date] = max(0.0, float(risk_adjusted))

    return {
        model_name: pd.Series(values, index=f_dates[: len(values)])
        for model_name, values in predictions.items()
    }


def _forecast_segmented_tracks(
    df_tour: pd.DataFrame,
    df_others: pd.DataFrame,
    f_steps: int = 30,
    seed: int = 42,
    use_prophet: bool = True,
):
    total_ts = _build_revenue_timeseries(df_tour, df_others)
    if len(total_ts) < 14:
        return total_ts, None

    line_ts = _split_business_line_timeseries(df_tour, df_others, total_ts.index)
    if not all(_is_viable_segment(ts) for ts in line_ts.values()):
        return total_ts, _forecast_tracks_from_timeseries(total_ts, f_steps=f_steps, seed=seed, use_prophet=use_prophet)

    summed_tracks: list[pd.Series] | None = None
    for idx, (_line_name, ts_data) in enumerate(line_ts.items()):
        tracks = _forecast_tracks_from_timeseries(ts_data, f_steps=f_steps, seed=seed + idx * 100, use_prophet=use_prophet)
        if summed_tracks is None:
            summed_tracks = [track.copy() for track in tracks]
        else:
            summed_tracks = [current.add(track, fill_value=0) for current, track in zip(summed_tracks, tracks)]

    if summed_tracks is None:
        return total_ts, _forecast_tracks_from_timeseries(total_ts, f_steps=f_steps, seed=seed, use_prophet=use_prophet)
    return total_ts, tuple(summed_tracks)


def _forecast_by_strategy(
    df_tour: pd.DataFrame,
    df_others: pd.DataFrame,
    strategy: str,
    f_steps: int = 30,
    seed: int = 42,
    use_prophet: bool = True,
):
    total_ts = _build_revenue_timeseries(df_tour, df_others)
    if strategy == STRATEGY_SEGMENTED:
        ts_data, segmented_tracks = _forecast_segmented_tracks(
            df_tour,
            df_others,
            f_steps=f_steps,
            seed=seed,
            use_prophet=use_prophet,
        )
        return ts_data, segmented_tracks or _forecast_tracks_from_timeseries(ts_data, f_steps=f_steps, seed=seed, use_prophet=use_prophet)
    return total_ts, _forecast_tracks_from_timeseries(total_ts, f_steps=f_steps, seed=seed, use_prophet=use_prophet)


def _strategy_bucket_for_horizon(horizon: int) -> int:
    if horizon <= 1:
        return 1
    if horizon <= 7:
        return 7
    return 30


def _stitch_strategy_tracks(
    total_tracks: tuple[pd.Series, pd.Series, pd.Series],
    segmented_tracks: tuple[pd.Series, pd.Series, pd.Series],
    strategy_by_horizon: dict[int, str] | None,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    if not strategy_by_horizon:
        return total_tracks

    stitched = [track.copy() * 0 for track in total_tracks]
    for idx, date_value in enumerate(total_tracks[0].index):
        horizon = idx + 1
        bucket = _strategy_bucket_for_horizon(horizon)
        selected_strategy = strategy_by_horizon.get(bucket, STRATEGY_TOTAL)
        source_tracks = segmented_tracks if selected_strategy == STRATEGY_SEGMENTED else total_tracks
        for model_idx, source in enumerate(source_tracks):
            stitched[model_idx].loc[date_value] = source.loc[date_value]
    return tuple(stitched)


def run_ai_prediction_tracks(
    df_tour: pd.DataFrame,
    df_others: pd.DataFrame,
    strategy_by_horizon: dict[int, str] | None = None,
    f_steps: int = 35,
):
    try:
        ts_data = _build_revenue_timeseries(df_tour, df_others)
        if len(ts_data) < 14:
            return None, "歷史數據不足 14 天"
        _, total_tracks = _forecast_by_strategy(df_tour, df_others, STRATEGY_TOTAL, f_steps=f_steps, seed=42, use_prophet=True)
        _, segmented_tracks = _forecast_by_strategy(df_tour, df_others, STRATEGY_SEGMENTED, f_steps=f_steps, seed=42, use_prophet=True)
        arima_fcst, prophet_fcst, lgb_fcst = _stitch_strategy_tracks(total_tracks, segmented_tracks, strategy_by_horizon)
        return (ts_data, arima_fcst, prophet_fcst, lgb_fcst), None
    except Exception as exc:
        return None, str(exc)


def _bounded_inverse_metric_weights(
    metric_df: pd.DataFrame,
    horizon: int,
    strategy: str | None = None,
    metric: str = "WAPE",
    min_weight: float = 0.10,
    max_weight: float = 0.65,
) -> dict[str, float]:
    models = ["ARIMA", "Prophet", "LightGBM"]
    subset = metric_df[(metric_df["預測天期"] == horizon) & (metric_df["模型"].isin(models))].copy()
    if strategy and "策略" in subset.columns:
        subset = subset[subset["策略"] == strategy]
    if subset.empty:
        return {model: 1 / len(models) for model in models}

    metric_name = metric if metric in subset.columns else "MAPE"
    metric_by_model = subset.set_index("模型")[metric_name].reindex(models)
    if metric_by_model.isna().all():
        return {model: 1 / len(models) for model in models}

    safe_metric = metric_by_model.fillna(metric_by_model.dropna().max()).clip(lower=0.01)
    scores = 1 / safe_metric
    raw_weights = (scores / scores.sum()).to_dict()
    weights: dict[str, float] = {}
    remaining = set(models)
    remaining_total = 1.0

    while remaining:
        remaining_score = sum(raw_weights[model] for model in remaining)
        if remaining_score <= 0:
            even = remaining_total / len(remaining)
            for model in remaining:
                weights[model] = even
            break

        assigned = False
        for model in list(remaining):
            tentative = raw_weights[model] / remaining_score * remaining_total
            if tentative < min_weight:
                weights[model] = min_weight
                remaining_total -= min_weight
                remaining.remove(model)
                assigned = True
                break
            elif tentative > max_weight:
                weights[model] = max_weight
                remaining_total -= max_weight
                remaining.remove(model)
                assigned = True
                break

        if not assigned:
            for model in remaining:
                weights[model] = raw_weights[model] / remaining_score * remaining_total
            break

    total = sum(weights.values())
    if total <= 0:
        return {model: 1 / len(models) for model in models}
    return {model: float(weights.get(model, 0) / total) for model in models}


def _recommended_strategy(metric_df: pd.DataFrame, horizon: int, metric: str = "WAPE") -> str:
    models = ["ARIMA", "Prophet", "LightGBM"]
    subset = metric_df[(metric_df["預測天期"] == horizon) & (metric_df["模型"].isin(models))].copy()
    if subset.empty or "策略" not in subset.columns:
        return STRATEGY_TOTAL
    metric_name = metric if metric in subset.columns else "MAPE"
    ranked = (
        subset.dropna(subset=[metric_name])
        .sort_values([metric_name, "MAPE", "策略", "模型"])
        .reset_index(drop=True)
    )
    if ranked.empty:
        return STRATEGY_TOTAL
    return str(ranked.iloc[0]["策略"])


def _summarize_backtest_detail(detail_df: pd.DataFrame) -> pd.DataFrame:
    summary_df = (
        detail_df.groupby(["策略", "模型", "預測天期"])
        .agg(
            MAE=("AbsError", "mean"),
            WAPE_分子=("AbsError", "sum"),
            WAPE_分母=("Actual", "sum"),
            MAPE=("APE", "mean"),
            MedianAPE=("APE", "median"),
            SMAPE=("SMAPE", "mean"),
            Bias=("Error", "mean"),
            樣本數=("APE", "count"),
        )
        .reset_index()
    )
    summary_df["WAPE"] = np.where(
        summary_df["WAPE_分母"] != 0,
        summary_df["WAPE_分子"] / summary_df["WAPE_分母"] * 100,
        np.nan,
    )
    return (
        summary_df.drop(columns=["WAPE_分子", "WAPE_分母"])
        .sort_values(["預測天期", "WAPE", "MAPE", "策略", "模型"])
        .reset_index(drop=True)
    )


def _robust_wape_columns() -> list[str]:
    return [
        "評估口徑",
        "策略",
        "模型",
        "預測天期",
        "MAE",
        "WAPE",
        "MAPE",
        "MedianAPE",
        "SMAPE",
        "Bias",
        "樣本數",
        "保留樣本數",
        "剔除樣本數",
        "剔除ActualTotal",
        "剔除AbsErrorTotal",
        "剔除誤差佔比",
        "ActualThreshold",
        "AbsErrorThreshold",
    ]


def _metric_summary_for_subset(source: pd.DataFrame, kept: pd.DataFrame, label: str, actual_threshold=np.nan, abs_error_threshold=np.nan) -> dict:
    kept_actual_total = float(kept["Actual"].sum()) if not kept.empty else 0.0
    kept_abs_error_total = float(kept["AbsError"].sum()) if not kept.empty else 0.0
    source_abs_error_total = float(source["AbsError"].sum()) if not source.empty else 0.0
    removed = source.loc[~source.index.isin(kept.index)]
    removed_actual_total = float(removed["Actual"].sum()) if not removed.empty else 0.0
    removed_abs_error_total = float(removed["AbsError"].sum()) if not removed.empty else 0.0
    return {
        "評估口徑": label,
        "MAE": float(kept["AbsError"].mean()) if not kept.empty else np.nan,
        "WAPE": kept_abs_error_total / kept_actual_total * 100 if kept_actual_total else np.nan,
        "MAPE": float(kept["APE"].mean()) if not kept.empty else np.nan,
        "MedianAPE": float(kept["APE"].median()) if not kept.empty else np.nan,
        "SMAPE": float(kept["SMAPE"].mean()) if not kept.empty else np.nan,
        "Bias": float(kept["Error"].mean()) if not kept.empty else np.nan,
        "樣本數": int(len(source)),
        "保留樣本數": int(len(kept)),
        "剔除樣本數": int(len(source) - len(kept)),
        "剔除ActualTotal": removed_actual_total,
        "剔除AbsErrorTotal": removed_abs_error_total,
        "剔除誤差佔比": removed_abs_error_total / source_abs_error_total * 100 if source_abs_error_total else np.nan,
        "ActualThreshold": actual_threshold,
        "AbsErrorThreshold": abs_error_threshold,
    }


def _summarize_daily_robust_wape(detail_df: pd.DataFrame) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame(columns=_robust_wape_columns())

    base = detail_df[detail_df["預測天期"] == 1].copy()
    if base.empty:
        return pd.DataFrame(columns=_robust_wape_columns())

    rows: list[dict] = []
    group_cols = ["策略", "模型", "預測天期"]
    for group_key, group in base.groupby(group_cols, dropna=False):
        strategy, model, horizon = group_key
        work = group.copy()
        work["Actual"] = pd.to_numeric(work["Actual"], errors="coerce").fillna(0)
        work["AbsError"] = pd.to_numeric(work["AbsError"], errors="coerce").fillna(0)
        if work.empty:
            continue
        actual_threshold_95 = float(work["Actual"].quantile(0.95))
        abs_error_threshold_95 = float(work["AbsError"].quantile(0.95))
        actual_threshold_90 = float(work["Actual"].quantile(0.90))
        abs_error_threshold_90 = float(work["AbsError"].quantile(0.90))

        variants = [
            ("All Days", work, np.nan, np.nan),
            ("Trim Actual Top 5%", work[work["Actual"] <= actual_threshold_95], actual_threshold_95, np.nan),
            ("Trim AbsError Top 5%", work[work["AbsError"] <= abs_error_threshold_95], np.nan, abs_error_threshold_95),
            (
                "Trim Actual or AbsError Top 5%",
                work[(work["Actual"] <= actual_threshold_95) & (work["AbsError"] <= abs_error_threshold_95)],
                actual_threshold_95,
                abs_error_threshold_95,
            ),
            (
                "Normal Days - Trim Top 10%",
                work[(work["Actual"] <= actual_threshold_90) & (work["AbsError"] <= abs_error_threshold_90)],
                actual_threshold_90,
                abs_error_threshold_90,
            ),
        ]
        for label, kept, actual_threshold, abs_error_threshold in variants:
            row = _metric_summary_for_subset(work, kept, label, actual_threshold, abs_error_threshold)
            row.update({"策略": strategy, "模型": model, "預測天期": int(horizon)})
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=_robust_wape_columns())
    result = pd.DataFrame(rows)
    return (
        result[_robust_wape_columns()]
        .sort_values(["評估口徑", "WAPE", "MAPE", "策略", "模型"])
        .reset_index(drop=True)
    )


def _daily_wape_baseline_columns() -> list[str]:
    return [
        "基準指標",
        "來源表",
        "評估口徑",
        "策略",
        "模型",
        "預測天期",
        "WAPE",
        "MAPE",
        "MedianAPE",
        "SMAPE",
        "Bias",
        "樣本數",
        "保留樣本數",
        "剔除樣本數",
        "ExtremeDaysErrorShare",
        "SpikeRiskLevel",
        "HighRevenueCaptureRate",
        "NoFutureLeak",
        "IsOfficialAccuracy",
        "備註",
    ]


def _empty_daily_wape_baseline() -> pd.DataFrame:
    return pd.DataFrame(columns=_daily_wape_baseline_columns())


def _row_value(row: pd.Series, key: str, default=np.nan):
    if row is None or key not in row:
        return default
    return row.get(key, default)


def _append_daily_wape_baseline_row(rows: list[dict], **values) -> None:
    row = {column: values.get(column, np.nan) for column in _daily_wape_baseline_columns()}
    rows.append(row)


def _build_daily_wape_baseline(
    summary_df: pd.DataFrame,
    robust_df: pd.DataFrame,
    spike_signal_summary_df: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict] = []

    h1_summary = summary_df[summary_df["預測天期"] == 1].copy() if not summary_df.empty else pd.DataFrame()
    if not h1_summary.empty:
        official = h1_summary.sort_values(["WAPE", "MAPE", "策略", "模型"]).iloc[0]
        _append_daily_wape_baseline_row(
            rows,
            基準指標="Official All Days WAPE",
            來源表="summary",
            評估口徑="All Days",
            策略=_row_value(official, "策略", ""),
            模型=_row_value(official, "模型", ""),
            預測天期=int(_row_value(official, "預測天期", 1)),
            WAPE=float(_row_value(official, "WAPE")),
            MAPE=float(_row_value(official, "MAPE")),
            MedianAPE=float(_row_value(official, "MedianAPE")),
            SMAPE=float(_row_value(official, "SMAPE")),
            Bias=float(_row_value(official, "Bias")),
            樣本數=int(_row_value(official, "樣本數", 0)),
            保留樣本數=int(_row_value(official, "樣本數", 0)),
            剔除樣本數=0,
            ExtremeDaysErrorShare=0.0,
            IsOfficialAccuracy=True,
            備註="正式 Daily 全量回測口徑；不能用 trimmed WAPE 取代此數字。",
        )

        selector = h1_summary[h1_summary["策略"] == STRATEGY_SPIKE_AWARE].copy()
        if not selector.empty:
            selector_best = selector.sort_values(["WAPE", "MAPE", "模型"]).iloc[0]
            _append_daily_wape_baseline_row(
                rows,
                基準指標="Selector Result",
                來源表="summary",
                評估口徑="All Days",
                策略=_row_value(selector_best, "策略", ""),
                模型=_row_value(selector_best, "模型", ""),
                預測天期=int(_row_value(selector_best, "預測天期", 1)),
                WAPE=float(_row_value(selector_best, "WAPE")),
                MAPE=float(_row_value(selector_best, "MAPE")),
                MedianAPE=float(_row_value(selector_best, "MedianAPE")),
                SMAPE=float(_row_value(selector_best, "SMAPE")),
                Bias=float(_row_value(selector_best, "Bias")),
                樣本數=int(_row_value(selector_best, "樣本數", 0)),
                保留樣本數=int(_row_value(selector_best, "樣本數", 0)),
                剔除樣本數=0,
                ExtremeDaysErrorShare=0.0,
                IsOfficialAccuracy=False,
                備註="Step 2D-B spike-aware selector 的全量結果；只作對照，未驗證改善前不接入正式預測。",
            )

    if not robust_df.empty:
        top5 = robust_df[robust_df["評估口徑"] == "Trim Actual or AbsError Top 5%"].copy()
        if not top5.empty:
            trimmed = top5.sort_values(["WAPE", "MAPE", "策略", "模型"]).iloc[0]
            _append_daily_wape_baseline_row(
                rows,
                基準指標="Trimmed WAPE",
                來源表="daily_robust_wape",
                評估口徑=_row_value(trimmed, "評估口徑", ""),
                策略=_row_value(trimmed, "策略", ""),
                模型=_row_value(trimmed, "模型", ""),
                預測天期=int(_row_value(trimmed, "預測天期", 1)),
                WAPE=float(_row_value(trimmed, "WAPE")),
                MAPE=float(_row_value(trimmed, "MAPE")),
                MedianAPE=float(_row_value(trimmed, "MedianAPE")),
                SMAPE=float(_row_value(trimmed, "SMAPE")),
                Bias=float(_row_value(trimmed, "Bias")),
                樣本數=int(_row_value(trimmed, "樣本數", 0)),
                保留樣本數=int(_row_value(trimmed, "保留樣本數", 0)),
                剔除樣本數=int(_row_value(trimmed, "剔除樣本數", 0)),
                ExtremeDaysErrorShare=float(_row_value(trimmed, "剔除誤差佔比")),
                IsOfficialAccuracy=False,
                備註="剔除極端 actual 或 abs error top 5% 後的穩健診斷，不是正式準確率。",
            )

        normal = robust_df[robust_df["評估口徑"] == "Normal Days - Trim Top 10%"].copy()
        if not normal.empty:
            normal_best = normal.sort_values(["WAPE", "MAPE", "策略", "模型"]).iloc[0]
            _append_daily_wape_baseline_row(
                rows,
                基準指標="Normal Days WAPE",
                來源表="daily_robust_wape",
                評估口徑=_row_value(normal_best, "評估口徑", ""),
                策略=_row_value(normal_best, "策略", ""),
                模型=_row_value(normal_best, "模型", ""),
                預測天期=int(_row_value(normal_best, "預測天期", 1)),
                WAPE=float(_row_value(normal_best, "WAPE")),
                MAPE=float(_row_value(normal_best, "MAPE")),
                MedianAPE=float(_row_value(normal_best, "MedianAPE")),
                SMAPE=float(_row_value(normal_best, "SMAPE")),
                Bias=float(_row_value(normal_best, "Bias")),
                樣本數=int(_row_value(normal_best, "樣本數", 0)),
                保留樣本數=int(_row_value(normal_best, "保留樣本數", 0)),
                剔除樣本數=int(_row_value(normal_best, "剔除樣本數", 0)),
                ExtremeDaysErrorShare=float(_row_value(normal_best, "剔除誤差佔比")),
                IsOfficialAccuracy=False,
                備註="常規日診斷基準；用來衡量 Step 3B 是否能把 normal-day WAPE 壓低。",
            )
            _append_daily_wape_baseline_row(
                rows,
                基準指標="Extreme Days Error Share",
                來源表="daily_robust_wape",
                評估口徑=_row_value(normal_best, "評估口徑", ""),
                策略=_row_value(normal_best, "策略", ""),
                模型=_row_value(normal_best, "模型", ""),
                預測天期=int(_row_value(normal_best, "預測天期", 1)),
                WAPE=np.nan,
                MAPE=np.nan,
                MedianAPE=np.nan,
                SMAPE=np.nan,
                Bias=np.nan,
                樣本數=int(_row_value(normal_best, "樣本數", 0)),
                保留樣本數=int(_row_value(normal_best, "保留樣本數", 0)),
                剔除樣本數=int(_row_value(normal_best, "剔除樣本數", 0)),
                ExtremeDaysErrorShare=float(_row_value(normal_best, "剔除誤差佔比")),
                IsOfficialAccuracy=False,
                備註="被剔除極端樣本佔總 AbsError 的比例；用來衡量尖峰日對 Daily WAPE 的拖累。",
            )

    if not spike_signal_summary_df.empty:
        signal = spike_signal_summary_df.copy()
        signal["_risk_order"] = signal["SpikeRiskLevel"].map({"High": 0, "Medium": 1, "Low": 2}).fillna(99)
        signal_row = signal.sort_values(["_risk_order", "WAPE"]).iloc[0]
        _append_daily_wape_baseline_row(
            rows,
            基準指標="Spike Signal",
            來源表="daily_spike_signal_summary",
            評估口徑="Lead signal by risk level",
            策略="Spike Signal",
            模型=str(_row_value(signal_row, "SpikeRiskLevel", "")),
            預測天期=1,
            WAPE=float(_row_value(signal_row, "WAPE")),
            MAPE=np.nan,
            MedianAPE=np.nan,
            SMAPE=np.nan,
            Bias=np.nan,
            樣本數=int(_row_value(signal_row, "樣本數", 0)),
            保留樣本數=int(_row_value(signal_row, "樣本數", 0)),
            剔除樣本數=0,
            SpikeRiskLevel=_row_value(signal_row, "SpikeRiskLevel", ""),
            HighRevenueCaptureRate=float(_row_value(signal_row, "HighRevenueCaptureRate")),
            NoFutureLeak=bool(_row_value(signal_row, "NoFutureLeak", False)),
            IsOfficialAccuracy=False,
            備註="只檢查 cutoff 前 lead signal 的分群效果；不是預測準確率。",
        )

    if not rows:
        return _empty_daily_wape_baseline()
    return pd.DataFrame(rows, columns=_daily_wape_baseline_columns())


def _normal_day_experiment_detail_columns() -> list[str]:
    return [
        "Cutoff",
        "ActualDate",
        "FeatureMaxDate",
        "NoFutureLeak",
        "NormalSampleDefinition",
        "IsNormalDay",
        "ReferenceStrategy",
        "ReferenceModel",
        "ActualThreshold",
        "ReferenceAbsErrorThreshold",
        "策略",
        "模型",
        "預測天期",
        "Actual",
        "Prediction",
        "Error",
        "AbsError",
        "APE",
        "SMAPE",
        "Rule",
    ]


def _normal_day_experiment_summary_columns() -> list[str]:
    return [
        "評估口徑",
        "策略",
        "模型",
        "預測天期",
        "MAE",
        "WAPE",
        "MAPE",
        "MedianAPE",
        "SMAPE",
        "Bias",
        "樣本數",
        "ActualThreshold",
        "ReferenceAbsErrorThreshold",
        "NoFutureLeak",
        "備註",
    ]


def _same_weekday_median(revenue: pd.Series, forecast_date: pd.Timestamp, fallback: float) -> float:
    values = []
    for weeks_back in range(1, 9):
        date_value = forecast_date - pd.Timedelta(days=7 * weeks_back)
        if date_value in revenue.index:
            values.append(float(revenue.loc[date_value]))
    if not values:
        return float(fallback)
    return float(np.median(values))


def _recent_weighted_trimmed_average(revenue: pd.Series, window: int, fallback: float) -> float:
    recent = pd.to_numeric(revenue.tail(window), errors="coerce").dropna()
    if recent.empty:
        return float(fallback)
    if len(recent) >= 8:
        high = recent.quantile(0.90)
        recent = recent[recent <= high]
    if recent.empty:
        return float(fallback)
    weights = np.linspace(1.0, 2.0, len(recent))
    return float(np.average(recent.values, weights=weights))


def _build_daily_normal_day_experiment_detail(
    detail_df: pd.DataFrame,
    robust_df: pd.DataFrame,
    ts_data: pd.DataFrame,
) -> pd.DataFrame:
    if detail_df.empty or robust_df.empty or ts_data.empty:
        return pd.DataFrame(columns=_normal_day_experiment_detail_columns())

    normal_rows = robust_df[robust_df["評估口徑"] == "Normal Days - Trim Top 10%"].copy()
    if normal_rows.empty:
        return pd.DataFrame(columns=_normal_day_experiment_detail_columns())

    reference = normal_rows.sort_values(["WAPE", "MAPE", "策略", "模型"]).iloc[0]
    reference_strategy = str(reference["策略"])
    reference_model = str(reference["模型"])
    actual_threshold = float(reference["ActualThreshold"])
    abs_error_threshold = float(reference["AbsErrorThreshold"])

    reference_detail = detail_df[
        (detail_df["預測天期"] == 1)
        & (detail_df["策略"] == reference_strategy)
        & (detail_df["模型"] == reference_model)
    ].copy()
    if reference_detail.empty:
        return pd.DataFrame(columns=_normal_day_experiment_detail_columns())

    reference_detail["Cutoff_dt"] = pd.to_datetime(reference_detail["Cutoff"], errors="coerce")
    reference_detail["ActualDate_dt"] = pd.to_datetime(reference_detail["ActualDate"], errors="coerce")
    reference_detail = reference_detail.dropna(subset=["Cutoff_dt", "ActualDate_dt"]).sort_values(["Cutoff_dt", "ActualDate_dt"])

    revenue = pd.to_numeric(ts_data["Revenue"], errors="coerce").fillna(0).sort_index()
    rows: list[dict] = []
    prior_model_errors: dict[str, list[dict]] = {model_name: [] for model_name in NORMAL_DAY_MODEL_NAMES}

    def recent_known_errors(model_name: str, cutoff_date: pd.Timestamp, max_points: int = 7) -> list[dict]:
        known = [
            item
            for item in prior_model_errors.get(model_name, [])
            if item["actual_date"] <= cutoff_date and item["is_normal_day"]
        ]
        return known[-max_points:]

    def bias_calibrated_prediction(model_name: str, raw_pred: float, cutoff_date: pd.Timestamp) -> tuple[float, str]:
        history = recent_known_errors(model_name, cutoff_date, max_points=7)
        if len(history) >= 3:
            bias = float(np.median([item["error"] for item in history]))
            shrink = 0.72 if bias > 0 else 0.35
            return max(0.0, float(raw_pred - bias * shrink)), f"recent-normal-bias-correction-n{len(history)}"
        return max(0.0, float(raw_pred * 0.96)), "early-sample-light-downshift"

    def adaptive_blend_prediction(base_values: dict[str, float], cutoff_date: pd.Timestamp) -> tuple[float, str]:
        candidate_names = ["LightGBM Normal-Day Cap", "Normal-Day Ensemble", "Recent Weighted Baseline"]
        scores: dict[str, float] = {}
        for name in candidate_names:
            history = recent_known_errors(name, cutoff_date, max_points=7)
            if len(history) >= 3:
                mae = float(np.mean([abs(item["error"]) for item in history]))
                scores[name] = 1.0 / max(mae, 1.0)
        if scores:
            total_score = sum(scores.values())
            pred = sum(base_values[name] * scores[name] / total_score for name in scores)
            return max(0.0, float(pred)), "inverse-recent-normal-mae-blend"
        pred = 0.52 * base_values["Normal-Day Ensemble"] + 0.28 * base_values["LightGBM Normal-Day Cap"] + 0.20 * base_values["Recent Weighted Baseline"]
        return max(0.0, float(pred)), "early-sample-static-blend"

    for _, row in reference_detail.iterrows():
        cutoff = pd.Timestamp(row["Cutoff_dt"]).normalize()
        actual_date = pd.Timestamp(row["ActualDate_dt"]).normalize()
        actual = float(row["Actual"])
        reference_pred = float(row["Prediction"])
        reference_abs_error = float(row["AbsError"])
        is_normal_day = bool(actual <= actual_threshold and reference_abs_error <= abs_error_threshold)

        hist = revenue.loc[:cutoff].copy()
        if hist.empty:
            continue
        nonzero_hist = hist[hist > 0]
        profile = nonzero_hist if not nonzero_hist.empty else hist
        fallback = float(profile.tail(30).mean()) if not profile.empty else 0.0
        q25 = float(profile.quantile(0.25)) if not profile.empty else fallback
        q50 = float(profile.quantile(0.50)) if not profile.empty else fallback
        q75 = float(profile.quantile(0.75)) if not profile.empty else fallback
        q85 = float(profile.quantile(0.85)) if not profile.empty else q75

        same_weekday_median = _same_weekday_median(hist, actual_date, fallback)
        recent_weighted = _recent_weighted_trimmed_average(hist, 21, fallback)
        mtd_pace = _mtd_pace_daily_allocation(hist, actual_date, recent_weighted)
        cap_reference = max(q75 * 1.08, q50, same_weekday_median * 1.25, recent_weighted * 1.15)
        cap_reference = min(cap_reference, q85 * 1.20 if q85 else cap_reference)
        lgb_normal_cap = min(reference_pred, cap_reference)
        lgb_normal_cap = max(0.0, float(lgb_normal_cap))

        median_weekday_baseline = max(0.0, float(same_weekday_median))
        recent_weighted_baseline = max(0.0, float(0.72 * recent_weighted + 0.28 * same_weekday_median))
        normal_day_ensemble = max(
            0.0,
            float(
                0.42 * lgb_normal_cap
                + 0.24 * median_weekday_baseline
                + 0.24 * recent_weighted_baseline
                + 0.10 * max(0.0, mtd_pace)
            ),
        )
        base_candidate_values = {
            "LightGBM Normal-Day Cap": lgb_normal_cap,
            "Normal-Day Ensemble": normal_day_ensemble,
            "Recent Weighted Baseline": recent_weighted_baseline,
        }
        bias_calibrated, bias_rule = bias_calibrated_prediction(
            "Normal-Day Ensemble",
            normal_day_ensemble,
            cutoff,
        )
        adaptive_blend, adaptive_rule = adaptive_blend_prediction(base_candidate_values, cutoff)
        downside_guardrail = min(
            normal_day_ensemble,
            0.55 * normal_day_ensemble + 0.45 * min(lgb_normal_cap, recent_weighted_baseline),
        )
        downside_guardrail = max(0.0, float(downside_guardrail))
        tight_guardrail = min(
            downside_guardrail,
            0.45 * normal_day_ensemble + 0.55 * min(lgb_normal_cap, recent_weighted_baseline, median_weekday_baseline),
        )
        tight_guardrail = max(0.0, float(tight_guardrail))
        bias_history = recent_known_errors("Normal-Day Downside Guardrail", cutoff, max_points=7)
        positive_bias = [item["error"] for item in bias_history if item["error"] > 0]
        bias_guardrail = downside_guardrail
        bias_guardrail_rule = "downside-guardrail-no-prior-positive-bias"
        if len(positive_bias) >= 2:
            bias_shift = float(np.median(positive_bias)) * 0.28
            bias_guardrail = max(0.0, downside_guardrail - bias_shift)
            bias_guardrail_rule = f"downside-guardrail-minus-positive-bias-n{len(positive_bias)}"
        quantile_cap = max(q25 * 0.85, min(q75 * 1.02, 0.48 * recent_weighted_baseline + 0.32 * median_weekday_baseline + 0.20 * max(0.0, mtd_pace)))
        quantile_guardrail = max(0.0, min(downside_guardrail, quantile_cap))

        candidates = {
            "Step 3A Current Best": (reference_pred, "step3a-current-best"),
            "LightGBM Normal-Day Cap": (lgb_normal_cap, "cap-current-best-by-cutoff-normal-profile"),
            "Median Weekday Baseline": (median_weekday_baseline, "median-of-previous-same-weekdays"),
            "Recent Weighted Baseline": (recent_weighted_baseline, "trimmed-recent-weighted-with-weekday-anchor"),
            "Normal-Day Ensemble": (normal_day_ensemble, "cap-plus-weekday-plus-recent-plus-mtd-blend"),
            "Normal-Day Bias Calibrated": (bias_calibrated, bias_rule),
            "Normal-Day Adaptive Blend": (adaptive_blend, adaptive_rule),
            "Normal-Day Downside Guardrail": (downside_guardrail, "ensemble-capped-by-recent-and-lgb-normal-cap"),
            "Normal-Day Tight Guardrail": (tight_guardrail, "downside-guardrail-tightened-by-weekday-anchor"),
            "Normal-Day Bias Guardrail": (bias_guardrail, bias_guardrail_rule),
            "Normal-Day Quantile Guardrail": (quantile_guardrail, "downside-guardrail-capped-by-normal-quantile-profile"),
        }
        for model_name, (prediction, rule) in candidates.items():
            metric = _metric_columns(actual, float(prediction))
            prior_model_errors.setdefault(model_name, []).append(
                {
                    "actual_date": actual_date,
                    "error": float(metric["Error"]),
                    "abs_error": float(metric["AbsError"]),
                    "is_normal_day": is_normal_day,
                }
            )
            rows.append(
                {
                    "Cutoff": cutoff.date().isoformat(),
                    "ActualDate": actual_date.date().isoformat(),
                    "FeatureMaxDate": cutoff.date().isoformat(),
                    "NoFutureLeak": True,
                    "NormalSampleDefinition": "Step 3A Normal Days - Trim Top 10% by reference actual and abs error",
                    "IsNormalDay": is_normal_day,
                    "ReferenceStrategy": reference_strategy,
                    "ReferenceModel": reference_model,
                    "ActualThreshold": actual_threshold,
                    "ReferenceAbsErrorThreshold": abs_error_threshold,
                    "策略": STRATEGY_NORMAL_DAY_EXPERIMENT,
                    "模型": model_name,
                    "預測天期": 1,
                    "Actual": actual,
                    "Prediction": float(prediction),
                    **metric,
                    "Rule": rule,
                }
            )

    if not rows:
        return pd.DataFrame(columns=_normal_day_experiment_detail_columns())
    return pd.DataFrame(rows, columns=_normal_day_experiment_detail_columns())


def _summarize_daily_normal_day_experiment(detail_df: pd.DataFrame) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame(columns=_normal_day_experiment_summary_columns())
    normal = detail_df[detail_df["IsNormalDay"] == True].copy()
    if normal.empty:
        return pd.DataFrame(columns=_normal_day_experiment_summary_columns())

    summary_df = (
        normal.groupby(["策略", "模型", "預測天期"], dropna=False)
        .agg(
            MAE=("AbsError", "mean"),
            WAPE_分子=("AbsError", "sum"),
            WAPE_分母=("Actual", "sum"),
            MAPE=("APE", "mean"),
            MedianAPE=("APE", "median"),
            SMAPE=("SMAPE", "mean"),
            Bias=("Error", "mean"),
            樣本數=("APE", "count"),
            ActualThreshold=("ActualThreshold", "first"),
            ReferenceAbsErrorThreshold=("ReferenceAbsErrorThreshold", "first"),
            NoFutureLeak=("NoFutureLeak", "all"),
        )
        .reset_index()
    )
    summary_df["WAPE"] = np.where(
        summary_df["WAPE_分母"] != 0,
        summary_df["WAPE_分子"] / summary_df["WAPE_分母"] * 100,
        np.nan,
    )
    summary_df["評估口徑"] = "Normal Days - Step 3A Trim Top 10%"
    summary_df["備註"] = np.where(
        summary_df["模型"] == "Step 3A Current Best",
        "Step 3A 常規日基準，不是新增模型。",
        "Step 3B 常規日候選，只作 backtest 診斷，未接入正式預測。",
    )
    return (
        summary_df[
            [
                "評估口徑",
                "策略",
                "模型",
                "預測天期",
                "MAE",
                "WAPE",
                "MAPE",
                "MedianAPE",
                "SMAPE",
                "Bias",
                "樣本數",
                "ActualThreshold",
                "ReferenceAbsErrorThreshold",
                "NoFutureLeak",
                "備註",
            ]
        ]
        .sort_values(["WAPE", "MAPE", "模型"])
        .reset_index(drop=True)
    )


def _daily_diagnostic_segments(detail_df: pd.DataFrame) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame()

    base = detail_df.copy()
    base["ActualDate_dt"] = pd.to_datetime(base["ActualDate"], errors="coerce")
    base = base.dropna(subset=["ActualDate_dt"]).copy()
    if base.empty:
        return pd.DataFrame()

    actual_by_date = base.groupby("ActualDate_dt")["Actual"].first()
    nonzero_actuals = actual_by_date[actual_by_date > 0]
    high_threshold = float(nonzero_actuals.quantile(0.75)) if not nonzero_actuals.empty else 0.0
    low_threshold = float(nonzero_actuals.quantile(0.25)) if not nonzero_actuals.empty else 0.0

    calendar_features = _calendar_feature_frame(base["ActualDate_dt"].drop_duplicates())
    if calendar_features.empty:
        calendar_features = pd.DataFrame(index=base["ActualDate_dt"].drop_duplicates())

    rows: list[pd.DataFrame] = []

    def add_segment(category: str, segment: pd.Series) -> None:
        part = base.copy()
        part["診斷分類"] = category
        part["分層"] = segment.reindex(base.index).astype(str).values
        rows.append(part)

    actual = pd.to_numeric(base["Actual"], errors="coerce").fillna(0)
    add_segment("All Days WAPE", pd.Series("All Days", index=base.index))
    add_segment("Non-zero Days WAPE", pd.Series(np.where(actual > 0, "Non-zero Days", "Zero Days"), index=base.index))
    add_segment(
        "Revenue Size WAPE",
        pd.Series(
            np.select(
                [
                    actual >= high_threshold,
                    (actual > 0) & (actual <= low_threshold),
                    actual == 0,
                ],
                ["High Revenue Days", "Low Revenue Days", "Zero Days"],
                default="Mid Revenue Days",
            ),
            index=base.index,
        ),
    )

    weekday = base["ActualDate_dt"].dt.dayofweek
    add_segment("Weekend / Weekday WAPE", pd.Series(np.where(weekday.isin([5, 6]), "Weekend", "Weekday"), index=base.index))

    day_of_month = base["ActualDate_dt"].dt.day
    days_to_month_end = base["ActualDate_dt"].dt.days_in_month - day_of_month
    add_segment(
        "Month Window WAPE",
        pd.Series(
            np.select(
                [
                    day_of_month <= 3,
                    day_of_month.between(14, 16),
                    days_to_month_end <= 3,
                ],
                ["Month Start", "Month Mid", "Month End"],
                default="Regular Month Days",
            ),
            index=base.index,
        ),
    )

    cal = calendar_features.reindex(base["ActualDate_dt"].values).reset_index(drop=True)
    cal.index = base.index
    add_segment(
        "Holiday / Travel Expo Near-window WAPE",
        pd.Series(
            np.select(
                [
                    cal.get("is_public_holiday", pd.Series(0, index=base.index)).fillna(0).astype(int) == 1,
                    cal.get("is_travel_expo", pd.Series(0, index=base.index)).fillna(0).astype(int) == 1,
                    cal.get("is_near_public_holiday", pd.Series(0, index=base.index)).fillna(0).astype(int) == 1,
                    cal.get("is_near_travel_expo", pd.Series(0, index=base.index)).fillna(0).astype(int) == 1,
                ],
                ["Public Holiday", "Travel Expo", "Near Public Holiday", "Near Travel Expo"],
                default="Normal Calendar Day",
            ),
            index=base.index,
        ),
    )

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _summarize_daily_diagnostics(detail_df: pd.DataFrame) -> pd.DataFrame:
    segmented = _daily_diagnostic_segments(detail_df)
    if segmented.empty:
        return pd.DataFrame(
            columns=[
                "診斷分類",
                "分層",
                "策略",
                "模型",
                "預測天期",
                "MAE",
                "WAPE",
                "MAPE",
                "MedianAPE",
                "SMAPE",
                "Bias",
                "ActualTotal",
                "AbsErrorTotal",
                "樣本數",
            ]
        )

    summary_df = (
        segmented.groupby(["診斷分類", "分層", "策略", "模型", "預測天期"])
        .agg(
            MAE=("AbsError", "mean"),
            WAPE_分子=("AbsError", "sum"),
            WAPE_分母=("Actual", "sum"),
            MAPE=("APE", "mean"),
            MedianAPE=("APE", "median"),
            SMAPE=("SMAPE", "mean"),
            Bias=("Error", "mean"),
            樣本數=("APE", "count"),
        )
        .reset_index()
    )
    summary_df["WAPE"] = np.where(
        summary_df["WAPE_分母"] != 0,
        summary_df["WAPE_分子"] / summary_df["WAPE_分母"] * 100,
        np.nan,
    )
    summary_df = summary_df.rename(columns={"WAPE_分母": "ActualTotal", "WAPE_分子": "AbsErrorTotal"})
    return (
        summary_df[
            [
                "診斷分類",
                "分層",
                "策略",
                "模型",
                "預測天期",
                "MAE",
                "WAPE",
                "MAPE",
                "MedianAPE",
                "SMAPE",
                "Bias",
                "ActualTotal",
                "AbsErrorTotal",
                "樣本數",
            ]
        ]
        .sort_values(["診斷分類", "預測天期", "WAPE", "MAPE", "策略", "模型", "分層"])
        .reset_index(drop=True)
    )


def _prepared_spike_source_frame(df_tour: pd.DataFrame, df_others: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cruise_depts = _cruise_departments()

    for source_name, source_df in (("旅行團", df_tour), ("票務", df_others)):
        if source_df.empty:
            continue
        work = ensure_numeric(normalize_runtime_columns(source_df.copy()), COL_MONEY)
        if COL_DATE not in work.columns or COL_MONEY not in work.columns:
            continue
        work["Date"] = pd.to_datetime(format_date_to_daily(work[COL_DATE]), errors="coerce")
        work = work.dropna(subset=["Date"]).copy()
        if work.empty:
            continue

        if source_name == "旅行團":
            dept = work[COL_DEPT].astype(str).str.strip() if COL_DEPT in work.columns else pd.Series("", index=work.index)
            work["BusinessLine"] = np.where(dept.isin(cruise_depts), "郵輪", "旅行團")
        else:
            work["BusinessLine"] = "票務"

        work["SourceTable"] = source_name
        frames.append(work)

    if not frames:
        return pd.DataFrame(columns=["Date", "BusinessLine", COL_MONEY])
    return pd.concat(frames, ignore_index=True, sort=False)


def _top_group_value(day_df: pd.DataFrame, group_col: str, total: float) -> tuple[str, float, float]:
    if day_df.empty or group_col not in day_df.columns or not total:
        return "—", 0.0, 0.0
    grouped = (
        day_df.assign(_group=day_df[group_col].fillna("").astype(str).str.strip().replace("", "未知"))
        .groupby("_group", dropna=False)[COL_MONEY]
        .sum()
        .sort_values(ascending=False)
    )
    if grouped.empty:
        return "—", 0.0, 0.0
    label = str(grouped.index[0])
    amount = float(grouped.iloc[0])
    return label, amount, amount / total * 100


def _spike_contributor_rows(day_df: pd.DataFrame, actual_date: pd.Timestamp, actual_total: float) -> list[dict]:
    dimensions = [
        ("業務線", "BusinessLine"),
        ("資料來源", "SourceTable"),
        ("分社", COL_BRANCH),
        ("部門", COL_DEPT),
        ("銷售員", COL_SALESPERSON),
        ("目的地大類", COL_DEST_CATEGORY),
        ("來源標籤", COL_SOURCE_TAG),
        ("收款操作員", COL_RECEIPT_OPERATOR),
    ]
    rows: list[dict] = []
    for dimension, col in dimensions:
        if col not in day_df.columns:
            continue
        grouped = (
            day_df.assign(_group=day_df[col].fillna("").astype(str).str.strip().replace("", "未知"))
            .groupby("_group", dropna=False)
            .agg(Revenue=(COL_MONEY, "sum"), Records=(COL_MONEY, "count"))
            .reset_index()
            .sort_values("Revenue", ascending=False)
            .head(8)
        )
        for rank, (_, row) in enumerate(grouped.iterrows(), start=1):
            revenue = float(row["Revenue"])
            rows.append(
                {
                    "ActualDate": actual_date.date().isoformat(),
                    "診斷維度": dimension,
                    "排名": rank,
                    "項目": str(row["_group"]),
                    "Revenue": revenue,
                    "RevenueShare": revenue / actual_total * 100 if actual_total else np.nan,
                    "Records": int(row["Records"]),
                }
            )
    return rows


def _summarize_daily_spike_diagnostics(
    detail_df: pd.DataFrame,
    df_tour: pd.DataFrame,
    df_others: pd.DataFrame,
    top_n: int = 8,
) -> dict[str, pd.DataFrame]:
    empty_summary = pd.DataFrame(
        columns=[
            "ActualDate",
            "Cutoff",
            "Actual",
            "Prediction",
            "Error",
            "AbsError",
            "APE",
            "SpikeReason",
            "TopBusinessLine",
            "TopBusinessLineShare",
            "TopBranch",
            "TopBranchShare",
            "TopDept",
            "TopDeptShare",
            "OrderCount",
            "MaxOrderAmount",
            "TopOrderShare",
        ]
    )
    empty_contributors = pd.DataFrame(columns=["ActualDate", "診斷維度", "排名", "項目", "Revenue", "RevenueShare", "Records"])
    if detail_df.empty:
        return {"summary": empty_summary, "contributors": empty_contributors}

    base = detail_df[
        (detail_df["預測天期"] == 1)
        & (detail_df["策略"] == STRATEGY_TOTAL)
        & (detail_df["模型"] == "LightGBM")
    ].copy()
    if base.empty:
        return {"summary": empty_summary, "contributors": empty_contributors}

    base["ActualDate_dt"] = pd.to_datetime(base["ActualDate"], errors="coerce")
    base = base.dropna(subset=["ActualDate_dt"]).copy()
    if base.empty:
        return {"summary": empty_summary, "contributors": empty_contributors}

    actual = pd.to_numeric(base["Actual"], errors="coerce").fillna(0)
    nonzero = actual[actual > 0]
    high_threshold = float(nonzero.quantile(0.75)) if not nonzero.empty else float(actual.quantile(0.75))
    candidate = base[(base["Actual"] >= high_threshold) | (base["AbsError"].rank(method="first", ascending=False) <= top_n)].copy()
    candidate = candidate.sort_values(["AbsError", "Actual"], ascending=False).head(top_n)
    if candidate.empty:
        return {"summary": empty_summary, "contributors": empty_contributors}

    source_df = _prepared_spike_source_frame(df_tour, df_others)
    summary_rows: list[dict] = []
    contributor_rows: list[dict] = []
    calendar_features = _calendar_feature_frame(candidate["ActualDate_dt"].drop_duplicates())

    for _, row in candidate.iterrows():
        actual_date = pd.Timestamp(row["ActualDate_dt"]).normalize()
        actual_total = float(row["Actual"])
        day_df = source_df[source_df["Date"] == actual_date].copy() if not source_df.empty else pd.DataFrame()
        line_label, line_amount, line_share = _top_group_value(day_df, "BusinessLine", actual_total)
        branch_label, branch_amount, branch_share = _top_group_value(day_df, COL_BRANCH, actual_total)
        dept_label, dept_amount, dept_share = _top_group_value(day_df, COL_DEPT, actual_total)

        order_count = int(day_df[COL_ORDER_ID].nunique()) if COL_ORDER_ID in day_df.columns and not day_df.empty else int(len(day_df))
        max_order_amount = 0.0
        top_order_share = 0.0
        if COL_ORDER_ID in day_df.columns and not day_df.empty:
            order_amounts = day_df.groupby(COL_ORDER_ID, dropna=False)[COL_MONEY].sum().sort_values(ascending=False)
            if not order_amounts.empty:
                max_order_amount = float(order_amounts.iloc[0])
                top_order_share = max_order_amount / actual_total * 100 if actual_total else np.nan
        elif not day_df.empty:
            max_order_amount = float(pd.to_numeric(day_df[COL_MONEY], errors="coerce").fillna(0).max())
            top_order_share = max_order_amount / actual_total * 100 if actual_total else np.nan

        cal = calendar_features.reindex([actual_date])
        cal_row = cal.iloc[0] if not cal.empty else pd.Series(dtype=float)
        reasons = []
        if float(row["Error"]) < 0:
            reasons.append("模型低估")
        else:
            reasons.append("模型高估")
        if actual_total >= high_threshold:
            reasons.append("高收入日")
        if int(cal_row.get("is_public_holiday", 0) or 0):
            reasons.append("公眾假期")
        elif int(cal_row.get("is_near_public_holiday", 0) or 0):
            reasons.append("假期近窗")
        if int(cal_row.get("is_travel_expo", 0) or 0):
            reasons.append("旅遊展")
        elif int(cal_row.get("is_near_travel_expo", 0) or 0):
            reasons.append("旅遊展近窗")
        if line_share >= 70:
            reasons.append(f"{line_label}集中")
        if branch_share >= 35:
            reasons.append(f"{branch_label}集中")
        if top_order_share >= 20:
            reasons.append("大單集中")

        summary_rows.append(
            {
                "ActualDate": actual_date.date().isoformat(),
                "Cutoff": row["Cutoff"],
                "Actual": actual_total,
                "Prediction": float(row["Prediction"]),
                "Error": float(row["Error"]),
                "AbsError": float(row["AbsError"]),
                "APE": float(row["APE"]) if pd.notna(row["APE"]) else np.nan,
                "SpikeReason": " / ".join(reasons),
                "TopBusinessLine": line_label,
                "TopBusinessLineAmount": line_amount,
                "TopBusinessLineShare": line_share,
                "TopBranch": branch_label,
                "TopBranchAmount": branch_amount,
                "TopBranchShare": branch_share,
                "TopDept": dept_label,
                "TopDeptAmount": dept_amount,
                "TopDeptShare": dept_share,
                "OrderCount": order_count,
                "MaxOrderAmount": max_order_amount,
                "TopOrderShare": top_order_share,
            }
        )
        contributor_rows.extend(_spike_contributor_rows(day_df, actual_date, actual_total))

    return {
        "summary": pd.DataFrame(summary_rows),
        "contributors": pd.DataFrame(contributor_rows),
    }


def _longhaul_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(False, index=frame.index)
    parts = []
    for col in (COL_DEST_CATEGORY, COL_DEPT):
        if col in frame.columns:
            parts.append(frame[col].fillna("").astype(str))
    if not parts:
        return pd.Series(False, index=frame.index)
    text = parts[0]
    for part in parts[1:]:
        text = text + " " + part
    return text.str.contains("長線|長綫|長線組|長綫組|中國長", regex=True, na=False)


def _sum_money(frame: pd.DataFrame) -> float:
    if frame.empty or COL_MONEY not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[COL_MONEY], errors="coerce").fillna(0).sum())


def _window_source_frame(source_df: pd.DataFrame, cutoff: pd.Timestamp, days: int) -> pd.DataFrame:
    if source_df.empty or "Date" not in source_df.columns:
        return pd.DataFrame(columns=source_df.columns)
    start_date = cutoff - pd.Timedelta(days=days - 1)
    return source_df[(source_df["Date"] >= start_date) & (source_df["Date"] <= cutoff)].copy()


def _large_order_stats(frame: pd.DataFrame, history_df: pd.DataFrame) -> tuple[int, float, float]:
    if frame.empty or COL_ORDER_ID not in frame.columns:
        return 0, 0.0, 0.0
    history_orders = (
        history_df.groupby(COL_ORDER_ID, dropna=False)[COL_MONEY].sum()
        if not history_df.empty and COL_ORDER_ID in history_df.columns
        else pd.Series(dtype=float)
    )
    threshold = float(history_orders.quantile(0.95)) if not history_orders.empty else 0.0
    threshold = max(100000.0, threshold)
    order_amounts = frame.groupby(COL_ORDER_ID, dropna=False)[COL_MONEY].sum().sort_values(ascending=False)
    if order_amounts.empty:
        return 0, 0.0, threshold
    large_orders = order_amounts[order_amounts >= threshold]
    return int(len(large_orders)), float(order_amounts.iloc[0]), threshold


def _top_salesperson_share(frame: pd.DataFrame, total: float) -> float:
    if frame.empty or COL_SALESPERSON not in frame.columns or not total:
        return 0.0
    grouped = frame.groupby(COL_SALESPERSON, dropna=False)[COL_MONEY].sum().sort_values(ascending=False)
    if grouped.empty:
        return 0.0
    return float(grouped.iloc[0]) / total * 100


def _specialist_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(False, index=frame.index)
    mask = pd.Series(False, index=frame.index)
    for col in (COL_BRANCH, COL_DEPT, COL_SOURCE_TAG):
        if col in frame.columns:
            text = frame[col].fillna("").astype(str).str.strip()
            mask |= text.eq(TARGET_DEPT_FOR_REP) | text.str.contains("專職", regex=False, na=False)
    return mask


def _order_amounts(frame: pd.DataFrame) -> pd.Series:
    if frame.empty or COL_MONEY not in frame.columns:
        return pd.Series(dtype=float)
    if COL_ORDER_ID in frame.columns:
        return frame.groupby(COL_ORDER_ID, dropna=False)[COL_MONEY].sum().sort_values(ascending=False)
    return pd.to_numeric(frame[COL_MONEY], errors="coerce").fillna(0).sort_values(ascending=False)


def _large_order_threshold(history_df: pd.DataFrame) -> float:
    history_orders = _order_amounts(history_df)
    threshold = float(history_orders.quantile(0.95)) if not history_orders.empty else 0.0
    return max(100000.0, threshold)


def _event_window_stats(frame: pd.DataFrame, history_df: pd.DataFrame, threshold: float) -> dict[str, float]:
    if frame.empty:
        return {
            "OrderCount": 0,
            "LargeOrderCount": 0,
            "MaxOrderAmount": 0.0,
            "LargeOrderTotal": 0.0,
            "SpecialistOrderCount": 0,
            "SpecialistLargeOrderCount": 0,
            "SpecialistLargeOrderTotal": 0.0,
            "LonghaulOrderCount": 0,
            "LonghaulLargeOrderCount": 0,
            "LonghaulLargeOrderTotal": 0.0,
            "TopSalespersonOrderShare": 0.0,
            "UniqueSalespersons": 0,
        }

    order_amounts = _order_amounts(frame)
    large_orders = order_amounts[order_amounts >= threshold]
    specialist = frame[_specialist_mask(frame)]
    longhaul = frame[_longhaul_mask(frame)]
    specialist_orders = _order_amounts(specialist)
    longhaul_orders = _order_amounts(longhaul)
    specialist_large = specialist_orders[specialist_orders >= threshold]
    longhaul_large = longhaul_orders[longhaul_orders >= threshold]

    order_count = int(len(order_amounts))
    top_sales_share = 0.0
    unique_salespersons = 0
    if COL_SALESPERSON in frame.columns and order_count:
        sales_orders = (
            frame.assign(_sales=frame[COL_SALESPERSON].fillna("").astype(str).str.strip().replace("", "未知"))
            .drop_duplicates(subset=[COL_ORDER_ID, "_sales"] if COL_ORDER_ID in frame.columns else None)
            .groupby("_sales", dropna=False)
            .size()
            .sort_values(ascending=False)
        )
        unique_salespersons = int(len(sales_orders))
        top_sales_share = float(sales_orders.iloc[0] / order_count * 100) if not sales_orders.empty else 0.0

    return {
        "OrderCount": order_count,
        "LargeOrderCount": int(len(large_orders)),
        "MaxOrderAmount": float(order_amounts.iloc[0]) if not order_amounts.empty else 0.0,
        "LargeOrderTotal": float(large_orders.sum()) if not large_orders.empty else 0.0,
        "SpecialistOrderCount": int(len(specialist_orders)),
        "SpecialistLargeOrderCount": int(len(specialist_large)),
        "SpecialistLargeOrderTotal": float(specialist_large.sum()) if not specialist_large.empty else 0.0,
        "LonghaulOrderCount": int(len(longhaul_orders)),
        "LonghaulLargeOrderCount": int(len(longhaul_large)),
        "LonghaulLargeOrderTotal": float(longhaul_large.sum()) if not longhaul_large.empty else 0.0,
        "TopSalespersonOrderShare": top_sales_share,
        "UniqueSalespersons": unique_salespersons,
    }


def _daily_window_stats(frame: pd.DataFrame, total: float, total_14: float) -> dict[str, float]:
    if frame.empty or "Date" not in frame.columns:
        return {
            "RecentMaxDailyRevenue7D": 0.0,
            "RecentMaxDailyRevenueShare7D": 0.0,
            "RecentRevenueVolatility7D": 0.0,
            "RecentRevenueVs14DAvg": np.nan,
        }
    daily = frame.groupby("Date", dropna=False)[COL_MONEY].sum().sort_index()
    if daily.empty:
        return {
            "RecentMaxDailyRevenue7D": 0.0,
            "RecentMaxDailyRevenueShare7D": 0.0,
            "RecentRevenueVolatility7D": 0.0,
            "RecentRevenueVs14DAvg": np.nan,
        }
    max_daily = float(daily.max())
    mean_daily = float(daily.mean())
    volatility = float(daily.std(ddof=0) / mean_daily) if mean_daily else 0.0
    expected_7_from_14 = total_14 / 2 if total_14 else np.nan
    return {
        "RecentMaxDailyRevenue7D": max_daily,
        "RecentMaxDailyRevenueShare7D": max_daily / total * 100 if total else 0.0,
        "RecentRevenueVolatility7D": volatility,
        "RecentRevenueVs14DAvg": total / expected_7_from_14 if expected_7_from_14 else np.nan,
    }


def _refined_spike_signal_class(
    risk_level: str,
    risk_score: int,
    specialist_share_7: float,
    specialist_momentum: float,
    longhaul_share_7: float,
    longhaul_momentum: float,
    large_order_count_7: int,
    large_order_amount_7: float,
    large_order_threshold: float,
    top_sales_share_7: float,
    is_post_holiday: bool,
    is_month_end: bool,
    recent_max_daily_share_7: float,
    recent_volatility_7: float,
    recent_vs_14_avg: float,
) -> tuple[str, str, str]:
    strong_specialist = specialist_share_7 >= 45 or (pd.notna(specialist_momentum) and specialist_momentum >= 1.45)
    strong_longhaul = longhaul_share_7 >= 50 or (pd.notna(longhaul_momentum) and longhaul_momentum >= 1.45)
    strong_large_order = large_order_count_7 >= 1 and large_order_amount_7 >= max(large_order_threshold * 1.35, 220000)
    very_large_order = large_order_count_7 >= 1 and large_order_amount_7 >= max(large_order_threshold * 1.85, 650000)
    concentrated_recent_spike = recent_max_daily_share_7 >= 38 or top_sales_share_7 >= 55
    cooling_pace = pd.notna(recent_vs_14_avg) and recent_vs_14_avg <= 0.82
    overheating_pace = pd.notna(recent_vs_14_avg) and recent_vs_14_avg >= 1.20
    volatile = recent_volatility_7 >= 0.85 or (large_order_count_7 >= 1 and top_sales_share_7 >= 45)

    if risk_level == "Low":
        return "Normal", "Hold", "低風險，沒有足夠 lead signal"

    if (
        risk_level == "High"
        and risk_score >= 6
        and strong_large_order
        and longhaul_share_7 >= 65
        and specialist_share_7 >= 45
        and not concentrated_recent_spike
        and (is_post_holiday or is_month_end or overheating_pace)
    ):
        return "Pre-spike", "Selective uplift", "專職、長線、大單與窗口信號同向，可能低估尖峰"

    if (
        strong_longhaul
        and specialist_share_7 < 28
        and large_order_amount_7 >= max(large_order_threshold * 1.05, 180000)
        and not is_post_holiday
        and not is_month_end
    ):
        return "Post-spike Cooldown", "Downshift or cap", "長線與大單仍在近窗，但專職跟進不足，較像尖峰後回落"

    if concentrated_recent_spike and cooling_pace and not is_post_holiday:
        return "Post-spike Cooldown", "Downshift or cap", "近期單日或銷售員高度集中，且節奏轉冷"

    if volatile or (strong_large_order and (strong_longhaul or strong_specialist)):
        return "Volatile Hold", "Hold and monitor", "信號偏強但方向不穩，先不做上調或下調"

    if risk_level == "High":
        return "Volatile Hold", "Hold and monitor", "高風險但缺少穩定 pre-spike 或 cooldown 組合"
    return "Watch", "Hold", "中風險觀察"


def _actual_spike_outcome(actual_high: bool, large_under: bool, large_over: bool) -> str:
    if large_under and actual_high:
        return "Pre-spike"
    if large_over:
        return "Post-spike Cooldown"
    if actual_high or large_under:
        return "Volatile Hold"
    return "Normal"


def _spike_signal_columns() -> list[str]:
    return [
        "Cutoff",
        "ActualDate",
        "FeatureMaxDate",
        "NoFutureLeak",
        "Actual",
        "Prediction",
        "Error",
        "AbsError",
        "APE",
        "ActualHighRevenue",
        "ActualLargeUnderestimate",
        "ActualLargeOverestimate",
        "RecentTotalRevenue7D",
        "RecentTotalRevenue14D",
        "RecentSpecialistRevenue7D",
        "RecentSpecialistShare7D",
        "RecentSpecialistRevenue14D",
        "RecentSpecialistShare14D",
        "SpecialistMomentumRatio",
        "RecentLonghaulRevenue7D",
        "RecentLonghaulShare7D",
        "RecentLonghaulRevenue14D",
        "RecentLonghaulShare14D",
        "LonghaulMomentumRatio",
        "RecentLargeOrderCount7D",
        "RecentLargeOrderAmount7D",
        "LargeOrderThreshold",
        "RecentTopSalespersonShare7D",
        "RecentMaxDailyRevenue7D",
        "RecentMaxDailyRevenueShare7D",
        "RecentRevenueVolatility7D",
        "RecentRevenueVs14DAvg",
        "IsPostHolidayForecastDate",
        "IsMonthEndForecastDate",
        "SpikeRiskScore",
        "SpikeRiskLevel",
        "SpikeRiskReasons",
        "SpikeSignalClass",
        "SpikeSignalAction",
        "SpikeSignalClassReason",
        "ActualSpikeOutcome",
    ]


def _build_daily_spike_signal_detail(detail_df: pd.DataFrame, df_tour: pd.DataFrame, df_others: pd.DataFrame) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame(columns=_spike_signal_columns())
    base = detail_df[
        (detail_df["預測天期"] == 1)
        & (detail_df["策略"] == STRATEGY_TOTAL)
        & (detail_df["模型"] == "LightGBM")
    ].copy()
    if base.empty:
        return pd.DataFrame(columns=_spike_signal_columns())

    base["Cutoff_dt"] = pd.to_datetime(base["Cutoff"], errors="coerce")
    base["ActualDate_dt"] = pd.to_datetime(base["ActualDate"], errors="coerce")
    base = base.dropna(subset=["Cutoff_dt", "ActualDate_dt"]).copy()
    if base.empty:
        return pd.DataFrame(columns=_spike_signal_columns())

    source_df = _prepared_spike_source_frame(df_tour, df_others)
    if not source_df.empty:
        source_df[COL_MONEY] = pd.to_numeric(source_df[COL_MONEY], errors="coerce").fillna(0)

    actual_values = pd.to_numeric(base["Actual"], errors="coerce").fillna(0)
    nonzero_actuals = actual_values[actual_values > 0]
    high_actual_threshold = float(nonzero_actuals.quantile(0.75)) if not nonzero_actuals.empty else float(actual_values.quantile(0.75))
    large_abs_error_threshold = float(base["AbsError"].quantile(0.75)) if "AbsError" in base.columns else 0.0

    rows: list[dict] = []
    for _, row in base.sort_values("ActualDate_dt").iterrows():
        cutoff = pd.Timestamp(row["Cutoff_dt"]).normalize()
        actual_date = pd.Timestamp(row["ActualDate_dt"]).normalize()
        history = source_df[source_df["Date"] <= cutoff].copy() if not source_df.empty else pd.DataFrame()
        w7 = _window_source_frame(source_df, cutoff, 7)
        w14 = _window_source_frame(source_df, cutoff, 14)
        w28 = _window_source_frame(source_df, cutoff, 28)

        total_7 = _sum_money(w7)
        total_14 = _sum_money(w14)
        total_28 = _sum_money(w28)
        recent_window_stats = _daily_window_stats(w7, total_7, total_14)

        specialist_7 = _sum_money(w7[w7.get(COL_BRANCH, pd.Series("", index=w7.index)).astype(str).str.strip() == TARGET_DEPT_FOR_REP]) if not w7.empty else 0.0
        specialist_14 = _sum_money(w14[w14.get(COL_BRANCH, pd.Series("", index=w14.index)).astype(str).str.strip() == TARGET_DEPT_FOR_REP]) if not w14.empty else 0.0
        specialist_28 = _sum_money(w28[w28.get(COL_BRANCH, pd.Series("", index=w28.index)).astype(str).str.strip() == TARGET_DEPT_FOR_REP]) if not w28.empty else 0.0

        longhaul_7 = _sum_money(w7[_longhaul_mask(w7)]) if not w7.empty else 0.0
        longhaul_14 = _sum_money(w14[_longhaul_mask(w14)]) if not w14.empty else 0.0
        longhaul_28 = _sum_money(w28[_longhaul_mask(w28)]) if not w28.empty else 0.0

        large_order_count_7, large_order_amount_7, large_order_threshold = _large_order_stats(w7, history)
        feature_max_date = w28["Date"].max() if not w28.empty and "Date" in w28.columns else pd.NaT

        calendar_features = _calendar_feature_frame([actual_date])
        cal_row = calendar_features.iloc[0] if not calendar_features.empty else pd.Series(dtype=float)
        prior_calendar = _calendar_feature_frame([actual_date - pd.Timedelta(days=i) for i in (1, 2, 3)])
        is_post_holiday = (
            not prior_calendar.empty
            and prior_calendar.get("is_public_holiday", pd.Series(dtype=float)).fillna(0).astype(int).eq(1).any()
        )
        is_month_end = bool(int(cal_row.get("is_month_end_window", 0) or 0))

        specialist_share_7 = specialist_7 / total_7 * 100 if total_7 else 0.0
        specialist_share_14 = specialist_14 / total_14 * 100 if total_14 else 0.0
        longhaul_share_7 = longhaul_7 / total_7 * 100 if total_7 else 0.0
        longhaul_share_14 = longhaul_14 / total_14 * 100 if total_14 else 0.0
        specialist_momentum = specialist_7 / (specialist_28 / 4) if specialist_28 else np.nan
        longhaul_momentum = longhaul_7 / (longhaul_28 / 4) if longhaul_28 else np.nan
        top_sales_share_7 = _top_salesperson_share(w7, total_7)

        score = 0
        reasons: list[str] = []
        if specialist_share_7 >= 50:
            score += 2
            reasons.append("專職7日佔比高")
        elif specialist_share_7 >= 30:
            score += 1
            reasons.append("專職7日佔比中")
        if pd.notna(specialist_momentum) and specialist_momentum >= 1.35 and specialist_7 >= 150000:
            score += 1
            reasons.append("專職成交升溫")
        if longhaul_share_7 >= 55:
            score += 2
            reasons.append("長線7日佔比高")
        elif longhaul_share_7 >= 35:
            score += 1
            reasons.append("長線7日佔比中")
        if pd.notna(longhaul_momentum) and longhaul_momentum >= 1.35 and longhaul_7 >= 150000:
            score += 1
            reasons.append("長線成交升溫")
        if large_order_count_7 >= 1:
            score += 1
            reasons.append("近7日有大單")
        if large_order_amount_7 >= max(large_order_threshold * 1.5, 250000):
            score += 1
            reasons.append("近7日大單金額高")
        if top_sales_share_7 >= 45:
            score += 1
            reasons.append("銷售員集中")
        if is_post_holiday:
            score += 1
            reasons.append("假期後窗口")
        if is_month_end:
            score += 1
            reasons.append("月底窗口")

        if score >= 5:
            risk_level = "High"
        elif score >= 3:
            risk_level = "Medium"
        else:
            risk_level = "Low"

        actual = float(row["Actual"])
        error = float(row["Error"])
        abs_error = float(row["AbsError"])
        actual_high = bool(actual >= high_actual_threshold)
        actual_large_under = bool(error < 0 and abs_error >= large_abs_error_threshold)
        actual_large_over = bool(error > 0 and abs_error >= large_abs_error_threshold)
        spike_class, spike_action, spike_class_reason = _refined_spike_signal_class(
            risk_level=risk_level,
            risk_score=int(score),
            specialist_share_7=specialist_share_7,
            specialist_momentum=specialist_momentum,
            longhaul_share_7=longhaul_share_7,
            longhaul_momentum=longhaul_momentum,
            large_order_count_7=large_order_count_7,
            large_order_amount_7=large_order_amount_7,
            large_order_threshold=large_order_threshold,
            top_sales_share_7=top_sales_share_7,
            is_post_holiday=bool(is_post_holiday),
            is_month_end=bool(is_month_end),
            recent_max_daily_share_7=float(recent_window_stats["RecentMaxDailyRevenueShare7D"]),
            recent_volatility_7=float(recent_window_stats["RecentRevenueVolatility7D"]),
            recent_vs_14_avg=float(recent_window_stats["RecentRevenueVs14DAvg"])
            if pd.notna(recent_window_stats["RecentRevenueVs14DAvg"])
            else np.nan,
        )
        rows.append(
            {
                "Cutoff": cutoff.date().isoformat(),
                "ActualDate": actual_date.date().isoformat(),
                "FeatureMaxDate": feature_max_date.date().isoformat() if pd.notna(feature_max_date) else "",
                "NoFutureLeak": bool(pd.isna(feature_max_date) or pd.Timestamp(feature_max_date).normalize() <= cutoff),
                "Actual": actual,
                "Prediction": float(row["Prediction"]),
                "Error": error,
                "AbsError": abs_error,
                "APE": float(row["APE"]) if pd.notna(row["APE"]) else np.nan,
                "ActualHighRevenue": actual_high,
                "ActualLargeUnderestimate": actual_large_under,
                "ActualLargeOverestimate": actual_large_over,
                "RecentTotalRevenue7D": total_7,
                "RecentTotalRevenue14D": total_14,
                "RecentSpecialistRevenue7D": specialist_7,
                "RecentSpecialistShare7D": specialist_share_7,
                "RecentSpecialistRevenue14D": specialist_14,
                "RecentSpecialistShare14D": specialist_share_14,
                "SpecialistMomentumRatio": specialist_momentum,
                "RecentLonghaulRevenue7D": longhaul_7,
                "RecentLonghaulShare7D": longhaul_share_7,
                "RecentLonghaulRevenue14D": longhaul_14,
                "RecentLonghaulShare14D": longhaul_share_14,
                "LonghaulMomentumRatio": longhaul_momentum,
                "RecentLargeOrderCount7D": large_order_count_7,
                "RecentLargeOrderAmount7D": large_order_amount_7,
                "LargeOrderThreshold": large_order_threshold,
                "RecentTopSalespersonShare7D": top_sales_share_7,
                **recent_window_stats,
                "IsPostHolidayForecastDate": bool(is_post_holiday),
                "IsMonthEndForecastDate": bool(is_month_end),
                "SpikeRiskScore": int(score),
                "SpikeRiskLevel": risk_level,
                "SpikeRiskReasons": " / ".join(reasons) if reasons else "無明顯提前信號",
                "SpikeSignalClass": spike_class,
                "SpikeSignalAction": spike_action,
                "SpikeSignalClassReason": spike_class_reason,
                "ActualSpikeOutcome": _actual_spike_outcome(actual_high, actual_large_under, actual_large_over),
            }
        )

    return pd.DataFrame(rows, columns=_spike_signal_columns())


def _summarize_spike_signal_detail(signal_df: pd.DataFrame) -> pd.DataFrame:
    if signal_df.empty:
        return pd.DataFrame(
            columns=[
                "SpikeRiskLevel",
                "樣本數",
                "ActualHighRevenueDays",
                "LargeUnderestimateDays",
                "LargeOverestimateDays",
                "HighRevenueCaptureRate",
                "AvgActual",
                "AvgAbsError",
                "WAPE",
                "AvgSpecialistShare7D",
                "AvgLonghaulShare7D",
                "AvgLargeOrderCount7D",
                "NoFutureLeak",
            ]
        )
    total_high_days = int(signal_df["ActualHighRevenue"].sum())
    summary = (
        signal_df.groupby("SpikeRiskLevel", dropna=False)
        .agg(
            樣本數=("ActualDate", "count"),
            ActualHighRevenueDays=("ActualHighRevenue", "sum"),
            LargeUnderestimateDays=("ActualLargeUnderestimate", "sum"),
            LargeOverestimateDays=("ActualLargeOverestimate", "sum"),
            AvgActual=("Actual", "mean"),
            AbsErrorTotal=("AbsError", "sum"),
            ActualTotal=("Actual", "sum"),
            AvgAbsError=("AbsError", "mean"),
            AvgSpecialistShare7D=("RecentSpecialistShare7D", "mean"),
            AvgLonghaulShare7D=("RecentLonghaulShare7D", "mean"),
            AvgLargeOrderCount7D=("RecentLargeOrderCount7D", "mean"),
            NoFutureLeak=("NoFutureLeak", "all"),
        )
        .reset_index()
    )
    summary["HighRevenueCaptureRate"] = np.where(
        total_high_days > 0,
        summary["ActualHighRevenueDays"] / total_high_days * 100,
        np.nan,
    )
    summary["WAPE"] = np.where(
        summary["ActualTotal"] != 0,
        summary["AbsErrorTotal"] / summary["ActualTotal"] * 100,
        np.nan,
    )
    order = {"High": 0, "Medium": 1, "Low": 2}
    summary["_order"] = summary["SpikeRiskLevel"].map(order).fillna(99)
    return (
        summary[
            [
                "SpikeRiskLevel",
                "樣本數",
                "ActualHighRevenueDays",
                "LargeUnderestimateDays",
                "LargeOverestimateDays",
                "HighRevenueCaptureRate",
                "AvgActual",
                "AvgAbsError",
                "WAPE",
                "AvgSpecialistShare7D",
                "AvgLonghaulShare7D",
                "AvgLargeOrderCount7D",
                "NoFutureLeak",
                "_order",
            ]
        ]
        .sort_values(["_order", "WAPE"])
        .drop(columns=["_order"])
        .reset_index(drop=True)
    )


def _spike_refinement_summary_columns() -> list[str]:
    return [
        "SpikeSignalClass",
        "SpikeSignalAction",
        "樣本數",
        "TargetOutcomeDays",
        "TruePositive",
        "FalsePositive",
        "FalseNegative",
        "Precision",
        "Recall",
        "AvgActual",
        "AvgAbsError",
        "WAPE",
        "AvgRiskScore",
        "AvgSpecialistShare7D",
        "AvgLonghaulShare7D",
        "AvgLargeOrderAmount7D",
        "AvgRecentMaxDailyRevenueShare7D",
        "NoFutureLeak",
        "備註",
    ]


def _summarize_spike_signal_refinement(signal_df: pd.DataFrame) -> pd.DataFrame:
    if signal_df.empty or "SpikeSignalClass" not in signal_df.columns:
        return pd.DataFrame(columns=_spike_refinement_summary_columns())

    rows: list[dict] = []
    classes = ["Pre-spike", "Post-spike Cooldown", "Volatile Hold", "Watch", "Normal"]
    actual_outcome = signal_df["ActualSpikeOutcome"].fillna("Normal").astype(str)
    for class_name in classes:
        predicted = signal_df["SpikeSignalClass"].fillna("").astype(str) == class_name
        actual = actual_outcome == class_name
        subset = signal_df[predicted].copy()
        tp = int((predicted & actual).sum())
        fp = int((predicted & ~actual).sum())
        fn = int((~predicted & actual).sum())
        pred_n = int(predicted.sum())
        actual_n = int(actual.sum())
        action = ""
        if not subset.empty and "SpikeSignalAction" in subset.columns:
            action = str(subset["SpikeSignalAction"].mode().iloc[0]) if not subset["SpikeSignalAction"].mode().empty else ""
        actual_total = float(subset["Actual"].sum()) if not subset.empty else 0.0
        abs_error_total = float(subset["AbsError"].sum()) if not subset.empty else 0.0
        rows.append(
            {
                "SpikeSignalClass": class_name,
                "SpikeSignalAction": action,
                "樣本數": pred_n,
                "TargetOutcomeDays": actual_n,
                "TruePositive": tp,
                "FalsePositive": fp,
                "FalseNegative": fn,
                "Precision": tp / pred_n * 100 if pred_n else np.nan,
                "Recall": tp / actual_n * 100 if actual_n else np.nan,
                "AvgActual": float(subset["Actual"].mean()) if not subset.empty else np.nan,
                "AvgAbsError": float(subset["AbsError"].mean()) if not subset.empty else np.nan,
                "WAPE": abs_error_total / actual_total * 100 if actual_total else np.nan,
                "AvgRiskScore": float(subset["SpikeRiskScore"].mean()) if not subset.empty else np.nan,
                "AvgSpecialistShare7D": float(subset["RecentSpecialistShare7D"].mean()) if not subset.empty else np.nan,
                "AvgLonghaulShare7D": float(subset["RecentLonghaulShare7D"].mean()) if not subset.empty else np.nan,
                "AvgLargeOrderAmount7D": float(subset["RecentLargeOrderAmount7D"].mean()) if not subset.empty else np.nan,
                "AvgRecentMaxDailyRevenueShare7D": float(subset["RecentMaxDailyRevenueShare7D"].mean()) if not subset.empty else np.nan,
                "NoFutureLeak": bool(subset["NoFutureLeak"].all()) if not subset.empty else True,
                "備註": "Precision/Recall 用 ActualSpikeOutcome 事後標籤驗證；分類規則本身只用 cutoff 前 lead features。",
            }
        )

    result = pd.DataFrame(rows, columns=_spike_refinement_summary_columns())
    order = {"Pre-spike": 0, "Post-spike Cooldown": 1, "Volatile Hold": 2, "Watch": 3, "Normal": 4}
    result["_order"] = result["SpikeSignalClass"].map(order).fillna(99)
    return result.sort_values(["_order"]).drop(columns=["_order"]).reset_index(drop=True)


def _daily_event_lead_signal_columns() -> list[str]:
    return [
        "Cutoff",
        "ActualDate",
        "FeatureMaxDate",
        "NoFutureLeak",
        "Actual",
        "Prediction",
        "Error",
        "AbsError",
        "ActualSpikeOutcome",
        "EventLeadClass",
        "EventLeadAction",
        "EventLeadReason",
        "EventOrderMomentum7v14",
        "EventLargeOrderThreshold",
        "OrderCount3D",
        "OrderCount7D",
        "OrderCount14D",
        "LargeOrderCount3D",
        "LargeOrderCount7D",
        "MaxOrderAmount3D",
        "MaxOrderAmount7D",
        "LargeOrderTotal3D",
        "LargeOrderTotal7D",
        "SpecialistOrderCount7D",
        "SpecialistLargeOrderCount7D",
        "SpecialistLargeOrderTotal7D",
        "LonghaulOrderCount7D",
        "LonghaulLargeOrderCount7D",
        "LonghaulLargeOrderTotal7D",
        "TopSalespersonOrderShare7D",
        "UniqueSalespersons7D",
        "SpikeRiskLevel",
        "SpikeRiskScore",
    ]


def _classify_event_lead_signal(stats3: dict[str, float], stats7: dict[str, float], stats14: dict[str, float]) -> tuple[str, str, str]:
    order_7 = float(stats7.get("OrderCount", 0) or 0)
    order_14 = float(stats14.get("OrderCount", 0) or 0)
    order_momentum = order_7 / (order_14 / 2) if order_14 else np.nan
    large_3 = int(stats3.get("LargeOrderCount", 0) or 0)
    large_7 = int(stats7.get("LargeOrderCount", 0) or 0)
    max_order_3 = float(stats3.get("MaxOrderAmount", 0) or 0)
    max_order_7 = float(stats7.get("MaxOrderAmount", 0) or 0)
    specialist_large_7 = int(stats7.get("SpecialistLargeOrderCount", 0) or 0)
    longhaul_large_7 = int(stats7.get("LonghaulLargeOrderCount", 0) or 0)
    specialist_large_total_7 = float(stats7.get("SpecialistLargeOrderTotal", 0) or 0)
    longhaul_large_total_7 = float(stats7.get("LonghaulLargeOrderTotal", 0) or 0)
    top_sales_share_7 = float(stats7.get("TopSalespersonOrderShare", 0) or 0)
    unique_sales_7 = int(stats7.get("UniqueSalespersons", 0) or 0)

    has_specialist_or_longhaul_large = specialist_large_7 >= 1 or longhaul_large_7 >= 1
    strong_large_event = large_3 >= 1 and max_order_3 >= 180000 and has_specialist_or_longhaul_large
    broad_enough = unique_sales_7 >= 3 and top_sales_share_7 <= 55
    event_momentum_up = pd.notna(order_momentum) and order_momentum >= 1.05
    event_momentum_down = pd.notna(order_momentum) and order_momentum <= 0.82

    if strong_large_event and broad_enough and event_momentum_up:
        return "Event Pre-spike", "Selective uplift candidate", "近3日有大單，且專職/長線大單與訂單動能同步升溫"

    if large_7 >= 1 and large_3 == 0 and (event_momentum_down or top_sales_share_7 >= 50):
        return "Event Cooldown", "Downshift candidate", "近7日仍有大單痕跡，但近3日沒有新大單且訂單動能轉弱或銷售集中"

    if large_7 >= 1 or specialist_large_total_7 > 0 or longhaul_large_total_7 > 0:
        return "Event Volatile", "Hold and monitor", "有事件級大單/專職/長線信號，但方向不足以自動調整"

    return "Event Normal", "Hold", "沒有事件級大單或專職/長線集中信號"


def _build_daily_event_lead_signal_detail(signal_df: pd.DataFrame, df_tour: pd.DataFrame, df_others: pd.DataFrame) -> pd.DataFrame:
    if signal_df.empty:
        return pd.DataFrame(columns=_daily_event_lead_signal_columns())
    source_df = _prepared_spike_source_frame(df_tour, df_others)
    if source_df.empty:
        return pd.DataFrame(columns=_daily_event_lead_signal_columns())
    source_df[COL_MONEY] = pd.to_numeric(source_df[COL_MONEY], errors="coerce").fillna(0)

    rows: list[dict] = []
    for _, signal in signal_df.iterrows():
        cutoff = pd.Timestamp(signal["Cutoff"]).normalize()
        actual_date = pd.Timestamp(signal["ActualDate"]).normalize()
        history = source_df[source_df["Date"] <= cutoff].copy()
        w3 = _window_source_frame(source_df, cutoff, 3)
        w7 = _window_source_frame(source_df, cutoff, 7)
        w14 = _window_source_frame(source_df, cutoff, 14)
        threshold = _large_order_threshold(history)
        stats3 = _event_window_stats(w3, history, threshold)
        stats7 = _event_window_stats(w7, history, threshold)
        stats14 = _event_window_stats(w14, history, threshold)
        event_class, event_action, event_reason = _classify_event_lead_signal(stats3, stats7, stats14)
        feature_max_date = pd.concat([w3, w7, w14], ignore_index=True)["Date"].max() if not (w3.empty and w7.empty and w14.empty) else pd.NaT
        order_7 = float(stats7.get("OrderCount", 0) or 0)
        order_14 = float(stats14.get("OrderCount", 0) or 0)
        order_momentum = order_7 / (order_14 / 2) if order_14 else np.nan
        rows.append(
            {
                "Cutoff": cutoff.date().isoformat(),
                "ActualDate": actual_date.date().isoformat(),
                "FeatureMaxDate": feature_max_date.date().isoformat() if pd.notna(feature_max_date) else "",
                "NoFutureLeak": bool(pd.isna(feature_max_date) or pd.Timestamp(feature_max_date).normalize() <= cutoff),
                "Actual": float(signal["Actual"]),
                "Prediction": float(signal["Prediction"]),
                "Error": float(signal["Error"]),
                "AbsError": float(signal["AbsError"]),
                "ActualSpikeOutcome": str(signal.get("ActualSpikeOutcome", "Normal")),
                "EventLeadClass": event_class,
                "EventLeadAction": event_action,
                "EventLeadReason": event_reason,
                "EventOrderMomentum7v14": order_momentum,
                "EventLargeOrderThreshold": threshold,
                "OrderCount3D": int(stats3["OrderCount"]),
                "OrderCount7D": int(stats7["OrderCount"]),
                "OrderCount14D": int(stats14["OrderCount"]),
                "LargeOrderCount3D": int(stats3["LargeOrderCount"]),
                "LargeOrderCount7D": int(stats7["LargeOrderCount"]),
                "MaxOrderAmount3D": float(stats3["MaxOrderAmount"]),
                "MaxOrderAmount7D": float(stats7["MaxOrderAmount"]),
                "LargeOrderTotal3D": float(stats3["LargeOrderTotal"]),
                "LargeOrderTotal7D": float(stats7["LargeOrderTotal"]),
                "SpecialistOrderCount7D": int(stats7["SpecialistOrderCount"]),
                "SpecialistLargeOrderCount7D": int(stats7["SpecialistLargeOrderCount"]),
                "SpecialistLargeOrderTotal7D": float(stats7["SpecialistLargeOrderTotal"]),
                "LonghaulOrderCount7D": int(stats7["LonghaulOrderCount"]),
                "LonghaulLargeOrderCount7D": int(stats7["LonghaulLargeOrderCount"]),
                "LonghaulLargeOrderTotal7D": float(stats7["LonghaulLargeOrderTotal"]),
                "TopSalespersonOrderShare7D": float(stats7["TopSalespersonOrderShare"]),
                "UniqueSalespersons7D": int(stats7["UniqueSalespersons"]),
                "SpikeRiskLevel": str(signal.get("SpikeRiskLevel", "")),
                "SpikeRiskScore": int(signal.get("SpikeRiskScore", 0) or 0),
            }
        )

    return pd.DataFrame(rows, columns=_daily_event_lead_signal_columns())


def _event_class_target(class_name: str) -> str:
    return {
        "Event Pre-spike": "Pre-spike",
        "Event Cooldown": "Post-spike Cooldown",
        "Event Volatile": "Volatile Hold",
        "Event Normal": "Normal",
    }.get(class_name, "Normal")


def _summarize_daily_event_lead_signal(detail_df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "EventLeadClass",
        "EventLeadAction",
        "樣本數",
        "TargetOutcomeDays",
        "TruePositive",
        "FalsePositive",
        "FalseNegative",
        "Precision",
        "Recall",
        "AvgActual",
        "AvgAbsError",
        "WAPE",
        "AvgOrderMomentum7v14",
        "AvgLargeOrderCount3D",
        "AvgLargeOrderCount7D",
        "AvgSpecialistLargeOrderCount7D",
        "AvgLonghaulLargeOrderCount7D",
        "AvgTopSalespersonOrderShare7D",
        "NoFutureLeak",
        "備註",
    ]
    if detail_df.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    actual_outcome = detail_df["ActualSpikeOutcome"].fillna("Normal").astype(str)
    for class_name in ["Event Pre-spike", "Event Cooldown", "Event Volatile", "Event Normal"]:
        predicted = detail_df["EventLeadClass"].fillna("").astype(str) == class_name
        target = _event_class_target(class_name)
        actual = actual_outcome == target
        subset = detail_df[predicted].copy()
        tp = int((predicted & actual).sum())
        fp = int((predicted & ~actual).sum())
        fn = int((~predicted & actual).sum())
        pred_n = int(predicted.sum())
        actual_n = int(actual.sum())
        actual_total = float(subset["Actual"].sum()) if not subset.empty else 0.0
        abs_error_total = float(subset["AbsError"].sum()) if not subset.empty else 0.0
        action = ""
        if not subset.empty:
            mode = subset["EventLeadAction"].mode()
            action = str(mode.iloc[0]) if not mode.empty else ""
        rows.append(
            {
                "EventLeadClass": class_name,
                "EventLeadAction": action,
                "樣本數": pred_n,
                "TargetOutcomeDays": actual_n,
                "TruePositive": tp,
                "FalsePositive": fp,
                "FalseNegative": fn,
                "Precision": tp / pred_n * 100 if pred_n else np.nan,
                "Recall": tp / actual_n * 100 if actual_n else np.nan,
                "AvgActual": float(subset["Actual"].mean()) if not subset.empty else np.nan,
                "AvgAbsError": float(subset["AbsError"].mean()) if not subset.empty else np.nan,
                "WAPE": abs_error_total / actual_total * 100 if actual_total else np.nan,
                "AvgOrderMomentum7v14": float(subset["EventOrderMomentum7v14"].mean()) if not subset.empty else np.nan,
                "AvgLargeOrderCount3D": float(subset["LargeOrderCount3D"].mean()) if not subset.empty else np.nan,
                "AvgLargeOrderCount7D": float(subset["LargeOrderCount7D"].mean()) if not subset.empty else np.nan,
                "AvgSpecialistLargeOrderCount7D": float(subset["SpecialistLargeOrderCount7D"].mean()) if not subset.empty else np.nan,
                "AvgLonghaulLargeOrderCount7D": float(subset["LonghaulLargeOrderCount7D"].mean()) if not subset.empty else np.nan,
                "AvgTopSalespersonOrderShare7D": float(subset["TopSalespersonOrderShare7D"].mean()) if not subset.empty else np.nan,
                "NoFutureLeak": bool(subset["NoFutureLeak"].all()) if not subset.empty else True,
                "備註": "事件級 lead signal 只使用 cutoff 前訂單/銷售員/專職/長線特徵；precision/recall 用事後 outcome 驗證。",
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _two_lane_selector_detail_columns() -> list[str]:
    return [
        "Cutoff",
        "ActualDate",
        "FeatureMaxDate",
        "NoFutureLeak",
        "EventLeadClass",
        "NormalLaneModel",
        "策略",
        "模型",
        "預測天期",
        "Actual",
        "BasePrediction",
        "NormalLanePrediction",
        "Prediction",
        "Error",
        "AbsError",
        "APE",
        "SMAPE",
        "Rule",
    ]


def _two_lane_selector_summary_columns() -> list[str]:
    return [
        "評估口徑",
        "策略",
        "模型",
        "NormalLaneModel",
        "預測天期",
        "MAE",
        "WAPE",
        "MAPE",
        "MedianAPE",
        "SMAPE",
        "Bias",
        "樣本數",
        "NoFutureLeak",
        "備註",
    ]


def _normal_lane_prediction_lookup(normal_detail_df: pd.DataFrame, cutoff: str, actual_date: str, model_name: str) -> float | None:
    if normal_detail_df.empty:
        return None
    match = normal_detail_df[
        (normal_detail_df["Cutoff"] == cutoff)
        & (normal_detail_df["ActualDate"] == actual_date)
        & (normal_detail_df["模型"] == model_name)
    ]
    if match.empty:
        return None
    return float(match["Prediction"].iloc[0])


def _build_two_lane_selector_records(
    event_detail_df: pd.DataFrame,
    normal_detail_df: pd.DataFrame,
) -> pd.DataFrame:
    if event_detail_df.empty or normal_detail_df.empty:
        return pd.DataFrame(columns=_two_lane_selector_detail_columns())

    normal_lane_models = [
        "Normal-Day Downside Guardrail",
        "Normal-Day Tight Guardrail",
        "Normal-Day Bias Guardrail",
        "Normal-Day Quantile Guardrail",
    ]
    selector_modes = {
        "Two-Lane Downside Only": "cooldown-only",
        "Two-Lane Conservative": "cooldown-normal",
        "Two-Lane Normal Guardrail": "cooldown-normal-volatile-blend",
    }
    rows: list[dict] = []
    for _, event in event_detail_df.iterrows():
        cutoff = str(event["Cutoff"])
        actual_date = str(event["ActualDate"])
        actual = float(event["Actual"])
        base_pred = float(event["Prediction"])
        event_class = str(event.get("EventLeadClass", "Event Normal"))
        for normal_model in normal_lane_models:
            normal_pred = _normal_lane_prediction_lookup(normal_detail_df, cutoff, actual_date, normal_model)
            if normal_pred is None:
                continue
            for selector_name, mode in selector_modes.items():
                pred = base_pred
                rule = "hold-base"
                if mode == "cooldown-only":
                    if event_class == "Event Cooldown":
                        pred = min(base_pred, normal_pred)
                        rule = "cooldown-downside-only"
                elif mode == "cooldown-normal":
                    if event_class in ("Event Cooldown", "Event Normal"):
                        pred = min(base_pred, normal_pred)
                        rule = "cooldown-or-normal-downside"
                elif mode == "cooldown-normal-volatile-blend":
                    if event_class in ("Event Cooldown", "Event Normal"):
                        pred = min(base_pred, normal_pred)
                        rule = "cooldown-or-normal-downside"
                    elif event_class == "Event Volatile":
                        pred = 0.82 * base_pred + 0.18 * normal_pred
                        rule = "volatile-small-normal-blend"
                metric = _metric_columns(actual, float(pred))
                rows.append(
                    {
                        "Cutoff": cutoff,
                        "ActualDate": actual_date,
                        "FeatureMaxDate": event.get("FeatureMaxDate", ""),
                        "NoFutureLeak": bool(event.get("NoFutureLeak", False)),
                        "EventLeadClass": event_class,
                        "NormalLaneModel": normal_model,
                        "策略": STRATEGY_TWO_LANE_SELECTOR,
                        "模型": selector_name,
                        "預測天期": 1,
                        "Actual": actual,
                        "BasePrediction": base_pred,
                        "NormalLanePrediction": normal_pred,
                        "Prediction": float(pred),
                        **metric,
                        "Rule": rule,
                    }
                )
    if not rows:
        return pd.DataFrame(columns=_two_lane_selector_detail_columns())
    return pd.DataFrame(rows, columns=_two_lane_selector_detail_columns())


def _summarize_two_lane_selector(detail_df: pd.DataFrame) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame(columns=_two_lane_selector_summary_columns())
    summary_df = (
        detail_df.groupby(["策略", "模型", "NormalLaneModel", "預測天期"], dropna=False)
        .agg(
            MAE=("AbsError", "mean"),
            WAPE_分子=("AbsError", "sum"),
            WAPE_分母=("Actual", "sum"),
            MAPE=("APE", "mean"),
            MedianAPE=("APE", "median"),
            SMAPE=("SMAPE", "mean"),
            Bias=("Error", "mean"),
            樣本數=("APE", "count"),
            NoFutureLeak=("NoFutureLeak", "all"),
        )
        .reset_index()
    )
    summary_df["WAPE"] = np.where(
        summary_df["WAPE_分母"] != 0,
        summary_df["WAPE_分子"] / summary_df["WAPE_分母"] * 100,
        np.nan,
    )
    summary_df["評估口徑"] = "All Days - Event Lead Two-Lane"
    summary_df["備註"] = "Step 3D 獨立 backtest；只用 cutoff 前 event lead signal 選 lane，未接入正式預測。"
    return (
        summary_df[
            [
                "評估口徑",
                "策略",
                "模型",
                "NormalLaneModel",
                "預測天期",
                "MAE",
                "WAPE",
                "MAPE",
                "MedianAPE",
                "SMAPE",
                "Bias",
                "樣本數",
                "NoFutureLeak",
                "備註",
            ]
        ]
        .sort_values(["WAPE", "MAPE", "模型", "NormalLaneModel"])
        .reset_index(drop=True)
    )


def _prediction_lookup(detail_df: pd.DataFrame, cutoff: str, actual_date: str, strategy: str, model: str) -> float | None:
    match = detail_df[
        (detail_df["Cutoff"] == cutoff)
        & (detail_df["ActualDate"] == actual_date)
        & (detail_df["預測天期"] == 1)
        & (detail_df["策略"] == strategy)
        & (detail_df["模型"] == model)
    ]
    if match.empty:
        return None
    return float(match["Prediction"].iloc[0])


def _bounded_spike_uplift(base_pred: float, signal: pd.Series, mode: str) -> tuple[float, str]:
    score = float(signal.get("SpikeRiskScore", 0) or 0)
    risk_level = str(signal.get("SpikeRiskLevel", "Low"))
    specialist_share = float(signal.get("RecentSpecialistShare7D", 0) or 0)
    longhaul_share = float(signal.get("RecentLonghaulShare7D", 0) or 0)
    large_order_amount = float(signal.get("RecentLargeOrderAmount7D", 0) or 0)
    top_sales_share = float(signal.get("RecentTopSalespersonShare7D", 0) or 0)
    is_post_holiday = bool(signal.get("IsPostHolidayForecastDate", False))
    is_month_end = bool(signal.get("IsMonthEndForecastDate", False))
    specialist_momentum = float(signal.get("SpecialistMomentumRatio", 0) or 0)
    longhaul_momentum = float(signal.get("LonghaulMomentumRatio", 0) or 0)

    if risk_level != "High":
        return base_pred, "non-high-risk-no-uplift"

    strong_longhaul = longhaul_share >= 55
    specialist_visible = specialist_share >= 30 or specialist_momentum >= 1.35
    large_order_visible = large_order_amount >= 250000
    repeated_big_order = large_order_amount >= 700000 and top_sales_share >= 55
    pre_spike_pattern = strong_longhaul and specialist_visible and large_order_visible

    if mode == "conservative":
        if pre_spike_pattern and is_post_holiday and not repeated_big_order:
            target = max(base_pred * 1.20, base_pred + large_order_amount * 0.65)
            return min(target, base_pred * 2.10), "post-holiday-longhaul-specialist-uplift"
        if pre_spike_pattern and is_month_end and repeated_big_order:
            target = base_pred + large_order_amount * 0.18
            return min(target, base_pred * 1.65), "month-end-large-order-light-uplift"
        return base_pred, "high-risk-held"

    if mode == "uplift":
        if pre_spike_pattern:
            factor = 1.10 + min(0.55, max(0, score - 4) * 0.08)
            if is_post_holiday:
                factor += 0.15
            if is_month_end:
                factor += 0.08
            target = max(base_pred * factor, base_pred + large_order_amount * 0.55)
            return min(target, base_pred * 2.60), "broad-high-risk-uplift"
        return base_pred, "high-risk-no-core-pattern"

    if mode == "hybrid":
        if repeated_big_order and not is_post_holiday:
            target = base_pred * 0.88
            return max(0.0, target), "post-spike-cooldown-cap"
        if pre_spike_pattern and (is_post_holiday or (is_month_end and specialist_share >= 50)):
            target = max(base_pred * 1.18, base_pred + large_order_amount * 0.35)
            return min(target, base_pred * 2.00), "hybrid-selective-uplift"
        if strong_longhaul and score >= 6 and not repeated_big_order:
            target = base_pred * 1.12
            return min(target, base_pred * 1.35), "hybrid-longhaul-small-uplift"
        return base_pred, "hybrid-held"

    return base_pred, "unknown-mode-held"


def _build_spike_aware_selector_records(detail_df: pd.DataFrame, signal_df: pd.DataFrame) -> tuple[list[dict], pd.DataFrame]:
    if detail_df.empty or signal_df.empty:
        return [], pd.DataFrame(columns=["Cutoff", "ActualDate", "模型", "SelectedRule", "SelectedPrediction"])

    records: list[dict] = []
    selection_rows: list[dict] = []
    for _, signal in signal_df.iterrows():
        cutoff = str(signal["Cutoff"])
        actual_date = str(signal["ActualDate"])
        actual = float(signal["Actual"])
        base_pred = _prediction_lookup(detail_df, cutoff, actual_date, STRATEGY_TOTAL, "LightGBM")
        if base_pred is None:
            continue
        risk_adjusted = _prediction_lookup(detail_df, cutoff, actual_date, STRATEGY_DAILY_ADJUSTMENT, "LightGBM Risk Adjusted")
        low_day_cap = _prediction_lookup(detail_df, cutoff, actual_date, STRATEGY_DAILY_ADJUSTMENT, "LightGBM Low-Day Cap")
        mtd_pace = _prediction_lookup(detail_df, cutoff, actual_date, STRATEGY_BASELINE, "MTD Pace Daily Allocation")
        hybrid_baseline = _prediction_lookup(detail_df, cutoff, actual_date, STRATEGY_BASELINE, "Hybrid Baseline")
        base_floor = risk_adjusted if risk_adjusted is not None else base_pred
        cap_pred = low_day_cap if low_day_cap is not None else base_floor
        baseline_blend = np.nanmean([v for v in (mtd_pace, hybrid_baseline, base_floor) if v is not None])
        if pd.isna(baseline_blend):
            baseline_blend = base_floor

        conservative, conservative_rule = _bounded_spike_uplift(base_floor, signal, "conservative")
        uplift, uplift_rule = _bounded_spike_uplift(base_floor, signal, "uplift")
        hybrid, hybrid_rule = _bounded_spike_uplift(base_floor, signal, "hybrid")

        if str(signal.get("SpikeRiskLevel", "Low")) == "Low":
            conservative = cap_pred
            conservative_rule = "low-risk-step2b-cap"
            uplift = base_floor
            uplift_rule = "low-risk-base"
            hybrid = min(base_floor, baseline_blend * 1.10)
            hybrid_rule = "low-risk-baseline-guardrail"
        elif str(signal.get("SpikeRiskLevel", "Low")) == "Medium":
            conservative = min(base_floor, max(cap_pred, baseline_blend))
            conservative_rule = "medium-risk-guardrail"
            uplift = max(base_floor, baseline_blend)
            uplift_rule = "medium-risk-pace-blend"
            hybrid = 0.65 * base_floor + 0.35 * baseline_blend
            hybrid_rule = "medium-risk-hybrid-blend"

        model_values = {
            "Spike-aware Conservative": (conservative, conservative_rule),
            "Spike-aware Uplift": (uplift, uplift_rule),
            "Spike-aware Hybrid Selector": (hybrid, hybrid_rule),
        }
        for model_name, (pred, rule) in model_values.items():
            pred = max(0.0, float(pred))
            metric = _metric_columns(actual, pred)
            records.append(
                {
                    "Cutoff": cutoff,
                    "ActualDate": actual_date,
                    "預測天期": 1,
                    "策略": STRATEGY_SPIKE_AWARE,
                    "模型": model_name,
                    "Actual": actual,
                    "Prediction": pred,
                    **metric,
                }
            )
            selection_rows.append(
                {
                    "Cutoff": cutoff,
                    "ActualDate": actual_date,
                    "模型": model_name,
                    "SpikeRiskLevel": signal.get("SpikeRiskLevel", ""),
                    "SpikeRiskScore": signal.get("SpikeRiskScore", np.nan),
                    "SelectedRule": rule,
                    "SelectedPrediction": pred,
                    "BaseLightGBM": base_pred,
                    "RiskAdjusted": base_floor,
                    "RecentSpecialistShare7D": signal.get("RecentSpecialistShare7D", np.nan),
                    "RecentLonghaulShare7D": signal.get("RecentLonghaulShare7D", np.nan),
                    "RecentLargeOrderAmount7D": signal.get("RecentLargeOrderAmount7D", np.nan),
                    "NoFutureLeak": signal.get("NoFutureLeak", False),
                }
            )

    return records, pd.DataFrame(selection_rows)


def _build_fusion_records(base_detail_df: pd.DataFrame, base_summary_df: pd.DataFrame) -> list[dict]:
    fusion_records: list[dict] = []
    models = ["ARIMA", "Prophet", "LightGBM"]
    grouped = base_detail_df.groupby(["Cutoff", "ActualDate", "預測天期", "策略"], sort=False)
    weight_cache: dict[tuple[int, str], dict[str, float]] = {}

    for (cutoff, actual_date, horizon, strategy), group in grouped:
        pred_by_model = group.set_index("模型")["Prediction"].reindex(models)
        if pred_by_model.isna().any():
            continue
        cache_key = (int(horizon), str(strategy))
        if cache_key not in weight_cache:
            weight_cache[cache_key] = _bounded_inverse_metric_weights(
                base_summary_df,
                int(horizon),
                strategy=str(strategy),
                metric="WAPE",
            )
        weights = weight_cache[cache_key]
        actual = float(group["Actual"].iloc[0])
        pred = sum(float(weights[model]) * float(pred_by_model.loc[model]) for model in models)
        fusion_records.append(
            {
                "Cutoff": cutoff,
                "ActualDate": actual_date,
                "預測天期": int(horizon),
                "策略": strategy,
                "模型": "Fusion",
                "Actual": actual,
                "Prediction": pred,
                "Error": pred - actual,
                "AbsError": abs(pred - actual),
                "APE": abs(pred - actual) / actual * 100 if actual else np.nan,
                "SMAPE": (2 * abs(pred - actual) / (abs(actual) + abs(pred)) * 100) if (abs(actual) + abs(pred)) else np.nan,
            }
        )
    return fusion_records


def _metric_columns(actual: float, pred: float) -> dict[str, float]:
    error = pred - actual
    abs_error = abs(error)
    return {
        "Error": error,
        "AbsError": abs_error,
        "APE": abs_error / actual * 100 if actual else np.nan,
        "SMAPE": (2 * abs_error / (abs(actual) + abs(pred)) * 100) if (abs(actual) + abs(pred)) else np.nan,
    }


def _summarize_macro_detail(detail_df: pd.DataFrame) -> pd.DataFrame:
    summary_df = (
        detail_df.groupby(["聚合層級", "策略", "模型"])
        .agg(
            MAE=("AbsError", "mean"),
            WAPE_分子=("AbsError", "sum"),
            WAPE_分母=("Actual", "sum"),
            MAPE=("APE", "mean"),
            MedianAPE=("APE", "median"),
            SMAPE=("SMAPE", "mean"),
            Bias=("Error", "mean"),
            平均預測天數=("預測天期", "mean"),
            樣本數=("APE", "count"),
        )
        .reset_index()
    )
    summary_df["WAPE"] = np.where(
        summary_df["WAPE_分母"] != 0,
        summary_df["WAPE_分子"] / summary_df["WAPE_分母"] * 100,
        np.nan,
    )
    return (
        summary_df.drop(columns=["WAPE_分子", "WAPE_分母"])
        .sort_values(["聚合層級", "WAPE", "MAPE", "策略", "模型"])
        .reset_index(drop=True)
    )


def _macro_inverse_weights(summary_df: pd.DataFrame, layer: str, strategy: str) -> dict[str, float]:
    subset = summary_df[
        (summary_df["聚合層級"] == layer)
        & (summary_df["策略"] == strategy)
        & (summary_df["模型"].isin(MODEL_NAMES))
    ].copy()
    if subset.empty:
        return {model: 1 / len(MODEL_NAMES) for model in MODEL_NAMES}
    metric_by_model = subset.set_index("模型")["WAPE"].reindex(MODEL_NAMES)
    if metric_by_model.isna().all():
        return {model: 1 / len(MODEL_NAMES) for model in MODEL_NAMES}
    safe_metric = metric_by_model.fillna(metric_by_model.dropna().max()).clip(lower=0.01)
    scores = 1 / safe_metric
    weights = scores / scores.sum()
    return {model: float(weights.loc[model]) for model in MODEL_NAMES}


def _build_macro_fusion_records(base_detail_df: pd.DataFrame, base_summary_df: pd.DataFrame) -> list[dict]:
    fusion_records: list[dict] = []
    group_cols = ["Cutoff", "WindowStart", "WindowEnd", "聚合層級", "策略"]
    weight_cache: dict[tuple[str, str], dict[str, float]] = {}
    for group_key, group in base_detail_df.groupby(group_cols, sort=False):
        cutoff, window_start, window_end, layer, strategy = group_key
        pred_by_model = group.set_index("模型")["Prediction"].reindex(MODEL_NAMES)
        if pred_by_model.isna().any():
            continue
        cache_key = (str(layer), str(strategy))
        if cache_key not in weight_cache:
            weight_cache[cache_key] = _macro_inverse_weights(base_summary_df, str(layer), str(strategy))
        weights = weight_cache[cache_key]
        actual = float(group["Actual"].iloc[0])
        pred = sum(float(weights[model]) * float(pred_by_model.loc[model]) for model in MODEL_NAMES)
        record = {
            "Cutoff": cutoff,
            "WindowStart": window_start,
            "WindowEnd": window_end,
            "聚合層級": layer,
            "預測天期": int(group["預測天期"].iloc[0]),
            "策略": strategy,
            "模型": "Fusion",
            "Actual": actual,
            "Prediction": pred,
        }
        record.update(_metric_columns(actual, pred))
        fusion_records.append(record)
    return fusion_records


def _filter_frames_to_cutoff(df_tour: pd.DataFrame, df_others: pd.DataFrame, cutoff: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_tour = df_tour.copy()
    train_others = df_others.copy()
    for frame in (train_tour, train_others):
        if not frame.empty and COL_DATE in frame.columns:
            frame["_forecast_date"] = pd.to_datetime(format_date_to_daily(frame[COL_DATE]), errors="coerce")
    if not train_tour.empty and "_forecast_date" in train_tour.columns:
        train_tour = train_tour.loc[train_tour["_forecast_date"] <= cutoff].drop(columns=["_forecast_date"])
    if not train_others.empty and "_forecast_date" in train_others.columns:
        train_others = train_others.loc[train_others["_forecast_date"] <= cutoff].drop(columns=["_forecast_date"])
    return train_tour, train_others


def run_ai_macro_backtest_report(df_tour: pd.DataFrame, df_others: pd.DataFrame, window_days: int = 30):
    try:
        ts_data = _build_revenue_timeseries(df_tour, df_others)
        if len(ts_data) < 60:
            return None, "歷史數據不足 60 天，暫不能穩定執行週/月宏觀 rolling backtest"

        revenue = ts_data["Revenue"]
        available_dates = list(revenue.index)
        latest_actual_date = available_dates[-1]
        cutoff_requirements: dict[pd.Timestamp, int] = {}

        seven_day_cutoffs = [
            cutoff
            for cutoff in available_dates
            if cutoff + pd.Timedelta(days=7) <= latest_actual_date
        ][-window_days:]
        for cutoff in seven_day_cutoffs:
            cutoff_requirements[cutoff] = max(cutoff_requirements.get(cutoff, 0), 7)

        complete_month_cutoffs = []
        for cutoff in available_dates:
            month_end = cutoff + pd.offsets.MonthEnd(0)
            if cutoff < month_end <= latest_actual_date:
                complete_month_cutoffs.append(cutoff)
        complete_month_cutoffs = complete_month_cutoffs[-window_days:]
        for cutoff in complete_month_cutoffs:
            month_end = cutoff + pd.offsets.MonthEnd(0)
            horizon = int((month_end - cutoff).days)
            cutoff_requirements[cutoff] = max(cutoff_requirements.get(cutoff, 0), horizon)

        records: list[dict] = []
        for idx, cutoff in enumerate(sorted(cutoff_requirements)):
            train_tour, train_others = _filter_frames_to_cutoff(df_tour, df_others, cutoff)
            train_ts = _build_revenue_timeseries(train_tour, train_others)
            if len(train_ts) < 14:
                continue

            strategy_preds: dict[str, dict[str, pd.Series]] = {}
            for strategy in (STRATEGY_TOTAL, STRATEGY_SEGMENTED):
                _, tracks = _forecast_by_strategy(
                    train_tour,
                    train_others,
                    strategy,
                    f_steps=cutoff_requirements[cutoff],
                    seed=6200 + idx,
                    use_prophet=False,
                )
                arima_fcst, prophet_fcst, lgb_fcst = tracks
                strategy_preds[strategy] = {
                    "ARIMA": arima_fcst,
                    "Prophet": prophet_fcst,
                    "LightGBM": lgb_fcst,
                }

            if cutoff in seven_day_cutoffs:
                window_start = cutoff + pd.Timedelta(days=1)
                window_end = cutoff + pd.Timedelta(days=7)
                actual = float(revenue.loc[window_start:window_end].sum())
                for strategy, model_preds in strategy_preds.items():
                    for model_name, preds in model_preds.items():
                        pred = float(preds.loc[window_start:window_end].sum())
                        record = {
                            "Cutoff": cutoff.date().isoformat(),
                            "WindowStart": window_start.date().isoformat(),
                            "WindowEnd": window_end.date().isoformat(),
                            "聚合層級": MACRO_7D,
                            "預測天期": 7,
                            "策略": strategy,
                            "模型": model_name,
                            "Actual": actual,
                            "Prediction": pred,
                        }
                        record.update(_metric_columns(actual, pred))
                        records.append(record)

            if cutoff in complete_month_cutoffs:
                month_start = cutoff.replace(day=1)
                month_end = cutoff + pd.offsets.MonthEnd(0)
                window_start = cutoff + pd.Timedelta(days=1)
                actual = float(revenue.loc[month_start:month_end].sum())
                mtd_actual = float(revenue.loc[month_start:cutoff].sum())
                horizon = int((month_end - cutoff).days)
                for strategy, model_preds in strategy_preds.items():
                    for model_name, preds in model_preds.items():
                        remaining_pred = float(preds.loc[window_start:month_end].sum()) if horizon > 0 else 0.0
                        pred = mtd_actual + remaining_pred
                        record = {
                            "Cutoff": cutoff.date().isoformat(),
                            "WindowStart": month_start.date().isoformat(),
                            "WindowEnd": month_end.date().isoformat(),
                            "聚合層級": MACRO_MONTH_END,
                            "預測天期": horizon,
                            "策略": strategy,
                            "模型": model_name,
                            "Actual": actual,
                            "Prediction": pred,
                            "MTDActual": mtd_actual,
                            "RemainingPrediction": remaining_pred,
                        }
                        record.update(_metric_columns(actual, pred))
                        records.append(record)

        detail_df = pd.DataFrame(records)
        if detail_df.empty:
            return None, "沒有足夠已完成的 7 日窗口或完整月份可與宏觀預測比較"

        base_summary_df = _summarize_macro_detail(detail_df)
        fusion_records = _build_macro_fusion_records(detail_df, base_summary_df)
        if fusion_records:
            detail_df = pd.concat([detail_df, pd.DataFrame(fusion_records)], ignore_index=True)
        summary_df = _summarize_macro_detail(detail_df)
        return {"summary": summary_df, "detail": detail_df}, None
    except Exception as exc:
        return None, str(exc)


def build_macro_forecast_summary(
    ts_data: pd.DataFrame,
    arima_fcst: pd.Series,
    prophet_fcst: pd.Series,
    lgb_fcst: pd.Series,
    consensus: pd.Series,
    lower: pd.Series,
    upper: pd.Series,
) -> dict[str, pd.DataFrame]:
    if ts_data.empty or consensus.empty:
        return {"seven_day": pd.DataFrame(), "month_end": pd.DataFrame()}

    forecast_start = consensus.index[0]
    seven_end = forecast_start + pd.Timedelta(days=6)
    seven_slice = consensus.loc[forecast_start:seven_end]
    seven_day = pd.DataFrame(
        [
            {
                "視角": "未來7日總收入",
                "WindowStart": forecast_start.date().isoformat(),
                "WindowEnd": seven_end.date().isoformat(),
                "ARIMA": float(arima_fcst.loc[forecast_start:seven_end].sum()),
                "Prophet": float(prophet_fcst.loc[forecast_start:seven_end].sum()),
                "LightGBM": float(lgb_fcst.loc[forecast_start:seven_end].sum()),
                "Consensus (共識)": float(seven_slice.sum()),
                "Lower": float(lower.loc[forecast_start:seven_end].sum()),
                "Upper": float(upper.loc[forecast_start:seven_end].sum()),
            }
        ]
    )

    latest_actual = ts_data.index[-1]
    target_month = forecast_start.to_period("M")
    month_start = target_month.start_time.normalize()
    month_end = target_month.end_time.normalize()
    mtd_actual = (
        float(ts_data["Revenue"].loc[month_start:latest_actual].sum())
        if latest_actual.to_period("M") == target_month
        else 0.0
    )
    remaining_consensus = consensus.loc[forecast_start:month_end]
    month_end_df = pd.DataFrame(
        [
            {
                "視角": "本月月底落點",
                "Month": str(target_month),
                "MTDActual": mtd_actual,
                "RemainingDays": int(len(remaining_consensus)),
                "RemainingPrediction": float(remaining_consensus.sum()),
                "MonthEnd Consensus": float(mtd_actual + remaining_consensus.sum()),
                "Lower": float(mtd_actual + lower.loc[forecast_start:month_end].sum()),
                "Upper": float(mtd_actual + upper.loc[forecast_start:month_end].sum()),
            }
        ]
    )
    return {"seven_day": seven_day, "month_end": month_end_df}


def run_ai_backtest_report(df_tour: pd.DataFrame, df_others: pd.DataFrame, window_days: int = 30, horizons: tuple[int, ...] = (1, 7, 30)):
    try:
        ts_data = _build_revenue_timeseries(df_tour, df_others)
        if len(ts_data) < 45:
            return None, "歷史數據不足 45 天，暫不能穩定執行 30 天 rolling backtest"

        revenue = ts_data["Revenue"]
        available_dates = list(revenue.index)
        latest_actual_date = available_dates[-1]
        cutoffs_by_horizon: dict[int, list[pd.Timestamp]] = {}
        required_horizons_by_cutoff: dict[pd.Timestamp, set[int]] = {}

        for horizon in horizons:
            horizon_cutoffs = [
                cutoff
                for cutoff in available_dates
                if cutoff + pd.Timedelta(days=horizon) <= latest_actual_date
            ][-window_days:]
            cutoffs_by_horizon[horizon] = horizon_cutoffs
            for cutoff in horizon_cutoffs:
                required_horizons_by_cutoff.setdefault(cutoff, set()).add(horizon)

        cutoffs = sorted(required_horizons_by_cutoff)
        records: list[dict] = []

        for idx, cutoff in enumerate(cutoffs):
            train_tour = df_tour.copy()
            train_others = df_others.copy()
            for frame in (train_tour, train_others):
                if not frame.empty and COL_DATE in frame.columns:
                    frame["_forecast_date"] = pd.to_datetime(format_date_to_daily(frame[COL_DATE]), errors="coerce")
            if not train_tour.empty and "_forecast_date" in train_tour.columns:
                train_tour = train_tour.loc[train_tour["_forecast_date"] <= cutoff].drop(columns=["_forecast_date"])
            if not train_others.empty and "_forecast_date" in train_others.columns:
                train_others = train_others.loc[train_others["_forecast_date"] <= cutoff].drop(columns=["_forecast_date"])

            max_horizon = max(required_horizons_by_cutoff[cutoff])
            train_ts = _build_revenue_timeseries(train_tour, train_others)
            if len(train_ts) < 14:
                continue

            strategy_preds: dict[str, dict[str, pd.Series]] = {}
            for strategy in (STRATEGY_TOTAL, STRATEGY_SEGMENTED):
                _, tracks = _forecast_by_strategy(
                    train_tour,
                    train_others,
                    strategy,
                    f_steps=max_horizon,
                    seed=4200 + idx,
                    use_prophet=False,
                )
                arima_fcst, prophet_fcst, lgb_fcst = tracks
                strategy_preds[strategy] = {
                    "ARIMA": arima_fcst,
                    "Prophet": prophet_fcst,
                    "LightGBM": lgb_fcst,
                }
            baseline_preds = _forecast_baseline_tracks_from_timeseries(train_ts, f_steps=max_horizon)
            adjustment_preds = _forecast_daily_adjustment_tracks_from_timeseries(
                train_ts,
                strategy_preds.get(STRATEGY_TOTAL, {}).get("LightGBM", pd.Series(dtype=float)),
                f_steps=max_horizon,
            )

            for horizon in sorted(required_horizons_by_cutoff[cutoff]):
                actual_date = cutoff + pd.Timedelta(days=horizon)
                if actual_date not in revenue.index:
                    continue
                actual = float(revenue.loc[actual_date])
                for strategy, model_preds in strategy_preds.items():
                    for model_name, preds in model_preds.items():
                        if actual_date not in preds.index:
                            continue
                        pred = float(preds.loc[actual_date])
                        records.append(
                            {
                                "Cutoff": cutoff.date().isoformat(),
                                "ActualDate": actual_date.date().isoformat(),
                                "預測天期": horizon,
                                "策略": strategy,
                                "模型": model_name,
                                "Actual": actual,
                                "Prediction": pred,
                                "Error": pred - actual,
                                "AbsError": abs(pred - actual),
                                "APE": abs(pred - actual) / actual * 100 if actual else np.nan,
                                "SMAPE": (2 * abs(pred - actual) / (abs(actual) + abs(pred)) * 100) if (abs(actual) + abs(pred)) else np.nan,
                            }
                        )
                for model_name, preds in baseline_preds.items():
                    if actual_date not in preds.index:
                        continue
                    pred = float(preds.loc[actual_date])
                    records.append(
                        {
                            "Cutoff": cutoff.date().isoformat(),
                            "ActualDate": actual_date.date().isoformat(),
                            "預測天期": horizon,
                            "策略": STRATEGY_BASELINE,
                            "模型": model_name,
                            "Actual": actual,
                            "Prediction": pred,
                            "Error": pred - actual,
                            "AbsError": abs(pred - actual),
                            "APE": abs(pred - actual) / actual * 100 if actual else np.nan,
                            "SMAPE": (2 * abs(pred - actual) / (abs(actual) + abs(pred)) * 100) if (abs(actual) + abs(pred)) else np.nan,
                            }
                        )
                for model_name, preds in adjustment_preds.items():
                    if actual_date not in preds.index:
                        continue
                    pred = float(preds.loc[actual_date])
                    records.append(
                        {
                            "Cutoff": cutoff.date().isoformat(),
                            "ActualDate": actual_date.date().isoformat(),
                            "預測天期": horizon,
                            "策略": STRATEGY_DAILY_ADJUSTMENT,
                            "模型": model_name,
                            "Actual": actual,
                            "Prediction": pred,
                            "Error": pred - actual,
                            "AbsError": abs(pred - actual),
                            "APE": abs(pred - actual) / actual * 100 if actual else np.nan,
                            "SMAPE": (2 * abs(pred - actual) / (abs(actual) + abs(pred)) * 100) if (abs(actual) + abs(pred)) else np.nan,
                        }
                    )

        detail_df = pd.DataFrame(records)
        if detail_df.empty:
            return None, "沒有足夠已完成實際日可與回測預測值比較"

        daily_spike_signal_detail = _build_daily_spike_signal_detail(detail_df, df_tour, df_others)
        daily_spike_signal_summary = _summarize_spike_signal_detail(daily_spike_signal_detail)
        daily_spike_refinement_summary = _summarize_spike_signal_refinement(daily_spike_signal_detail)
        daily_event_lead_signal_detail = _build_daily_event_lead_signal_detail(
            daily_spike_signal_detail,
            df_tour,
            df_others,
        )
        daily_event_lead_signal_summary = _summarize_daily_event_lead_signal(
            daily_event_lead_signal_detail
        )
        spike_selector_records, daily_spike_selector_detail = _build_spike_aware_selector_records(
            detail_df,
            daily_spike_signal_detail,
        )
        if spike_selector_records:
            detail_df = pd.concat([detail_df, pd.DataFrame(spike_selector_records)], ignore_index=True)

        base_summary_df = _summarize_backtest_detail(detail_df)
        fusion_records = _build_fusion_records(detail_df, base_summary_df)
        if fusion_records:
            detail_df = pd.concat([detail_df, pd.DataFrame(fusion_records)], ignore_index=True)
        summary_df = _summarize_backtest_detail(detail_df)
        daily_diagnostics_df = _summarize_daily_diagnostics(detail_df)
        daily_robust_wape_df = _summarize_daily_robust_wape(detail_df)
        daily_spike_diagnostics = _summarize_daily_spike_diagnostics(detail_df, df_tour, df_others)
        daily_wape_baseline_df = _build_daily_wape_baseline(
            summary_df,
            daily_robust_wape_df,
            daily_spike_signal_summary,
        )
        daily_normal_day_experiment_detail = _build_daily_normal_day_experiment_detail(
            detail_df,
            daily_robust_wape_df,
            ts_data,
        )
        daily_normal_day_experiment_summary = _summarize_daily_normal_day_experiment(
            daily_normal_day_experiment_detail
        )
        daily_two_lane_selector_detail = _build_two_lane_selector_records(
            daily_event_lead_signal_detail,
            daily_normal_day_experiment_detail,
        )
        daily_two_lane_selector_summary = _summarize_two_lane_selector(
            daily_two_lane_selector_detail
        )

        strategy_rows = []
        for horizon in horizons:
            subset = base_summary_df[(base_summary_df["預測天期"] == horizon) & (base_summary_df["模型"].isin(["ARIMA", "Prophet", "LightGBM"]))]
            if subset.empty:
                continue
            for strategy in (STRATEGY_TOTAL, STRATEGY_SEGMENTED):
                strategy_subset = subset[subset["策略"] == strategy]
                if strategy_subset.empty:
                    continue
                best_row = strategy_subset.sort_values(["WAPE", "MAPE", "模型"]).iloc[0]
                strategy_rows.append(
                    {
                        "預測天期": horizon,
                        "策略": strategy,
                        "最佳模型": best_row["模型"],
                        "最佳WAPE": best_row["WAPE"],
                        "最佳MAPE": best_row["MAPE"],
                        "樣本數": best_row["樣本數"],
                    }
                )
        strategy_df = pd.DataFrame(strategy_rows)

        weight_rows = []
        for horizon in horizons:
            recommended = _recommended_strategy(base_summary_df, horizon, metric="WAPE")
            weights = _bounded_inverse_metric_weights(base_summary_df, horizon, strategy=recommended, metric="WAPE")
            sample_series = base_summary_df[
                (base_summary_df["預測天期"] == horizon)
                & (base_summary_df["策略"] == recommended)
                & (base_summary_df["模型"].isin(["ARIMA", "Prophet", "LightGBM"]))
            ]["樣本數"]
            sample_max = sample_series.max() if not sample_series.empty else 0
            sample_n = int(sample_max) if pd.notna(sample_max) else 0
            best_strategy_wape = np.nan
            if not strategy_df.empty:
                strategy_match = strategy_df[(strategy_df["預測天期"] == horizon) & (strategy_df["策略"] == recommended)]
                if not strategy_match.empty:
                    best_strategy_wape = float(strategy_match["最佳WAPE"].iloc[0])
            weight_rows.append(
                {
                    "權重版本": f"{horizon} 日預測",
                    "推薦策略": recommended,
                    "ARIMA": weights["ARIMA"] * 100,
                    "Prophet": weights["Prophet"] * 100,
                    "LightGBM": weights["LightGBM"] * 100,
                    "依據樣本數": sample_n,
                    "策略最佳WAPE": best_strategy_wape,
                }
            )

        return {
            "detail": detail_df,
            "summary": summary_df,
            "daily_diagnostics": daily_diagnostics_df,
            "daily_robust_wape": daily_robust_wape_df,
            "daily_wape_baseline": daily_wape_baseline_df,
            "daily_normal_day_experiment_detail": daily_normal_day_experiment_detail,
            "daily_normal_day_experiment_summary": daily_normal_day_experiment_summary,
            "daily_spike_summary": daily_spike_diagnostics["summary"],
            "daily_spike_contributors": daily_spike_diagnostics["contributors"],
            "daily_spike_signal_detail": daily_spike_signal_detail,
            "daily_spike_signal_summary": daily_spike_signal_summary,
            "daily_spike_refinement_summary": daily_spike_refinement_summary,
            "daily_event_lead_signal_detail": daily_event_lead_signal_detail,
            "daily_event_lead_signal_summary": daily_event_lead_signal_summary,
            "daily_two_lane_selector_detail": daily_two_lane_selector_detail,
            "daily_two_lane_selector_summary": daily_two_lane_selector_summary,
            "daily_spike_selector_detail": daily_spike_selector_detail,
            "weights": pd.DataFrame(weight_rows),
            "strategy_comparison": strategy_df,
        }, None
    except Exception as exc:
        return None, str(exc)
