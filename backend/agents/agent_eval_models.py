"""Bounded, source-aware models for offline agent evaluation artifacts."""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any


SCHEMA = "agent-eval-observation-v1"
USAGE_FIELDS = {
    "measuredInputTokens", "measuredOutputTokens", "estimatedInputTokens",
    "estimatedOutputTokens", "cachedInputTokens", "reasoningTokens",
    "estimatorVersion", "source", "cachedIsInputSubset", "reasoningIsOutputSubset",
}
IDENTITY_FIELDS = {
    "projectId", "consumerId", "runId", "sessionId", "taskId", "repeatIndex",
    "cohort", "provider", "model", "settingsFingerprint", "sourceCommit",
    "dirtyFingerprint", "workloadFingerprint", "catalogFingerprint",
    "policyFingerprint", "allowedFilesFingerprint", "commandsFingerprint",
}
OBSERVATION_FIELDS = {
    "schemaVersion", "identity", "callId", "origin", "producerId",
    "producerFingerprint", "sourceSchema", "artifactRef", "startedAt", "endedAt",
    "result", "usage", "callLatencyMs", "legacyField", "diagnostics",
}
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$")
REASON_CODES = {
    "missing_usage", "unknown_producer", "invalid_binding", "source_drift",
    "duplicate_call", "unexpected_call", "missing_ledger", "missing_quality",
    "insufficient_samples", "synthetic_only", "timeout", "budget_exceeded",
}


def _fail(code: str) -> None:
    raise ValueError(code)


def _exact(value: Any, fields: set[str], code: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)


def _safe_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        _fail(f"invalid_{name}")
    return value


def _fingerprint(value: Any, name: str) -> str:
    if not isinstance(value, str) or not FINGERPRINT_RE.fullmatch(value):
        _fail(f"invalid_{name}")
    return value


def canonical_bytes(value: dict) -> bytes:
    """Serialize JSON deterministically without accepting NaN or Infinity."""
    if not isinstance(value, dict):
        _fail("invalid_object")
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("non_finite")


def _optional_nonnegative_int(value: Any, name: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100_000_000):
        _fail(f"invalid_{name}")


def validate_usage(value: dict) -> dict:
    _exact(value, USAGE_FIELDS, "invalid_usage_fields")
    for name in (
        "measuredInputTokens", "measuredOutputTokens", "estimatedInputTokens",
        "estimatedOutputTokens", "cachedInputTokens", "reasoningTokens",
    ):
        _optional_nonnegative_int(value[name], name)
    source = value["source"]
    if source not in {"provider_usage", "estimate", "missing"}:
        _fail("invalid_usage_source")
    estimator = value["estimatorVersion"]
    if estimator is not None:
        _safe_id(estimator, "estimator_version")
    for name in ("cachedIsInputSubset", "reasoningIsOutputSubset"):
        if value[name] is not None and not isinstance(value[name], bool):
            _fail(f"invalid_{name}")
    if source == "provider_usage" and (value["measuredInputTokens"] is None or value["measuredOutputTokens"] is None):
        _fail("provider_usage_missing_measured")
    if source == "estimate" and value["measuredInputTokens"] is not None:
        _fail("estimate_has_measured")
    return copy.deepcopy(value)


def _validate_timestamp(value: Any, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        _fail(f"invalid_{name}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(f"invalid_{name}")
    if parsed.utcoffset() is None:
        _fail(f"invalid_{name}_timezone")


def _validate_path(value: Any) -> None:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        _fail("unsafe_path")
    path = PurePosixPath(value)
    if value.startswith("/") or "\\" in value or ".." in path.parts or ":" in value.split("/", 1)[0]:
        _fail("unsafe_path")


def _validate_artifact(value: Any) -> dict:
    _exact(value, {"path", "sha256"}, "invalid_artifact_ref")
    _validate_path(value["path"])
    _fingerprint(value["sha256"], "artifact_sha256")
    return value


def _validate_identity(value: Any) -> dict:
    _exact(value, IDENTITY_FIELDS, "invalid_identity_fields")
    for name in IDENTITY_FIELDS - {"repeatIndex"}:
        _safe_id(value[name], name)
    if not isinstance(value["repeatIndex"], int) or isinstance(value["repeatIndex"], bool) or not 0 <= value["repeatIndex"] <= 2:
        _fail("invalid_repeat_index")
    if value["cohort"] not in {"recall_off", "recall_on"}:
        _fail("invalid_cohort")
    _fingerprint(value["settingsFingerprint"], "settings_fingerprint")
    _fingerprint(value["dirtyFingerprint"], "dirty_fingerprint")
    _fingerprint(value["workloadFingerprint"], "workload_fingerprint")
    _fingerprint(value["catalogFingerprint"], "catalog_fingerprint")
    _fingerprint(value["policyFingerprint"], "policy_fingerprint")
    _fingerprint(value["allowedFilesFingerprint"], "allowed_files_fingerprint")
    _fingerprint(value["commandsFingerprint"], "commands_fingerprint")
    if not COMMIT_RE.fullmatch(value["sourceCommit"]):
        _fail("invalid_source_commit")
    return value


def validate_observation(value: dict) -> dict:
    _exact(value, OBSERVATION_FIELDS, "invalid_observation_fields")
    if value["schemaVersion"] != SCHEMA:
        _fail("invalid_observation_schema")
    _validate_identity(value["identity"])
    for name in ("callId", "producerId", "sourceSchema"):
        _safe_id(value[name], name)
    _fingerprint(value["producerFingerprint"], "producer_fingerprint")
    if value["origin"] not in {"real", "synthetic"}:
        _fail("invalid_origin")
    _validate_artifact(value["artifactRef"])
    _validate_timestamp(value["startedAt"], "started_at")
    _validate_timestamp(value["endedAt"], "ended_at")
    if value["startedAt"] and value["endedAt"]:
        start = datetime.fromisoformat(value["startedAt"])
        end = datetime.fromisoformat(value["endedAt"])
        if end < start:
            _fail("invalid_time_order")
    if not isinstance(value["result"], str) or not value["result"]:
        _fail("invalid_result")
    validate_usage(value["usage"])
    latency = value["callLatencyMs"]
    if latency is not None and (isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0 or latency > 604_800_000):
        _fail("invalid_call_latency")
    if value["legacyField"] is not None:
        _safe_id(value["legacyField"], "legacy_field")
    diagnostics = value["diagnostics"]
    if not isinstance(diagnostics, list) or len(diagnostics) > 16 or any(item not in REASON_CODES for item in diagnostics):
        _fail("invalid_diagnostics")
    return copy.deepcopy(value)
