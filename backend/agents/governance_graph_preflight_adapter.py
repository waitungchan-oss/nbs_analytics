from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_BYTES = 64 * 1024
_MAX_DIAGNOSTICS = 8
_MAX_TEXT = 512


def read_governance_observation(project_root: Path, *, session_source: str) -> dict[str, Any]:
    """Read an optional graph observation; never build or persist a graph."""
    if not isinstance(project_root, Path) or project_root.is_symlink() or not isinstance(session_source, str) or not _SHA256.fullmatch(session_source):
        return _result("invalid_evidence", "invalid_source")
    path = project_root / ".nbs_agent_runtime" / "governance-graph-observation.json"
    try:
        if path.is_symlink() or not path.is_file():
            return _result("unavailable", "observation_missing")
        if path.stat().st_size > _MAX_BYTES:
            return _result("invalid_evidence", "observation_over_cap")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("sourceFingerprint") != session_source:
            return _result("invalid_evidence", "source_mismatch")
        if payload.get("schemaVersion") != "governance-graph-preflight-observation-v1":
            return _result("invalid_evidence", "schema_mismatch")
        status = payload.get("status")
        if status not in {"available", "degraded", "unavailable"}:
            return _result("invalid_evidence", "status_invalid")
        return {"authority": "read_only_governance_graph", "sourceFingerprint": session_source, "status": status, "graphFingerprint": payload.get("graphFingerprint"), "blockers": _bounded_list(payload.get("blockers", [])), "diagnostics": _bounded_list(payload.get("diagnostics", []))}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return _result("invalid_evidence", "observation_invalid")


def _bounded_list(value: object) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item if not isinstance(item, str) else item[:_MAX_TEXT] for item in value[:_MAX_DIAGNOSTICS]]


def _result(status: str, reason: str) -> dict[str, Any]:
    return {"authority": "read_only_governance_graph", "status": status, "diagnostics": [{"code": reason}]}
