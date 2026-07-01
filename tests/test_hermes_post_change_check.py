from scripts import hermes_post_change_check as post_check


def test_default_plan_includes_git_runtime_baseline_and_targeted_tests():
    plan = post_check.build_check_plan(include_monitor=True, include_tests=True)
    labels = [step.label for step in plan]

    assert labels[:3] == ["git-status", "git-diff-stat", "git-diff-name-only"]
    assert "system-status" in labels
    assert "system-acceptance" in labels
    assert "system-monitor" in labels
    assert "phase2-baseline" in labels
    assert "targeted-tests" in labels


def test_plan_can_skip_monitor_and_tests_for_fast_dry_run():
    plan = post_check.build_check_plan(include_monitor=False, include_tests=False)
    labels = [step.label for step in plan]

    assert "system-monitor" not in labels
    assert "targeted-tests" not in labels
    assert "phase2-baseline" in labels


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
