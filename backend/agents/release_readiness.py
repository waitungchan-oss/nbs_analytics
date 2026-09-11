"""Read-only mapping between verification completion and release readiness."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .evidence_models import canonical_fingerprint
from .release_gate_models import ReleaseGateValidationError, validate_release_gate_aggregate


VERIFICATION_INCOMPLETE = "verification_incomplete"
VERIFICATION_COMPLETE = "verification_complete"
RELEASE_READY = "release_ready"
BLOCKED = "blocked"

_REQUIRED_GATES = ("strictReview", "fullPytest", "hermes")
_TERMINAL_BLOCKED = {
    "blocked_runner_capability", "blocked_runner_transport", "blocked_source_probe",
    "stale_source", "invalid_evidence", "verification_failed", "hermes_failed",
    "review_changes_required", "context_overflow",
}


def _gate_passed(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("gateStatus") == "pass" or value.get("status") == "pass"
    return value == "pass"


def _completion_payload(session: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if session.get("schemaVersion") == "completion-attestation-v1":
        return session
    for key in ("completion", "completionAttestation"):
        value = session.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _required_gate_statuses(session: Mapping[str, Any], completion: Mapping[str, Any] | None) -> dict[str, Any]:
    gates = session.get("gates")
    if isinstance(gates, Mapping):
        return {name: gates.get(name) for name in _REQUIRED_GATES}
    if isinstance(completion, Mapping) and isinstance(completion.get("requiredGates"), Mapping):
        return {name: completion["requiredGates"].get(name) for name in _REQUIRED_GATES}
    return {name: None for name in _REQUIRED_GATES}


_SESSION_SOURCE_KEYS = (
    "baseSha", "headSha", "briefPath", "briefFingerprint",
    "worktreeFingerprint", "diffFingerprint", "contractFingerprint",
    "policyFingerprint",
)


def _session_source_fingerprint(session: Mapping[str, Any]) -> str | None:
    """Derive the canonical source identity from verification-session-v1."""
    if session.get("schemaVersion") == "verification-session-v1":
        if not all(isinstance(session.get(key), str) for key in _SESSION_SOURCE_KEYS):
            return None
        return canonical_fingerprint({key: session[key] for key in _SESSION_SOURCE_KEYS})
    value = session.get("sourceFingerprint")
    return value if isinstance(value, str) else None


def _verification_stage(session: Mapping[str, Any]) -> str:
    if not isinstance(session, Mapping):
        return BLOCKED
    status = session.get("status")
    if not isinstance(status, str):
        return BLOCKED
    if status in _TERMINAL_BLOCKED:
        return BLOCKED
    completion = _completion_payload(session)
    required = _required_gate_statuses(session, completion)
    if completion is None:
        if status == "complete":
            return BLOCKED
        return VERIFICATION_INCOMPLETE
    if completion.get("schemaVersion") != "completion-attestation-v1":
        return BLOCKED
    expected_source = _session_source_fingerprint(session)
    if (
        not isinstance(session.get("sessionId"), str)
        or completion.get("sessionId") != session.get("sessionId")
        or expected_source is None
        or completion.get("sourceFingerprint") != expected_source
    ):
        return BLOCKED
    completion_status = completion.get("status")
    if completion_status in {"blocked", "failed"}:
        return BLOCKED
    if completion_status != "complete":
        return VERIFICATION_INCOMPLETE
    if status != "complete":
        return VERIFICATION_INCOMPLETE
    if not all(_gate_passed(required[name]) for name in _REQUIRED_GATES):
        return BLOCKED
    return VERIFICATION_COMPLETE


def classify_acceptance_stage(
    session: Mapping[str, Any],
    release: Mapping[str, Any] | None = None,
) -> str:
    """Classify an operator-facing stage without granting gate authority.

    A completion attestation can only mean ``verification_complete``.  The
    ``release_ready`` stage additionally requires a fresh, identity-matching
    ``release-gate-result-v1`` aggregate whose three child gates are PASS.
    Malformed or mismatched input is always ``blocked``.
    """
    if not isinstance(session, Mapping):
        return BLOCKED
    stage = _verification_stage(session)
    if release is None:
        return stage
    if stage != VERIFICATION_COMPLETE:
        return BLOCKED
    if not isinstance(release, Mapping):
        return BLOCKED

    expected_commit = session.get("headSha") or session.get("commitSha")
    expected_source = _session_source_fingerprint(session)
    if not isinstance(expected_commit, str) or not isinstance(expected_source, str):
        return BLOCKED
    if release.get("schemaVersion") != "release-gate-result-v1":
        return BLOCKED
    try:
        aggregate = validate_release_gate_aggregate(
            release,
            expected_commit,
            datetime.now(timezone.utc),
        )
    except (ReleaseGateValidationError, TypeError, ValueError):
        return BLOCKED
    if aggregate.status != "PASS" or aggregate.source_fingerprint != expected_source:
        return BLOCKED
    return RELEASE_READY
