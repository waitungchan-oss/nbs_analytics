from __future__ import annotations

from typing import Any, Mapping

from .governance_graph_catalog_models import (
    CATALOG_STATUSES,
    DEPENDENCY_POLICY_VERSION,
    OWNER_POLICY_VERSION,
    GovernanceGraphCatalogSchemaError,
    GovernanceGraphDependencyCatalog,
    GovernanceGraphOwnerCatalog,
    GovernanceGraphOwnerDependencyReadModel,
)


STATUS_PRECEDENCE = ("invalid", "stale", "blocked", "unknown", "missing", "unavailable", "available")


def _highest_status(statuses: list[str]) -> str:
    if not statuses:
        return "unavailable"
    return min(statuses, key=STATUS_PRECEDENCE.index)


def _catalog_status(entries: tuple[Mapping[str, Any], ...]) -> str:
    if not entries:
        return "missing"
    return _highest_status([str(entry["status"]) for entry in entries])


def _diagnostic(code: str, summary: str) -> dict[str, str]:
    return {"code": code, "summary": summary}


class OwnerDependencyReadService:
    """Validate explicit owner/dependency envelopes and return a bounded read model."""

    def resolve(
        self,
        *,
        snapshot_fingerprint: str,
        owner_catalog: Mapping[str, Any] | None,
        dependency_catalog: Mapping[str, Any] | None,
    ) -> GovernanceGraphOwnerDependencyReadModel:
        owner = self._parse_owner(owner_catalog, snapshot_fingerprint)
        dependency = self._parse_dependency(dependency_catalog, snapshot_fingerprint)
        statuses = [owner["status"], dependency["status"]]
        overall = _highest_status(statuses)
        if owner["status"] == "stale" or dependency["status"] == "stale":
            overall = "stale"
        diagnostics = [*owner["diagnostics"], *dependency["diagnostics"]]
        owners = owner["entries"] if owner["status"] not in {"invalid", "stale", "unavailable"} else ()
        dependencies = dependency["entries"] if dependency["status"] not in {"invalid", "stale", "unavailable"} else ()
        owner_fp = owner["fingerprint"] if owner["status"] not in {"invalid", "stale", "unavailable"} else None
        dependency_fp = dependency["fingerprint"] if dependency["status"] not in {"invalid", "stale", "unavailable"} else None
        coverage = {
            "ownerStatus": owner["status"],
            "dependencyStatus": dependency["status"],
            "ownerEntries": len(owners),
            "dependencyEntries": len(dependencies),
            "unknownCount": int(owner["status"] == "unknown") + int(dependency["status"] == "unknown"),
            "missingCount": int(owner["status"] == "missing") + int(dependency["status"] == "missing"),
            "staleCount": int(owner["status"] == "stale") + int(dependency["status"] == "stale"),
            "blockedCount": int(owner["status"] == "blocked") + int(dependency["status"] == "blocked"),
        }
        return GovernanceGraphOwnerDependencyReadModel.from_parts(
            status=overall,
            snapshot_fingerprint=snapshot_fingerprint,
            owner_catalog_fingerprint=owner_fp,
            dependency_catalog_fingerprint=dependency_fp,
            owner_policy_version=OWNER_POLICY_VERSION,
            dependency_policy_version=DEPENDENCY_POLICY_VERSION,
            owners=owners,
            dependencies=dependencies,
            coverage=coverage,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _parse_owner(payload: Mapping[str, Any] | None, snapshot_fingerprint: str) -> dict[str, Any]:
        if payload is None:
            return {"status": "unavailable", "entries": (), "fingerprint": None, "diagnostics": [_diagnostic("owner_catalog_unavailable", "Owner catalog is unavailable.")]}
        try:
            catalog = GovernanceGraphOwnerCatalog.from_dict(payload)
        except (GovernanceGraphCatalogSchemaError, TypeError, ValueError):
            return {"status": "invalid", "entries": (), "fingerprint": None, "diagnostics": [_diagnostic("owner_catalog_invalid", "Owner catalog failed strict validation.")]}
        if catalog.snapshot_fingerprint != snapshot_fingerprint:
            return {"status": "stale", "entries": (), "fingerprint": None, "diagnostics": [_diagnostic("owner_catalog_stale", "Owner catalog does not match the selected snapshot.")]}
        return {"status": _catalog_status(catalog.entries), "entries": catalog.entries, "fingerprint": catalog.catalog_fingerprint, "diagnostics": []}

    @staticmethod
    def _parse_dependency(payload: Mapping[str, Any] | None, snapshot_fingerprint: str) -> dict[str, Any]:
        if payload is None:
            return {"status": "unavailable", "entries": (), "fingerprint": None, "diagnostics": [_diagnostic("dependency_catalog_unavailable", "Dependency catalog is unavailable.")]}
        try:
            catalog = GovernanceGraphDependencyCatalog.from_dict(payload)
        except (GovernanceGraphCatalogSchemaError, TypeError, ValueError):
            return {"status": "invalid", "entries": (), "fingerprint": None, "diagnostics": [_diagnostic("dependency_catalog_invalid", "Dependency catalog failed strict validation.")]}
        if catalog.snapshot_fingerprint != snapshot_fingerprint:
            return {"status": "stale", "entries": (), "fingerprint": None, "diagnostics": [_diagnostic("dependency_catalog_stale", "Dependency catalog does not match the selected snapshot.")]}
        return {"status": _catalog_status(catalog.entries), "entries": catalog.entries, "fingerprint": catalog.catalog_fingerprint, "diagnostics": []}


__all__ = ["OwnerDependencyReadService", "STATUS_PRECEDENCE"]
