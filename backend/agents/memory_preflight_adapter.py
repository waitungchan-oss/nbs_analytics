from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_BYTES = 64 * 1024


def read_memory_observation(project_root: Path, *, session_source: str) -> dict[str, Any]:
    """Read a bounded Memory Hub/Sidecar observation without invoking a provider."""
    base = {"authority": "non_authoritative_memory"}
    if not isinstance(project_root, Path) or project_root.is_symlink() or not isinstance(session_source, str) or not _SHA256.fullmatch(session_source):
        return {**base, "status": "invalid_evidence", "diagnostics": [{"code": "invalid_source"}]}
    path = project_root / ".nbs_agent_runtime" / "memory-preflight-observation.json"
    try:
        if path.is_symlink() or not path.is_file():
            return {**base, "status": "unavailable", "diagnostics": [{"code": "observation_missing"}]}
        if path.stat().st_size > _MAX_BYTES:
            return {**base, "status": "invalid_evidence", "diagnostics": [{"code": "observation_over_cap"}]}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("sourceFingerprint") != session_source:
            return {**base, "status": "invalid_evidence", "diagnostics": [{"code": "source_mismatch"}]}
        if payload.get("schemaVersion") != "strict-review-memory-observation-v1" or payload.get("authority") != "non_authoritative_memory":
            return {**base, "status": "invalid_evidence", "diagnostics": [{"code": "schema_mismatch"}]}
        status = payload.get("status")
        if status not in {"ready", "degraded", "unavailable"}:
            return {**base, "status": "invalid_evidence", "diagnostics": [{"code": "status_invalid"}]}
        return {**base, "sourceFingerprint": session_source, "status": status, "diagnostics": _bounded(payload.get("diagnostics", [])), "hints": _bounded(payload.get("hints", []))}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {**base, "status": "invalid_evidence", "diagnostics": [{"code": "observation_invalid"}]}


def merge_non_authoritative_observations(preflight: dict, *, governance: dict | None, memory: dict | None) -> dict:
    if not isinstance(preflight, dict):
        raise ValueError("preflight must be an object")
    result = copy.deepcopy(preflight)
    result["observations"] = {"governance": copy.deepcopy(governance) if governance is not None else None, "memory": copy.deepcopy(memory) if memory is not None else None}
    return result


def _bounded(value: object) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item if not isinstance(item, str) else item[:512] for item in value[:8]]
