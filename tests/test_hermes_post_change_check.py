from scripts import hermes_post_change_check as post_check


def test_default_plan_includes_git_runtime_baseline_and_targeted_tests():
    plan = post_check.build_check_plan(include_monitor=True, include_tests=True)
    labels = [step.label for step in plan]

    assert labels[:3] == ["git-status", "git-diff-stat", "git-diff-name-only"]
    assert "system-status" in labels
    assert "system-acceptance" in labels
    assert "system-monitor" in labels
    assert "phase2-baseline" in labels
    assert "monthly-baseline-governance" in labels
    assert "implementation-agent-files" in labels
    assert "targeted-tests" in labels
    assert "documentation-artifact-report" in labels
    targeted = next(step for step in plan if step.label == "targeted-tests")
    assert "tests/test_monthly_baseline_service.py" in targeted.command
    assert "tests/test_monthly_baseline_check_cli.py" in targeted.command
    for test_name in [
        "tests/test_database_explicit_path.py",
        "tests/test_upload_lock_service.py",
        "tests/test_cache_generation_service.py",
        "tests/test_upload_orchestrator_service.py",
        "tests/test_upload_action_service.py",
        "tests/test_upload_single_writer_integration.py",
        "tests/test_business_rules_service.py",
        "tests/test_application_snapshot_service.py",
        "tests/test_decision_service.py",
        "tests/test_decision_api.py",
        "tests/test_evidence_models.py",
        "tests/test_evidence_collector.py",
        "tests/test_agent_runtime.py",
        "tests/test_context_agent_service.py",
        "tests/test_review_agent_service.py",
        "tests/test_agent_cli.py",
        "tests/test_agent_dispatch_contract.py",
        "tests/test_agent_read_only_contract.py",
        "tests/test_implementation_models.py",
        "tests/test_implementation_guard.py",
        "tests/test_validation_runner.py",
        "tests/test_implementation_agent_service.py",
        "tests/test_implementation_agent_cli.py",
        "tests/test_implementation_agent_integration.py",
    ]:
        assert test_name in targeted.command


def test_implementation_agent_file_gate_is_read_only():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    file_gate = next(step for step in plan if step.label == "implementation-agent-files")

    assert file_gate.command[1:3] == ["-c", file_gate.command[2]]
    assert "backend/agents/implementation_models.py" in file_gate.command[2]
    assert "backend/agents/implementation_guard.py" in file_gate.command[2]
    assert "backend/agents/implementation_agent_service.py" in file_gate.command[2]
    assert "scripts/implementation_agent.py" in file_gate.command[2]
    assert "read_text" not in file_gate.command[2]
    assert "write_text" not in file_gate.command[2]
    assert "worktree" not in file_gate.command[2]


def test_plan_has_exact_implementation_agent_test_pack_commands():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=True)
    commands = {step.label: step.command for step in plan}
    py = post_check.python_bin()

    assert commands["implementation-agent-core-tests"] == [
        py,
        "-m",
        "pytest",
        "tests/test_implementation_models.py",
        "tests/test_implementation_guard.py",
        "tests/test_validation_runner.py",
        "-q",
    ]
    assert commands["implementation-agent-integration-tests"] == [
        py,
        "-m",
        "pytest",
        "tests/test_implementation_agent_service.py",
        "tests/test_implementation_agent_cli.py",
        "tests/test_implementation_agent_integration.py",
        "-q",
    ]


def test_plan_can_skip_monitor_and_tests_for_fast_dry_run():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    labels = [step.label for step in plan]

    assert "system-monitor" not in labels
    assert "targeted-tests" not in labels
    assert "phase2-baseline" in labels
    assert "monthly-baseline-governance" in labels


def test_documentation_hermes_check_is_read_only_and_does_not_dispatch():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    step = next(item for item in plan if item.label == "documentation-artifact-report")
    command = " ".join(step.command)
    source = post_check.documentation_artifact_report.__code__.co_consts

    assert any("documentation-application.json" in str(value) for value in source)
    assert any("documentation-proposal.json" in str(value) for value in source)
    assert any("documentation-evidence.json" in str(value) for value in source)
    assert any("documentation-telemetry.json" in str(value) for value in source)
    assert "agent_workflow.py" not in command
    assert "documentation_agent.py" not in command
    assert "write_text" not in command
    assert "write_artifact" not in command
    assert "document" not in command.lower().replace("documentation", "")


