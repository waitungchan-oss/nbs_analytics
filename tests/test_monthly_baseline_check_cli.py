import json
import subprocess
import sys
from pathlib import Path

from scripts import monthly_baseline_check

ROOT = Path(__file__).resolve().parents[1]


def test_cli_returns_zero_for_monitoring_drift(monkeypatch, capsys):
    monkeypatch.setattr(
        monthly_baseline_check,
        "build_monthly_baseline_report",
        lambda: {
            "status": "drift",
            "blockingStatus": "matched",
            "registryVersion": "monthly-revenue-v1",
            "checks": [{"month": "2026-01", "mode": "monitoring", "status": "drift"}],
            "stableUploadCycles": 0,
            "promotionReady": False,
            "latestPromotion": None,
        },
    )

    exit_code = monthly_baseline_check.main([])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "drift"
    assert payload["blockingStatus"] == "matched"


def test_cli_returns_failure_only_for_blocking_drift(monkeypatch, capsys):
    monkeypatch.setattr(
        monthly_baseline_check,
        "build_monthly_baseline_report",
        lambda: {
            "status": "drift",
            "blockingStatus": "drift",
            "registryVersion": "monthly-revenue-v1",
            "checks": [{"month": "2026-01", "mode": "blocking", "status": "drift"}],
            "stableUploadCycles": 1,
            "promotionReady": False,
            "latestPromotion": {"id": 1},
        },
    )

    exit_code = monthly_baseline_check.main([])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["blockingStatus"] == "drift"


def test_script_runs_directly_from_project_root():
    completed = subprocess.run(
        [sys.executable, "scripts/monthly_baseline_check.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["registryVersion"] == "monthly-revenue-v1"
    assert payload["blockingStatus"] == "matched"
