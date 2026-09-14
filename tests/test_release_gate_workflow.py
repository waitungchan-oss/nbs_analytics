from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "release-gates.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_defines_independent_required_jobs_and_aggregate():
    source = _workflow_text()
    for name in ("Full pytest release gate", "Hermes release gate", "UI acceptance release gate", "Release gate aggregate"):
        assert name in source
    assert "needs: [full-pytest, hermes, ui-acceptance]" in source
    assert "requirements.txt" in source
    assert "python -m venv .venv" in source
    assert ".venv/bin/python -m pip install -r requirements.txt" in source
    assert "upload-artifact@v4" in source
    assert "download-artifact@v4" in source


def test_release_aggregate_job_name_matches_branch_protection_contract():
    source = _workflow_text()
    assert "name: Release gate aggregate" in source
    assert source.count("name: Release gate aggregate") == 1


def test_release_workflow_is_fresh_for_pr_and_release_tags_and_fail_closed():
    source = _workflow_text()
    assert "pull_request:" in source
    assert "tags:" in source
    assert "--sandbox-preflight required" in source
    assert "file://" not in source
    assert "continue-on-error" not in source
    assert "nbs_marketing_data.db" not in source
    assert "governance_graph.py build" not in source
    assert "memory-hub" not in source.lower()


def test_release_workflow_runs_hermes_on_mac_and_ui_against_streamlit_app():
    source = _workflow_text()
    full_block = source.split("  full-pytest:\n", 1)[1].split("  hermes:\n", 1)[0]
    hermes_block = source.split("  hermes:\n", 1)[1].split("  ui-acceptance:\n", 1)[0]
    ui_block = source.split("  ui-acceptance:\n", 1)[1].split("  aggregate:\n", 1)[0]
    assert "fetch-depth: 0" in full_block
    assert "git fetch --no-tags origin main:refs/heads/main" in full_block
    assert "prepare_release_gate_fixtures.py --output" in full_block
    assert "prepare_release_gate_fixtures.py --output \"$RUNNER_TEMP/nbs-full-pytest-fixture\" --source-fingerprint \"$SOURCE_FINGERPRINT\"" in full_block
    assert "NBS_ANALYTICS_CACHE_DIR=$RUNNER_TEMP/nbs-full-pytest-fixture/release_gate_cache" in full_block
    assert "NBS_ANALYTICS_DB_FILE" in full_block
    assert "NBS_ANALYTICS_CACHE_DIR" in full_block
    assert "NBS_ANALYTICS_COORDINATION_DB" in full_block
    assert "runs-on: macos-14" in hermes_block
    assert "fetch-depth: 0" in hermes_block
    assert "--skip-system-acceptance" in hermes_block
    assert "prepare_release_gate_fixtures.py --output \"$RUNNER_TEMP/nbs-hermes-fixture\" --source-fingerprint \"$SOURCE_FINGERPRINT\"" in hermes_block
    assert "NBS_ANALYTICS_CACHE_DIR=$RUNNER_TEMP/nbs-hermes-fixture/release_gate_cache" in hermes_block
    assert "git fetch --no-tags origin main:refs/heads/main" in hermes_block
    assert ".venv/bin/python -m streamlit run app.py" in ui_block
    assert "streamlit_ui_smoke.py" in ui_block
    assert "prepare_release_gate_fixtures.py" in ui_block
    assert "prepare_release_gate_fixtures.py --profile ui --output \"$RUNNER_TEMP/nbs-ui-fixture\" --source-fingerprint \"$SOURCE_FINGERPRINT\"" in ui_block
    assert "NBS_ANALYTICS_CACHE_DIR=$RUNNER_TEMP/nbs-ui-fixture/release_gate_cache" in ui_block
    assert "NBS_ANALYTICS_DB_FILE" in ui_block
    assert "NBS_ANALYTICS_CACHE_DIR" in ui_block
    assert "NBS_ANALYTICS_COORDINATION_DB" in ui_block
    assert "$RUNNER_TEMP/nbs-ui-fixture/upload_coordination.db" in ui_block
    assert "fetch-depth: 0" in ui_block
    assert "git fetch --no-tags origin main:refs/heads/main" in ui_block
    assert ".venv/bin/python -m playwright install --with-deps chromium" in ui_block
    assert "--served-url http://127.0.0.1:8765/" in ui_block
    assert "curl --fail" in ui_block
    assert "--retry-connrefused" in ui_block
    assert "python -m http.server" not in ui_block


def test_full_pytest_job_uploads_diagnostic_manifest_without_replacing_serial_gate():
    source = _workflow_text()
    full_block = source.split("  full-pytest:\n", 1)[1].split("  hermes:\n", 1)[0]

    assert "scripts/pytest_manifest.py" in full_block
    assert "pytest-manifest.json" in full_block
    assert "release-gate-pytest-manifest-${{ github.sha }}" in full_block
    assert "scripts/full_pytest_gate.py" in full_block
    assert "full-pytest.json" in full_block
    assert "strategy:" not in full_block


def test_release_workflow_cancels_superseded_pr_runs_but_not_release_tags():
    text = _workflow_text()
    assert "concurrency:" in text
    assert "github.event.pull_request.number || github.ref" in text
    assert "startsWith(github.ref, 'refs/tags/') == false" in text


def test_release_child_artifacts_are_isolated_by_commit_and_use_dependency_cache():
    text = _workflow_text()
    for name in ("full-pytest", "hermes", "ui-acceptance"):
        assert f"name: release-gate-{name}-${{{{ github.sha }}}}" in text
    assert text.count("cache: pip") == 3
    assert text.count("cache-dependency-path: requirements.txt") == 3


def test_release_aggregate_uses_minimal_runtime_and_runs_after_failed_children():
    text = _workflow_text()
    aggregate = text[text.index("  aggregate:"):]
    assert "if: always()" in aggregate
    assert "pip install -r requirements.txt" not in aggregate
    assert "python scripts/release_gate.py aggregate" in aggregate
    assert "if-no-files-found: error" in aggregate


def test_serial_full_pytest_remains_default_and_required():
    source = _workflow_text()
    full_block = source.split("  full-pytest:\n", 1)[1].split("  hermes:\n", 1)[0]

    assert "name: Full pytest release gate" in full_block
    assert "strategy:" not in full_block


def test_shard_matrix_is_manual_and_default_off():
    source = _workflow_text()

    assert "workflow_dispatch:" in source
    assert "enable_acceptance_shards" in source
    assert "default: false" in source
    for name in ("acceptance-shard-preflight", "acceptance-shard-canary", "acceptance-shard-aggregate"):
        assert name in source


def test_shard_jobs_are_advisory_and_do_not_replace_serial_authority():
    source = _workflow_text()
    full_block = source.split("  full-pytest:\n", 1)[1].split("  hermes:\n", 1)[0]
    shard_block = source.split("  acceptance-shard-preflight:\n", 1)[1].split("  aggregate:\n", 1)[0]
    formal_aggregate = source.split("  aggregate:\n", 1)[1]

    assert "name: Full pytest release gate" in full_block
    assert "strategy:" not in full_block
    assert "formalReleaseEnabled" in shard_block
    assert "acceptance-shard-aggregate" in shard_block
    assert "needs: [full-pytest, hermes, ui-acceptance]" in formal_aggregate
    assert "acceptance-shard" not in formal_aggregate
