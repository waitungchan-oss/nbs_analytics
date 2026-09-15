from __future__ import annotations

import pytest


def _contract(**overrides):
    from backend.agents.acceptance_contract import build_acceptance_contract

    values = {
        "contract_id": "nbs-acceptance",
        "contract_version": "formal-scope-v1",
        "scope_fingerprint": "a" * 64,
        "semantic_rules_fingerprint": "b" * 64,
        "dataset_snapshot_fingerprint": "c" * 64,
        "supersedes_contract_fingerprint": None,
        "status": "active",
    }
    values.update(overrides)
    return build_acceptance_contract(**values)


def test_contract_is_canonical_and_versioned():
    from backend.agents.acceptance_contract import validate_acceptance_contract

    value = _contract()
    validate_acceptance_contract(value)
    assert value["schemaVersion"] == "acceptance-contract-v1"
    assert value["authority"] == "metadata"
    assert len(value["evidenceFingerprint"]) == 64


def test_contract_fingerprint_is_stable_for_same_semantics():
    from backend.agents.acceptance_contract import contract_fingerprint

    assert contract_fingerprint(_contract()) == contract_fingerprint(_contract())
    assert contract_fingerprint(_contract(status="retired")) != contract_fingerprint(_contract())


def test_contract_rejects_invalid_fingerprint_or_status():
    from backend.agents.acceptance_contract import validate_acceptance_contract

    for field, value in [
        ("scopeFingerprint", "x"),
        ("semanticRulesFingerprint", ""),
        ("datasetSnapshotFingerprint", "z" * 63),
    ]:
        payload = _contract()
        payload[field] = value
        with pytest.raises(ValueError):
            validate_acceptance_contract(payload)

    payload = _contract(status="retired")
    validate_acceptance_contract(payload)
    payload["status"] = "draft"
    with pytest.raises(ValueError):
        validate_acceptance_contract(payload)


def test_contract_rejects_tampered_evidence_and_invalid_supersedes_reference():
    from backend.agents.acceptance_contract import validate_acceptance_contract

    payload = _contract()
    payload["evidenceFingerprint"] = "d" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        validate_acceptance_contract(payload)

    payload = _contract()
    payload["supersedesContractFingerprint"] = "e" * 63
    with pytest.raises(ValueError, match="supersedes"):
        validate_acceptance_contract(payload)
