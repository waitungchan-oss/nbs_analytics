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


def test_parallel_readiness_barrier_is_not_an_application_service_health_gate():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "不會在此 barrier 啟動或等待 Streamlit、MCP 或 health 服務" in text


def test_parallel_rollout_platform_contract_matches_qualified_ci_runner():
    text = RUNBOOK.read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release-gates.yml").read_text(encoding="utf-8")

    assert "reserved-fd-v1" in text
    assert "Windows" in text and "unsupported" in text
    assert "failureCode=unsupported_platform" in text
    rollout_job = workflow.split("  acceptance-parallel-rollout:\n", 1)[1].split("\n  aggregate:", 1)[0]
    assert "runs-on: macos-14" in rollout_job


def test_legacy_shard_runbook_points_to_new_rollout_boundary():
    text = Path("docs/agents/ACCEPTANCE_SHARD_ROLLOUT_RUNBOOK.md").read_text(encoding="utf-8")

    assert "acceptance-parallel-rollout" in text
    assert "72-slot" in text
    assert "serial" in text
