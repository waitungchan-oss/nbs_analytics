import subprocess
import sys
from pathlib import Path

from scripts import profile_decision_api


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_profile_report_uses_warm_median_and_limit():
    report = profile_decision_api.build_profile_report(
        cold_seconds=0.8,
        warm_seconds=[0.08, 0.12, 0.10],
        warm_limit_ms=300.0,
    )

    assert report["coldMs"] == 800.0
    assert report["warmMedianMs"] == 100.0
    assert report["warmLimitMs"] == 300.0
    assert report["status"] == "passed"
    assert profile_decision_api.exit_code_for_report(report) == 0


def test_profile_report_fails_when_warm_median_exceeds_limit():
    report = profile_decision_api.build_profile_report(
        cold_seconds=0.8,
        warm_seconds=[0.31, 0.35, 0.40],
        warm_limit_ms=300.0,
    )

    assert report["warmMedianMs"] == 350.0
    assert report["status"] == "failed"
    assert profile_decision_api.exit_code_for_report(report) == 1


def test_profile_script_can_run_directly_from_project_root():
    result = subprocess.run(
        [sys.executable, "scripts/profile_decision_api.py", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--warm-limit-ms" in result.stdout
