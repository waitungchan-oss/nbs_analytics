from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Callable, Iterable, TypeVar


_SCHEMA_VERSION = "memory-sidecar-telemetry-v1"
_AGGREGATE_SCHEMA_VERSION = "memory-sidecar-telemetry-aggregate-v1"
_MODES = ("recall_on", "recall_off", "shadow")
_STATUSES = ("ready", "empty", "timeout", "degraded", "stale", "conflict")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_CALLBACK_RESULT = TypeVar("_CALLBACK_RESULT")


def _bounded_int(value: int, *, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"memory sidecar telemetry {name} is out of range")
    return value


def _fingerprint(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("memory sidecar telemetry query fingerprint is invalid")
    return value


@dataclass(frozen=True)
class MemorySidecarTelemetryEvent:
    run_id: str
    mode: str
    query_fingerprint: str
    status: str
    latency_ms: int
    hint_count: int
    input_bytes: int
    fallback: bool
    redaction_count: int

    @classmethod
    def from_parts(
        cls,
        *,
        run_id: str,
        mode: str,
        query_fingerprint: str,
        status: str,
        latency_ms: int,
        hint_count: int,
        input_bytes: int,
        fallback: bool,
        redaction_count: int,
    ) -> "MemorySidecarTelemetryEvent":
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("memory sidecar telemetry run id is invalid")
        if mode not in _MODES:
            raise ValueError("memory sidecar telemetry mode is invalid")
        if status not in _STATUSES:
            raise ValueError("memory sidecar telemetry status is invalid")
        if not isinstance(fallback, bool):
            raise ValueError("memory sidecar telemetry fallback is invalid")
        return cls(
            run_id=run_id,
            mode=mode,
            query_fingerprint=_fingerprint(query_fingerprint),
            status=status,
            latency_ms=_bounded_int(latency_ms, name="latency", maximum=800),
            hint_count=_bounded_int(hint_count, name="hint count", maximum=3),
            input_bytes=_bounded_int(input_bytes, name="input bytes", maximum=6000),
            fallback=fallback,
            redaction_count=_bounded_int(redaction_count, name="redaction count", maximum=100),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": _SCHEMA_VERSION,
            "runId": self.run_id,
            "mode": self.mode,
            "queryFingerprint": self.query_fingerprint,
            "status": self.status,
            "latencyMs": self.latency_ms,
            "hintCount": self.hint_count,
            "inputBytes": self.input_bytes,
            "fallback": self.fallback,
            "redactionCount": self.redaction_count,
        }


class MemorySidecarTelemetryAggregator:
    @staticmethod
    def aggregate(events: Iterable[MemorySidecarTelemetryEvent]) -> dict[str, object]:
        cohorts: dict[str, dict[str, object]] = {}
        total = 0
        for mode in _MODES:
            cohorts[mode] = {
                "eventCount": 0,
                "p95LatencyMs": 0,
                "hintCount": 0,
                "inputBytes": 0,
                "fallbackCount": 0,
                "redactionCount": 0,
                "statusCounts": {status: 0 for status in _STATUSES},
                "_latencies": [],
            }
        for event in events:
            if not isinstance(event, MemorySidecarTelemetryEvent):
                raise ValueError("memory sidecar telemetry event is invalid")
            cohort = cohorts[event.mode]
            cohort["eventCount"] += 1
            cohort["hintCount"] += event.hint_count
            cohort["inputBytes"] += event.input_bytes
            cohort["fallbackCount"] += int(event.fallback)
            cohort["redactionCount"] += event.redaction_count
            cohort["statusCounts"][event.status] += 1
            cohort["_latencies"].append(event.latency_ms)
            total += 1
        for cohort in cohorts.values():
            latencies = sorted(cohort.pop("_latencies"))
            if latencies:
                cohort["p95LatencyMs"] = latencies[(len(latencies) * 95 + 99) // 100 - 1]
        return {
            "schemaVersion": _AGGREGATE_SCHEMA_VERSION,
            "eventCount": total,
            "cohorts": cohorts,
        }


@dataclass(frozen=True)
class MemorySidecarFeatureFlags:
    recall_enabled: bool = False
    writer_enabled: bool = False
    shadow_mode: bool = True

    def __post_init__(self) -> None:
        if not all(isinstance(value, bool) for value in (self.recall_enabled, self.writer_enabled, self.shadow_mode)):
            raise ValueError("memory sidecar feature flags must be boolean")

    @property
    def recall_mode(self) -> str:
        if self.recall_enabled:
            return "recall_on"
        if self.shadow_mode:
            return "shadow"
        return "recall_off"

    def invoke_recall(self, callback: Callable[[], _CALLBACK_RESULT]) -> _CALLBACK_RESULT | None:
        if self.recall_mode == "recall_off":
            return None
        return callback()

    def invoke_writer(self, callback: Callable[[], object]) -> bool:
        if not self.writer_enabled:
            return False
        callback()
        return True


def _profile_identity(index: int) -> str:
    return hashlib.sha256(f"memory-sidecar-shadow-profile-{index}".encode("utf-8")).hexdigest()


def build_shadow_ab_fixture() -> dict[str, object]:
    """Return fixed, non-network A/B evidence for the pilot's ten non-R2 tasks."""
    profiles = []
    for index in range(1, 11):
        identity = _profile_identity(index)
        shared = {
            "briefFingerprint": identity,
            "head": f"fixture-head-{index:02d}",
            "allowedFiles": [f"backend/agents/fixture_{index}.py", f"tests/test_fixture_{index}.py"],
            "commands": [f"pytest tests/test_fixture_{index}.py -q"],
        }
        profiles.append({
            "taskId": f"shadow-profile-{index:02d}",
            "riskTier": "R1",
            "recallOff": {**shared, "estimatedInputTokens": 1000 + index * 10, "latencyMs": 0, "explorationCount": 4, "findings": 1, "fallback": False},
            "recallOn": {**shared, "estimatedInputTokens": 780 + index * 10, "latencyMs": 20 + index, "explorationCount": 3, "findings": 1, "fallback": False},
        })

    def summarize(cohort: str) -> dict[str, int]:
        rows = [profile[cohort] for profile in profiles]
        return {
            "estimatedInputTokens": sum(row["estimatedInputTokens"] for row in rows),
            "latencyMs": sum(row["latencyMs"] for row in rows),
            "explorationCount": sum(row["explorationCount"] for row in rows),
            "findings": sum(row["findings"] for row in rows),
            "fallbackCount": sum(int(row["fallback"]) for row in rows),
        }

    return {"schemaVersion": "memory-sidecar-shadow-ab-v1", "profiles": profiles, "aggregate": {"recallOff": summarize("recallOff"), "recallOn": summarize("recallOn")}}
