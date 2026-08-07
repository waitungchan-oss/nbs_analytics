import json

import pytest

from backend.agents.memory_sidecar_telemetry import (
    MemorySidecarFeatureFlags,
    MemorySidecarTelemetryAggregator,
    MemorySidecarTelemetryEvent,
    build_shadow_ab_fixture,
)
from backend.agents.agent_runtime import AgentRuntime


FINGERPRINT = "a" * 64


def _event(**changes):
    values = {
        "run_id": "run-001",
        "mode": "recall_on",
        "query_fingerprint": FINGERPRINT,
        "status": "ready",
        "latency_ms": 20,
        "hint_count": 1,
        "input_bytes": 240,
        "fallback": False,
        "redaction_count": 0,
    }
    values.update(changes)
    return MemorySidecarTelemetryEvent.from_parts(**values)


def test_telemetry_event_has_exact_safe_schema_without_raw_query_or_summary():
    payload = _event().to_dict()

    assert payload == {
        "schemaVersion": "memory-sidecar-telemetry-v1",
        "runId": "run-001",
        "mode": "recall_on",
        "queryFingerprint": FINGERPRINT,
        "status": "ready",
        "latencyMs": 20,
        "hintCount": 1,
        "inputBytes": 240,
        "fallback": False,
        "redactionCount": 0,
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "query" not in {key.lower() for key in payload if key != "queryFingerprint"}
    assert "summary" not in serialized.lower()
    assert "source" not in serialized.lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latency_ms", -1), ("latency_ms", 801),
        ("hint_count", -1), ("hint_count", 4),
        ("input_bytes", -1), ("input_bytes", 6001),
        ("redaction_count", -1), ("redaction_count", 101),
    ],
)
def test_telemetry_event_rejects_out_of_range_counts(field, value):
    with pytest.raises(ValueError, match="telemetry"):
        _event(**{field: value})


def test_telemetry_aggregation_separates_recall_cohorts_and_calculates_p95():
    events = [
        _event(run_id=f"on-{index}", latency_ms=latency)
        for index, latency in enumerate((10, 20, 30, 40, 800), start=1)
    ] + [
        _event(run_id="off-1", mode="recall_off", status="empty", latency_ms=0, hint_count=0),
        _event(run_id="off-2", mode="recall_off", status="timeout", latency_ms=800, hint_count=0, fallback=True),
        _event(run_id="shadow-1", mode="shadow", status="degraded", latency_ms=15, hint_count=0, fallback=True),
        _event(run_id="shadow-2", mode="shadow", status="stale", latency_ms=16, hint_count=0, fallback=True),
        _event(run_id="shadow-3", mode="shadow", status="conflict", latency_ms=17, hint_count=0, fallback=True),
    ]

    aggregate = MemorySidecarTelemetryAggregator.aggregate(events)

    assert aggregate["schemaVersion"] == "memory-sidecar-telemetry-aggregate-v1"
    assert aggregate["eventCount"] == 10
    assert aggregate["cohorts"]["recall_on"]["p95LatencyMs"] == 800
    assert aggregate["cohorts"]["recall_on"]["statusCounts"]["ready"] == 5
    assert aggregate["cohorts"]["recall_off"]["statusCounts"]["empty"] == 1
    assert aggregate["cohorts"]["recall_off"]["statusCounts"]["timeout"] == 1
    assert aggregate["cohorts"]["shadow"]["statusCounts"] == {
        "ready": 0, "empty": 0, "timeout": 0, "degraded": 1, "stale": 1, "conflict": 1,
    }


def test_disabled_flags_do_not_invoke_sidecar_callback():
    calls = []
    flags = MemorySidecarFeatureFlags(recall_enabled=False, writer_enabled=False, shadow_mode=False)

    result = flags.invoke_recall(lambda: calls.append("called") or "unexpected")

    assert result is None
    assert calls == []
    assert flags.invoke_writer(lambda: calls.append("writer")) is False
    assert calls == []


def test_shadow_mode_invokes_recall_without_enabling_injection():
    calls = []
    flags = MemorySidecarFeatureFlags(recall_enabled=False, writer_enabled=False, shadow_mode=True)

    assert flags.invoke_recall(lambda: calls.append("shadow") or "observed") == "observed"
    assert calls == ["shadow"]
    assert flags.recall_mode == "shadow"


def test_shadow_ab_fixture_is_deterministic_and_keeps_ten_non_r2_profiles_equal():
    fixture = build_shadow_ab_fixture()

    assert len(fixture["profiles"]) == 10
    assert fixture["profiles"] == build_shadow_ab_fixture()["profiles"]
    assert all(profile["riskTier"] != "R2" for profile in fixture["profiles"])
    for profile in fixture["profiles"]:
        assert profile["recallOff"]["briefFingerprint"] == profile["recallOn"]["briefFingerprint"]
        assert profile["recallOff"]["head"] == profile["recallOn"]["head"]
        assert profile["recallOff"]["allowedFiles"] == profile["recallOn"]["allowedFiles"]
        assert profile["recallOff"]["commands"] == profile["recallOn"]["commands"]
    assert set(fixture["aggregate"]) == {"recallOff", "recallOn"}
    assert set(fixture["aggregate"]["recallOn"]) == {
        "estimatedInputTokens", "latencyMs", "explorationCount", "findings", "fallbackCount",
    }


def test_agent_runtime_persists_exact_bounded_sidecar_schema_and_rotates(tmp_path):
    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime")
    runtime.append_memory_sidecar_telemetry(_event())
    telemetry = tmp_path / ".nbs_agent_runtime/telemetry/memory_sidecar.jsonl"
    assert telemetry.exists()
    assert json.loads(telemetry.read_text(encoding="utf-8")) == _event().to_dict()

    telemetry.write_text("x" * (1024 * 1024), encoding="utf-8")
    runtime.append_memory_sidecar_telemetry(_event(run_id="rotated"))
    assert telemetry.with_name("memory_sidecar.jsonl.1").exists()
    assert json.loads(telemetry.read_text(encoding="utf-8"))["runId"] == "rotated"


def test_agent_runtime_rejects_non_event_or_extra_raw_content(tmp_path):
    runtime = AgentRuntime(tmp_path / ".nbs_agent_runtime")

    class RawTelemetry:
        def to_dict(self):
            return {"schemaVersion": "memory-sidecar-telemetry-v1", "rawQuery": "secret"}

    with pytest.raises(ValueError, match="telemetry event"):
        runtime.append_memory_sidecar_telemetry(RawTelemetry())
