from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .governance_graph_management_summary_models import validate_management_summary_payload


def serialize_management_summary_export(summary: Mapping[str, Any], preset_id: str | None = None, snapshot_fingerprint: str | None = None) -> dict[str, Any]:
    if not isinstance(summary, Mapping) or summary.get("schemaVersion") != "governance-graph-management-summary-v1":
        raise ValueError("summary schema is invalid")
    validated = validate_management_summary_payload(summary)
    if snapshot_fingerprint is not None and validated["snapshotFingerprint"] != snapshot_fingerprint:
        preset_id = None
    if preset_id is not None and not any(item.get("presetId") == preset_id and item.get("available") is True for item in validated.get("presets", [])):
        raise ValueError("selected preset is not available")
    if preset_id is not None and preset_id not in {"protected_surfaces", "blocked_verification", "unknown_coverage", "owner_dependency_gaps", "recent_changes"}:
        raise ValueError("preset id is invalid")
    result = {"schemaVersion": "governance-graph-management-summary-export-v1", "summarySchemaVersion": validated["schemaVersion"], "managementPolicyVersion": validated["managementPolicyVersion"], "snapshotFingerprint": validated["snapshotFingerprint"], "summaryFingerprint": validated["summaryFingerprint"], "selectedPresetId": preset_id, "summary": validated}
    body = {key: value for key, value in result.items() if key != "exportFingerprint"}
    result["exportFingerprint"] = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
