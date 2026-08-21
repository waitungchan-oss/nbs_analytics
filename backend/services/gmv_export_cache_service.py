"""Atomic, version-scoped derived artifacts for formal GMV exports."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app_workflows import OFFICIAL_EXPORT_SCHEMA_CONTRACT


CACHE_SCHEMA_VERSION = "gmv-formal-export-cache-v1"


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
    payload = {
        "schemaVersion": CACHE_SCHEMA_VERSION,
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


def build_gmv_export_cache(
    *, version_id: str, revenue_generation_token: str, rule_version: str,
    total_workbooks: dict[str, bytes], paid_workbooks: dict[str, bytes],
    total_detail: pd.DataFrame, paid_detail: pd.DataFrame,
    summaries: list[dict[str, Any]], cache_dir: Path,
) -> GmvExportCacheManifest:
    cache_key = gmv_export_cache_key(
        version_id=version_id,
        revenue_generation_token=revenue_generation_token,
        rule_version=rule_version,
    )
    target = _target_dir(cache_dir, version_id, cache_key)
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
        manifest = GmvExportCacheManifest(
            cache_key, version_id, revenue_generation_token, rule_version,
            CACHE_SCHEMA_VERSION, "ready", artifacts, round((time.perf_counter() - started) * 1000), None,
        )
        _write_manifest(target / "manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest = GmvExportCacheManifest(
            cache_key, version_id, revenue_generation_token, rule_version,
            CACHE_SCHEMA_VERSION, "failed", {}, round((time.perf_counter() - started) * 1000),
            f"serialize_artifacts: {type(exc).__name__}: {exc}",
        )
        _write_manifest(target / "manifest.json", manifest)
        return manifest
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def load_gmv_export_cache(
    *, version_id: str, revenue_generation_token: str, rule_version: str,
    cache_dir: Path,
) -> GmvExportCacheManifest | None:
    cache_key = gmv_export_cache_key(
        version_id=version_id,
        revenue_generation_token=revenue_generation_token,
        rule_version=rule_version,
    )
    target = _target_dir(cache_dir, version_id, cache_key)
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = GmvExportCacheManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        return None
    if (
        manifest.status != "ready"
        or manifest.cache_key != cache_key
        or manifest.version_id != version_id
        or manifest.revenue_generation_token != revenue_generation_token
        or manifest.rule_version != rule_version
        or manifest.schema_version != CACHE_SCHEMA_VERSION
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
