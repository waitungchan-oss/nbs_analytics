"""Bounded, versioned acceptance semantics for diagnostic evidence lineage."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .evidence_models import canonical_fingerprint


SCHEMA_VERSION = "acceptance-contract-v1"
AUTHORITY = "metadata"
CONTRACT_STATUSES = frozenset({"active", "superseded", "retired", "invalidated"})
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REQUIRED_KEYS = frozenset(
    {
        "schemaVersion",
        "contractId",
        "contractVersion",
        "scopeFingerprint",
        "semanticRulesFingerprint",
        "datasetSnapshotFingerprint",
        "supersedesContractFingerprint",
        "status",
        "authority",
        "evidenceFingerprint",
    }
)


def _require_sha(value: Any, field: str, *, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not _SHA64.fullmatch(value):
        raise ValueError(f"{field} is invalid")


def _require_identifier(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} is invalid")


def _unsigned(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in payload if key != "evidenceFingerprint"}


def build_acceptance_contract(
    *,
    contract_id: str,
    contract_version: str,
    scope_fingerprint: str,
    semantic_rules_fingerprint: str,
    dataset_snapshot_fingerprint: str,
    supersedes_contract_fingerprint: str | None,
    status: str,
) -> dict[str, Any]:
    """Build a canonical contract artifact without side effects."""
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "contractId": contract_id,
        "contractVersion": contract_version,
        "scopeFingerprint": scope_fingerprint,
        "semanticRulesFingerprint": semantic_rules_fingerprint,
        "datasetSnapshotFingerprint": dataset_snapshot_fingerprint,
        "supersedesContractFingerprint": supersedes_contract_fingerprint,
        "status": status,
        "authority": AUTHORITY,
    }
    _validate_unsigned(value)
    return {**value, "evidenceFingerprint": canonical_fingerprint(value)}


def _validate_unsigned(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping) or set(payload) != (_REQUIRED_KEYS - {"evidenceFingerprint"}):
        raise ValueError("acceptance contract schema is invalid")
    if payload["schemaVersion"] != SCHEMA_VERSION or payload["authority"] != AUTHORITY:
        raise ValueError("acceptance contract authority is invalid")
    _require_identifier(payload["contractId"], "contractId")
    _require_identifier(payload["contractVersion"], "contractVersion")
    _require_sha(payload["scopeFingerprint"], "scopeFingerprint")
    _require_sha(payload["semanticRulesFingerprint"], "semanticRulesFingerprint")
    _require_sha(payload["datasetSnapshotFingerprint"], "datasetSnapshotFingerprint")
    _require_sha(payload["supersedesContractFingerprint"], "supersedesContractFingerprint", allow_none=True)
    if not isinstance(payload["status"], str) or payload["status"] not in CONTRACT_STATUSES:
        raise ValueError("status is invalid")


def validate_acceptance_contract(payload: Mapping[str, Any]) -> None:
    """Strictly validate a contract artifact and its canonical fingerprint."""
    if not isinstance(payload, Mapping) or set(payload) != _REQUIRED_KEYS:
        raise ValueError("acceptance contract schema is invalid")
    _validate_unsigned(_unsigned(payload))
    _require_sha(payload["evidenceFingerprint"], "evidenceFingerprint")
    if canonical_fingerprint(_unsigned(payload)) != payload["evidenceFingerprint"]:
        raise ValueError("acceptance contract evidence fingerprint mismatch")


def contract_fingerprint(payload: Mapping[str, Any]) -> str:
    """Return the canonical identity of a validated contract artifact."""
    validate_acceptance_contract(payload)
    return canonical_fingerprint(_unsigned(payload))
