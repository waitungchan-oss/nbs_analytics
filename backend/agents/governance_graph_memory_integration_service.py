from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .evidence_models import canonical_fingerprint
from .memory_hub_integration_models import MemoryHubIntegrationEvidence


LINEAGE_SCHEMA = "memory-hub-lineage-v1"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RELATIONS = frozenset({"derived_from", "produces", "verifies", "documented_by"})


class GovernanceGraphMemoryIntegrationError(ValueError):
    """Raised only for malformed bounded memory lineage evidence."""


def _run_id(value: str) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise GovernanceGraphMemoryIntegrationError("runId is invalid")
    return value


def _lineage(status: str, run_id: str, *, links: list[dict[str, Any]] | None = None, diagnostics: list[dict[str, str]] | None = None) -> dict[str, Any]:
    links = links or []
    diagnostics = diagnostics or []
    unsigned = {
        "schemaVersion": LINEAGE_SCHEMA,
        "runId": run_id,
        "status": status,
        "links": links,
        "evidenceRefs": sorted({ref for link in links for ref in link.get("sourceRefs", [])}),
        "diagnostics": diagnostics,
    }
    return {**unsigned, "lineageFingerprint": canonical_fingerprint(unsigned)}


class GovernanceGraphMemoryIntegrationService:
    """Project optional Memory Hub evidence without touching Graph snapshots."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve(strict=True)

    def _evidence_path(self, run_id: str) -> Path:
        return self.project_root / ".nbs_agent_runtime" / "runs" / run_id / "memory-hub-integration.json"

    def project(self, run_id: str) -> dict[str, Any]:
        run_id = _run_id(run_id)
        path = self._evidence_path(run_id)
        if not path.exists():
            return _lineage("missing", run_id, diagnostics=[{"code": "memory_evidence_missing"}])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            return _lineage("blocked", run_id, diagnostics=[{"code": "memory_evidence_invalid_path"}])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            evidence = MemoryHubIntegrationEvidence.from_dict(payload)
        except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
            return _lineage("blocked", run_id, diagnostics=[{"code": "memory_evidence_invalid"}])
        if evidence.status != "ready":
            return _lineage(evidence.status, run_id, diagnostics=[{"code": evidence.reason}])
        link = {
            "source": evidence.consumer_id,
            "target": f"memory-hub-{evidence.integration_mode}",
            "relation": "produces",
            "sourceRefs": list(evidence.source_refs),
            "evidenceFingerprint": evidence.evidence_fingerprint,
        }
        return _lineage("ready", run_id, links=[link])
