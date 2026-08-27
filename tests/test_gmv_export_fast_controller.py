import pandas as pd


def _frames():
    from backend.services.gmv_refund_service import RevenueFrames

    empty = pd.DataFrame()
    return RevenueFrames(empty, empty, empty, empty)


def test_gmv_baseline_status_maps_monthly_blocking_evaluation(monkeypatch, tmp_path):
    import backend.services.gmv_refund_service as service

    class Repository:
        db_path = tmp_path / "nbs.sqlite"

    facts = {
        "branchFacts": pd.DataFrame(),
        "specialistFacts": pd.DataFrame(),
    }
    monkeypatch.setattr(
        "app_workflows._current_rules",
        lambda: ({}, [], [], [], []),
    )
    monkeypatch.setattr(
        "backend.services.dashboard_facts_service.build_dashboard_facts",
        lambda **kwargs: facts,
    )
    captured = {}

    def evaluate(**kwargs):
        captured["analytics"] = kwargs["analytics_builder"]({})
        return {"blockingStatus": "matched", "scope": "不含掛賬核銷與TT退款轉團款"}

    monkeypatch.setattr(
        "backend.services.monthly_baseline_service.evaluate_monthly_baselines",
        evaluate,
    )

    assert service._gmv_baseline_status(
        repository=Repository(), generation_token="generation-1", cache_dir=tmp_path,
    ) == "PASS"
    assert captured["analytics"]["revenueScope"] == "不含掛賬核銷與TT退款轉團款"

    monkeypatch.setattr(
        "backend.services.monthly_baseline_service.evaluate_monthly_baselines",
        lambda **kwargs: {"blockingStatus": "drift", "scope": "不含掛賬核銷與TT退款轉團款"},
    )
    assert service._gmv_baseline_status(
        repository=Repository(), generation_token="generation-1", cache_dir=tmp_path,
    ) == "FAIL"


def test_adjusted_scope_masks_are_rebased_to_adjusted_frame_indexes():
    import backend.services.gmv_refund_service as service

    tour = pd.DataFrame(
        {"收款類型": ["一般", "掛賬核銷"], "收款方式": ["普通", "普通"]},
        index=[101, 205],
    )
    others = pd.DataFrame(
        {"收款類型": ["一般"], "收款方式": ["TT 退款轉團款"]},
        index=[309],
    )

    masks = service._gmv_scope_masks_for_adjusted_frames(tour, others)

    tour_official, others_official = masks["official"]
    assert tour_official.index.tolist() == [101, 205]
    assert tour_official.tolist() == [True, False]
    assert others_official.index.tolist() == [309]
    assert others_official.tolist() == [False]


def test_fast_controller_falls_back_to_legacy_when_active_identity_is_unavailable(monkeypatch, tmp_path):
    import backend.services.gmv_refund_service as service

    class Manifest:
        status = "ready"
        builder_mode = "fast"
        equivalence_status = "PASS"

    expected = object()
    calls = []
    monkeypatch.setattr(
        service,
        "build_gmv_formal_artifacts",
        lambda **kwargs: (calls.append("legacy"), expected)[1],
    )
    monkeypatch.setattr(service, "_run_fast_export_gate", lambda **kwargs: calls.append("fast") or None)
    result = service.build_gmv_formal_artifacts_fast_or_legacy(
        repository=object(), version_id="v1", revenue_frames=_frames(),
        rule_version="rules", cache_dir=tmp_path,
    )
    assert result is expected
    assert calls == ["legacy"]


def test_fast_controller_falls_back_after_fast_failure(monkeypatch, tmp_path):
    import backend.services.gmv_refund_service as service

    calls = []
    legacy_result = object()

    def fake_builder(**kwargs):
        calls.append(kwargs["builder_mode"])
        if kwargs["builder_mode"] == "fast":
            raise RuntimeError("fast serializer failure")
        return legacy_result

    monkeypatch.setattr(service, "build_gmv_formal_artifacts", fake_builder)
    monkeypatch.setattr(service, "_run_fast_export_gate", lambda **kwargs: None)
    result = service.build_gmv_formal_artifacts_fast_or_legacy(
        repository=object(), version_id="v1", revenue_frames=_frames(),
        rule_version="rules", cache_dir=tmp_path,
    )
    assert result is legacy_result
    assert calls == ["legacy_fallback"]


