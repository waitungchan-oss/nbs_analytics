import pytest

from backend.agents.agent_eval_models import canonical_bytes, validate_observation, validate_usage


def test_canonical_bytes_is_sorted_utf8_and_rejects_nonfinite_values():
    assert canonical_bytes({"b": 1, "a": "中"}) == '{"a":"中","b":1}'.encode()
    with pytest.raises(ValueError, match="non_finite"):
        canonical_bytes({"metric": float("nan")})


def test_validate_usage_does_not_turn_missing_measured_values_into_zero():
    usage = validate_usage({
        "measuredInputTokens": None,
        "measuredOutputTokens": None,
        "estimatedInputTokens": 80,
        "estimatedOutputTokens": 20,
        "cachedInputTokens": None,
        "reasoningTokens": None,
        "estimatorVersion": "estimate-v1",
        "source": "estimate",
        "cachedIsInputSubset": None,
        "reasoningIsOutputSubset": None,
    })
    assert usage["measuredInputTokens"] is None
    assert usage["estimatedOutputTokens"] == 20


def test_validate_observation_requires_identity_and_bounded_artifact_reference():
    payload = {
        "schemaVersion": "agent-eval-observation-v1",
        "identity": {
            "projectId": "nbs_analytics",
            "consumerId": "context-agent",
            "runId": "run-1",
            "sessionId": "session-1",
            "taskId": "task-1",
            "repeatIndex": 0,
            "cohort": "recall_off",
            "provider": "local",
            "model": "model-v1",
            "settingsFingerprint": "a" * 64,
            "sourceCommit": "b" * 40,
            "dirtyFingerprint": "c" * 64,
            "workloadFingerprint": "d" * 64,
            "catalogFingerprint": "e" * 64,
            "policyFingerprint": "f" * 64,
            "allowedFilesFingerprint": "0" * 64,
            "commandsFingerprint": "1" * 64,
        },
        "callId": "call-1",
        "origin": "synthetic",
        "producerId": "fixture-v1",
        "producerFingerprint": "2" * 64,
        "sourceSchema": "fixture-v1",
        "artifactRef": {"path": "tests/fixtures/observation.json", "sha256": "3" * 64},
        "startedAt": None,
        "endedAt": None,
        "result": "completed",
        "usage": {
            "measuredInputTokens": None,
            "measuredOutputTokens": None,
            "estimatedInputTokens": 10,
            "estimatedOutputTokens": 5,
            "cachedInputTokens": None,
            "reasoningTokens": None,
            "estimatorVersion": "estimate-v1",
            "source": "estimate",
            "cachedIsInputSubset": None,
            "reasoningIsOutputSubset": None,
        },
        "callLatencyMs": 25.0,
        "legacyField": None,
        "diagnostics": [],
    }
    result = validate_observation(payload)
    assert result == payload
    payload["artifactRef"]["path"] = "../secret.json"
    with pytest.raises(ValueError, match="unsafe_path"):
        validate_observation(payload)
