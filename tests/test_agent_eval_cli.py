from __future__ import annotations

from scripts.agent_eval_report import main
from scripts.agent_eval_report import _load_input


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
