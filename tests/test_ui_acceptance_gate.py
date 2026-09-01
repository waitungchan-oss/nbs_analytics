import json

from scripts.ui_acceptance_gate import run_ui_acceptance_gate


COMMIT = "a" * 40
SOURCE = "b" * 64


def _evidence(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({
        "route": "http://127.0.0.1:8501/",
        "initialStatus": "CURRENT", "mergeStatus": "READY", "activeVersionId": "v1",
        "manifestSha256": "c" * 64,
        "downloadedArtifacts": {"total.detail": 10, "paid.detail": 11},
        "refreshedVersionId": "v1", "commitSha": COMMIT, "sourceFingerprint": SOURCE,
    }), encoding="utf-8")
    return path


def test_ui_acceptance_gate_binds_http_and_identity(monkeypatch, tmp_path):
    evidence_path = _evidence(tmp_path)
    monkeypatch.setattr("scripts.ui_acceptance_gate.run_ui_acceptance", lambda **kwargs: {
        "status": "PASS", "route": kwargs["url"], "httpStatus": 200,
        "evidenceStatus": "PASS", "failureReasons": [],
    })
    result = run_ui_acceptance_gate(tmp_path, "http://127.0.0.1:8501/", tmp_path, evidence_path, COMMIT, SOURCE)
    assert result["schemaVersion"] == "ui-acceptance-gate-v1"
    assert result["status"] == "PASS"
    assert result["result"]["route"] == "http://127.0.0.1:8501/"


def test_ui_acceptance_gate_rejects_file_url_and_identity_mismatch(tmp_path):
    evidence_path = _evidence(tmp_path)
    try:
        run_ui_acceptance_gate(tmp_path, "file:///tmp/app", tmp_path, evidence_path, COMMIT, SOURCE)
    except ValueError as exc:
        assert "HTTP" in str(exc)
    else:
        raise AssertionError("file URL must be rejected")

    try:
        run_ui_acceptance_gate(tmp_path, "http://127.0.0.1:8501/", tmp_path, evidence_path, "d" * 40, SOURCE)
    except ValueError as exc:
        assert "commit" in str(exc)
    else:
        raise AssertionError("mismatched identity must be rejected")


def test_ui_acceptance_gate_maps_runner_failure_to_fail(monkeypatch, tmp_path):
    evidence_path = _evidence(tmp_path)
    monkeypatch.setattr("scripts.ui_acceptance_gate.run_ui_acceptance", lambda **kwargs: {
        "status": "FAIL", "route": kwargs["url"], "httpStatus": None,
        "evidenceStatus": "PASS", "failureReasons": ["HTTP_PROBE_FAILED"],
    })
    result = run_ui_acceptance_gate(tmp_path, "http://127.0.0.1:8501/", tmp_path, evidence_path, COMMIT, SOURCE)
    assert result["status"] == "FAIL"
    assert result["result"]["failureReasons"] == ["HTTP_PROBE_FAILED"]