def test_fast_controller_warm_reference_does_not_call_legacy(monkeypatch, tmp_path):
    import backend.services.gmv_export_cache_service as cache_service
    import backend.services.gmv_refund_service as service
    import backend.services.gmv_trusted_reference_service as reference_service

    class Repository:
        db_path = tmp_path / "nbs.sqlite"

        def load_active_scope(self):
            return {
                "version_id": "v1",
                "revenue_generation_token": "revenue-v1",
                "refund_state_sha256": "a" * 64,
            }

    class Reference:
        status = "TRUSTED"
        content_fingerprint = "a" * 64
        reference_id = "gmv-trusted-reference-v1:" + "a" * 64

        def to_dict(self):
            return {"referenceId": self.reference_id, "contentFingerprint": self.content_fingerprint}

    class Manifest:
        status = "ready"
        cache_key = "cache-v1"

    candidate = service.GmvFastCandidate(
        artifacts={},
        total_adjusted={"adjusted_detail": pd.DataFrame()},
        paid_adjusted={"adjusted_detail": pd.DataFrame()},
        total_summary_rows=[],
        paid_summary_rows=[],
        shadow_status="PASS",
        reference_status="HIT",
    )
    calls = []
    monkeypatch.setattr(reference_service, "load_trusted_reference", lambda **kwargs: Reference())
    monkeypatch.setattr(cache_service, "load_gmv_export_cache", lambda **kwargs: None)
    monkeypatch.setattr(cache_service, "build_gmv_export_cache", lambda **kwargs: Manifest())
    monkeypatch.setattr(service, "_run_fast_export_gate", lambda **kwargs: calls.append("fast") or candidate)
    monkeypatch.setattr(
        service,
        "build_gmv_formal_artifacts",
        lambda **kwargs: calls.append("legacy") or AssertionError("warm path called legacy"),
    )

    result = service.build_gmv_formal_artifacts_fast_or_legacy(
        repository=Repository(), version_id="v1", revenue_frames=_frames(),
        rule_version="rules", cache_dir=tmp_path,
    )

    assert result.cache_manifest.status == "ready"
    assert calls == ["fast"]


def test_fast_controller_passes_affected_receipts_to_candidate(monkeypatch, tmp_path):
    import backend.services.gmv_export_cache_service as cache_service
    import backend.services.gmv_refund_service as service
    import backend.services.gmv_trusted_reference_service as reference_service

    class Repository:
        db_path = tmp_path / "nbs.sqlite"

        def load_active_scope(self):
            return {
                "version_id": "v1",
                "revenue_generation_token": "revenue-v1",
                "refund_state_sha256": "a" * 64,
            }

    class Reference:
        status = "TRUSTED"
        content_fingerprint = "a" * 64
        reference_id = "gmv-trusted-reference-v1:" + "a" * 64

        def to_dict(self):
            return {"referenceId": self.reference_id, "contentFingerprint": self.content_fingerprint}

    class Manifest:
        status = "ready"

    candidate = service.GmvFastCandidate(
        artifacts={}, total_adjusted={"adjusted_detail": pd.DataFrame()},
        paid_adjusted={"adjusted_detail": pd.DataFrame()}, total_summary_rows=[],
        paid_summary_rows=[], shadow_status="PASS", reference_status="HIT",
        performance={"aggregationMode": "affected_only", "unaffectedAggregationCalls": 0},
    )
    captured = {}
    monkeypatch.setattr(reference_service, "load_trusted_reference", lambda **kwargs: Reference())
    monkeypatch.setattr(cache_service, "load_gmv_export_cache", lambda **kwargs: None)
    monkeypatch.setattr(cache_service, "build_gmv_export_cache", lambda **kwargs: Manifest())
    monkeypatch.setattr(service, "_gmv_baseline_status", lambda **kwargs: "PASS")

    def fake_gate(**kwargs):
        captured["affected"] = kwargs["affected_source_receipt_nos"]
        return candidate

    monkeypatch.setattr(service, "_run_fast_export_gate", fake_gate)
    result = service.build_gmv_formal_artifacts_fast_or_legacy(
        repository=Repository(), version_id="v1", revenue_frames=_frames(),
        rule_version="rules", cache_dir=tmp_path,
        affected_source_receipt_nos=(" S-2 ", "S-1", "S-2"),
    )

    assert result.cache_manifest.status == "ready"
    assert captured["affected"] == ("S-1", "S-2")


