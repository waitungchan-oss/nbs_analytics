from __future__ import annotations

from pathlib import Path

from database import validate_sqlite_database
from backend.services.backup_retention_service import DEFAULT_CAPACITY_WARNING_BYTES
from backend.services.cache_generation_service import load_cache_generation
from backend.services.operational_monitor_service import read_health_history
from backend.services.stability_history_service import list_stability_history
from backend.services.upload_lock_service import probe_upload_lease


def _diagnosis_source_label(value: dict | None) -> str | None:
    diagnosis = (value or {}).get("driftDiagnosis") or {}
    if not diagnosis.get("status"):
        return None
    created_at = value.get("createdAt") if value else None
    if not created_at:
        return None
    return f"Record #{value.get('id')} · {created_at}"


def _inventory(paths: list[Path]) -> dict:
    existing = [path for path in paths if path.is_file()]
    return {
        "count": len(existing),
        "totalBytes": sum(path.stat().st_size for path in existing),
        "latestPath": str(max(existing, key=lambda item: item.stat().st_mtime)) if existing else None,
    }


def _directory_inventory(path: Path) -> dict:
    files = [item for item in path.rglob("*") if item.is_file()] if path.exists() else []
    return {
        "path": str(path),
        "exists": path.exists(),
        "fileCount": len(files),
        "totalBytes": sum(item.stat().st_size for item in files),
    }


def _compact_acceptance(value: dict | None) -> dict | None:
    if not value:
        return None
    keys = (
        "id",
        "createdAt",
        "uploadStatus",
        "coreStatus",
        "freshnessStatus",
        "latestDataDate",
        "rollbackStatus",
        "rollbackError",
        "backupPath",
        "quarantinePath",
        "latestDiagnosisSourceLabel",
    )
    compact = {key: value.get(key) for key in keys}
    compact["latestDiagnosisSourceLabel"] = _diagnosis_source_label(value)
    return compact


def build_system_health(db_path: Path, cache_path: Path, runtime_dir: Path | None = None) -> dict:
    issues: list[str] = []
    db_exists = db_path.exists()
    integrity = validate_sqlite_database(db_path) if db_exists else {
        "ok": False,
        "integrity": "database missing",
    }
    if not integrity["ok"]:
        issues.append(f"SQLite integrity check failed: {integrity['integrity']}")

    try:
        history = list_stability_history(limit=20, db_path=db_path) if db_exists and integrity["ok"] else []
    except Exception as exc:
        history = []
        issues.append(f"Acceptance history unavailable: {type(exc).__name__}: {exc}")
    latest = _compact_acceptance(history[0]) if history else None
    if latest and latest.get("uploadStatus") == "rollback_failed":
        issues.append(f"Latest rollback failed: {latest.get('rollbackError') or 'unknown error'}")

    parent = db_path.parent
    backups = _inventory(list(parent.glob(f"{db_path.name}.backup_*")))
    quarantines = _inventory(list(parent.glob(f"{db_path.name}.quarantine_*")))
    backup_capacity_warning = backups["totalBytes"] > DEFAULT_CAPACITY_WARNING_BYTES
    if backup_capacity_warning:
        issues.append(
            f"Backup storage exceeds 3 GB: {backups['totalBytes']} bytes"
        )
    runtime_cache = _directory_inventory(cache_path)
    if not runtime_cache["exists"]:
        issues.append("Runtime cache directory is missing")

    status = "ok"
    if not integrity["ok"] or (latest and latest.get("uploadStatus") == "rollback_failed"):
        status = "critical"
    elif issues:
        status = "degraded"

    runtime_dir = runtime_dir or parent / ".nbs_runtime"
    coordination = probe_upload_lease(runtime_dir / "upload_coordination.db")
    generation = load_cache_generation(runtime_dir / "data_generation.json", db_path=db_path)
    operation_id = generation.get("operationId")
    matching_history = next((row for row in history if row.get("operationId") == operation_id), None)
    upload_evidence = {
        "generationOperationId": operation_id,
        "historyRecordId": matching_history.get("id") if matching_history else None,
        "matched": None if not operation_id else matching_history is not None,
    }
    if operation_id and not matching_history:
        issues.append(f"Upload evidence missing for generation operation: {operation_id}")
    if matching_history and matching_history.get("cacheError"):
        issues.append(f"Upload cache generation error: {matching_history['cacheError']}")
    if not generation.get("currentDbSignature"):
        issues.append("Current database signature is unavailable")
    elif generation.get("generation", 0) and not generation.get("signatureMatched"):
        issues.append("Cache generation signature does not match current database")
    if not integrity["ok"] or (latest and latest.get("uploadStatus") == "rollback_failed"):
        status = "critical"
    elif issues:
        status = "degraded"
    operational_history = read_health_history(runtime_dir / "health_history.jsonl", limit=20)
    return {
        "status": status,
        "service": "nbs-analytics-api",
        "issues": issues,
        "db": {
            "path": str(db_path),
            "exists": db_exists,
            "sizeBytes": db_path.stat().st_size if db_exists else 0,
            "integrity": integrity["integrity"],
            "integrityOk": bool(integrity["ok"]),
        },
        "latestAcceptance": latest,
        "storage": {
            "backups": {
                **backups,
                "capacityWarning": backup_capacity_warning,
                "capacityWarningBytes": DEFAULT_CAPACITY_WARNING_BYTES,
            },
            "quarantines": quarantines,
        },
        "runtimeCache": runtime_cache,
        "uploadCoordination": coordination,
        "dataGeneration": generation,
        "uploadEvidence": upload_evidence,
        "operationalHistory": operational_history,
    }
