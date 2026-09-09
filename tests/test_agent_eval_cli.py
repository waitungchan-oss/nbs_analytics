from __future__ import annotations

from scripts.agent_eval_report import main


def test_missing_manifest_is_blocked(tmp_path, capsys):
    code = main(["--root", str(tmp_path), "--manifest", "missing.json", "--inputs", "inputs.json", "--format", "json"])
    assert code == 2
    assert "missing_manifest" in capsys.readouterr().out
