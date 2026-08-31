import pytest


def _coverage(**overrides):
    value = {
        "targetedTests": "pass",
        "compileStatic": "pass",
        "diffCheck": "pass",
        "runnerCapability": "pass",
        "contextCompatibility": "pass",
        "governanceLineage": "pass",
        "memoryReadiness": "ready",
    }
    value.update(overrides)
    return value


def _payload(**overrides):
    value = {
        "schemaVersion": "strict-review-preflight-v1",
        "status": "ready",
        "sessionId": "session-1",
        "sourceFingerprint": "a" * 64,
        "bundleFingerprint": "b" * 64,
        "changedFiles": ["backend/agents/review_agent_service.py"],
        "coverage": _coverage(),
        "generatedEvidence": ["targeted-tests", "python-compile", "git-diff-check"],
        "verificationPath": ".nbs_agent_runtime/verification_sessions/session-1/verification-v1.json",
        "diagnostics": [],
        "createdAt": "2026-08-31T00:00:00Z",
    }
    value.update(overrides)
    return value


def test_preflight_result_serializes_the_strict_public_contract():
    from backend.agents.strict_review_preflight_models import PreflightResult, validate_preflight_result

    result = PreflightResult.from_dict(_payload())

    assert validate_preflight_result(result.to_dict())["status"] == "ready"
    assert result.to_dict()["schemaVersion"] == "strict-review-preflight-v1"


def test_preflight_result_rejects_unknown_top_level_fields():
    from backend.agents.strict_review_preflight_models import validate_preflight_result

    with pytest.raises(ValueError, match="schema"):
        validate_preflight_result(_payload(unknown="not-allowed"))


def test_preflight_result_rejects_invalid_status_and_fingerprint():
    from backend.agents.strict_review_preflight_models import validate_preflight_result

    with pytest.raises(ValueError, match="status"):
        validate_preflight_result(_payload(status="passed"))
    with pytest.raises(ValueError, match="fingerprint"):
        validate_preflight_result(_payload(sourceFingerprint="not-a-sha"))


def test_preflight_fingerprint_is_stable_for_same_payload():
    from backend.agents.strict_review_preflight_models import build_preflight_fingerprint

    first = build_preflight_fingerprint(_payload())
    second = build_preflight_fingerprint(_payload())

    assert first == second
    assert len(first) == 64


def test_preflight_result_bounds_diagnostics():
    from backend.agents.strict_review_preflight_models import validate_preflight_result

    with pytest.raises(ValueError, match="diagnostic"):
        validate_preflight_result(_payload(diagnostics=["x" * 513]))
