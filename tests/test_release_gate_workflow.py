from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release-gates.yml"


def test_release_workflow_defines_independent_required_jobs_and_aggregate():
    source = WORKFLOW.read_text(encoding="utf-8")
    for name in ("Full pytest release gate", "Hermes release gate", "UI acceptance release gate", "Release gate aggregate"):
        assert name in source
    assert "needs: [full-pytest, hermes, ui-acceptance]" in source
    assert "requirements.txt" in source
    assert "upload-artifact@v4" in source
    assert "download-artifact@v4" in source


def test_release_workflow_is_fresh_for_pr_and_release_tags_and_fail_closed():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in source
    assert "tags:" in source
    assert "--sandbox-preflight required" in source
    assert "file://" not in source
    assert "continue-on-error" not in source
    assert "nbs_marketing_data.db" not in source
    assert "governance_graph.py build" not in source
    assert "memory-hub" not in source.lower()
