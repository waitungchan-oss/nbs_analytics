from __future__ import annotations

from copy import deepcopy

from backend.agents.governance_graph_catalog_models import (
    DEPENDENCY_CATALOG_SCHEMA,
    OWNER_CATALOG_SCHEMA,
    GovernanceGraphOwnerDependencyReadModel,
)
from backend.agents.governance_graph_catalog_service import OwnerDependencyReadService
from backend.agents.workflow_models import canonical_sha256


FINGERPRINT = "a" * 64


def _source(identity: str) -> dict[str, str]:
    return {"kind": "approved_catalog", "identity": identity, "fingerprint": FINGERPRINT}


def _owner_catalog(*, snapshot: str = FINGERPRINT, entries: list[dict] | None = None) -> dict:
    entries = entries if entries is not None else [{
        "subject": {"kind": "node", "id": "review"},
        "owner": {"kind": "governance_role", "id": "review_owner"},
        "source": _source("owner-catalog-v1"),
        "snapshotFingerprint": snapshot,
        "status": "available",
    }]
    payload = {
        "schemaVersion": OWNER_CATALOG_SCHEMA,
        "catalogPolicyVersion": "e3-owner-policy-v1",
        "catalogFingerprint": FINGERPRINT,
        "snapshotFingerprint": snapshot,
        "source": _source("owner-catalog-v1"),
        "entries": entries,
        "diagnostics": [],
    }
    payload["catalogFingerprint"] = canonical_sha256({key: value for key, value in payload.items() if key != "catalogFingerprint"})
    return payload


def _dependency_catalog(*, snapshot: str = FINGERPRINT, entries: list[dict] | None = None) -> dict:
    entries = entries if entries is not None else [{
        "from": {"kind": "node", "id": "implementation"},
        "to": {"kind": "node", "id": "verification"},
        "relation": "requires",
        "relationKind": "workflow_edge",
        "source": _source("dependency-catalog-v1"),
        "snapshotFingerprint": snapshot,
        "status": "available",
    }]
    payload = {
        "schemaVersion": DEPENDENCY_CATALOG_SCHEMA,
        "catalogPolicyVersion": "e3-dependency-policy-v1",
        "catalogFingerprint": FINGERPRINT,
        "snapshotFingerprint": snapshot,
        "source": _source("dependency-catalog-v1"),
        "entries": entries,
        "diagnostics": [],
    }
    payload["catalogFingerprint"] = canonical_sha256({key: value for key, value in payload.items() if key != "catalogFingerprint"})
    return payload


def test_resolve_available_catalogs_returns_bounded_read_model() -> None:
    result = OwnerDependencyReadService().resolve(
        snapshot_fingerprint=FINGERPRINT,
        owner_catalog=_owner_catalog(),
        dependency_catalog=_dependency_catalog(),
    )

    assert isinstance(result, GovernanceGraphOwnerDependencyReadModel)
    assert result.status == "available"
    assert result.to_dict()["coverage"]["ownerEntries"] == 1
    assert result.to_dict()["coverage"]["dependencyEntries"] == 1
    assert result.read_model_fingerprint


def test_resolve_without_catalogs_is_unavailable_without_fingerprints() -> None:
    result = OwnerDependencyReadService().resolve(snapshot_fingerprint=FINGERPRINT, owner_catalog=None, dependency_catalog=None)

    assert result.status == "unavailable"
    assert result.owner_catalog_fingerprint is None
    assert result.dependency_catalog_fingerprint is None
    assert result.read_model_fingerprint is None


def test_resolve_missing_entries_is_missing_not_zero_owner_or_dependency() -> None:
    result = OwnerDependencyReadService().resolve(
        snapshot_fingerprint=FINGERPRINT,
        owner_catalog=_owner_catalog(entries=[]),
        dependency_catalog=_dependency_catalog(entries=[]),
    )

    assert result.status == "missing"
    assert result.to_dict()["coverage"]["ownerStatus"] == "missing"
    assert result.to_dict()["coverage"]["dependencyStatus"] == "missing"


def test_resolve_unknown_entry_preserves_unknown_status() -> None:
    owner = _owner_catalog(entries=[{**_owner_catalog()["entries"][0], "status": "unknown"}])

    result = OwnerDependencyReadService().resolve(snapshot_fingerprint=FINGERPRINT, owner_catalog=owner, dependency_catalog=None)

    assert result.status == "unknown"
    assert result.to_dict()["coverage"]["ownerStatus"] == "unknown"


def test_resolve_snapshot_mismatch_is_stale_and_does_not_fingerprint() -> None:
    result = OwnerDependencyReadService().resolve(
        snapshot_fingerprint=FINGERPRINT,
        owner_catalog=_owner_catalog(snapshot="b" * 64),
        dependency_catalog=_dependency_catalog(),
    )

    assert result.status == "stale"
    assert result.read_model_fingerprint is None
    assert result.to_dict()["coverage"]["staleCount"] == 1


def test_resolve_malformed_catalog_is_invalid_without_raw_error() -> None:
    malformed = deepcopy(_owner_catalog())
    malformed["source"] = {"kind": "arbitrary", "identity": "bad", "fingerprint": FINGERPRINT}

    result = OwnerDependencyReadService().resolve(snapshot_fingerprint=FINGERPRINT, owner_catalog=malformed, dependency_catalog=None)

    assert result.status == "invalid"
    assert result.owner_catalog_fingerprint is None
    assert result.to_dict()["diagnostics"]
    assert "Traceback" not in str(result.to_dict())


def test_resolve_repeated_input_is_byte_stable_and_does_not_write(tmp_path) -> None:
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    service = OwnerDependencyReadService()
    first = service.resolve(snapshot_fingerprint=FINGERPRINT, owner_catalog=_owner_catalog(), dependency_catalog=_dependency_catalog()).to_dict()
    second = service.resolve(snapshot_fingerprint=FINGERPRINT, owner_catalog=_owner_catalog(), dependency_catalog=_dependency_catalog()).to_dict()

    assert first == second
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before
