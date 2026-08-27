"""Fast export controller with semantic gate and legacy fail-closed fallback."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import shutil
from typing import Callable, Mapping

import pandas as pd

from .export_equivalence_service import (
    build_workbook_metric_digest,
    compare_export_digests,
    compare_export_sets,
)
from .export_intermediate_service import ExportScope, build_export_intermediate
from .export_manifest_service import ExportArtifact, publish_export_manifest
from .export_reference_cache_service import (
    TrustedReferenceIdentity,
    load_trusted_reference,
    materialize_trusted_reference,
    publish_trusted_reference,
    trusted_reference_identity_fingerprint,
)
from .export_serializer_service import ExportSerializerJob, serialize_export_jobs_parallel


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


@dataclass(frozen=True, slots=True)
class ReferenceRolloutDecision:
    mode: str
    reason: str


def decide_reference_rollout(evidence: Mapping[str, object]) -> ReferenceRolloutDecision:
    """Apply a bounded, fail-closed gate to trusted-reference promotion."""
    requested = str(evidence.get("requested_mode", ExportRolloutMode.SHADOW.value)).lower()
    allowed = {item.value for item in ExportRolloutMode}
    if requested not in allowed:
        return ReferenceRolloutDecision(ExportRolloutMode.SHADOW.value, "INVALID_REQUESTED_MODE")
    if requested == ExportRolloutMode.DISABLED.value:
        return ReferenceRolloutDecision(ExportRolloutMode.DISABLED.value, "DISABLED_BY_CONFIGURATION")
    if requested == ExportRolloutMode.SHADOW.value:
        return ReferenceRolloutDecision(ExportRolloutMode.SHADOW.value, "SHADOW_BY_CONFIGURATION")

    required = {
        "equivalence_status": "PASS",
        "baseline_status": "PASS",
        "database_mutated": False,
        "stale_count": 0,
        "corrupt_count": 0,
        "fallback_count": 0,
    }
    for key, expected in required.items():
        if evidence.get(key) != expected:
            return ReferenceRolloutDecision(ExportRolloutMode.SHADOW.value, f"{key.upper()}_GATE_FAILED")
    if evidence.get("reference_status") not in {"HIT", "MATERIALIZED"}:
        return ReferenceRolloutDecision(ExportRolloutMode.SHADOW.value, "REFERENCE_STATUS_INVALID")
    hit = evidence.get("same_identity_hit")
    if not isinstance(hit, Mapping) or hit.get("reference_status") != "HIT" or hit.get("equivalence_status") != "PASS":
        return ReferenceRolloutDecision(ExportRolloutMode.SHADOW.value, "CACHE_HIT_BENCHMARK_FAILED")
    if requested == ExportRolloutMode.DEFAULT.value:
        return ReferenceRolloutDecision(ExportRolloutMode.DEFAULT.value, "ALL_REFERENCE_GATES_PASSED")
    return ReferenceRolloutDecision(ExportRolloutMode.OPT_IN.value, "OPT_IN_REFERENCE_GATES_PASSED")


def _serialize_scope(builder: Callable, scope_id: str, intermediate) -> tuple[str, bytes]:
    return scope_id, builder(scope_id, intermediate)


def _bounded_reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:180]}"


def _source_fingerprint(intermediate) -> str:
    encoded = json.dumps(
        dict(sorted(intermediate.source_fingerprints.items())),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    baseline_status: str = "PASS",
    pipeline_fingerprint: str = "export-pipeline-v1",
) -> ExportJobResult:
    started = time.perf_counter()
    job_id = str(generation_token)
    try:
        if baseline_status != "PASS":
            raise ValueError("baseline status is not PASS; fast export publication blocked")
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
        reference_payload = dict(reference_builder(raw_tour.copy(deep=True), raw_others.copy(deep=True)))
        reference_ms = round((time.perf_counter() - reference_started) * 1000)
        reference = {key: reference_payload.get(key) for key in EXPORT_KEYS}
        if not all(isinstance(value, bytes) and value for value in reference.values()):
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


def build_fast_export_job_from_facts(
    raw_tour: pd.DataFrame,
    raw_others: pd.DataFrame,
    *,
    generation_token: str,
    rules_fingerprint: str,
    export_schema_version: str,
    cache_root: Path,
    reference_builder: Callable[[pd.DataFrame, pd.DataFrame], Mapping[str, bytes]],
    facts_builder: Callable[[object], Mapping[str, object]],
    writer: Callable[[object, Path], None],
    worker_count: int = 3,
    baseline_status: str = "PASS",
    pipeline_fingerprint: str = "export-pipeline-v1",
) -> ExportJobResult:
    """Build complete export artifacts from one shared facts preparation."""
    started = time.perf_counter()
    staging_dir: Path | None = None
    try:
        if baseline_status != "PASS":
            raise ValueError("baseline status is not PASS; fast export publication blocked")
        intermediate_started = time.perf_counter()
        intermediate = build_export_intermediate(
            raw_tour,
            raw_others,
            generation_token=generation_token,
            rules_fingerprint=rules_fingerprint,
            schema_version=export_schema_version,
        )
        facts_started = time.perf_counter()
        facts_by_scope = dict(facts_builder(intermediate))
        expected_scopes = {scope.value for scope in ExportScope}
        if set(facts_by_scope) != expected_scopes:
            raise ValueError("facts builder must return all export scopes")
        intermediate_ms = round((time.perf_counter() - intermediate_started) * 1000)
        facts_ms = round((time.perf_counter() - facts_started) * 1000)

        staging_dir = Path(cache_root) / ".staging"
        jobs = tuple(
            ExportSerializerJob(
                artifact_id=f"{key}.xlsx",
                scope_id=scope_id,
                facts=facts_by_scope[scope_id],
                target_path=staging_dir / f"{key}.xlsx",
                schema_fingerprint=facts_by_scope[scope_id].schema_fingerprint,
                data_fingerprint=facts_by_scope[scope_id].data_fingerprint,
            )
            for key, scope_id in (
                ("ex", "all"),
                ("ex_no_writeoff", "no_writeoff"),
                ("ex_no_writeoff_refund_transfer", "official"),
            )
        )
        serialization_started = time.perf_counter()
        serializer_results = serialize_export_jobs_parallel(
            jobs, writer=writer, max_workers=worker_count,
        )
        serialization_ms = round((time.perf_counter() - serialization_started) * 1000)
        if not all(result.status == "READY" for result in serializer_results):
            failed = next(result for result in serializer_results if result.status != "READY")
            raise ValueError(f"serializer failed for {failed.artifact_id}: {failed.error}")
        candidates = {
            key: (staging_dir / f"{key}.xlsx").read_bytes()
            for key in EXPORT_KEYS
        }
        identity = TrustedReferenceIdentity(
            source_fingerprint=_source_fingerprint(intermediate),
            generation_token=str(generation_token),
            rules_fingerprint=str(rules_fingerprint),
            export_schema_version=str(export_schema_version),
            pipeline_fingerprint=str(pipeline_fingerprint),
        )
        reference_lookup_started = time.perf_counter()
        trusted_reference = load_trusted_reference(Path(cache_root), identity)
        reference_lookup_ms = round((time.perf_counter() - reference_lookup_started) * 1000)
        candidate_digests = {
            key: build_workbook_metric_digest(value)
            for key, value in candidates.items()
        }
        reference_status = "HIT" if trusted_reference is not None else "MATERIALIZED"
        deep_diff_skipped = False
        reference_materialize_ms = 0
        equivalence_deep_diff_ms = 0
        equivalence_digest_started = time.perf_counter()
        digest_matches = (
            trusted_reference is not None
            and compare_export_digests(trusted_reference.artifact_digests, candidate_digests)
        )
        equivalence_digest_ms = round((time.perf_counter() - equivalence_digest_started) * 1000)
        if digest_matches:
            equivalence_status = "PASS"
            equivalence_mismatch_count = 0
            equivalence_examples = []
            deep_diff_skipped = True
        else:
            reference_started = time.perf_counter()
            reference_payload = dict(reference_builder(raw_tour.copy(deep=True), raw_others.copy(deep=True)))
            reference_materialize_ms = round((time.perf_counter() - reference_started) * 1000)
            reference = {key: reference_payload.get(key) for key in EXPORT_KEYS}
            if not all(isinstance(value, bytes) and value for value in reference.values()):
                raise ValueError("legacy reference export contract invalid")
            equivalence_started = time.perf_counter()
            equivalence = compare_export_sets(reference, candidates)
            equivalence_deep_diff_ms = round((time.perf_counter() - equivalence_started) * 1000)
            equivalence_status = equivalence.status
            equivalence_mismatch_count = equivalence.mismatch_count
            equivalence_examples = list(equivalence.mismatch_examples)
        if equivalence_status != "PASS":
            raise ValueError(f"semantic equivalence failed: {equivalence_mismatch_count} mismatch(es)")
        snapshot = None
        if not digest_matches:
            snapshot = materialize_trusted_reference(
                Path(cache_root), identity, reference,
                artifact_digests=candidate_digests,
            )
        manifest_path = publish_export_manifest(
            Path(cache_root), generation_token=generation_token,
            rules_fingerprint=rules_fingerprint,
            export_schema_version=export_schema_version,
            artifacts={key: ExportArtifact(key, f"{key}.xlsx", content) for key, content in candidates.items()},
            equivalence_status="PASS",
            telemetry={
                "intermediate_ms": intermediate_ms,
                "facts_ms": facts_ms,
                "serialization_ms": {
                    result.artifact_id: result.duration_ms for result in serializer_results
                },
                "reference_lookup_ms": reference_lookup_ms,
                "reference_materialize_ms": reference_materialize_ms,
                "equivalence_digest_ms": equivalence_digest_ms,
                "equivalence_deep_diff_ms": equivalence_deep_diff_ms if not digest_matches else 0,
                "worker_count": min(int(worker_count), len(jobs)),
            },
            equivalence_report={
                "status": equivalence_status,
                "mismatch_count": equivalence_mismatch_count,
                "mismatch_examples": equivalence_examples,
                "deep_diff_skipped": deep_diff_skipped,
            },
            reference={
                "status": reference_status,
                "identity_fingerprint": trusted_reference_identity_fingerprint(identity),
                "deep_diff_skipped": deep_diff_skipped,
            },
        )
        # The active reference pointer is published only after the export
        # manifest has reached READY. This keeps reference provenance bound to
        # a verified package when manifest publication fails.
        if snapshot is not None:
            publish_trusted_reference(Path(cache_root), snapshot)
        return ExportJobResult(
            job_id=str(generation_token), status="READY", manifest_path=manifest_path,
            fallback_reason=None,
            timings={
                "intermediate_ms": intermediate_ms,
                "facts_ms": facts_ms,
                "serialization_ms": serialization_ms,
                "reference_lookup_ms": reference_lookup_ms,
                "reference_materialize_ms": reference_materialize_ms,
                "equivalence_digest_ms": equivalence_digest_ms,
                "equivalence_deep_diff_ms": equivalence_deep_diff_ms if not digest_matches else 0,
                "total_ms": round((time.perf_counter() - started) * 1000),
            },
        )
    except Exception as exc:
        return ExportJobResult(
            job_id=str(generation_token), status="FALLBACK", manifest_path=None,
            fallback_reason=_bounded_reason(exc),
            timings={"total_ms": round((time.perf_counter() - started) * 1000)},
        )
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


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
    "ReferenceRolloutDecision",
    "build_fast_export_job",
    "build_fast_export_job_from_facts",
    "select_export_path",
    "decide_reference_rollout",
]
