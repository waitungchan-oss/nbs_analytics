from datetime import datetime, timedelta, timezone

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import aggregate_release_gates
from backend.agents.release_readiness import classify_acceptance_stage


COMMIT = "a" * 40
SOURCE = canonical_fingerprint({
    "baseSha": "c" * 40,
    "headSha": COMMIT,
    "briefPath": "docs/task.md",
    "briefFingerprint": "d" * 64,
    "worktreeFingerprint": "e" * 64,
    "diffFingerprint": "f" * 64,
    "contractFingerprint": "1" * 64,
    "policyFingerprint": "2" * 64,
})
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _session(status="complete", **overrides):
    value = {
        "schemaVersion": "verification-session-v1",
        "sessionId": "session-1",
        "status": status,
        "baseSha": "c" * 40,
        "headSha": COMMIT,
        "briefPath": "docs/task.md",
        "briefFingerprint": "d" * 64,
        "worktreeFingerprint": "e" * 64,
        "diffFingerprint": "f" * 64,
        "contractFingerprint": "1" * 64,
        "policyFingerprint": "2" * 64,
        "gates": {
            "strictReview": {"gateStatus": "pass"},
            "fullPytest": {"gateStatus": "pass"},
            "hermes": {"gateStatus": "pass"},
        },
        "completion": {
            "schemaVersion": "completion-attestation-v1",
            "status": "complete",
            "sessionId": "session-1",
        },
    }
    value["sourceFingerprint"] = SOURCE
    value["completion"]["sourceFingerprint"] = SOURCE
    value.update(overrides)
    return value


def _child(gate, status="PASS"):
    value = {
        "schemaVersion": f"{gate.replace('_', '-')}-gate-v1",
        "gate": gate,
        "status": status,
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "startedAt": (NOW - timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
        "finishedAt": (NOW - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
        "result": {"passed": 1},
        "metadata": {"commandId": gate},
    }
    value["evidenceFingerprint"] = canonical_fingerprint(value)
    return value


def _release(statuses=None):
    statuses = statuses or {}
    children = {
        gate: _child(gate, statuses.get(gate, "PASS"))
        for gate in ("full_pytest", "hermes", "ui_acceptance")
    }
    return aggregate_release_gates(children, COMMIT, SOURCE, NOW)


def test_incomplete_chain_is_not_release_ready():
    assert classify_acceptance_stage(_session("review_passed")) == "verification_incomplete"


def test_completion_attestation_maps_to_verification_complete():
    assert classify_acceptance_stage(_session()) == "verification_complete"


def test_release_aggregate_is_the_only_path_to_release_ready():
    assert classify_acceptance_stage(_session(), _release()) == "release_ready"


def test_blocked_or_mismatched_release_is_fail_closed():
    assert classify_acceptance_stage(_session(), _release({"hermes": "BLOCKED"})) == "blocked"
    assert classify_acceptance_stage(_session(), {**_release(), "sourceFingerprint": "c" * 64}) == "blocked"


def test_malformed_or_terminal_session_is_blocked():
    assert classify_acceptance_stage({"status": "complete"}) == "blocked"
    assert classify_acceptance_stage(_session("stale_source")) == "blocked"
    assert classify_acceptance_stage(_session(), []) == "blocked"


def test_completion_attestation_must_bind_session_and_source():
    session = _session()
    session["completion"]["sourceFingerprint"] = "0" * 64
    assert classify_acceptance_stage(session) == "blocked"

    session = _session()
    session["completion"]["sessionId"] = "other-session"
    assert classify_acceptance_stage(session) == "blocked"
