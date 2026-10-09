from __future__ import annotations

import os
import pickle
from pathlib import Path

import pandas as pd

from pipeline import COL_BRANCH, COL_ORDER_ID, process_raw_files
from rules import (
    BETA_ONLY_EXCEPTION_PREFIXES,
    BETA_ONLY_EXCEPTION_SALES_POINTS,
    BETA_ONLY_EXCEPTION_VERSION,
)


def _default_cache_path() -> Path:
    cache_root = Path(os.environ.get("NBS_ANALYTICS_CACHE_DIR", ".nbs_runtime_cache"))
    return cache_root / "beta_only_exception.pkl"


def _rewind(value) -> None:
    if value is not None and hasattr(value, "seek"):
        value.seek(0)


def _select_sales_points(frame: pd.DataFrame, sales_points) -> pd.DataFrame:
    if frame.empty or COL_BRANCH not in frame.columns:
        return frame.iloc[0:0].copy()
    targets = {str(value).strip() for value in sales_points if str(value).strip()}
    mask = frame[COL_BRANCH].astype(str).str.strip().isin(targets)
    return frame.loc[mask].copy()


def _select_exception_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or COL_ORDER_ID not in frame.columns:
        return frame.iloc[0:0].copy()
    prefixes = tuple(
        str(value).strip() for value in BETA_ONLY_EXCEPTION_PREFIXES if str(value).strip()
    )
    if not prefixes:
        return frame.iloc[0:0].copy()
    order_ids = frame[COL_ORDER_ID].fillna("").astype(str).str.strip()
    return _select_sales_points(
        frame.loc[order_ids.str.startswith(prefixes)], BETA_ONLY_EXCEPTION_SALES_POINTS
    )


def build_beta_only_exception_frames(
    main_file,
    tour_file,
    other_files,
    branch_mapping: dict,
    exclude_prefixes: list[str],
    sales_rep_list: list[str],
    process_runner=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rebuild only the approved Beta exception rows without changing formal inputs."""
    allowed = {str(value).strip() for value in BETA_ONLY_EXCEPTION_PREFIXES if str(value).strip()}
    remaining_excludes = [
        str(value).strip()
        for value in exclude_prefixes
        if str(value).strip() and str(value).strip() not in allowed
    ]
    _rewind(main_file)
    _rewind(tour_file)
    for item in other_files or []:
        _rewind(item)
    runner = process_runner or process_raw_files
    reopened_tour, reopened_others, _, _ = runner(
        main_file,
        tour_file,
        other_files or [],
        branch_mapping,
        remaining_excludes,
        sales_rep_list,
        return_entity_audit=True,
    )
    return (
        _select_exception_rows(reopened_tour),
        _select_exception_rows(reopened_others),
    )


def _cache_key(frame: pd.DataFrame) -> pd.Series:
    for column in ("收款單號", "來源單據號"):
        if column in frame.columns:
            values = frame[column].fillna("").astype(str).str.strip()
            if values.ne("").any():
                return values
    if frame.empty:
        return pd.Series(dtype="string")
    return pd.Series(pd.util.hash_pandas_object(frame, index=False).astype(str), index=frame.index)


def _merge_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty and incoming.empty:
        return pd.DataFrame()
    merged = pd.concat([existing, incoming], ignore_index=True, sort=False)
    keys = _cache_key(merged)
    return merged.assign(_beta_exception_key=keys).drop_duplicates(
        subset=["_beta_exception_key"], keep="last"
    ).drop(columns=["_beta_exception_key"]).reset_index(drop=True)


def load_beta_only_exception_frames(
    *, cache_path: str | Path | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    if not path.exists():
        return pd.DataFrame(), pd.DataFrame()
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
        if payload.get("version") != BETA_ONLY_EXCEPTION_VERSION:
            return pd.DataFrame(), pd.DataFrame()
        tour = payload.get("tour")
        others = payload.get("others")
        return (
            _select_exception_rows(tour) if isinstance(tour, pd.DataFrame) else pd.DataFrame(),
            _select_exception_rows(others) if isinstance(others, pd.DataFrame) else pd.DataFrame(),
        )
    except Exception:
        return pd.DataFrame(), pd.DataFrame()


def persist_beta_only_exception_frames(
    tour: pd.DataFrame,
    others: pd.DataFrame,
    *,
    cache_path: str | Path | None = None,
) -> dict:
    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    existing_tour, existing_others = load_beta_only_exception_frames(cache_path=path)
    merged_tour = _merge_frames(existing_tour, _select_exception_rows(tour))
    merged_others = _merge_frames(existing_others, _select_exception_rows(others))
    if merged_tour.empty and merged_others.empty:
        return {"status": "empty", "tourRows": 0, "othersRows": 0, "path": str(path)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as handle:
        pickle.dump(
            {"version": BETA_ONLY_EXCEPTION_VERSION, "tour": merged_tour, "others": merged_others},
            handle,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    temporary.replace(path)
    return {
        "status": "saved",
        "tourRows": int(len(merged_tour)),
        "othersRows": int(len(merged_others)),
        "path": str(path),
    }


def clear_beta_only_exception_cache(*, cache_path: str | Path | None = None) -> None:
    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    if path.exists():
        path.unlink()