def test_fast_candidate_exposes_full_candidate_aggregation_telemetry():
    import backend.services.gmv_refund_service as service

    candidate = service.GmvFastCandidate(
        artifacts={}, total_adjusted={}, paid_adjusted={}, total_summary_rows=[],
        paid_summary_rows=[], shadow_status="PASS", reference_status="HIT",
        performance={
            "aggregationMode": "full_candidate",
            "unaffectedAggregationCalls": 2,
            "affectedAggregationCalls": 0,
        },
    )

    assert candidate.performance["aggregationMode"] == "full_candidate"
    assert candidate.performance["unaffectedAggregationCalls"] == 2
    assert candidate.performance["affectedAggregationCalls"] == 0


def test_fast_controller_cold_miss_seeds_reference_once_before_fast(monkeypatch, tmp_path):
    import backend.services.gmv_export_cache_service as cache_service
    import backend.services.gmv_refund_service as service
    import backend.services.gmv_trusted_reference_service as reference_service

    class Repository:
        db_path = tmp_path / "nbs.sqlite"

        def load_active_scope(self):
            return {
                "version_id": "v1",
                "revenue_generation_token": "revenue-v1",
                "refund_state_sha256": "a" * 64,
            }

    class Manifest:
        status = "ready"
        cache_key = "cache-v1"
        generation_path = "generations/seed"

        def to_dict(self):
            return {"cacheKey": self.cache_key, "generationPath": self.generation_path}

    class Reference:
        status = "TRUSTED"
        content_fingerprint = "a" * 64
        reference_id = "gmv-trusted-reference-v1:" + "a" * 64

        def to_dict(self):
            return {"referenceId": self.reference_id, "contentFingerprint": self.content_fingerprint}

    seed = service.GmvFormalArtifacts({}, {}, [], [], Manifest())
    candidate = service.GmvFastCandidate({}, {"adjusted_detail": pd.DataFrame()}, {"adjusted_detail": pd.DataFrame()}, [], [], "PASS", "MISS")
    fast_manifest = Manifest()
    calls = []
    monkeypatch.setattr(cache_service, "load_gmv_export_cache", lambda **kwargs: None)
    monkeypatch.setattr(reference_service, "load_trusted_reference", lambda **kwargs: None)
    monkeypatch.setattr(reference_service, "write_trusted_reference", lambda **kwargs: calls.append("reference") or kwargs["manifest"])
    monkeypatch.setattr(service, "_read_gmv_artifacts", lambda *args, **kwargs: {key: b"artifact" for key in service._gmv_artifact_kinds()})
    monkeypatch.setattr(service, "_build_trusted_reference_manifest", lambda **kwargs: Reference())
    monkeypatch.setattr(service, "_run_fast_export_gate", lambda **kwargs: calls.append("fast") or candidate)
    monkeypatch.setattr(cache_service, "build_gmv_export_cache", lambda **kwargs: calls.append("publish") or fast_manifest)

    def fake_builder(**kwargs):
        calls.append(kwargs["builder_mode"])
        assert kwargs["publish_active"] is False
        return seed

    monkeypatch.setattr(service, "build_gmv_formal_artifacts", fake_builder)
    result = service.build_gmv_formal_artifacts_fast_or_legacy(
        repository=Repository(), version_id="v1", revenue_frames=_frames(),
        rule_version="rules", cache_dir=tmp_path,
    )

    assert result.cache_manifest is fast_manifest
    assert calls == ["legacy_seed", "reference", "fast", "publish"]


