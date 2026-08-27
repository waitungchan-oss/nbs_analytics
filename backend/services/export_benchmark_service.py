"""Read-only timing helpers for comparing export implementations."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Mapping

import pandas as pd


LEGACY_EXPORT_KEYS = (
    "ex",
    "ex_no_writeoff",
    "ex_no_writeoff_refund_transfer",
)


@dataclass(frozen=True, slots=True)
class ExportArtifactMeasurement:
    key: str
    bytes_written: int


@dataclass(frozen=True, slots=True)
class ExportMeasurement:
    artifacts: Mapping[str, ExportArtifactMeasurement]
    timings: Mapping[str, int]


def measure_legacy_export(
    builder: Callable[[pd.DataFrame, pd.DataFrame], Mapping[str, object]],
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
) -> ExportMeasurement:
    """Measure the existing builder without changing its inputs or outputs."""
    started = time.perf_counter()
    payload = dict(builder(raw_tour.copy(deep=True), raw_others.copy(deep=True)))
    serialization_ms = round((time.perf_counter() - started) * 1000)
    artifacts = {}
    for key in LEGACY_EXPORT_KEYS:
        content = payload.get(key)
        if not isinstance(content, bytes):
            raise ValueError(f"legacy export artifact {key!r} is not bytes")
        artifacts[key] = ExportArtifactMeasurement(key=key, bytes_written=len(content))
    return ExportMeasurement(
        artifacts=artifacts,
        timings={
            "serialization_ms": serialization_ms,
            "total_ms": round((time.perf_counter() - started) * 1000),
        },
    )


__all__ = [
    "ExportArtifactMeasurement",
    "ExportMeasurement",
    "LEGACY_EXPORT_KEYS",
    "measure_legacy_export",
]
