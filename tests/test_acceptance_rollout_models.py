from __future__ import annotations

import pytest

from backend.agents.evidence_models import canonical_fingerprint
from backend.agents.release_gate_models import ReleaseGateValidationError, validate_release_gate_evidence
from backend.agents.acceptance_rollout_models import (
    RolloutConfig,
    build_rollout_preflight,
    validate_rollout_config,
    validate_rollout_preflight,
)


COMMIT = "a" * 40
SOURCE = "b" * 64


def _manifest() -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "pytest-test-manifest-v1",
        "status": "PASS",
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": ["tests/test_example.py::test_one"],
    }
    payload["manifestFingerprint"] = canonical_fingerprint({
        "schemaVersion": payload["schemaVersion"],
        "commitSha": COMMIT,
        "sourceFingerprint": SOURCE,
        "nodeids": payload["nodeids"],
    })
    return payload


def _ready_kwargs() -> dict[str, object]:
    binding = {"commitSha": COMMIT, "sourceFingerprint": SOURCE}
    return {
        "runner_capability": {"sourceBinding": binding, "sandbox": "available", "interpreter": "qualified"},
        "isolation": {
            "sourceBinding": binding,
            "fixtureRoots": "unique",
            "sqlite": "isolated",
            "cache": "isolated",
            "processes": "profile-bound",
        },
        "ui_cache": {"sourceBinding": binding, "status": "PASS", "sourceMatched": True},
    }


def test_shards_are_disabled_by_default():
    config = RolloutConfig.from_environment({})

    assert config.shards_enabled is False
    assert config.formal_release_enabled is False
    validate_rollout_config(config)


def test_invalid_true_value_is_rejected():
    with pytest.raises(ValueError, match="ACCEPTANCE_SHARDS_ENABLED"):
        RolloutConfig.from_environment({"ACCEPTANCE_SHARDS_ENABLED": "yes"})


def test_preflight_blocks_ui_cache_token_mismatch():
    payload = build_rollout_preflight(
        config=RolloutConfig.disabled(),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        runner_capability={
            "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
            "sandbox": "available", "interpreter": "qualified",
        },
        isolation={
            "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
            "fixtureRoots": "unique",
            "sqlite": "isolated",
            "cache": "isolated",
            "processes": "profile-bound",
        },
        ui_cache={
            "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
            "status": "mismatch", "reason": "revenue_token_mismatch",
        },
    )

    assert payload["status"] == "BLOCKED"
    assert "ui_cache_mismatch" in payload["blockers"]
    validate_rollout_preflight(payload)


def test_source_bound_ready_preflight_is_valid_but_never_formal_release():
    payload = build_rollout_preflight(
        config=RolloutConfig.from_environment({"ACCEPTANCE_SHARDS_ENABLED": "true"}),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        **_ready_kwargs(),
    )

    assert payload["status"] == "PASS"
    assert payload["rolloutCandidate"] is False
    assert payload["formalReleaseEnabled"] is False
    validate_rollout_preflight(payload)


def test_formal_release_promotion_is_rejected_in_rollout_preflight():
    config = RolloutConfig(
        shards_enabled=True,
        formal_release_enabled=True,
        rollout_level="L3",
        shard_count=2,
    )

    with pytest.raises(ValueError, match="formal release"):
        validate_rollout_config(config)


def test_rollout_preflight_is_not_a_release_gate_child():
    payload = build_rollout_preflight(
        config=RolloutConfig.disabled(),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        **_ready_kwargs(),
    )

    with pytest.raises(ReleaseGateValidationError):
        validate_release_gate_evidence(payload, COMMIT, SOURCE)


def test_validator_rejects_pass_payload_with_unavailable_runner():
    payload = build_rollout_preflight(
        config=RolloutConfig.disabled(),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        **_ready_kwargs(),
    )
    payload["runnerCapability"] = {
        "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
        "sandbox": "unavailable", "interpreter": "unqualified",
    }
    payload["evidenceFingerprint"] = canonical_fingerprint(
        {key: value for key, value in payload.items() if key != "evidenceFingerprint"}
    )

    with pytest.raises(ValueError, match="blockers"):
        validate_rollout_preflight(payload)


def test_validator_round_trips_blocked_isolation_evidence():
    payload = build_rollout_preflight(
        config=RolloutConfig.disabled(),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        runner_capability={
            "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
            "sandbox": "available", "interpreter": "qualified",
        },
        isolation={
            "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
            "fixtureRoots": "unknown",
            "sqlite": "unknown",
            "cache": "unknown",
            "processes": "unknown",
        },
        ui_cache={
            "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
            "status": "PASS", "sourceMatched": True,
        },
    )

    assert payload["status"] == "BLOCKED"
    validate_rollout_preflight(payload)


def test_preflight_blocks_missing_runner_source_binding():
    ready = _ready_kwargs()
    ready["runner_capability"] = {"sandbox": "available", "interpreter": "qualified"}

    payload = build_rollout_preflight(
        config=RolloutConfig.disabled(),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        **ready,
    )

    assert payload["status"] == "BLOCKED"
    assert "runner_capability_source_mismatch" in payload["blockers"]
    validate_rollout_preflight(payload)


def test_preflight_blocks_cross_source_ui_cache():
    ready = _ready_kwargs()
    ready["ui_cache"] = {
        "sourceBinding": {"commitSha": "c" * 40, "sourceFingerprint": "d" * 64},
        "status": "PASS", "sourceMatched": True,
    }

    payload = build_rollout_preflight(
        config=RolloutConfig.disabled(),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        **ready,
    )

    assert payload["status"] == "BLOCKED"
    assert "ui_cache_mismatch" in payload["blockers"]
    validate_rollout_preflight(payload)


def test_preflight_blocks_pass_ui_cache_with_failure_reason():
    ready = _ready_kwargs()
    ready["ui_cache"] = {
        "sourceBinding": {"commitSha": COMMIT, "sourceFingerprint": SOURCE},
        "status": "PASS", "sourceMatched": True, "reason": "revenue_token_mismatch",
    }

    payload = build_rollout_preflight(
        config=RolloutConfig.disabled(),
        commit_sha=COMMIT,
        source_fingerprint=SOURCE,
        manifest=_manifest(),
        **ready,
    )

    assert payload["status"] == "BLOCKED"
    assert payload["uiCache"]["reason"] == "revenue_token_mismatch"
    validate_rollout_preflight(payload)
