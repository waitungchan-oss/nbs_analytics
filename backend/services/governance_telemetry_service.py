from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.services.governance_telemetry_aggregation import (
    MAX_DURATION_MS,
    MAX_REPAIR_LOOPS,
    MAX_TOKEN_COUNT,
    TelemetryAggregator,
)


class GovernanceTelemetryService:
    """Build a bounded, read-only telemetry projection from retained run evidence."""

    def __init__(self, project_root: Path, runtime_root: Path | None = None) -> None:
        from backend.services.agent_operations_service import AgentOperationsService

        self.reader = AgentOperationsService(project_root, runtime_root=runtime_root)

    def build_snapshot(
        self,
        *,
        runs: list[dict[str, Any]] | None = None,
        diagnostics: list[dict[str, str]] | None = None,
        hard_cap: int | None = None,
    ) -> dict[str, Any]:
        if runs is None:
            diagnostics = []
            _retention, policy = self.reader._retention(diagnostics)
            hard_cap = policy.stage_artifact_max_bytes if policy is not None else 5 * 1024 * 1024
            runs = self.reader._load_runs(diagnostics, hard_cap)
        else:
            diagnostics = list(diagnostics or [])
            hard_cap = hard_cap or 5 * 1024 * 1024
        return TelemetryAggregator(self.reader).build(runs, diagnostics, hard_cap)


__all__ = ["GovernanceTelemetryService", "MAX_DURATION_MS", "MAX_REPAIR_LOOPS", "MAX_TOKEN_COUNT"]
