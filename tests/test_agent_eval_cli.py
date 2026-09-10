from __future__ import annotations

from scripts.agent_eval_report import main
from scripts.agent_eval_report import _load_input


def test_main_valid_empty_input_index_emits_canonical_72_slot_report(tmp_path, monkeypatch, capsys):
    import json
    from backend.agents.agent_eval_manifest import validate_manifest
    from scripts import agent_eval_report
    from tests.test_agent_eval_manifest import _manifest

    manifest = _manifest()
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    index = {"schemaVersion": "agent-eval-input-index-v1", "observations": [],
             "ledgers": [], "quality": [], "diagnostics": []}
    (tmp_path / "inputs.json").write_text(json.dumps(index), encoding="utf-8")

    def fake_build_report(manifest_value, observations, ledgers, quality, diagnostics, **kwargs):
        assert validate_manifest(manifest_value)["plannedSlots"] == 72
        assert observations == ledgers == quality == diagnostics == []
        return {"schemaVersion": "agent-eval-report-v1", "status": "available",
                "slots": [{"slot": index} for index in range(72)]}

    monkeypatch.setattr(agent_eval_report, "build_report", fake_build_report)
    code = agent_eval_report.main(["--root", str(tmp_path), "--manifest", "manifest.json",
                                   "--inputs", "inputs.json", "--format", "json"])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "available"
    assert len(output["slots"]) == 72


def test_missing_manifest_is_blocked(tmp_path, capsys):
    code = main(["--root", str(tmp_path), "--manifest", "missing.json", "--inputs", "inputs.json", "--format", "json"])
    assert code == 2
    assert "missing_manifest" in capsys.readouterr().out


def test_load_input_uses_index_reference_as_independent_artifact(tmp_path):
    import hashlib
    payload_path = tmp_path / "observation.json"
    payload_path.write_text('{"artifactRef":{"path":"other.json","sha256":"%s"}}' % ("0" * 64), encoding="utf-8")
    item = {"path": "observation.json", "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest()}
    payload = _load_input(tmp_path, item)
    assert payload["artifactRef"]["path"] == "other.json"
    assert item["path"] == "observation.json"


def test_load_input_rejects_non_object_index_item(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="invalid_input_index"):
        _load_input(tmp_path, "not-an-object")
