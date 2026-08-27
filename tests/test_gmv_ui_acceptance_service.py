import pytest


def _evidence(**overrides):
    value = {
        "route": "http://127.0.0.1:8502/",
        "initialStatus": "CURRENT",
        "mergeStatus": "READY",
        "activeVersionId": "v2",
        "manifestSha256": "a" * 64,
        "downloadedArtifacts": {
            "total.detail": 100,
            "paid.detail": 90,
        },
        "refreshedVersionId": "v2",
        "blockingError": None,
    }
    value.update(overrides)
    return value


def test_ui_acceptance_passes_two_dimensions_and_restart_read():
    from backend.services.gmv_ui_acceptance_service import validate_ui_acceptance_evidence

    result = validate_ui_acceptance_evidence(_evidence())

    assert result.status == "PASS"
    assert result.failure_reasons == ()


def test_ui_acceptance_rejects_missing_paid_artifact_or_blocking_error():
    from backend.services.gmv_ui_acceptance_service import validate_ui_acceptance_evidence

    missing = _evidence(downloadedArtifacts={"total.detail": 100})
    assert validate_ui_acceptance_evidence(missing).status == "FAIL"

    blocked = _evidence(blockingError="CACHE_INVALID")
    result = validate_ui_acceptance_evidence(blocked)
    assert result.status == "FAIL"
    assert "BLOCKING_ERROR" in result.failure_reasons


def test_ui_acceptance_rejects_raw_business_payload():
    from backend.services.gmv_ui_acceptance_service import validate_ui_acceptance_evidence

    with pytest.raises(ValueError, match="raw business data"):
        validate_ui_acceptance_evidence(_evidence(rawRows=[{"來源單據號": "S-1"}]))
