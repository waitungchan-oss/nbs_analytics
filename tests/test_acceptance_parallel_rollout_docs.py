from pathlib import Path


RUNBOOK = Path("docs/agents/ACCEPTANCE_PARALLEL_ROLLOUT_RUNBOOK.md")


def test_parallel_rollout_runbook_defines_population_and_release_boundaries():
    text = RUNBOOK.read_text(encoding="utf-8")

    for marker in (
        "agent-eval-72-slot",
        "full-pytest-nodeid",
        "formalReleaseEnabled=false",
        "speedupMultiple",
        "enable_acceptance_parallel_rollout=false",
    ):
        assert marker in text


def test_parallel_rollout_runbook_keeps_serial_authority_and_read_only_context():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "serial Full pytest" in text
    assert "diagnostic-only" in text
    assert "Memory Hub" in text
    assert "Memory Sidecar" in text
    assert "Governance Graph" in text
    assert "cannot approve" in text


def test_legacy_shard_runbook_points_to_new_rollout_boundary():
    text = Path("docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md").read_text(encoding="utf-8")

    assert "acceptance-parallel-rollout" in text
    assert "72-slot" in text
    assert "serial" in text

