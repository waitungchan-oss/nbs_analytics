from backend.services.gmv_refund_models import (
    IncrementalRebuildPlan,
    IncrementalRebuildResult,
    RebuildDecision,
    RebuildReasonCode,
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
