from __future__ import annotations

import pytest

from backend.agents.governance_graph_catalog_models import (
    DEPENDENCY_CATALOG_SCHEMA,
    OWNER_CATALOG_SCHEMA,
    OWNER_ROLES,
    GovernanceGraphCatalogSchemaError,
    GovernanceGraphDependencyCatalog,
    GovernanceGraphOwnerCatalog,
)


FINGERPRINT = "a" * 64


def _source(kind: str = "approved_catalog", identity: str = "owner-catalog-v1") -> dict[str, str]:
    return {"kind": kind, "identity": identity, "fingerprint": FINGERPRINT}


def _owner_entry(**overrides: object) -> dict:
    entry = {
        "subject": {"kind": "node", "id": "review"},
        "owner": {"kind": "governance_role", "id": "review_owner"},
        "source": _source(),
        "snapshotFingerprint": FINGERPRINT,
        "status": "available",
    }
    entry.update(overrides)
    return entry


def _owner_catalog(entries: list[dict] | None = None, **overrides: object) -> dict:
    payload = {
        "schemaVersion": OWNER_CATALOG_SCHEMA,
        "catalogPolicyVersion": "e3-owner-policy-v1",
        "catalogFingerprint": FINGERPRINT,
        "snapshotFingerprint": FINGERPRINT,
        "source": _source(),
        "entries": entries if entries is not None else [_owner_entry()],
        "diagnostics": [],
    }
    payload.update(overrides)
    return payload


def _dependency_entry(**overrides: object) -> dict:
    entry = {
        "from": {"kind": "node", "id": "implementation"},
        "to": {"kind": "node", "id": "verification"},
        "relation": "requires",
        "relationKind": "workflow_edge",
        "source": _source(identity="dependency-catalog-v1"),
        "snapshotFingerprint": FINGERPRINT,
        "status": "available",
    }
    entry.update(overrides)
    return entry


def _dependency_catalog(entries: list[dict] | None = None, **overrides: object) -> dict:
    payload = {
        "schemaVersion": DEPENDENCY_CATALOG_SCHEMA,
        "catalogPolicyVersion": "e3-dependency-policy-v1",
        "catalogFingerprint": FINGERPRINT,
        "snapshotFingerprint": FINGERPRINT,
        "source": _source(identity="dependency-catalog-v1"),
        "entries": entries if entries is not None else [_dependency_entry()],
        "diagnostics": [],
    }
    payload.update(overrides)
    return payload


def test_owner_catalog_accepts_role_only_owner_and_round_trips() -> None:
    catalog = GovernanceGraphOwnerCatalog.from_dict(_owner_catalog())

    assert "review_owner" in OWNER_ROLES
    assert set(catalog.to_dict()) == {
        "schemaVersion", "catalogPolicyVersion", "catalogFingerprint", "snapshotFingerprint",
        "source", "entries", "diagnostics",
    }
    assert catalog.to_dict()["schemaVersion"] == OWNER_CATALOG_SCHEMA
    assert catalog.to_dict()["entries"][0]["owner"] == {
        "kind": "governance_role",
        "id": "review_owner",
    }
    assert catalog.catalog_fingerprint == FINGERPRINT


def test_dependency_catalog_accepts_workflow_edge_and_round_trips() -> None:
    catalog = GovernanceGraphDependencyCatalog.from_dict(_dependency_catalog())

    assert set(catalog.to_dict()) == {
        "schemaVersion", "catalogPolicyVersion", "catalogFingerprint", "snapshotFingerprint",
        "source", "entries", "diagnostics",
    }
    entry = catalog.to_dict()["entries"][0]
    assert entry["relation"] == "requires"
    assert entry["relationKind"] == "workflow_edge"
    assert catalog.catalog_fingerprint == FINGERPRINT


@pytest.mark.parametrize("source_kind", ["arbitrary", "file", "https"])
def test_catalog_rejects_source_kind_outside_closed_allowlist(source_kind: str) -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog(source={**_source(), "kind": source_kind}))


@pytest.mark.parametrize("value", ["/tmp/catalog.json", "https://example.test", "sk-secret-value", "{raw:json}", "run command"])
def test_catalog_rejects_unsafe_source_identity(value: str) -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog(source={**_source(), "identity": value}))


def test_catalog_rejects_unknown_owner_role() -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(
            _owner_catalog([_owner_entry(owner={"kind": "governance_role", "id": "business_owner"})])
        )


def test_catalog_rejects_conflicting_owner_entries() -> None:
    entries = [
        _owner_entry(),
        _owner_entry(owner={"kind": "governance_role", "id": "review_owner"}, source={**_source(), "identity": "other"}),
    ]
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog(entries))


def test_catalog_deduplicates_identical_owner_entries_deterministically() -> None:
    catalog = GovernanceGraphOwnerCatalog.from_dict(_owner_catalog([_owner_entry(), _owner_entry()]))

    assert len(catalog.entries) == 1


def test_dependency_rejects_unsupported_relation_and_self_loop() -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphDependencyCatalog.from_dict(
            _dependency_catalog([_dependency_entry(relation="causal_dependency")])
        )
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphDependencyCatalog.from_dict(
            _dependency_catalog(
                [_dependency_entry(to={"kind": "node", "id": "implementation"})]
            )
        )


def test_catalog_rejects_non_lowercase_or_invalid_snapshot_fingerprint() -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog(snapshotFingerprint="A" * 64))
