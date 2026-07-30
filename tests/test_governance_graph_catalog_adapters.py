from __future__ import annotations

import pytest

from backend.agents.governance_graph_catalog_adapters import (
    catalog_for_comparison,
    catalog_for_impact,
    catalog_for_query,
    catalog_for_risk,
)
from backend.agents.governance_graph_catalog_models import GovernanceGraphCatalogSchemaError, GovernanceGraphOwnerDependencyReadModel


FINGERPRINT = "a" * 64


def _model(*, status: str = "available", owner_status: str = "available", dependency_status: str = "available") -> GovernanceGraphOwnerDependencyReadModel:
    owners = ({
        "subject": {"kind": "node", "id": "review"},
        "owner": {"kind": "governance_role", "id": "review_owner"},
        "source": {"kind": "approved_catalog", "identity": "owner-catalog-v1", "fingerprint": FINGERPRINT},
        "snapshotFingerprint": FINGERPRINT,
        "status": owner_status,
    },) if owner_status == "available" else ()
    dependencies = ({
        "from": {"kind": "node", "id": "implementation"},
        "to": {"kind": "node", "id": "verification"},
        "relation": "requires",
        "relationKind": "workflow_edge",
        "source": {"kind": "approved_catalog", "identity": "dependency-catalog-v1", "fingerprint": FINGERPRINT},
        "snapshotFingerprint": FINGERPRINT,
        "status": dependency_status,
    },) if dependency_status == "available" else ()
    return GovernanceGraphOwnerDependencyReadModel.from_parts(
        status=status,
        snapshot_fingerprint=FINGERPRINT,
        owner_catalog_fingerprint=FINGERPRINT if owner_status == "available" else None,
        dependency_catalog_fingerprint=FINGERPRINT if dependency_status == "available" else None,
        owner_policy_version="e3-owner-policy-v1",
        dependency_policy_version="e3-dependency-policy-v1",
        owners=owners,
        dependencies=dependencies,
        coverage={"ownerStatus": owner_status, "dependencyStatus": dependency_status, "ownerEntries": len(owners), "dependencyEntries": len(dependencies), "unknownCount": int(owner_status == "unknown") + int(dependency_status == "unknown"), "missingCount": int(owner_status == "missing") + int(dependency_status == "missing"), "staleCount": int(owner_status == "stale") + int(dependency_status == "stale"), "blockedCount": int(owner_status == "blocked") + int(dependency_status == "blocked")},
        diagnostics=(),
    )


@pytest.mark.parametrize("adapter", [catalog_for_query, catalog_for_comparison, catalog_for_risk, catalog_for_impact])
def test_catalog_adapters_return_only_exact_additive_catalog_section(adapter) -> None:
    result = adapter(_model())

    assert set(result) == {"catalog"}
    assert result["catalog"]["schemaVersion"] == "governance-graph-owner-dependency-read-v1"
    assert result["catalog"]["dependencies"][0]["relationKind"] == "workflow_edge"


@pytest.mark.parametrize("adapter", [catalog_for_query, catalog_for_comparison, catalog_for_risk, catalog_for_impact])
def test_catalog_adapters_preserve_unknown_and_missing_without_inference(adapter) -> None:
    model = _model(status="unknown", owner_status="unknown", dependency_status="missing")

    result = adapter(model)

    assert result["catalog"]["status"] == "unknown"
    assert result["catalog"]["coverage"]["ownerStatus"] == "unknown"
    assert result["catalog"]["coverage"]["dependencyStatus"] == "missing"
    assert result["catalog"]["owners"] == []
    assert result["catalog"]["dependencies"] == []


def test_catalog_adapter_rejects_non_read_model_input() -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        catalog_for_query({"status": "available"})
