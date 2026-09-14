from pathlib import Path


RUNBOOK = Path("docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md")


def test_rollout_runbook_contains_boundaries_and_rollback():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "ACCEPTANCE_SHARDS_ENABLED=false" in text
    assert "formalReleaseEnabled=false" in text
    assert "ui_fixture_cache_mismatch" in text
    assert "serial" in text and "rollback" in text


def test_rollout_runbook_contains_source_bound_operator_sequence():
    text = RUNBOOK.read_text(encoding="utf-8")

    for marker in (
        "verification_chain.py seal",
        "acceptance_shard_rollout_preflight.py",
        "acceptance_shard_canary.py",
        "full_pytest_shard_aggregate.py",
        "hermes",
        "streamlit_ui_smoke.py",
        "release_gate.py",
        "retention",
    ):
        assert marker in text


def test_rollout_runbook_orders_gates_and_binds_aggregate_inputs():
    text = RUNBOOK.read_text(encoding="utf-8")
    markers = (
        "verification_chain.py seal",
        "acceptance_shard_rollout_preflight.py",
        "full_pytest_gate.py",
        "acceptance_shard_canary.py",
        "full_pytest_shard_aggregate.py",
        "hermes_gate.py",
        "ui_acceptance_fixture_preflight.py",
        "release_gate.py stage",
    )
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions)

    aggregate = text[text.index("full_pytest_shard_aggregate.py") : text.index("python scripts/hermes_gate.py")]
    for marker in (
        "EXPECTED_COMMIT_SHA",
        "EXPECTED_SOURCE_FINGERPRINT",
        "pytest-manifest.json",
        "shard-*.json",
        "canary-summary.json",
        "shard-aggregate.json",
        "authority=prototype",
        "formalReleaseEnabled=false",
    ):
        assert marker in aggregate

    assert ".nbs_agent_runtime/rollout/pytest-manifest.json" in text
    assert ".nbs_agent_runtime/rollout/rollout-preflight.json" in text
    assert ".nbs_agent_runtime/rollout/serial-full-pytest.json" in text
    assert ".nbs_agent_runtime/rollout/canary-summary.json" in text


def test_rollout_runbook_preserves_advisory_retention_and_rollback_boundaries():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "full-pytest-gate-v1" in text
    assert "release-gate-result-v1" in text
    assert text.index("prune --dry-run") < text.index("prune --apply")
    assert "gh workflow run release-gates.yml -f enable_acceptance_shards=false" in text
    assert "rolloutCandidate=ineligible" in text
    assert "ui_fixture_cache_mismatch" in text
