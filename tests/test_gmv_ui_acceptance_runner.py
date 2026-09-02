import json

import pytest


def test_ui_runner_requires_http_and_rejects_production_paths(tmp_path):
    from scripts.run_gmv_ui_acceptance import _validate_target

    with pytest.raises(ValueError, match="HTTP URL"):
        _validate_target("file:///tmp/app", tmp_path / "fixture")
    with pytest.raises(ValueError, match="production"):
        _validate_target("http://127.0.0.1:8502/", "/Users/chanwaitung2025/Downloads/nbs_analytics/.nbs_runtime_cache")


def test_ui_runner_accepts_explicit_ci_runner_temp(monkeypatch, tmp_path):
    from scripts.run_gmv_ui_acceptance import _validate_target

    runner_temp = tmp_path / "runner-temp"
    fixture = runner_temp / "nbs-ui-fixture"
    fixture.mkdir(parents=True)
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))

    assert _validate_target("http://127.0.0.1:8502/", fixture) == fixture.resolve()


def test_ui_runner_loads_only_bounded_evidence(tmp_path):
    from scripts.run_gmv_ui_acceptance import load_bounded_evidence

    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({
        "route": "http://127.0.0.1:8502/",
        "initialStatus": "CURRENT",
        "mergeStatus": "READY",
        "activeVersionId": "v1",
        "manifestSha256": "a" * 64,
        "downloadedArtifacts": {"total.detail": 10, "paid.detail": 11},
        "refreshedVersionId": "v1",
    }), encoding="utf-8")

    evidence = load_bounded_evidence(path)

    assert evidence.active_version_id == "v1"
    assert evidence.downloaded_artifacts == {"total.detail": 10, "paid.detail": 11}

    path.write_text(json.dumps({"rawRows": [{"來源單據號": "S-1"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="raw business data"):
        load_bounded_evidence(path)
