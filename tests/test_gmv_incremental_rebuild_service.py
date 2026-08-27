from backend.services.gmv_refund_models import (
    IncrementalRebuildPlan,
    IncrementalRebuildResult,
    RebuildDecision,
    RebuildReasonCode,
)
from backend.services.gmv_incremental_rebuild import (
    IncrementalRebuildThresholds,
    RebuildFingerprints,
    build_incremental_plan,
    resolve_rebuild_strategy,
)


def _delta(**kwargs):
    from backend.services.gmv_refund_models import RefundStateDelta

    return RefundStateDelta(**kwargs)


def _matching_fingerprints():
    return RebuildFingerprints(
        base_revenue_generation_token="revenue-v1",
        current_revenue_generation_token="revenue-v1",
        base_rules_fingerprint="rules-v1",
        current_rules_fingerprint="rules-v1",
        base_source_fingerprint="source-v1",
        current_source_fingerprint="source-v1",
    )


def test_incremental_rebuild_plan_normalizes_and_sorts_affected_receipts():
    plan = IncrementalRebuildPlan(
        base_version_id="v1",
        affected_source_receipt_nos=(" R2 ", "R1", "R2"),
        affected_refund_ids=("F2", "F1", "F2"),
        affected_count=2,
        unaffected_copy_candidate_count=98,
        revenue_generation_token="revenue-v1",
        rules_fingerprint="rules-v1",
        source_fingerprint="source-v1",
        decision=RebuildDecision.INCREMENTAL_ELIGIBLE,
        reason_codes=(),
    )

    assert plan.affected_source_receipt_nos == ("R1", "R2")
    assert plan.affected_refund_ids == ("F1", "F2")
    assert plan.decision is RebuildDecision.INCREMENTAL_ELIGIBLE


def test_incremental_rebuild_result_has_bounded_publish_contract():
    result = IncrementalRebuildResult(
        version_id="v2",
        recomputed_receipts=2,
        copied_receipts=98,
        recomputed_rows=4,
        copied_rows=196,
        dimensions=("TOTAL_REFUND", "REFUNDED"),
        equivalence_status="PASS",
        fallback_used=False,
        publish_status="READY",
    )

    assert result.version_id == "v2"
    assert result.dimensions == ("TOTAL_REFUND", "REFUNDED")
    assert result.equivalence_status == "PASS"
    assert result.publish_status == "READY"


def test_rebuild_reason_codes_are_stable_strings():
    assert RebuildReasonCode.IDENTITY_CONFLICT.value == "REFUND_IDENTITY_CONFLICT"
    assert RebuildReasonCode.FINGERPRINT_MISMATCH.value == "FINGERPRINT_MISMATCH"


def test_planner_builds_sorted_affected_set_from_all_mutation_types():
    plan = build_incremental_plan(
        base_version_id="v1",
        state_delta=_delta(
            new_refund_order_nos=("F-3",),
            status_changed_refund_order_nos=("F-1",),
            amount_changed_refund_order_nos=("F-2",),
            affected_source_receipt_nos=(" R2 ", "R1", "R2"),
        ),
        fingerprints=_matching_fingerprints(),
        source_receipt_universe_count=10,
    )

    assert plan.affected_source_receipt_nos == ("R1", "R2")
    assert plan.affected_refund_ids == ("F-1", "F-2", "F-3")
    assert plan.affected_count == 2
    assert plan.unaffected_copy_candidate_count == 8
    assert plan.decision is RebuildDecision.INCREMENTAL_ELIGIBLE


def test_planner_blocks_identity_conflict():
    plan = build_incremental_plan(
        base_version_id="v1",
        state_delta=_delta(
            identity_conflict_refund_order_nos=("F-1",),
            affected_source_receipt_nos=("R-1", "R-9"),
        ),
        fingerprints=_matching_fingerprints(),
    )

    assert plan.decision is RebuildDecision.BLOCKED
    assert plan.reason_codes == (RebuildReasonCode.IDENTITY_CONFLICT,)


def test_planner_requires_full_rebuild_for_fingerprint_or_snapshot_mismatch():
    fingerprints = RebuildFingerprints(
        base_revenue_generation_token="revenue-v1",
        current_revenue_generation_token="revenue-v2",
        base_rules_fingerprint="rules-v1",
        current_rules_fingerprint="rules-v1",
        base_source_fingerprint="source-v1",
        current_source_fingerprint="source-v1",
    )
    plan = build_incremental_plan(
        base_version_id="v1",
        state_delta=_delta(affected_source_receipt_nos=("R-1",)),
        fingerprints=fingerprints,
        snapshot_complete=False,
    )

    assert plan.decision is RebuildDecision.FULL_REBUILD_REQUIRED
    assert plan.reason_codes == (
        RebuildReasonCode.FINGERPRINT_MISMATCH,
        RebuildReasonCode.SNAPSHOT_INCOMPLETE,
    )


def test_planner_falls_back_when_affected_set_exceeds_bounded_guardrail():
    plan = build_incremental_plan(
        base_version_id="v1",
        state_delta=_delta(affected_source_receipt_nos=("R-1", "R-2", "R-3")),
        fingerprints=_matching_fingerprints(),
        source_receipt_universe_count=10,
        thresholds=IncrementalRebuildThresholds(
            max_affected_receipt_count=2,
            max_affected_receipt_ratio=0.2,
        ),
    )

    assert plan.decision is RebuildDecision.FULL_REBUILD_REQUIRED
    assert plan.reason_codes == (RebuildReasonCode.AFFECTED_SET_TOO_LARGE,)


def test_blocked_plan_never_selects_a_rebuild_strategy():
    plan = build_incremental_plan(
        base_version_id="v1",
        state_delta=_delta(identity_conflict_refund_order_nos=("F-1",)),
        fingerprints=_matching_fingerprints(),
    )

    assert resolve_rebuild_strategy(plan, incremental_available=True) == "BLOCKED"


def test_eligible_plan_uses_full_fallback_until_incremental_engine_is_available():
    plan = build_incremental_plan(
        base_version_id="v1",
        state_delta=_delta(affected_source_receipt_nos=("R-1",)),
        fingerprints=_matching_fingerprints(),
    )

    assert resolve_rebuild_strategy(plan, incremental_available=False) == "FULL_REBUILD"
    assert resolve_rebuild_strategy(plan, incremental_available=True) == "INCREMENTAL"
