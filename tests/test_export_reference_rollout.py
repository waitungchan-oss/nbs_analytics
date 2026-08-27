def _evidence(**overrides):
    evidence = {
        "requested_mode": "default",
        "equivalence_status": "PASS",
        "baseline_status": "PASS",
        "database_mutated": False,
        "stale_count": 0,
        "corrupt_count": 0,
        "fallback_count": 0,
        "reference_status": "HIT",
        "same_identity_hit": {"reference_status": "HIT", "equivalence_status": "PASS"},
    }
    evidence.update(overrides)
    return evidence


def test_reference_cache_failure_never_promotes_default():
    from backend.services.export_fast_path_service import decide_reference_rollout

    decision = decide_reference_rollout(_evidence(reference_status="INVALID"))
    assert decision.mode in {"shadow", "opt_in"}
    assert decision.mode != "default"


def test_default_requires_all_reference_gates():
    from backend.services.export_fast_path_service import decide_reference_rollout

    assert decide_reference_rollout(_evidence()).mode == "default"
    assert decide_reference_rollout(_evidence(database_mutated=True)).mode == "shadow"
    assert decide_reference_rollout(_evidence(same_identity_hit={"reference_status": "MISS"})).mode == "shadow"


def test_opt_in_can_be_used_without_default_promotion():
    from backend.services.export_fast_path_service import decide_reference_rollout

    decision = decide_reference_rollout(_evidence(requested_mode="opt_in"))
    assert decision.mode == "opt_in"
