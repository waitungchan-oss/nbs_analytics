"""Isolated, read-only benchmark contracts for GMV cache rebuilds."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from scripts.benchmark_gmv_refund_cache import (
    _validate_benchmark_cache_dir,
    run_gmv_cache_benchmark,
)


@dataclass(frozen=True, slots=True)
class GmvBenchmarkSample:
    mode: str
    workers: int
    total_ms: float
    stage_ms: Mapping[str, float]
    peak_rss_bytes: int | None
    artifact_fingerprints: Mapping[str, str]
    equivalence_status: str
    fallback_reason: str | None


_MODE_MAP = {
    "legacy-cold": "legacy",
    "fast-cold": "fast",
    "trusted-warm": "trusted_warm",
    "shadow": "shadow",
}


def run_gmv_rebuild_benchmark(
    *, db_path: str | Path, version_id: str,
    cache_dir: str | Path, mode: str, workers: int,
) -> GmvBenchmarkSample:
    """Run one benchmark in a caller-owned isolated directory."""
    if mode not in _MODE_MAP:
        raise ValueError(f"unsupported benchmark mode: {mode}")
    root = _validate_benchmark_cache_dir(cache_dir)
    actual_workers = 2 if mode == "legacy-cold" else workers
    result = run_gmv_cache_benchmark(
        db_path=db_path,
        version_id=version_id,
        cache_dir=root,
        mode=_MODE_MAP[mode],
        workers=actual_workers,
    )
    stage_ms = {
        key: float(result[key])
        for key in (
            "basePreparationMs", "adjustmentMs", "factsMs", "serializationMs",
            "equivalenceMs", "cacheWriteMs", "lookupMs", "candidateMs",
            "validationMs", "publishMs", "totalMs",
        )
        if key in result and result[key] is not None
    }
    equivalence_status = str(result.get("equivalenceStatus") or "NOT_RUN")
    fallback_reason = result.get("error")
    return GmvBenchmarkSample(
        mode=mode,
        workers=int(actual_workers),
        total_ms=float(result["totalMs"]),
        stage_ms=stage_ms,
        peak_rss_bytes=result.get("peakRssBytes"),
        artifact_fingerprints={
            str(key): str(value) for key, value in result.get("artifactFingerprints", {}).items()
        },
        equivalence_status=equivalence_status,
        fallback_reason=str(fallback_reason) if fallback_reason else None,
    )


def _nearest_rank_p95(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("cannot calculate p95 for empty samples")
    rank = max(1, math.ceil(len(values) * 0.95))
    return sorted(values)[rank - 1]


def run_gmv_rebuild_benchmark_suite(
    *, db_path: str | Path, version_id: str,
    cache_dir: str | Path, modes: Sequence[str], samples: int = 3,
    workers: int = 1,
) -> dict[str, object]:
    """Run repeatable samples without writing formal runtime cache or SQLite."""
    if samples < 3:
        raise ValueError("benchmark suite requires samples >= 3")
    root = _validate_benchmark_cache_dir(cache_dir)
    all_samples: dict[str, list[GmvBenchmarkSample]] = {}
    for mode in modes:
        if mode not in _MODE_MAP:
            raise ValueError(f"unsupported benchmark mode: {mode}")
        all_samples[mode] = [
            run_gmv_rebuild_benchmark(
                db_path=db_path, version_id=version_id,
                cache_dir=root / mode / f"sample-{index}",
                mode=mode, workers=workers,
            )
            for index in range(samples)
        ]

    rendered: dict[str, object] = {}
    for mode, entries in all_samples.items():
        totals = [entry.total_ms for entry in entries]
        successful = [entry for entry in entries if entry.equivalence_status not in {"FAIL", "FALLBACK"}]
        rendered[mode] = {
            "samples": [
                {
                    "mode": entry.mode,
                    "workers": entry.workers,
                    "totalMs": entry.total_ms,
                    "stageMs": dict(entry.stage_ms),
                    "peakRssBytes": entry.peak_rss_bytes,
                    "artifactFingerprints": dict(entry.artifact_fingerprints),
                    "equivalenceStatus": entry.equivalence_status,
                    "fallbackReason": entry.fallback_reason,
                }
                for entry in entries
            ],
            "medianMs": statistics.median(totals),
            "p95Ms": _nearest_rank_p95(totals),
            "equivalenceRate": len(successful) / len(entries),
        }
    return {
        "sampleCount": samples,
        "workers": workers,
        "modes": rendered,
        "cacheDir": str(root),
    }
