"""Atomic, version-scoped derived artifacts for formal GMV exports."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app_workflows import OFFICIAL_EXPORT_SCHEMA_CONTRACT


CACHE_SCHEMA_VERSION = "gmv-formal-export-cache-v2"
LEGACY_CACHE_SCHEMA_VERSION = "gmv-formal-export-cache-v1"
CANONICAL_CACHE_ARTIFACT_KEYS = frozenset({
    "total.detail", "total.workbook.ex.xlsx", "total.workbook.ex_no_writeoff.xlsx",
    "total.workbook.ex_no_writeoff_refund_transfer.xlsx", "total.workbook.audit.xlsx",
    "paid.detail", "paid.workbook.ex.xlsx", "paid.workbook.ex_no_writeoff.xlsx",
    "paid.workbook.ex_no_writeoff_refund_transfer.xlsx", "paid.workbook.audit.xlsx", "summaries",
})


@dataclass(frozen=True, slots=True)
class GmvExportCacheManifest:
    cache_key: str
    version_id: str
    revenue_generation_token: str
    rule_version: str
    schema_version: str
    status: str
    artifacts: dict[str, dict[str, object]]
    build_duration_ms: int
    error: str | None
    builder_mode: str = "legacy"
    equivalence_status: str = "NOT_RUN"
    artifact_count: int = 0
    generation_path: str = ""
    content_fingerprint: str | None = None
    reference_id: str | None = None
    validation_mode: str = "legacy"
    shadow_status: str = "NOT_RUN"
    reference_manifest_sha256: str | None = None
    reference_status: str = "N/A"
    performance: dict[str, object] = field(default_factory=dict)
    fallback: dict[str, object] = field(default_factory=dict)
    refund_state_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "cacheKey": self.cache_key,
            "versionId": self.version_id,
            "revenueGenerationToken": self.revenue_generation_token,
            "ruleVersion": self.rule_version,
            "schemaVersion": self.schema_version,
            "status": self.status,
            "artifacts": self.artifacts,
            "buildDurationMs": self.build_duration_ms,
            "error": self.error,
            "builderMode": self.builder_mode,
            "equivalenceStatus": self.equivalence_status,
            "artifactCount": self.artifact_count,
            "generationPath": self.generation_path,
            "contentFingerprint": self.content_fingerprint,
            "referenceId": self.reference_id,
            "validationMode": self.validation_mode,
            "shadowStatus": self.shadow_status,
            "referenceManifestSha256": self.reference_manifest_sha256,
            "referenceStatus": self.reference_status,
            "performance": self.performance,
            "fallback": self.fallback,
            "refundStateSha256": self.refund_state_sha256,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "GmvExportCacheManifest":
        return cls(
            cache_key=str(payload["cacheKey"]),
            version_id=str(payload["versionId"]),
            revenue_generation_token=str(payload["revenueGenerationToken"]),
            rule_version=str(payload["ruleVersion"]),
            schema_version=str(payload["schemaVersion"]),
            status=str(payload["status"]),
            artifacts=dict(payload.get("artifacts") or {}),
            build_duration_ms=int(payload.get("buildDurationMs", 0)),
            error=payload.get("error") if payload.get("error") is None else str(payload["error"]),
            builder_mode=str(payload.get("builderMode", "legacy")),
            equivalence_status=str(payload.get("equivalenceStatus", "NOT_RUN")),
            artifact_count=int(payload.get("artifactCount", len(payload.get("artifacts") or {}))),
            generation_path=str(payload.get("generationPath", "")),
            content_fingerprint=(str(payload["contentFingerprint"]) if payload.get("contentFingerprint") else None),
            reference_id=(str(payload["referenceId"]) if payload.get("referenceId") else None),
            validation_mode=str(payload.get("validationMode", "legacy")),
            shadow_status=str(payload.get("shadowStatus", "NOT_RUN")),
            reference_manifest_sha256=(str(payload["referenceManifestSha256"]) if payload.get("referenceManifestSha256") else None),
            reference_status=str(payload.get("referenceStatus", "N/A")),
            performance=dict(payload.get("performance") or {}),
            fallback=dict(payload.get("fallback") or {}),
            refund_state_sha256=(str(payload["refundStateSha256"]) if payload.get("refundStateSha256") else None),
        )


def _validate_component(value: str, label: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or Path(value).name != value
    ):
        raise ValueError(f"unsafe {label}")
    return value


def gmv_export_cache_key(
    *, version_id: str, revenue_generation_token: str, rule_version: str,
) -> str:
    return _gmv_export_cache_key_for_schema(
        version_id=version_id, revenue_generation_token=revenue_generation_token,
        rule_version=rule_version, schema_version=CACHE_SCHEMA_VERSION,
    )


def _gmv_export_cache_key_for_schema(
    *, version_id: str, revenue_generation_token: str, rule_version: str, schema_version: str,
) -> str:
    payload = {
        "schemaVersion": schema_version,
        "officialExportSchema": OFFICIAL_EXPORT_SCHEMA_CONTRACT,
        "versionId": version_id,
        "revenueGenerationToken": revenue_generation_token,
        "ruleVersion": rule_version,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"gmv-formal-export-v1:{digest}"


def _cache_root(cache_dir: Path) -> Path:
    root = Path(cache_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _target_dir(cache_dir: Path, version_id: str, cache_key: str) -> Path:
    return _target_dir_for_cache_key(cache_dir, version_id, cache_key)


def _target_dir_for_cache_key(cache_dir: Path, version_id: str, cache_key: str) -> Path:
    root = _cache_root(cache_dir)
    safe_version = _validate_component(version_id, "version id")
    safe_key = _validate_component(cache_key.replace(":", "-"), "cache key")
    target = (root / safe_version / safe_key).resolve()
    target.relative_to(root)
    return target


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def _artifact_record(path: Path, kind: str) -> dict[str, object]:
    data = path.read_bytes()
    return {"kind": kind, "path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _write_manifest(path: Path, manifest: GmvExportCacheManifest) -> None:
    _atomic_write(path, (json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def _write_active_pointer(cache_dir: Path, version_id: str, manifest: GmvExportCacheManifest) -> None:
    """Atomically publish the generation selected by the read model."""
    root = _cache_root(cache_dir)
    version_root = root / _validate_component(version_id, "version id")
    pointer = {
        "schemaVersion": "gmv-formal-export-active-pointer-v1",
        "versionId": version_id,
        "cacheKey": manifest.cache_key,
        "generationPath": str(manifest.generation_path),
        "manifestPath": f"{manifest.generation_path}/manifest.json",
        "manifestSha256": hashlib.sha256(
            (json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        ).hexdigest(),
        "contentFingerprint": manifest.content_fingerprint,
        "referenceId": manifest.reference_id,
        "validationMode": manifest.validation_mode,
        "shadowStatus": manifest.shadow_status,
        "referenceStatus": manifest.reference_status,
    }
    _atomic_write(
        version_root / "active.json",
        (json.dumps(pointer, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def build_gmv_export_cache(
    *, version_id: str, revenue_generation_token: str, rule_version: str,
    total_workbooks: dict[str, bytes], paid_workbooks: dict[str, bytes],
    total_detail: pd.DataFrame, paid_detail: pd.DataFrame,
    summaries: list[dict[str, Any]], cache_dir: Path,
    builder_mode: str = "legacy",
    equivalence_status: str = "NOT_RUN",
    publish_active: bool = True,
    content_fingerprint: str | None = None,
    reference_id: str | None = None,
    validation_mode: str = "legacy",
    shadow_status: str = "NOT_RUN",
    reference_manifest_sha256: str | None = None,
    reference_status: str = "N/A",
    ready_error: str | None = None,
    performance: dict[str, object] | None = None,
    fallback: dict[str, object] | None = None,
    refund_state_sha256: str | None = None,
) -> GmvExportCacheManifest:
    cache_key = gmv_export_cache_key(
        version_id=version_id,
        revenue_generation_token=revenue_generation_token,
        rule_version=rule_version,
    )
    root = _cache_root(cache_dir)
    version_root = root / _validate_component(version_id, "version id")
    generation_path = Path("generations") / uuid.uuid4().hex
    target = (version_root / generation_path).resolve()
    target.relative_to(root)
    target.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    temp_dir = Path(tempfile.mkdtemp(prefix=".gmv-export-", dir=target.parent))
    artifacts: dict[str, dict[str, object]] = {}
    try:
        for dimension, workbooks, detail in (
            ("total", total_workbooks, total_detail),
            ("paid", paid_workbooks, paid_detail),
        ):
            detail_path = temp_dir / f"{dimension}-detail.csv"
            detail.to_csv(detail_path, index=False)
            final_detail = target / detail_path.name
            os.replace(detail_path, final_detail)
            artifacts[f"{dimension}.detail"] = _artifact_record(final_detail, "csv")
            for name, content in workbooks.items():
                safe_name = _validate_component(Path(name).name, "workbook name")
                if not safe_name.lower().endswith((".xlsx", ".xls")):
                    raise ValueError("workbook cache artifact must be XLSX")
                workbook_path = temp_dir / f"{dimension}-{safe_name}"
                workbook_path.write_bytes(content)
                final_workbook = target / workbook_path.name
                os.replace(workbook_path, final_workbook)
                artifacts[f"{dimension}.workbook.{safe_name}"] = _artifact_record(final_workbook, "xlsx")
        summaries_path = temp_dir / "summaries.json"
        summaries_path.write_text(json.dumps(summaries, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        final_summaries = target / summaries_path.name
        os.replace(summaries_path, final_summaries)
        artifacts["summaries"] = _artifact_record(final_summaries, "json")
        if set(artifacts) != CANONICAL_CACHE_ARTIFACT_KEYS:
            raise ValueError("GMV cache artifact contract is incomplete")
        manifest = GmvExportCacheManifest(
            cache_key, version_id, revenue_generation_token, rule_version,
            CACHE_SCHEMA_VERSION, "ready", artifacts, round((time.perf_counter() - started) * 1000), ready_error,
            builder_mode, equivalence_status, len(artifacts), str(generation_path),
            content_fingerprint, reference_id, validation_mode, shadow_status, reference_manifest_sha256,
            reference_status, performance or {}, fallback or {}, refund_state_sha256,
        )
        _write_manifest(target / "manifest.json", manifest)
        if publish_active:
            _write_active_pointer(cache_dir, version_id, manifest)
        return manifest
    except Exception as exc:
        manifest = GmvExportCacheManifest(
            cache_key, version_id, revenue_generation_token, rule_version,
            CACHE_SCHEMA_VERSION, "failed", {}, round((time.perf_counter() - started) * 1000),
            f"serialize_artifacts: {type(exc).__name__}: {exc}", builder_mode, equivalence_status, 0,
            content_fingerprint, reference_id, validation_mode, shadow_status, reference_manifest_sha256,
            reference_status, performance or {}, fallback or {}, refund_state_sha256,
        )
        _write_manifest(target / "manifest.json", manifest)
        return manifest
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def publish_gmv_export_cache_manifest(*, cache_dir: Path, manifest: GmvExportCacheManifest) -> None:
    """Publish an already-written ready generation without rebuilding artifacts."""
    if manifest.status != "ready" or manifest.artifact_count != len(manifest.artifacts):
        raise ValueError("only a complete ready GMV cache can become active")
    if set(manifest.artifacts) != CANONICAL_CACHE_ARTIFACT_KEYS:
        raise ValueError("GMV cache artifact contract is incomplete")
    root = _cache_root(cache_dir)
    version_root = root / _validate_component(manifest.version_id, "version id")
    generation_root = (version_root / manifest.generation_path).resolve()
    generation_root.relative_to(version_root.resolve())
    for record in manifest.artifacts.values():
        artifact_name = _validate_component(str(record.get("path", "")), "artifact name")
        artifact_path = (generation_root / artifact_name).resolve()
        artifact_path.relative_to(generation_root)
        if not artifact_path.is_file():
            raise ValueError(f"artifact file is missing: {artifact_name}")
        content = artifact_path.read_bytes()
        if len(content) != int(record.get("bytes", -1)):
            raise ValueError(f"artifact file size mismatch: {artifact_name}")
        if hashlib.sha256(content).hexdigest() != str(record.get("sha256", "")):
            raise ValueError(f"artifact file checksum mismatch: {artifact_name}")
    _write_active_pointer(cache_dir, manifest.version_id, manifest)


def load_gmv_export_cache(
    *, version_id: str, revenue_generation_token: str, rule_version: str,
    cache_dir: Path,
) -> GmvExportCacheManifest | None:
    cache_key = gmv_export_cache_key(
        version_id=version_id,
        revenue_generation_token=revenue_generation_token,
        rule_version=rule_version,
    )
    root = _cache_root(cache_dir)
    version_root = root / _validate_component(version_id, "version id")
    pointer_path = version_root / "active.json"
    target = _target_dir(cache_dir, version_id, cache_key)
    expected_cache_key = cache_key
    if pointer_path.is_file():
        # An existing pointer is the publication boundary.  If its integrity
        # check fails, fail closed instead of exposing a stale deterministic
        # generation; deterministic lookup is reserved for pointer-less v1
        # compatibility caches.
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            manifest_path_value = str(pointer.get("manifestPath"))
            pointer_manifest_path = (version_root / manifest_path_value).resolve()
            pointer_manifest_path.relative_to(version_root.resolve())
            generation_path_value = str(pointer.get("generationPath"))
            pointer_generation_path = (version_root / generation_path_value).resolve()
            pointer_generation_path.relative_to(version_root.resolve())
            if (
                pointer.get("schemaVersion") != "gmv-formal-export-active-pointer-v1"
                or str(pointer.get("versionId")) != version_id
                or str(pointer.get("cacheKey")) != cache_key
                or str(pointer.get("manifestPath")) != f"{generation_path_value}/manifest.json"
                or str(pointer.get("generationPath")) != generation_path_value
                or pointer_generation_path != pointer_manifest_path.parent
                or not pointer_manifest_path.is_file()
                or hashlib.sha256(pointer_manifest_path.read_bytes()).hexdigest() != str(pointer.get("manifestSha256"))
            ):
                return None
            target = pointer_generation_path
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
    elif not (target / "manifest.json").is_file():
        legacy_key = _gmv_export_cache_key_for_schema(
            version_id=version_id, revenue_generation_token=revenue_generation_token,
            rule_version=rule_version, schema_version=LEGACY_CACHE_SCHEMA_VERSION,
        )
        target = _target_dir_for_cache_key(cache_dir, version_id, legacy_key)
        expected_cache_key = legacy_key
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = GmvExportCacheManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return None
    if (
        manifest.status != "ready"
        or manifest.cache_key != expected_cache_key
        or manifest.version_id != version_id
        or manifest.revenue_generation_token != revenue_generation_token
        or manifest.rule_version != rule_version
        or manifest.schema_version not in {CACHE_SCHEMA_VERSION, LEGACY_CACHE_SCHEMA_VERSION}
        or manifest.artifact_count != len(manifest.artifacts)
        or set(manifest.artifacts) != CANONICAL_CACHE_ARTIFACT_KEYS
    ):
        return None
    try:
        for record in manifest.artifacts.values():
            path = target / str(record["path"])
            path.resolve().relative_to(target.resolve())
            data = path.read_bytes()
            if len(data) != int(record["bytes"]) or hashlib.sha256(data).hexdigest() != record["sha256"]:
                return None
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return manifest


def read_gmv_export_artifact(manifest: GmvExportCacheManifest, cache_dir: Path, artifact_key: str) -> bytes:
    """Read one already-validated artifact from a matching cache manifest."""
    record = manifest.artifacts[artifact_key]
    if manifest.generation_path:
        root = _cache_root(cache_dir)
        version_root = root / _validate_component(manifest.version_id, "version id")
        target = (version_root / manifest.generation_path).resolve()
        target.relative_to(version_root.resolve())
    else:
        target = _target_dir(cache_dir, manifest.version_id, manifest.cache_key)
    path = (target / str(record["path"])).resolve()
    path.relative_to(target.resolve())
    data = path.read_bytes()
    if len(data) != int(record["bytes"]) or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("GMV cache artifact integrity mismatch")
    return data
