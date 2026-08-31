from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "sandbox-integration.yml"


def test_sandbox_ci_is_a_required_macos_job_with_fail_closed_preflight():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "runs-on: macos" in source
    assert "sandbox-capability-preflight" in source
    assert "--sandbox-preflight required" in source
    assert "continue-on-error" not in source
    assert "sudo" not in source.lower()
    assert "upload-artifact" in source


def test_sandbox_ci_does_not_reuse_governance_graph_or_write_formal_state():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "governance_graph.py build" not in source
    assert "nbs_marketing_data.db" not in source
    assert "baseline" not in source.lower()
