import pytest

from backend.agents.agent_eval_normalization import normalize_usage


def test_legacy_runtime_is_estimated():
    result = normalize_usage(
        {"estimatedInputTokens": 80, "outputTokens": 20},
        producer_id="agent-runtime-legacy-v1",
    )
    assert result["measuredInputTokens"] is None
    assert result["measuredOutputTokens"] is None
    assert result["estimatedInputTokens"] == 80
    assert result["estimatedOutputTokens"] == 20
    assert result["source"] == "estimate"


def test_hermes_usage_is_measured():
    result = normalize_usage(
        {"inputTokens": 80, "outputTokens": 20},
        producer_id="hermes-real-turn-v1",
    )
    assert result["measuredInputTokens"] == 80
    assert result["measuredOutputTokens"] == 20
    assert result["source"] == "provider_usage"


def test_unknown_producer_is_rejected_without_guessing_field_meanings():
    with pytest.raises(ValueError, match="unknown_producer"):
        normalize_usage({"p95Ms": 300}, producer_id="unknown")


def test_hermes_legacy_latency_is_not_task_duration():
    from backend.agents.agent_eval_normalization import normalize_observation

    result = normalize_observation(
        {
            "inputTokens": 80,
            "outputTokens": 20,
            "p95Ms": 300,
            "producerId": "hermes-real-turn-v1",
        },
        binding={"producerId": "hermes-real-turn-v1"},
    )
    assert result["usage"]["source"] == "provider_usage"
    assert result["callLatencyMs"] == 300
    assert result["legacyField"] == "p95Ms"
    assert "taskDurationMs" not in result


@pytest.mark.parametrize("latency", [float("nan"), float("inf"), -1, 604800001])
def test_hermes_legacy_latency_rejects_nonfinite_or_out_of_bound_values(latency):
    from backend.agents.agent_eval_normalization import normalize_observation

    with pytest.raises(ValueError, match="invalid_call_latency"):
        normalize_observation(
            {"inputTokens": 80, "outputTokens": 20, "p95Ms": latency,
             "producerId": "hermes-real-turn-v1"},
            binding={"producerId": "hermes-real-turn-v1"},
        )
