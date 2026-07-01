import json
from pathlib import Path

from scripts import phase2j_baseline_check


def test_main_prints_json_and_returns_zero_for_matched_baseline(monkeypatch, capsys):
    monkeypatch.setattr(
        phase2j_baseline_check,
        "check_phase2_baseline",
        lambda db_path: {
            "status": "matched",
            "baselineMonth": "2026-05",
            "formattedExpectedTotal": "HKD 12,057,968",
            "formattedActualTotal": "HKD 12,057,968",
            "deltaAmount": -0.08,
            "checks": [],
            "latestDataDate": "2026-06-29",
        },
    )

    exit_code = phase2j_baseline_check.main(["--db", "example.db"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "matched"
    assert payload["dbPath"] == str(Path("example.db"))


def test_main_returns_failure_for_drift(monkeypatch, capsys):
    monkeypatch.setattr(
        phase2j_baseline_check,
        "check_phase2_baseline",
        lambda db_path: {
            "status": "drift",
            "baselineMonth": "2026-05",
            "formattedExpectedTotal": "HKD 12,057,968",
            "formattedActualTotal": "HKD 1",
            "deltaAmount": -12057967,
            "checks": [],
            "latestDataDate": "2026-06-29",
        },
    )

    exit_code = phase2j_baseline_check.main([])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "drift"