def test_documentation_artifact_report_validates_schema_caps_and_writes_nothing(tmp_path):
    run = tmp_path / ".nbs_agent_runtime/runs/run-1"
    run.mkdir(parents=True)
    (run / "documentation-telemetry.json").write_text(
        '{"schemaVersion":"documentation-telemetry-v1","proposalCount":1,"result":"applied"}',
        encoding="utf-8",
    )

    report = post_check.documentation_artifact_report(tmp_path)

    assert report["schemaVersion"] == "documentation-hermes-report-v1"
    assert report["artifactCounts"]["documentation-telemetry.json"] == 1
    assert report["invalidRuns"] == []
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0


def test_governance_graph_report_is_read_only_and_bounded(tmp_path):
    report = post_check.governance_graph_artifact_report(tmp_path)

    assert report["schemaVersion"] == "governance-graph-hermes-report-v1"
    assert report["policy"] == "read-only"
    assert report["invocations"] == 0
    assert report["writes"] == 0
    assert report["runCount"] == 0


def test_hermes_targeted_tests_include_graph_pack():
    targeted = next(
        step for step in post_check.build_check_plan(include_monitor=False)
        if step.label == "targeted-tests"
    )
    for name in (
        "tests/test_governance_graph_models.py",
        "tests/test_governance_graph_policy.py",
        "tests/test_governance_graph_service.py",
        "tests/test_governance_graph_cli.py",
    ):
        assert name in targeted.command


def test_governance_graph_report_validates_schema_caps_and_symlinks(tmp_path):
    runs = tmp_path / ".nbs_agent_runtime" / "runs"
    valid = runs / "run-valid"
    valid.mkdir(parents=True)
    (valid / "governance-graph.json").write_text(
        '{"schemaVersion":"nbs-governance-graph-v1","runId":"run-valid","overallStatus":"blocked"}',
        encoding="utf-8",
    )
    malformed = runs / "run-malformed"
    malformed.mkdir()
    (malformed / "governance-graph.json").write_text("not-json", encoding="utf-8")
    dangling = runs / "run-dangling"
    dangling.mkdir()
    (dangling / "governance-graph.json").symlink_to(tmp_path / "missing.json")

    report = post_check.governance_graph_artifact_report(tmp_path)

    assert report["runCount"] == 3
    assert report["artifactCount"] == 2
    assert set(report["invalidRuns"]) == {"run-malformed", "run-dangling"}
    assert report["statusCounts"] == {"blocked": 1}


def test_overall_status_fails_on_failed_required_step():
    results = [
        {"label": "git-status", "required": True, "exitCode": 0},
        {"label": "phase2-baseline", "required": True, "exitCode": 1},
    ]

    assert post_check.compute_overall_status(results) == "fail"


def test_overall_status_warns_on_failed_optional_step():
    results = [
        {"label": "git-status", "required": True, "exitCode": 0},
        {"label": "git-diff-stat", "required": False, "exitCode": 1},
    ]

    assert post_check.compute_overall_status(results) == "warning"


def test_overall_status_passes_when_all_steps_pass():
    results = [
        {"label": "git-status", "required": True, "exitCode": 0},
        {"label": "phase2-baseline", "required": True, "exitCode": 0},
    ]

    assert post_check.compute_overall_status(results) == "pass"


def test_markdown_report_summarizes_baseline_tests_and_commit_advice():
    report = {
        "overallStatus": "pass",
        "projectRoot": "/tmp/nbs",
        "results": [
            {
                "label": "git-status",
                "required": True,
                "exitCode": 0,
                "stdout": "## main\n M pipeline.py\n?? tests/test_official_export_workbook_contract.py\n",
                "stderr": "",
            },
            {
                "label": "phase2-baseline",
                "required": True,
                "exitCode": 0,
                "stdout": '{"status":"matched","formattedActualTotal":"HKD 12,057,968"}',
                "stderr": "",
            },
            {
                "label": "targeted-tests",
                "required": True,
                "exitCode": 0,
                "stdout": "39 passed in 9.40s",
                "stderr": "",
            },
            {
                "label": "monthly-baseline-governance",
                "required": True,
                "exitCode": 0,
                "stdout": '{"status":"monitoring","blockingStatus":"matched","promotionReady":false}',
                "stderr": "",
            },
        ],
    }

    markdown = post_check.format_markdown_report(report)

    assert "# Hermes Post-Change Report" in markdown
    assert "Overall status: PASS" in markdown
    assert "phase2-baseline: PASS" in markdown
    assert "HKD 12,057,968" in markdown
    assert "targeted-tests: PASS" in markdown
    assert "monthly-baseline-governance: PASS" in markdown
    assert "promotionReady" in markdown
    assert "39 passed" in markdown
    assert "Commit recommendation: ready after reviewing grouped diff" in markdown
