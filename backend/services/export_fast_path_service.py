"""Fast export controller with semantic gate and legacy fail-closed fallback."""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

import pandas as pd

from .export_equivalence_service import compare_export_sets
from .export_intermediate_service import build_export_intermediate
from .export_manifest_service import ExportArtifact, publish_export_manifest


EXPORT_KEYS = ("ex", "ex_no_writeoff", "ex_no_writeoff_refund_transfer")
SCOPE_IDS = ("all", "no_writeoff", "official")


class ExportRolloutMode(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    OPT_IN = "opt_in"
    DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class ExportJobResult:
    job_id: str
    status: str
    manifest_path: Path | None
    fallback_reason: str | None
    timings: Mapping[str, int]


def _serialize_scope(builder: Callable, scope_id: str, intermediate) -> tuple[str, bytes]:
    return scope_id, builder(scope_id, intermediate)


def _bounded_reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:180]}"


def build_fast_export_job(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    generation_token: str,
    rules_fingerprint: str,
    export_schema_version: str,
    cache_root: Path,
    reference_builder: Callable[[pd.DataFrame, pd.DataFrame], Mapping[str, bytes]],
    candidate_builder: Callable[[str, object], bytes],
    worker_count: int = 3,
) -> ExportJobResult:
    started = time.perf_counter()
    job_id = str(generation_token)
    try:
        intermediate_started = time.perf_counter()
        intermediate = build_export_intermediate(
            raw_tour,
            raw_others,
            generation_token=generation_token,
            rules_fingerprint=rules_fingerprint,
            schema_version=export_schema_version,
        )
        intermediate_ms = round((time.perf_counter() - intermediate_started) * 1000)

        reference_started = time.perf_counter()
        reference = dict(reference_builder(raw_tour.copy(deep=True), raw_others.copy(deep=True)))
        reference_ms = round((time.perf_counter() - reference_started) * 1000)
        if set(reference) != set(EXPORT_KEYS) or not all(isinstance(value, bytes) and value for value in reference.values()):
            raise ValueError("legacy reference export contract invalid")

        candidate_started = time.perf_counter()
        candidates: dict[str, bytes] = {}
        if worker_count <= 1:
            for scope_id in SCOPE_IDS:
                key, content = _serialize_scope(candidate_builder, scope_id, intermediate)
                candidates[key] = content
        else:
            with ProcessPoolExecutor(max_workers=min(int(worker_count), len(SCOPE_IDS))) as pool:
                futures = [pool.submit(_serialize_scope, candidate_builder, scope_id, intermediate) for scope_id in SCOPE_IDS]
                for future in as_completed(futures):
                    key, content = future.result()
                    candidates[key] = content
        candidate_ms = round((time.perf_counter() - candidate_started) * 1000)
        candidates = {
            "ex": candidates["all"],
            "ex_no_writeoff": candidates["no_writeoff"],
            "ex_no_writeoff_refund_transfer": candidates["official"],
        }

        equivalence_started = time.perf_counter()
        equivalence = compare_export_sets(reference, candidates)
        equivalence_ms = round((time.perf_counter() - equivalence_started) * 1000)
        if equivalence.status != "PASS":
            raise ValueError(f"semantic equivalence failed: {equivalence.mismatch_count} mismatch(es)")

        artifacts = {
            key: ExportArtifact(key, f"{key}.xlsx", content)
            for key, content in candidates.items()
        }
        manifest_path = publish_export_manifest(
            Path(cache_root),
            generation_token=generation_token,
            rules_fingerprint=rules_fingerprint,
            export_schema_version=export_schema_version,
            artifacts=artifacts,
            equivalence_status="PASS",
        )
        return ExportJobResult(
            job_id=job_id,
            status="READY",
            manifest_path=manifest_path,
            fallback_reason=None,
            timings={
                "intermediate_ms": intermediate_ms,
                "reference_ms": reference_ms,
                "candidate_ms": candidate_ms,
                "equivalence_ms": equivalence_ms,
                "total_ms": round((time.perf_counter() - started) * 1000),
            },
        )
    except Exception as exc:
        return ExportJobResult(
            job_id=job_id,
            status="FALLBACK",
            manifest_path=None,
            fallback_reason=_bounded_reason(exc),
            timings={"total_ms": round((time.perf_counter() - started) * 1000)},
        )


def select_export_path(mode: ExportRolloutMode, *, fast_ready: bool) -> str:
    if not isinstance(mode, ExportRolloutMode):
        mode = ExportRolloutMode(str(mode))
    if mode in (ExportRolloutMode.OPT_IN, ExportRolloutMode.DEFAULT) and fast_ready:
        return "fast"
    return "legacy"


__all__ = [
    "EXPORT_KEYS",
    "ExportJobResult",
    "ExportRolloutMode",
    "build_fast_export_job",
    "select_export_path",
]
