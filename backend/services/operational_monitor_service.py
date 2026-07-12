from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path


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
        "latestDiagnosisSourceLabel",
    )
    return {key: value.get(key) for key in keys}


def compact_health_payload(health: dict) -> dict:
    acceptance = _compact_acceptance(health.get("latestAcceptance"))
    storage = health.get("storage") or {}
    backups = storage.get("backups") or {}
    quarantines = storage.get("quarantines") or {}
    cache = health.get("runtimeCache") or {}
    return {
        "status": health.get("status", "unknown"),
        "sqliteIntegrity": (health.get("db") or {}).get("integrity"),
        "sqliteIntegrityOk": bool((health.get("db") or {}).get("integrityOk")),
        "latestAcceptance": acceptance,
        "latestAcceptanceId": acceptance.get("id") if acceptance else None,
        "latestDataDate": acceptance.get("latestDataDate") if acceptance else None,
        "rollbackStatus": acceptance.get("rollbackStatus") if acceptance else None,
        "backupCount": int(backups.get("count", 0)),
        "backupBytes": int(backups.get("totalBytes", 0)),
        "quarantineCount": int(quarantines.get("count", 0)),
        "quarantineBytes": int(quarantines.get("totalBytes", 0)),
        "cacheFileCount": int(cache.get("fileCount", 0)),
        "cacheBytes": int(cache.get("totalBytes", 0)),
        "uploadCoordination": health.get("uploadCoordination") or {},
        "dataGeneration": health.get("dataGeneration") or {},
        "uploadEvidence": health.get("uploadEvidence") or {},
        "issues": [str(item) for item in health.get("issues", [])],
    }


def _probe_endpoint(url: str, timeout: float = 2.0) -> dict:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=timeout) as response:
        response.read(1)
        return {
            "ready": 200 <= response.status < 500,
            "statusCode": int(response.status),
            "responseMs": round((time.perf_counter() - started) * 1000, 2),
        }


def probe_endpoints(urls: dict[str, str]) -> dict:
    result = {}
    for name, url in urls.items():
        try:
            result[name] = _probe_endpoint(url)
        except Exception as exc:
            result[name] = {
                "ready": False,
                "statusCode": None,
                "responseMs": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result


def read_health_history(history_path: Path, limit: int = 50) -> list[dict]:
    if not history_path.exists():
        return []
    records = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return list(reversed(records[-max(1, int(limit)) :]))


def append_health_snapshot(
    health: dict,
    *,
    history_path: Path,
    endpoint_probes: dict | None = None,
    max_records: int = 288,
) -> dict:
    snapshot = {
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        **compact_health_payload(health),
        "endpoints": endpoint_probes or {},
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing = list(reversed(read_health_history(history_path, limit=max_records)))
    records = (existing + [snapshot])[-max(1, int(max_records)) :]
    temp_path = history_path.with_suffix(".tmp")
    temp_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in records),
        encoding="utf-8",
    )
    os.replace(temp_path, history_path)
    return snapshot
