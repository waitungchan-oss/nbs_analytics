import pytest


def test_backend_change_requires_compile_targeted_tests_and_diff_check():
    from backend.agents.strict_review_preflight import plan_required_checks

    assert plan_required_checks(
        ("backend/agents/review_agent_service.py",),
        ("tests/test_review_agent_service.py",),
    ) == ("python-compile", "targeted-tests", "git-diff-check")


def test_test_and_script_changes_are_mapped_without_duplicate_checks():
    from backend.agents.strict_review_preflight import plan_required_checks

    assert plan_required_checks(
        ("tests/test_review_agent_service.py", "scripts/review_agent.py"),
        ("tests/test_review_agent_service.py", "tests/test_review_agent_cli.py"),
    ) == ("python-compile", "targeted-tests", "git-diff-check")


def test_docs_only_change_uses_docs_validation():
    from backend.agents.strict_review_preflight import plan_required_checks

    assert plan_required_checks(("docs/agents/REVIEW_AGENT_CONTRACT.md",), ()) == ("docs-validation",)


def test_production_change_without_targeted_test_is_blocked():
    from backend.agents.strict_review_preflight import resolve_targeted_tests

    with pytest.raises(ValueError, match="targeted test"):
        resolve_targeted_tests(
            ("backend/agents/strict_review_preflight.py",),
            ("tests/test_unrelated.py",),
        )


def test_script_change_matches_cli_test_with_a_specific_suffix():
    from backend.agents.strict_review_preflight import resolve_targeted_tests

    assert resolve_targeted_tests(
        ("scripts/review_agent.py",),
        ("tests/test_review_agent_cli.py",),
    ) == ("tests/test_review_agent_cli.py",)


def test_source_binding_requires_verification_and_graph_to_match_session():
    from backend.agents.strict_review_preflight import validate_source_binding

    validate_source_binding("a" * 64, "a" * 64, "a" * 64, "a" * 64)

    with pytest.raises(ValueError, match="source fingerprint"):
        validate_source_binding("a" * 64, "b" * 64, "a" * 64, "a" * 64)


def test_context_fingerprint_is_independent_but_session_binding_is_required():
    from backend.agents.strict_review_preflight import validate_context_binding

    validate_context_binding("c" * 64, "a" * 64, "a" * 64)

    with pytest.raises(ValueError, match="context"):
        validate_context_binding("c" * 64, "a" * 64, "b" * 64)


def test_command_cache_requires_all_identity_parts_to_match():
    from backend.agents.strict_review_preflight import is_reusable

    assert is_reusable("a", "a", "b", "b", "c", "c", "d", "d") is True
    assert is_reusable("a", "b", "b", "b", "c", "c", "d", "d") is False
