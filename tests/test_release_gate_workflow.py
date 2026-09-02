from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release-gates.yml"


def test_release_workflow_defines_independent_required_jobs_and_aggregate():
    source = WORKFLOW.read_text(encoding="utf-8")
    for name in ("Full pytest release gate", "Hermes release gate", "UI acceptance release gate", "Release gate aggregate"):
        assert name in source
    assert "needs: [full-pytest, hermes, ui-acceptance]" in source
    assert "requirements.txt" in source
    assert "python -m venv .venv" in source
    assert ".venv/bin/python -m pip install -r requirements.txt" in source
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


def test_release_workflow_runs_hermes_on_mac_and_ui_against_streamlit_app():
    source = WORKFLOW.read_text(encoding="utf-8")
    hermes_block = source.split("  hermes:\n", 1)[1].split("  ui-acceptance:\n", 1)[0]
    ui_block = source.split("  ui-acceptance:\n", 1)[1].split("  aggregate:\n", 1)[0]
    assert "runs-on: macos-14" in hermes_block
    assert ".venv/bin/python -m streamlit run app.py" in ui_block
    assert "streamlit_ui_smoke.py" in ui_block
    assert "prepare_release_gate_fixtures.py" in ui_block
    assert "NBS_ANALYTICS_DB_FILE" in ui_block
    assert "NBS_ANALYTICS_CACHE_DIR" in ui_block
    assert "NBS_ANALYTICS_COORDINATION_DB" in ui_block
    assert "$RUNNER_TEMP/nbs-ui-fixture/upload_coordination.db" in ui_block
    assert ".venv/bin/python -m playwright install --with-deps chromium" in ui_block
    assert "--served-url http://127.0.0.1:8765/" in ui_block
    assert "curl --fail" in ui_block
    assert "--retry-connrefused" in ui_block
    assert "python -m http.server" not in ui_block
