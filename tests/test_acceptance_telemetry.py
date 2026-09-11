import pytest

from backend.agents.acceptance_telemetry import build_gate_telemetry, validate_gate_telemetry


def test_gate_telemetry_is_bounded_and_normalized():
    value = build_gate_telemetry(duration_seconds=1.25, failure_code=None)

    assert value == {
        "durationSeconds": 1.25,
        "attemptNumber": 1,
        "retryGroup": None,
        "cacheHit": False,
        "failureCode": None,
        "blockedReason": None,
    }


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"durationSeconds": -1, "attemptNumber": 1, "retryGroup": None, "cacheHit": False, "failureCode": None, "blockedReason": None}, "duration"),
        ({"durationSeconds": 1, "attemptNumber": 0, "retryGroup": None, "cacheHit": False, "failureCode": None, "blockedReason": None}, "attempt"),
        ({"durationSeconds": 1, "attemptNumber": 1, "retryGroup": None, "cacheHit": "false", "failureCode": None, "blockedReason": None}, "cacheHit"),
        ({"durationSeconds": 1, "attemptNumber": 1, "retryGroup": None, "cacheHit": False, "failureCode": "/private/secret", "blockedReason": None}, "failureCode"),
    ],
)
def test_gate_telemetry_rejects_unbounded_or_wrong_types(payload, message):
    with pytest.raises(ValueError, match=message):
        validate_gate_telemetry(payload)
