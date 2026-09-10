"""Immutable experiment manifests and provenance checks for offline evaluation."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from .agent_eval_models import canonical_bytes
from .evidence_models import canonical_fingerprint


SCHEMA = "agent-eval-manifest-v1"
_FINGERPRINT_FIELDS = {
    "settingsFingerprint", "sourceCommit", "dirtyFingerprint", "workloadFingerprint",
    "catalogFingerprint", "policyFingerprint", "allowedFilesFingerprint",
    "commandsFingerprint", "datasetFingerprint", "rubricFingerprint",
}
_IDENTITY_FIELDS = {"projectId", "consumerId", "provider", "model", *_FINGERPRINT_FIELDS}
_MANIFEST_FIELDS = {
    "schemaVersion", "experimentId", "identity", "caseIds", "splits", "repeatCount",
    "plannedSlots", "orderPolicy", "isolationPolicy", "createdAt", "expiresAt",
    "artifactBudgetBytes", "producerRegistry", "authorization", "manifestFingerprint",
}
_AUTH_FIELDS = {
    "scope", "runnerIdentity", "manifestFingerprint", "maxTaskTokens", "maxBatchTokens",
    "maxBatchCost", "currency", "priceTableFingerprint", "timeoutMs", "maxRetries",
}
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:@-")
_TERMINAL_STATES = {"completed", "failed", "timeout", "budget_exceeded", "missing"}


def _fail(code: str) -> None:
    raise ValueError(code)


def _id(value: Any, code: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 160 or value in {".", ".."} or any(c not in _ID_CHARS for c in value):
        _fail(code)


def _fp(value: Any, code: str, length: int = 64) -> None:
    if not isinstance(value, str) or len(value) != length or any(c not in "0123456789abcdef" for c in value):
        _fail(code)


def _timestamp(value: Any, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(code)
    if parsed.utcoffset() is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def planned_slots(case_ids: list[str], repeats: int) -> list[dict]:
    if not isinstance(case_ids, list) or not 1 <= len(case_ids) <= 12 or len(set(case_ids)) != len(case_ids):
        _fail("invalid_case_ids")
    if any(not isinstance(item, str) or not item for item in case_ids):
        _fail("invalid_case_ids")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 3:
        _fail("invalid_repeat_count")
    slots: list[dict] = []
    for case_index, task_id in enumerate(case_ids):
        for repeat_index in range(repeats):
            order = ("recall_off", "recall_on")
            if (case_index + repeat_index) % 2:
                order = tuple(reversed(order))
            slots.extend({"taskId": task_id, "repeatIndex": repeat_index, "cohort": cohort} for cohort in order)
    return slots


def _unsigned_manifest(value: dict) -> dict:
    return {key: value[key] for key in _MANIFEST_FIELDS if key != "manifestFingerprint"}


def validate_manifest(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != _MANIFEST_FIELDS:
        _fail("invalid_manifest_fields")
    if value["schemaVersion"] != SCHEMA:
        _fail("invalid_manifest_schema")
    _id(value["experimentId"], "invalid_experiment_id")
    identity = value["identity"]
    if not isinstance(identity, dict) or set(identity) != _IDENTITY_FIELDS:
        _fail("invalid_manifest_identity")
    for key in ("projectId", "consumerId", "provider", "model"):
        _id(identity[key], f"invalid_{key}")
    for key in _FINGERPRINT_FIELDS:
        _fp(identity[key], f"invalid_{key}", 40 if key == "sourceCommit" else 64)
    if not isinstance(value["caseIds"], list) or len(value["caseIds"]) != 12 or len(set(value["caseIds"])) != 12:
        _fail("invalid_case_ids")
    for case_id in value["caseIds"]:
        _id(case_id, "invalid_case_id")
    splits = value["splits"]
    if not isinstance(splits, dict) or set(splits) != {"dev", "holdout"}:
        _fail("invalid_splits")
    if splits["dev"] != 4 or splits["holdout"] != 8:
        _fail("invalid_splits")
    repeats = value["repeatCount"]
    if isinstance(repeats, bool) or repeats != 3:
        _fail("invalid_repeat_count")
    expected = planned_slots(value["caseIds"], repeats)
    if value["plannedSlots"] != 72 or value["plannedSlots"] != len(expected):
        _fail("invalid_planned_slots")
    if value["orderPolicy"] != "alternating-v1" or value["isolationPolicy"] != "fresh-session-v1":
        _fail("invalid_experiment_policy")
    created = _timestamp(value["createdAt"], "invalid_created_at")
    expires = _timestamp(value["expiresAt"], "invalid_expires_at")
    if expires <= created or (expires - created).total_seconds() > 30 * 86400:
        _fail("invalid_expiry")
    budget = value["artifactBudgetBytes"]
    if isinstance(budget, bool) or not isinstance(budget, int) or budget != 33_554_432:
        _fail("invalid_artifact_budget")
    registry = value["producerRegistry"]
    if not isinstance(registry, dict) or not registry:
        _fail("invalid_producer_registry")
    for producer_id, record in registry.items():
        _id(producer_id, "invalid_producer_id")
        if not isinstance(record, dict) or set(record) != {"sourceSchema", "producerFingerprint"}:
            _fail("invalid_producer_registry")
        _id(record["sourceSchema"], "invalid_source_schema")
        _fp(record["producerFingerprint"], "invalid_producer_fingerprint")
    authorization = value["authorization"]
    if not isinstance(authorization, dict) or set(authorization) != _AUTH_FIELDS:
        _fail("invalid_authorization")
    for key in ("scope", "runnerIdentity", "currency"):
        if authorization[key] is not None:
            _id(authorization[key], f"invalid_{key}")
    for key in ("manifestFingerprint", "priceTableFingerprint"):
        if authorization[key] is not None:
            _fp(authorization[key], f"invalid_{key}")
    for key in ("maxTaskTokens", "maxBatchTokens", "timeoutMs", "maxRetries"):
        number = authorization[key]
        if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number <= 0):
            _fail(f"invalid_{key}")
    if authorization["maxBatchCost"] is not None and (isinstance(authorization["maxBatchCost"], bool) or not isinstance(authorization["maxBatchCost"], (int, float)) or authorization["maxBatchCost"] <= 0):
        _fail("invalid_max_batch_cost")
    _fp(value["manifestFingerprint"], "invalid_manifest_fingerprint")
    if value["manifestFingerprint"] != canonical_fingerprint(_unsigned_manifest(value)):
        _fail("manifest_fingerprint_mismatch")
    return copy.deepcopy(value)


def verify_binding(payload: dict, *, manifest: dict, artifact_ref: dict, producer_registry: dict) -> dict:
    checked = validate_manifest(manifest)
    if not isinstance(payload, dict) or not isinstance(artifact_ref, dict):
        _fail("invalid_binding")
    if producer_registry != checked["producerRegistry"]:
        _fail("producer_registry_mismatch")
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        _fail("invalid_binding")
    if not isinstance(identity.get("sessionId"), str) or not identity["sessionId"]:
        _fail("invalid_session_id")
    for key in _FINGERPRINT_FIELDS | {"projectId", "consumerId", "provider", "model"}:
        expected = checked["identity"][key]
        if identity.get(key) != expected:
            _fail("binding_mismatch")
    record = checked["producerRegistry"].get(payload.get("producerId"))
    if record is None or payload.get("sourceSchema") != record["sourceSchema"] or payload.get("producerFingerprint") != record["producerFingerprint"]:
        _fail("producer_binding_mismatch")
    if payload.get("artifactRef") != artifact_ref:
        _fail("artifact_binding_mismatch")
    if identity.get("cohort") not in {"recall_off", "recall_on"}:
        _fail("invalid_cohort")
    planned = {(slot["taskId"], slot["repeatIndex"], slot["cohort"]) for slot in planned_slots(checked["caseIds"], checked["repeatCount"])}
    if (identity.get("taskId"), identity.get("repeatIndex"), identity.get("cohort")) not in planned:
        _fail("invalid_slot")
    return {"manifestFingerprint": checked["manifestFingerprint"], "producerId": payload["producerId"], "artifactRef": copy.deepcopy(artifact_ref)}


def live_preflight(manifest: dict, *, authorization_evidence: dict | None) -> list[str]:
    if authorization_evidence is None:
        return ["blocked_missing_authorization"]
    try:
        checked = validate_manifest(manifest)
    except ValueError as exc:
        return [str(exc)]
    if not isinstance(authorization_evidence, dict):
        return ["blocked_invalid_authorization"]
    required = {"runnerIdentity", "manifestFingerprint", "maxTaskTokens", "maxBatchTokens", "maxBatchCost", "currency", "priceTableFingerprint", "timeoutMs", "maxRetries", "sourceFingerprint", "freshSession"}
    missing = sorted(required - set(authorization_evidence))
    if missing:
        return [f"blocked_missing_{missing[0]}"]
    if not isinstance(authorization_evidence["freshSession"], bool):
        return ["blocked_invalid_authorization"]
    if not authorization_evidence["freshSession"]:
        return ["blocked_session_reuse"]
    auth = checked["authorization"]
    budget_fields = ("runnerIdentity", "maxTaskTokens", "maxBatchTokens", "maxBatchCost", "currency",
                     "priceTableFingerprint", "timeoutMs", "maxRetries")
    if any(auth[field] is None for field in budget_fields):
        return ["blocked_missing_budget"]
    if auth["manifestFingerprint"] != checked["manifestFingerprint"]:
        return ["blocked_authorization_mismatch"]
    if authorization_evidence["manifestFingerprint"] != checked["manifestFingerprint"]:
        return ["blocked_manifest_drift"]
    if authorization_evidence["runnerIdentity"] != checked["authorization"]["runnerIdentity"]:
        return ["blocked_runner_mismatch"]
    if authorization_evidence["sourceFingerprint"] != checked["identity"]["workloadFingerprint"]:
        return ["blocked_source_drift"]
    for field in budget_fields:
        if authorization_evidence[field] != auth[field]:
            return ["blocked_authorization_mismatch"]
    return []
