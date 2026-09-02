from datetime import datetime, timezone, timedelta

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import (
    ReleaseGateValidationError,
    aggregate_release_gates,
    validate_release_gate_aggregate,
    validate_release_gate_evidence,
)


COMMIT = "a" * 40
SOURCE = "b" * 64
NOW = datetime(2026, 9, 1, 0, 2, tzinfo=timezone.utc)


def _evidence(gate="full_pytest", status="PASS", **overrides):
    value = {
        "schemaVersion": f"{gate.replace('_', '-')}-gate-v1",
        "gate": gate,
        "status": status,
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "startedAt": "2026-09-01T00:00:00Z",
        "finishedAt": "2026-09-01T00:01:00Z",
        "result": {"passed": 10, "failed": 0, "skipped": 0},
        "metadata": {"commandId": f"{gate}-command"},
    }
    value.update(overrides)
    value["evidenceFingerprint"] = canonical_fingerprint(value)
    return value


def test_each_gate_round_trips_with_deterministic_fingerprint():
    for gate in ("full_pytest", "hermes", "ui_acceptance"):
        value = _evidence(gate)
        parsed = validate_release_gate_evidence(value, COMMIT, SOURCE, NOW)
        assert parsed.to_dict() == value
        assert parsed.fingerprint == value["evidenceFingerprint"]


def test_aggregate_is_deterministic_and_requires_all_three_passes():
    children = {gate: _evidence(gate) for gate in ("full_pytest", "hermes", "ui_acceptance")}
    aggregate = aggregate_release_gates(children, COMMIT, SOURCE, NOW)
    assert aggregate["schemaVersion"] == "release-gate-result-v1"
    assert aggregate["status"] == "PASS"
    assert validate_release_gate_aggregate(aggregate, COMMIT, NOW).to_dict() == aggregate

    blocked = {**children, "hermes": _evidence("hermes", "BLOCKED")}
    assert aggregate_release_gates(blocked, COMMIT, SOURCE, NOW)["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda v: v.pop("metadata"), "schema"),
        (lambda v: v.__setitem__("unexpected", True), "schema"),
        (lambda v: v.__setitem__("commitSha", "not-a-sha"), "commit"),
        (lambda v: v.__setitem__("sourceFingerprint", "not-a-fingerprint"), "source"),
        (lambda v: v.__setitem__("status", "MISSING"), "status"),
        (lambda v: v.__setitem__("evidenceFingerprint", "0" * 64), "fingerprint"),
        (lambda v: v.__setitem__("metadata", {"secret": "token=abc"}), "secret"),
        (lambda v: v.__setitem__("metadata", {"path": "/private/secret.json"}), "path"),
    ],
)
def test_evidence_rejects_tampering_unknown_fields_and_sensitive_payload(mutation, message):
    value = _evidence()
    mutation(value)
    with pytest.raises(ReleaseGateValidationError, match=message):
        validate_release_gate_evidence(value, COMMIT, SOURCE, NOW)


def test_evidence_rejects_stale_and_identity_mismatch():
    value = _evidence()
    with pytest.raises(ReleaseGateValidationError, match="stale"):
        validate_release_gate_evidence(value, COMMIT, SOURCE, NOW + timedelta(seconds=1801))
    with pytest.raises(ReleaseGateValidationError, match="commit"):
        validate_release_gate_evidence(value, "c" * 40, SOURCE, NOW)
    with pytest.raises(ReleaseGateValidationError, match="source"):
        validate_release_gate_evidence(value, COMMIT, "d" * 64, NOW)


def test_aggregate_rejects_unknown_duplicate_or_non_pass_child():
    children = {gate: _evidence(gate) for gate in ("full_pytest", "hermes", "ui_acceptance")}
    aggregate = aggregate_release_gates(children, COMMIT, SOURCE, NOW)
    aggregate["gates"]["unknown"] = {"status": "PASS", "evidenceFingerprint": "c" * 64}
    aggregate["evidenceFingerprint"] = canonical_fingerprint({k: v for k, v in aggregate.items() if k != "evidenceFingerprint"})
    with pytest.raises(ReleaseGateValidationError, match="gate"):
        validate_release_gate_aggregate(aggregate, COMMIT, NOW)


def test_evidence_rejects_sensitive_field_names_and_total_payload_over_cap():
    value = _evidence()
    value["metadata"] = {"token": "value"}
    value["evidenceFingerprint"] = canonical_fingerprint({k: v for k, v in value.items() if k != "evidenceFingerprint"})
    with pytest.raises(ReleaseGateValidationError, match="secret"):
        validate_release_gate_evidence(value, COMMIT, SOURCE, NOW)

    value = _evidence()
    value["metadata"] = {"first": "x" * 18000, "second": "y" * 18000}
    value["evidenceFingerprint"] = canonical_fingerprint({k: v for k, v in value.items() if k != "evidenceFingerprint"})
    with pytest.raises(ReleaseGateValidationError, match="size"):
        validate_release_gate_evidence(value, COMMIT, SOURCE, NOW)
