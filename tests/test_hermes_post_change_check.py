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


def test_plan_can_skip_monitor_and_tests_for_fast_dry_run():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    labels = [step.label for step in plan]

    assert "system-monitor" not in labels
    assert "targeted-tests" not in labels
    assert "phase2-baseline" in labels
    assert "monthly-baseline-governance" in labels


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
