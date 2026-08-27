"""Bounded, atomic serializer jobs for regular Data Export artifacts."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
from typing import Callable, Sequence

from .export_intermediate_service import DashboardReportFacts


@dataclass(frozen=True, slots=True)
class ExportSerializerJob:
    artifact_id: str
    scope_id: str
    facts: DashboardReportFacts
    target_path: Path
    schema_fingerprint: str
    data_fingerprint: str


@dataclass(frozen=True, slots=True)
class SerializerResult:
    artifact_id: str
    status: str
    path: Path
    bytes_written: int
    duration_ms: int
    error: str | None = None


def _failure(job: ExportSerializerJob, status: str, started: float, error: Exception) -> SerializerResult:
    return SerializerResult(
        artifact_id=job.artifact_id,
        status=status,
        path=Path(job.target_path),
        bytes_written=0,
        duration_ms=round((time.perf_counter() - started) * 1000),
        error=f"{type(error).__name__}: {str(error)[:180]}",
    )


def _serialize_one(
    job: ExportSerializerJob,
    writer: Callable[[DashboardReportFacts, Path], None],
) -> SerializerResult:
    started = time.perf_counter()
    target = Path(job.target_path)
    if target.suffix.lower() != ".xlsx":
        return _failure(job, "SERIALIZER_INVALID_ARTIFACT", started, ValueError("export artifact must be .xlsx"))
    if not job.schema_fingerprint or not job.data_fingerprint:
        return _failure(job, "SERIALIZER_INVALID_FACTS", started, ValueError("facts require schema and data fingerprints"))
    temporary_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        writer(job.facts, temporary_path)
        if not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
            raise ValueError("serializer produced an empty artifact")
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
        return SerializerResult(
            artifact_id=job.artifact_id,
            status="READY",
            path=target,
            bytes_written=target.stat().st_size,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        return _failure(job, "SERIALIZER_EXCEPTION", started, exc)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def serialize_export_jobs_parallel(
    jobs: Sequence[ExportSerializerJob],
    *,
    writer: Callable[[DashboardReportFacts, Path], None],
    max_workers: int = 3,
    timeout_seconds: float | None = None,
) -> tuple[SerializerResult, ...]:
    """Serialize bounded jobs concurrently and return results in input order."""
    ordered_jobs = tuple(jobs)
    if max_workers < 1 or max_workers > 3:
        raise ValueError("max_workers must be between 1 and 3")
    artifact_ids = [job.artifact_id for job in ordered_jobs]
    target_paths = [Path(job.target_path).resolve() for job in ordered_jobs]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError("artifact_id values must be unique")
    if len(target_paths) != len(set(target_paths)):
        raise ValueError("target paths must be unique")
    if not ordered_jobs:
        return ()

    results: dict[str, SerializerResult] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(ordered_jobs)), thread_name_prefix="export-serializer") as pool:
        futures = {pool.submit(_serialize_one, job, writer): job for job in ordered_jobs}
        try:
            iterator = as_completed(futures, timeout=timeout_seconds)
            for future in iterator:
                job = futures[future]
                try:
                    results[job.artifact_id] = future.result()
                except Exception as exc:
                    results[job.artifact_id] = _failure(job, "SERIALIZER_EXCEPTION", time.perf_counter(), exc)
        except FutureTimeoutError as exc:
            for future, job in futures.items():
                if job.artifact_id not in results:
                    future.cancel()
                    results[job.artifact_id] = _failure(job, "SERIALIZER_TIMEOUT", time.perf_counter(), exc)
    return tuple(results[job.artifact_id] for job in ordered_jobs)


__all__ = ["ExportSerializerJob", "SerializerResult", "serialize_export_jobs_parallel"]