def test_fast_controller_warm_failure_returns_previous_readable_cache(monkeypatch, tmp_path):
    import backend.services.gmv_export_cache_service as cache_service
    import backend.services.gmv_refund_service as service
    import backend.services.gmv_trusted_reference_service as reference_service

    class Repository:
        db_path = tmp_path / "nbs.sqlite"

        def load_active_scope(self):
            return {
                "version_id": "v1",
                "revenue_generation_token": "revenue-v1",
                "refund_state_sha256": "a" * 64,
            }

    class Reference:
        status = "TRUSTED"

    class Previous:
        status = "ready"

    previous = Previous()
    expected = object()
    monkeypatch.setattr(reference_service, "load_trusted_reference", lambda **kwargs: Reference())
    monkeypatch.setattr(cache_service, "load_gmv_export_cache", lambda **kwargs: previous)
    monkeypatch.setattr(service, "_run_fast_export_gate", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("shadow mismatch")))
    monkeypatch.setattr(service, "_cached_formal_artifacts", lambda **kwargs: expected)
    monkeypatch.setattr(service, "build_gmv_formal_artifacts", lambda **kwargs: (_ for _ in ()).throw(AssertionError("legacy rebuild")))

    result = service.build_gmv_formal_artifacts_fast_or_legacy(
        repository=Repository(), version_id="v1", revenue_frames=_frames(),
        rule_version="rules", cache_dir=tmp_path,
    )

    assert result is expected


def test_fast_failure_with_seed_and_previous_cache_preserves_previous_pointer(monkeypatch, tmp_path):
    import backend.services.gmv_export_cache_service as cache_service
    import backend.services.gmv_refund_service as service
    import backend.services.gmv_trusted_reference_service as reference_service

    class Repository:
        db_path = tmp_path / "nbs.sqlite"

        def load_active_scope(self):
            return {
                "version_id": "v1",
                "revenue_generation_token": "revenue-v1",
                "refund_state_sha256": "a" * 64,
            }

    class Manifest:
        status = "ready"
        cache_key = "cache-v1"
        version_id = "v1"
        generation_path = "generations/seed"

        def to_dict(self):
            return {"cacheKey": self.cache_key, "generationPath": self.generation_path}

    previous = Manifest()
    seed = service.GmvFormalArtifacts({}, {}, [], [], Manifest())
    expected = object()
    calls = []
    monkeypatch.setattr(reference_service, "load_trusted_reference", lambda **kwargs: None)
    monkeypatch.setattr(cache_service, "load_gmv_export_cache", lambda **kwargs: previous)
    monkeypatch.setattr(service, "_cached_formal_artifacts", lambda **kwargs: expected)
    monkeypatch.setattr(service, "_read_gmv_artifacts", lambda *args, **kwargs: {key: b"artifact" for key in service._gmv_artifact_kinds()})
    monkeypatch.setattr(service, "_build_trusted_reference_manifest", lambda **kwargs: object())
    monkeypatch.setattr(reference_service, "write_trusted_reference", lambda **kwargs: calls.append("reference"))
    monkeypatch.setattr(service, "_run_fast_export_gate", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("shadow mismatch")))
    monkeypatch.setattr(cache_service, "publish_gmv_export_cache_manifest", lambda **kwargs: calls.append("publish-seed"))
    monkeypatch.setattr(service, "build_gmv_formal_artifacts", lambda **kwargs: calls.append("legacy-seed") or seed)

    result = service.build_gmv_formal_artifacts_fast_or_legacy(
        repository=Repository(), version_id="v1", revenue_frames=_frames(),
        rule_version="rules", cache_dir=tmp_path,
    )

    assert result is expected
    assert calls == ["legacy-seed", "reference"]


def test_preparation_checksum_status_rejects_nonempty_but_incorrect_fingerprints():
    from dataclasses import replace

    from backend.services.gmv_export_intermediate_service import build_gmv_export_base_preparation
    from backend.services.gmv_refund_service import _gmv_preparation_checksum_status

    preparation = build_gmv_export_base_preparation(
        version_id="v1", revenue_generation_token="r1", rules_fingerprint="rules",
        export_schema_version="gmv-formal-export-v2", pipeline_fingerprint="pipeline",
        tour=pd.DataFrame([{"來源單據號": "T-1"}]), others=pd.DataFrame([{"來源單據號": "O-1"}]),
    )
    assert _gmv_preparation_checksum_status(preparation) == "PASS"

    broken = replace(
        preparation,
        source_fingerprints={"tour": "f" * 64, "others": "e" * 64},
    )
    assert _gmv_preparation_checksum_status(broken) == "FAIL"
