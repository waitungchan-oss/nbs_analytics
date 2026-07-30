from __future__ import annotations

import pytest

from backend.agents.governance_graph_catalog_models import (
    DEPENDENCY_CATALOG_SCHEMA,
    OWNER_CATALOG_SCHEMA,
    OWNER_ROLES,
    GovernanceGraphCatalogSchemaError,
    GovernanceGraphDependencyCatalog,
    GovernanceGraphOwnerDependencyReadModel,
    GovernanceGraphOwnerCatalog,
)
from backend.agents.workflow_models import canonical_sha256


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
    payload["catalogFingerprint"] = canonical_sha256({key: value for key, value in payload.items() if key != "catalogFingerprint"})
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
    payload["catalogFingerprint"] = canonical_sha256({key: value for key, value in payload.items() if key != "catalogFingerprint"})
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
    assert catalog.catalog_fingerprint == _owner_catalog()["catalogFingerprint"]


def test_dependency_catalog_accepts_workflow_edge_and_round_trips() -> None:
    catalog = GovernanceGraphDependencyCatalog.from_dict(_dependency_catalog())

    assert set(catalog.to_dict()) == {
        "schemaVersion", "catalogPolicyVersion", "catalogFingerprint", "snapshotFingerprint",
        "source", "entries", "diagnostics",
    }
    entry = catalog.to_dict()["entries"][0]
    assert entry["relation"] == "requires"
    assert entry["relationKind"] == "workflow_edge"
    assert catalog.catalog_fingerprint == _dependency_catalog()["catalogFingerprint"]


def test_catalog_rejects_catalog_fingerprint_that_does_not_match_canonical_envelope() -> None:
    payload = _owner_catalog()
    payload["catalogFingerprint"] = FINGERPRINT

    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(payload)


def test_catalog_rejects_entry_snapshot_fingerprint_mismatch() -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog([_owner_entry(snapshotFingerprint="b" * 64)]))


@pytest.mark.parametrize("field", ["subject", "owner"])
def test_catalog_rejects_unsafe_owner_metadata(field: str) -> None:
    entry = _owner_entry()
    entry[field] = {"kind": "governance_role", "id": "secret_prompt"}

    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog([entry]))


@pytest.mark.parametrize("payload", [
    _owner_catalog(source={**_source(), "kind": []}),
    _owner_catalog([_owner_entry(owner={"kind": "governance_role", "id": []})]),
])
def test_catalog_converts_malformed_unhashable_values_to_schema_error(payload: dict) -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(payload)


def test_read_model_contract_has_deterministic_public_fingerprint() -> None:
    model = GovernanceGraphOwnerDependencyReadModel.from_parts(
        status="available",
        snapshot_fingerprint=FINGERPRINT,
        owner_catalog_fingerprint=FINGERPRINT,
        dependency_catalog_fingerprint=None,
        owner_policy_version="e3-owner-policy-v1",
        dependency_policy_version="e3-dependency-policy-v1",
        owners=(_owner_entry(),),
        dependencies=(),
        coverage={"ownerStatus": "available", "dependencyStatus": "unavailable", "ownerEntries": 1, "dependencyEntries": 0, "unknownCount": 0, "missingCount": 0, "staleCount": 0, "blockedCount": 0},
        diagnostics=(),
    )

    assert model.to_dict()["schemaVersion"] == "governance-graph-owner-dependency-read-v1"
    assert model.to_dict()["readModelFingerprint"] == model.read_model_fingerprint
    with pytest.raises(TypeError):
        model.coverage["ownerEntries"] = 2
    with pytest.raises(TypeError):
        model.owners[0]["owner"]["id"] = "plan_owner"


def test_read_model_rejects_unvalidated_entry_mapping() -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerDependencyReadModel.from_parts(
            status="available",
            snapshot_fingerprint=FINGERPRINT,
            owner_catalog_fingerprint=FINGERPRINT,
            dependency_catalog_fingerprint=None,
            owner_policy_version="e3-owner-policy-v1",
            dependency_policy_version="e3-dependency-policy-v1",
            owners=({"subject": {"id": "review"}},),
            dependencies=(),
            coverage={"ownerStatus": "available", "dependencyStatus": "unavailable", "ownerEntries": 1, "dependencyEntries": 0, "unknownCount": 0, "missingCount": 0, "staleCount": 0, "blockedCount": 0},
            diagnostics=(),
        )


def test_read_model_does_not_fingerprint_stale_result() -> None:
    model = GovernanceGraphOwnerDependencyReadModel.from_parts(
        status="stale",
        snapshot_fingerprint=FINGERPRINT,
        owner_catalog_fingerprint=FINGERPRINT,
        dependency_catalog_fingerprint=None,
        owner_policy_version="e3-owner-policy-v1",
        dependency_policy_version="e3-dependency-policy-v1",
        owners=(),
        dependencies=(),
        coverage={"ownerStatus": "stale", "dependencyStatus": "unavailable", "ownerEntries": 0, "dependencyEntries": 0, "unknownCount": 0, "missingCount": 0, "staleCount": 1, "blockedCount": 0},
        diagnostics=(),
    )

    assert model.read_model_fingerprint is None


