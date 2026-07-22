from __future__ import annotations

import pytest

from backend.agents.governance_graph_policy import (
    INVALIDATION,
    R2_SURFACES,
    allowed_next_nodes,
    classify_risk,
    invalidate_downstream,
    resolve_retry,
    validate_gate,
)


def test_behavioral_python_change_is_r1_even_with_cache_hit():
    result = classify_risk(
        changed_paths=("backend/services/decision_service.py",),
        declared_surfaces=(),
        behavior_change=True,
        fingerprint_cache_hit=True,
    )

    assert result.level == "R1"
    assert result.reason_code == "behavioral_code_change"
    assert result.surfaces == ()


@pytest.mark.parametrize("surface", sorted(R2_SURFACES))
def test_protected_surface_is_r2(surface):
    result = classify_risk(
        changed_paths=(),
        declared_surfaces=(surface,),
        behavior_change=False,
        fingerprint_cache_hit=False,
    )

    assert result.level == "R2"
    assert result.reason_code == "protected_surface"
    assert result.surfaces == (surface,)


@pytest.mark.parametrize(
    "path",
    ("/tmp/risk.json", "../risk.json", "backend\\agents\\policy.py", "unknown.ext"),
)
def test_unknown_or_unsafe_path_routes_to_r2(path):
    result = classify_risk(
        changed_paths=(path,),
        declared_surfaces=(),
        behavior_change=False,
        fingerprint_cache_hit=False,
    )

    assert result.level == "R2"
    assert result.reason_code == "unknown_or_ambiguous_surface"


def test_cache_hit_is_r0_only_for_non_behavior_document_paths():
    result = classify_risk(
        changed_paths=("docs/agents/README.md",),
        declared_surfaces=(),
        behavior_change=False,
        fingerprint_cache_hit=True,
    )

    assert result.level == "R0"
    assert result.reason_code == "document_cache_reuse"


def test_behavioral_json_path_never_downgrades_to_r0_from_cache_hit():
    result = classify_risk(
        changed_paths=("configs/workflow.json",),
        declared_surfaces=(),
        behavior_change=True,
        fingerprint_cache_hit=True,
    )

    assert result.level == "R1"
    assert result.reason_code == "behavioral_code_change"


def test_validate_gate_routes_design_conflict_to_plan_gate():
    result = validate_gate("spec_gate", status="failed", failure_mode="design_conflict", budget_remaining=1)

    assert result.status == "blocked"
    assert result.reason_code == "design_conflict"
    assert result.next_node == "plan_gate"


@pytest.mark.parametrize(
    "failure_mode",
    ("baseline_drift", "revenue_scope_conflict", "unsafe_db_path", "protected_invariant_failure"),
)
def test_validate_gate_routes_protected_failures_to_incident(failure_mode):
    result = validate_gate("task_validation", status="failed", failure_mode=failure_mode, budget_remaining=1)

    assert result.status == "blocked"
    assert result.reason_code == "protected_incident"
    assert result.next_node is None
    assert result.overall_status == "protected_incident"


def test_allowed_next_nodes_blocks_terminal_blocked_statuses():
    assert allowed_next_nodes("protected_incident", current_node="hermes") == ()
    assert allowed_next_nodes("blocked_user_decision", current_node="plan_gate") == ()
    assert allowed_next_nodes("blocked_missing_runner", current_node="documentation") == ()


def test_allowed_next_nodes_follows_design_spec_table():
    assert allowed_next_nodes("awaiting_authorization", current_node="risk") == ("spec_gate",)
    assert allowed_next_nodes("awaiting_authorization", current_node="spec_gate") == ("plan_gate",)
    assert allowed_next_nodes("ready_for_integration", current_node="hermes") == ("documentation",)
    assert allowed_next_nodes("awaiting_documentation", current_node="documentation") == ("git_integration",)


def test_luna_repair_is_available_once_then_requires_diagnosis():
    first = resolve_retry("task_validation", repair_loops_used=0)
    second = resolve_retry("task_validation", repair_loops_used=1)

    assert first.next_status == "luna_repair"
    assert first.consume_repair_budget is True
    assert second.next_status == "diagnosis_required"
    assert second.consume_repair_budget is False


def test_environment_recovery_does_not_consume_repair_budget():
    result = resolve_retry("task_validation", repair_loops_used=1, failure_mode="environment")

    assert result.next_status == "environment_recovery"
    assert result.consume_repair_budget is False
    assert result.reason_code == "environment_recovery"


def test_invalidate_downstream_returns_declared_node_ids_only():
    assert INVALIDATION["risk"][0] == "spec_gate"
    assert invalidate_downstream("brief") == (
        "risk",
        "spec_gate",
        "plan_gate",
        "task",
        "targeted_verification",
        "review",
        "full_verification",
        "hermes",
        "documentation",
        "git_integration",
    )
    assert invalidate_downstream("hermes") == ("documentation", "git_integration")


def test_invalidate_downstream_rejects_unknown_trigger():
    with pytest.raises(ValueError):
        invalidate_downstream("unknown")
