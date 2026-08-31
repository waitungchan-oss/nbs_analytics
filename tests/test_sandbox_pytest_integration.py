from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agents.sandbox_capability_preflight import SandboxCapabilityEvidence
from tests.conftest import (
    render_sandbox_blocker,
    sandbox_gate_status,
    sandbox_markexpr_excludes,
)


def _blocked() -> SandboxCapabilityEvidence:
    return SandboxCapabilityEvidence._build(
        "blocked_environment", "darwin", "a" * 64, "b" * 64, "c" * 64,
        {"applicationApplied": False, "filesystemPolicyEnforced": False, "processPolicyEnforced": False, "networkPolicyEnforced": False},
        "sandbox_apply_denied", ("outer sandbox denied",), "2026-08-31T00:00:00Z", "2026-08-31T00:00:00Z",
    )


def test_blocked_preflight_render_contains_one_remediation_without_sensitive_path() -> None:
    message = render_sandbox_blocker(_blocked())
    assert "blocked_environment" in message
    assert "sandbox_apply_denied" in message
    assert "qualified macOS runner" in message
    assert "/Users/" not in message


def test_sandbox_gate_status_is_required_and_blocked_is_not_pass() -> None:
    config = SimpleNamespace(option=lambda name: "required")
    assert sandbox_gate_status(config, _blocked()) == "blocked_environment"


def test_marker_is_registered() -> None:
    assert pytest.mark.sandbox


def test_not_sandbox_mark_expression_is_detected_without_synthetic_blocker() -> None:
    config = SimpleNamespace(option=SimpleNamespace(markexpr="not sandbox"))

    assert sandbox_markexpr_excludes(config) is True


def test_default_auto_gate_skips_blocked_sandbox_pack_instead_of_failing() -> None:
    config = SimpleNamespace(
        option=SimpleNamespace(markexpr=""),
        getoption=lambda name, default=None: default,
    )

    assert sandbox_gate_status(config, _blocked()) == "blocked_environment"
