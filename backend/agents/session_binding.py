"""Pure truth-table validation for slot/session evidence bindings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .evidence_models import canonical_fingerprint


_ROLES = frozenset({"observation", "ledger", "quality"})
_FIELDS = frozenset({"slotId", "sessionId", "role", "recordFingerprint"})
_MAX_FIELD_LENGTH = 256


def _bounded_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_FIELD_LENGTH
        and bool(value.strip())
    )


def _normalize_binding(value: object) -> tuple[dict[str, str] | None, set[str]]:
    if not isinstance(value, Mapping):
        return None, {"missing_binding_field"}
    missing = _FIELDS - set(value)
    if missing:
        return None, {"missing_binding_field"}
    if any(not _bounded_text(value[field]) for field in _FIELDS):
        return None, {"invalid_binding_field"}
    role = value["role"]
    if role not in _ROLES:
        return None, {"unknown_role"}
    return {field: value[field] for field in _FIELDS}, set()


def validate_session_bindings(
    bindings: Sequence[Mapping[str, str]],
) -> tuple[str, ...]:
    """Return stable violation codes for normalized slot/session bindings."""
    violations: set[str] = set()
    if isinstance(bindings, (str, bytes)) or not isinstance(bindings, Sequence):
        return ("missing_binding_field",)

    session_slots: dict[str, str] = {}
    slot_sessions: dict[str, str] = {}
    slot_roles: dict[tuple[str, str], str] = {}
    for value in bindings:
        normalized, local_violations = _normalize_binding(value)
        violations.update(local_violations)
        if normalized is None:
            continue
        slot = normalized["slotId"]
        session = normalized["sessionId"]
        role = normalized["role"]
        record_fingerprint = normalized["recordFingerprint"]

        previous_slot = session_slots.get(session)
        if previous_slot is not None and previous_slot != slot:
            violations.add("session_cross_slot")
        else:
            session_slots[session] = slot

        previous_session = slot_sessions.get(slot)
        if previous_session is not None and previous_session != session:
            violations.add("binding_mismatch")
        else:
            slot_sessions[slot] = session

        identity = (slot, role)
        previous_fingerprint = slot_roles.get(identity)
        if previous_fingerprint is not None:
            violations.add("duplicate_role")
            if previous_fingerprint != record_fingerprint:
                violations.add("record_fingerprint_changed")
        else:
            slot_roles[identity] = record_fingerprint

    return tuple(sorted(violations))


def binding_record_fingerprint(record: Mapping[str, Any], *, role: str) -> str:
    """Return a deterministic role-bound fingerprint for one bounded record."""
    if role not in _ROLES:
        raise ValueError("role is invalid")
    if not isinstance(record, Mapping):
        raise ValueError("record must be a mapping")
    try:
        return canonical_fingerprint({"role": role, "record": dict(record)})
    except (TypeError, ValueError) as exc:
        raise ValueError("record is not canonically fingerprintable") from exc
