from __future__ import annotations

import pytest

from backend.agents.session_binding import (
    binding_record_fingerprint,
    validate_session_bindings,
)


def _binding(slot: str, session: str, role: str, fingerprint: str) -> dict[str, str]:
    return {
        "slotId": slot,
        "sessionId": session,
        "role": role,
        "recordFingerprint": fingerprint,
    }


def test_same_slot_allows_one_session_across_roles():
    bindings = [
        _binding("task-a/0/recall_off", "s1", "observation", "o"),
        _binding("task-a/0/recall_off", "s1", "ledger", "l"),
        _binding("task-a/0/recall_off", "s1", "quality", "q"),
    ]

    assert validate_session_bindings(bindings) == ()


def test_one_session_cannot_cross_slots():
    bindings = [
        _binding("task-a/0/recall_off", "s1", "observation", "o"),
        _binding("task-a/0/recall_on", "s1", "observation", "o2"),
    ]

    assert "session_cross_slot" in validate_session_bindings(bindings)


def test_one_slot_cannot_repeat_a_role():
    bindings = [
        _binding("task-a/0/recall_off", "s1", "ledger", "l1"),
        _binding("task-a/0/recall_off", "s1", "ledger", "l1"),
    ]

    assert "duplicate_role" in validate_session_bindings(bindings)


def test_same_slot_role_cannot_change_record_fingerprint():
    bindings = [
        _binding("task-a/0/recall_off", "s1", "quality", "q1"),
        _binding("task-a/0/recall_off", "s1", "quality", "q2"),
    ]

    violations = validate_session_bindings(bindings)
    assert "duplicate_role" in violations
    assert "record_fingerprint_changed" in violations


def test_missing_or_malformed_fields_fail_closed_without_raising():
    violations = validate_session_bindings([
        {"slotId": "task-a/0/recall_off", "sessionId": "s1", "role": "observation"},
        _binding("task-a/0/recall_off", "s1", "unknown", "x"),
        "malformed",
    ])

    assert "missing_binding_field" in violations
    assert "unknown_role" in violations


def test_different_sessions_in_one_slot_are_a_binding_mismatch():
    bindings = [
        _binding("task-a/0/recall_off", "observation-session", "observation", "o"),
        _binding("task-a/0/recall_off", "ledger-session", "ledger", "l"),
    ]

    assert "binding_mismatch" in validate_session_bindings(bindings)


def test_binding_record_fingerprint_is_role_specific_and_deterministic():
    record = {"slot": "task-a/0/recall_off", "value": 3}

    first = binding_record_fingerprint(record, role="ledger")
    second = binding_record_fingerprint({"value": 3, "slot": "task-a/0/recall_off"}, role="ledger")

    assert first == second
    assert first != binding_record_fingerprint(record, role="quality")
    assert len(first) == 64


def test_binding_record_fingerprint_rejects_unknown_role():
    with pytest.raises(ValueError, match="role"):
        binding_record_fingerprint({}, role="unknown")