def test_read_model_canonicalizes_owner_order_and_rejects_conflicting_duplicate() -> None:
    first = _owner_entry()
    second = _owner_entry(subject={"kind": "node", "id": "implementation"}, owner={"kind": "governance_role", "id": "implementation_owner"})
    coverage = {"ownerStatus": "available", "dependencyStatus": "unavailable", "ownerEntries": 2, "dependencyEntries": 0, "unknownCount": 0, "missingCount": 0, "staleCount": 0, "blockedCount": 0}
    kwargs = dict(status="available", snapshot_fingerprint=FINGERPRINT, owner_catalog_fingerprint=FINGERPRINT, dependency_catalog_fingerprint=None, owner_policy_version="e3-owner-policy-v1", dependency_policy_version="e3-dependency-policy-v1", dependencies=(), coverage=coverage, diagnostics=())
    left = GovernanceGraphOwnerDependencyReadModel.from_parts(owners=(first, second), **kwargs)
    right = GovernanceGraphOwnerDependencyReadModel.from_parts(owners=(second, first), **kwargs)

    assert left.read_model_fingerprint == right.read_model_fingerprint
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerDependencyReadModel.from_parts(owners=(first, first | {"status": "missing"}), **kwargs)


def test_read_model_rejects_coverage_count_mismatch() -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerDependencyReadModel.from_parts(
            status="available",
            snapshot_fingerprint=FINGERPRINT,
            owner_catalog_fingerprint=FINGERPRINT,
            dependency_catalog_fingerprint=None,
            owner_policy_version="e3-owner-policy-v1",
            dependency_policy_version="e3-dependency-policy-v1",
            owners=(_owner_entry(),),
            dependencies=(),
            coverage={"ownerStatus": "available", "dependencyStatus": "unavailable", "ownerEntries": 0, "dependencyEntries": 0, "unknownCount": 0, "missingCount": 0, "staleCount": 0, "blockedCount": 0},
            diagnostics=(),
        )


def test_read_model_rejects_unsafe_mapping_key_and_non_finite_scalar() -> None:
    unsafe_entry = _owner_entry()
    unsafe_entry["source"] = {"kind": "approved_catalog", "identity": "owner-catalog-v1", "fingerprint": FINGERPRINT, "Secret": "x"}
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerDependencyReadModel.from_parts(
            status="available", snapshot_fingerprint=FINGERPRINT, owner_catalog_fingerprint=FINGERPRINT,
            dependency_catalog_fingerprint=None, owner_policy_version="e3-owner-policy-v1",
            dependency_policy_version="e3-dependency-policy-v1", owners=(unsafe_entry,), dependencies=(),
            coverage={"ownerStatus": "available", "dependencyStatus": "unavailable", "ownerEntries": 1, "dependencyEntries": 0, "unknownCount": 0, "missingCount": 0, "staleCount": 0, "blockedCount": 0}, diagnostics=(),
        )

    non_finite_entry = _owner_entry(status=float("nan"))
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerDependencyReadModel.from_parts(
            status="available", snapshot_fingerprint=FINGERPRINT, owner_catalog_fingerprint=FINGERPRINT,
            dependency_catalog_fingerprint=None, owner_policy_version="e3-owner-policy-v1",
            dependency_policy_version="e3-dependency-policy-v1", owners=(non_finite_entry,), dependencies=(),
            coverage={"ownerStatus": "available", "dependencyStatus": "unavailable", "ownerEntries": 1, "dependencyEntries": 0, "unknownCount": 0, "missingCount": 0, "staleCount": 0, "blockedCount": 0}, diagnostics=(),
        )


@pytest.mark.parametrize("source_kind", ["arbitrary", "file", "https"])
def test_catalog_rejects_source_kind_outside_closed_allowlist(source_kind: str) -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog(source={**_source(), "kind": source_kind}))


@pytest.mark.parametrize("value", ["/tmp/catalog.json", "https://example.test", "sk-secret-value", "{raw:json}", "run command"])
def test_catalog_rejects_unsafe_source_identity(value: str) -> None:
    with pytest.raises(GovernanceGraphCatalogSchemaError):
        GovernanceGraphOwnerCatalog.from_dict(_owner_catalog(source={**_source(), "identity": value}))


@pytest.mark.parametrize("value", ["owner catalog", "foo..bar", "owner@catalog"])
def test_catalog_rejects_non_identifier_source_identity(value: str) -> None:
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
    payload = _owner_catalog([_owner_entry(), _owner_entry()])
    payload["catalogFingerprint"] = canonical_sha256({**{key: value for key, value in payload.items() if key != "catalogFingerprint"}, "entries": [payload["entries"][0]]})
    catalog = GovernanceGraphOwnerCatalog.from_dict(payload)

    assert len(catalog.entries) == 1


def test_catalog_duplicate_fingerprint_uses_normalized_entries() -> None:
    payload = _owner_catalog([_owner_entry(), _owner_entry()])
    normalized = dict(payload)
    normalized["entries"] = [payload["entries"][0]]
    normalized["catalogFingerprint"] = canonical_sha256({key: value for key, value in normalized.items() if key != "catalogFingerprint"})

    catalog = GovernanceGraphOwnerCatalog.from_dict(normalized)

    assert catalog.catalog_fingerprint == normalized["catalogFingerprint"]
    assert GovernanceGraphOwnerCatalog.from_dict(catalog.to_dict()).to_dict() == catalog.to_dict()


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
