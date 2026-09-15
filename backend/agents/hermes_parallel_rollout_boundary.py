"""Read-only Hermes boundary for the parallel acceptance candidate artifact.

This module validates an already-produced diagnostic artifact.  It deliberately
does not run pytest, write evidence, invoke memory providers, or change any
release/session state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.agents.acceptance_parallel_rollout import (
    AUTHORITY,
    POPULATION_KIND,
    ROLLOUT_MODE,
    SCHEMA_VERSION,
    validate_parallel_rollout_evidence,
)


def _blocked(code: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "failureCode": code,
        "authority": AUTHORITY,
        "formalReleaseEnabled": False,
        "populationKind": payload.get("populationKind") if payload is not None else None,
    }


def _failure_code(message: str) -> str:
    if "population kind" in message:
        return "population_kind_mismatch"
    if "formal release" in message:
        return "formal_release_forbidden"
    if "authority" in message:
        return "authority_mismatch"
    if "schema" in message:
        return "schema_invalid"
    if "fingerprint" in message:
        return "evidence_fingerprint_mismatch"
    if "lineage" in message or "source identity" in message:
        return "lineage_invalid"
    return "evidence_invalid"


def validate_parallel_rollout_boundary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a candidate without promoting it or mutating its input.

    Hermes can report that the diagnostic artifact is internally valid, but
    this result is never a formal release gate.  Population separation is
    checked before the generic validator so an otherwise well-formed 72-slot
    artifact cannot enter the Full pytest comparison path.
    """
    if not isinstance(payload, Mapping):
        return _blocked("schema_invalid")
    if payload.get("populationKind") != POPULATION_KIND:
        return _blocked("population_kind_mismatch", payload)
    if payload.get("formalReleaseEnabled") is not False:
        return _blocked("formal_release_forbidden", payload)
    if payload.get("authority") != AUTHORITY or payload.get("rolloutMode") != ROLLOUT_MODE:
        return _blocked("authority_mismatch", payload)

    try:
        validate_parallel_rollout_evidence(payload)
    except ValueError as exc:
        return _blocked(_failure_code(str(exc)), payload)

    result = {
        "status": payload["status"],
        "failureCode": None if payload["status"] == "PASS" else (payload["blockers"] or ["candidate_blocked"])[0],
        "schemaVersion": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "formalReleaseEnabled": False,
        "populationKind": POPULATION_KIND,
        "evidenceFingerprint": payload["evidenceFingerprint"],
    }
    return result

