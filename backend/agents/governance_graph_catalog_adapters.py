from __future__ import annotations

from typing import Any

from .governance_graph_catalog_models import GovernanceGraphCatalogSchemaError, GovernanceGraphOwnerDependencyReadModel


def _catalog_projection(read_model: GovernanceGraphOwnerDependencyReadModel) -> dict[str, Any]:
    if not isinstance(read_model, GovernanceGraphOwnerDependencyReadModel):
        raise GovernanceGraphCatalogSchemaError("catalog adapter requires a validated read model")
    return {"catalog": read_model.to_dict()}


def catalog_for_query(read_model: GovernanceGraphOwnerDependencyReadModel) -> dict[str, Any]:
    return _catalog_projection(read_model)


def catalog_for_comparison(read_model: GovernanceGraphOwnerDependencyReadModel) -> dict[str, Any]:
    return _catalog_projection(read_model)


def catalog_for_risk(read_model: GovernanceGraphOwnerDependencyReadModel) -> dict[str, Any]:
    return _catalog_projection(read_model)


def catalog_for_impact(read_model: GovernanceGraphOwnerDependencyReadModel) -> dict[str, Any]:
    return _catalog_projection(read_model)


__all__ = [
    "catalog_for_comparison",
    "catalog_for_impact",
    "catalog_for_query",
    "catalog_for_risk",
]
